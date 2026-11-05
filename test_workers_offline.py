"""Offline tests for the Week 3 workers (fake model, no Ollama needed). Run: python test_workers_offline.py"""
import deps

deps.ensure()  # installs pydantic on first run

import json
import re
from pathlib import Path

from pydantic import ValidationError

import run_loop
import workers
from worker_schemas import OutreachEmail, TransitLeg, TransitPlan

CASES = json.loads((Path(__file__).resolve().parent / "tests" / "w3_cases.json").read_text(encoding="utf-8"))


def good_llm(system_prompt, user_prompt, response_model, temperature=0.2):
    if response_model is TransitPlan:
        stops = re.findall(r"^\d+\. (.+)$", user_prompt, re.M)
        return TransitPlan(legs=[TransitLeg(from_stop=a, to_stop=b, mode="Metro", minutes=20, cost_eur=2.5)
                                 for a, b in zip(stops, stops[1:])])
    inst = re.search(r"Institution: (.+)", user_prompt).group(1)
    name = re.search(r"Professor: (.+)", user_prompt).group(1)
    topic = re.search(r"Recent research topics: (.+)", user_prompt).group(1)
    body = (f"We are a small group of engineering students and we admire your work at {inst} on {topic}. "
            "We would be grateful for a 30-minute lab tour. " + " ".join(["word"] * 120))
    return OutreachEmail(subject="Request for a short lab tour", greeting=f"Dear Professor {name.split()[-1]},",
                         body=body, sign_off="Sincerely")


def test_case_file():
    assert len(CASES["transit"]) == 20 and len(CASES["outreach"]) == 20
    for key in ("transit", "outreach"):
        ids = [c["id"] for c in CASES[key]]
        assert len(set(ids)) == 20
    assert all(len(c["stops"]) >= 2 for c in CASES["transit"])
    assert all(c["name"] and c["institution"] and c["topics"] and c["city"] for c in CASES["outreach"])


def test_schemas():
    for bad in (0, 301):
        try:
            TransitLeg(from_stop="a", to_stop="b", mode="x", minutes=bad, cost_eur=1)
        except ValidationError:
            pass
        else:
            raise AssertionError("minutes outside 1-300 must be rejected")
    for n in (50, 119, 181):
        try:
            OutreachEmail(subject="Hello there", greeting="Dear Prof", body=" ".join(["w"] * n), sign_off="Bye")
        except ValidationError:
            pass
        else:
            raise AssertionError(f"{n}-word body must be rejected")
    OutreachEmail(subject="Hello there", greeting="Dear Prof", body=" ".join(["w"] * 150), sign_off="Bye")


def test_all_cases_pass_first_try():
    run_loop.call_structured_llm = good_llm
    for c in CASES["transit"]:
        _, info = workers.agent_transit(c["city"], c["stops"])
        assert (info["attempts"], info["first_schema_ok"], info["first_semantic_ok"]) == (1, True, True), c["id"]
    for c in CASES["outreach"]:
        _, info = workers.agent_outreach(c)
        assert info["attempts"] == 1 and info["first_semantic_ok"], c["id"]


def test_retry_with_feedback():
    seen = []

    def wrong_then_right(system_prompt, user_prompt, response_model, temperature=0.2):
        seen.append(user_prompt)
        plan = good_llm(system_prompt, user_prompt, response_model, temperature)
        if len(seen) == 1:
            plan.legs = plan.legs[:-1]  # drop a leg on the first attempt
        return plan

    run_loop.call_structured_llm = wrong_then_right
    c = CASES["transit"][4]
    result, info = workers.agent_transit(c["city"], c["stops"])
    assert (info["attempts"], info["first_schema_ok"], info["first_semantic_ok"]) == (2, True, False)
    assert "legs" in info["first_problems"][0] and [a["status"] for a in info["attempt_log"]] == ["content_problems", "ok"]
    assert "REJECTED" in seen[1] and "legs" in seen[1] and len(result.legs) == len(c["stops"]) - 1


def test_schema_failure_then_success():
    calls = {"n": 0}

    def flaky(system_prompt, user_prompt, response_model, temperature=0.2):
        calls["n"] += 1
        if calls["n"] == 1:
            raise run_loop.StructuredOutputError("cut off")
        return good_llm(system_prompt, user_prompt, response_model, temperature)

    run_loop.call_structured_llm = flaky
    _, info = workers.agent_outreach(CASES["outreach"][0])
    assert (info["attempts"], info["first_schema_ok"], info["first_semantic_ok"]) == (2, False, False)
    assert info["attempt_log"][0]["status"] == "schema_error" and "cut off" in info["first_problems"][0]


def test_worker_failed_after_retries():
    def always_short(system_prompt, user_prompt, response_model, temperature=0.2):
        plan = good_llm(system_prompt, user_prompt, response_model, temperature)
        plan.legs = plan.legs[:1]
        return plan

    run_loop.call_structured_llm = always_short
    c = CASES["transit"][5]
    try:
        workers.agent_transit(c["city"], c["stops"], max_retries=2)
    except workers.WorkerFailed as e:
        assert e.info["attempts"] == 3
    else:
        raise AssertionError("must fail after exhausting retries")


def test_outreach_sender_rules():
    contact = {"name": "Prof. Liang Haotian", "institution": "Shenzhen University", "topics": ["drone swarm"],
               "city": "Shenzhen", "sender_name": "Anna Virtanen", "sender_university": "Aalto University"}
    body = ("We admire your drone swarm work at Shenzhen University. " + " ".join(["word"] * 100)
            + " We study at Aalto University and ask for a 30-minute lab tour.")
    good = OutreachEmail(subject="Lab tour request", greeting="Dear Prof. Liang Haotian,", body=body,
                         sign_off="Sincerely, Anna Virtanen")
    assert workers.validate_outreach(good, contact) == []
    anonymous = good.model_copy(update={"sign_off": "Sincerely"})
    assert any("sign-off" in p for p in workers.validate_outreach(anonymous, contact))
    no_univ = good.model_copy(update={"body": body.replace("Aalto University", "our school")})
    assert any("Aalto University" in p for p in workers.validate_outreach(no_univ, contact))
    plain = {k: v for k, v in contact.items() if not k.startswith("sender")}
    assert workers.validate_outreach(anonymous, plain) == [], "no sender given: no extra rules"


def test_transit_checked_against_driving_time():
    stops = ["A", "B", "C"]
    def plan(m1, m2):
        return TransitPlan(legs=[TransitLeg(from_stop="A", to_stop="B", mode="metro", minutes=m1, cost_eur=3),
                                 TransitLeg(from_stop="B", to_stop="C", mode="taxi", minutes=m2, cost_eur=9)])
    assert workers.validate_transit(plan(45, 10), stops) == [], "no measured time: no extra rule"
    bad = workers.validate_transit(plan(45, 10), stops, [5.0, None])
    assert len(bad) == 1 and "leg 1" in bad[0] and "driving takes about 5 min" in bad[0], bad
    assert workers.validate_transit(plan(25, 10), stops, [12.0, None]) == []
    assert workers.validate_transit(plan(1, 10), stops, [30.0, None]), "far too fast is also rejected"
    seen = {}
    real = run_loop.call_structured_llm
    run_loop.call_structured_llm = lambda sp, up, rm, temperature=0.2: seen.setdefault("p", up) and good_llm(sp, up, rm)
    try:
        workers.agent_transit("Shenzhen", stops, drive_minutes=[12.0, None])
    finally:
        run_loop.call_structured_llm = real
    assert "leg 1: about 12 min by car" in seen["p"] and "leg 2" not in seen["p"].split("Measured")[1], seen["p"]


def test_drive_times_from_osrm_skill():
    import coordinator
    from requirements_schema import load_scenario
    calls = []
    def fake_skill(name, **kw):
        calls.append(name)
        if name == "verify_place":
            return {"ok": True, "result": {"found": kw["place"] != "Ghost", "lat": 22.5, "lon": 114.0}}
        return {"ok": True, "result": {"minutes": 12.5}}
    real = coordinator.run_skill
    coordinator.run_skill = fake_skill
    try:
        out = coordinator.drive_times(["DJI", "Hotel", "OCT Loft", "Ghost"], load_scenario("shenzhen_3day"))
    finally:
        coordinator.run_skill = real
    assert out == [None, None, None], out  # Hotel and an unknown place have no coordinates
    coordinator.run_skill = fake_skill
    try:
        assert coordinator.drive_times(["DJI", "OCT Loft"], load_scenario("shenzhen_3day")) == [12.5]
    finally:
        coordinator.run_skill = real


def test_outreach_rejects_invented_claims():
    contact = {"name": "Prof. Liang Haotian", "institution": "Shenzhen University", "topics": ["drone swarm"],
               "city": "Shenzhen"}
    body = ("We admire your drone swarm work at Shenzhen University. " + " ".join(["word"] * 100)
            + " Our project on soft grippers is why we ask for a 30-minute lab tour.")
    email = OutreachEmail(subject="Lab tour request", greeting="Dear Prof. Liang Haotian,", body=body, sign_off="Sincerely")
    assert any("do not claim any project" in p for p in workers.validate_outreach(email, contact))
    clean = email.model_copy(update={"body": body.replace("Our project on soft grippers is why we", "We")})
    assert workers.validate_outreach(clean, contact) == []


def test_outreach_validator_rules():
    c = CASES["outreach"][2]  # SUSTech case, uses an alias
    good = good_llm("", f"Institution: {c['institution']}\nProfessor: {c['name']}\n"
                        f"Recent research topics: {'; '.join(c['topics'])}", OutreachEmail)
    assert workers.validate_outreach(good, c) == []
    generic = good.model_copy(update={"body": " ".join(["thanks"] * 20) + " 30 minute " + " ".join(["x"] * 100)})
    problems = " ".join(workers.validate_outreach(generic, c))
    assert "institution" in problems and "research topics" in problems
    sustech = good.model_copy(update={"body": good.body.replace(c["institution"], "SUSTech")})
    assert not any("institution" in p for p in workers.validate_outreach(sustech, c))
    wrong_name = good.model_copy(update={"greeting": "Dear colleague,"})
    assert any("name" in p for p in workers.validate_outreach(wrong_name, c))
    given = good.model_copy(update={"greeting": "Dear Professor Zhou,"})
    assert workers.validate_outreach(given, c) == []  # family name first is accepted


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} worker tests passed.")
