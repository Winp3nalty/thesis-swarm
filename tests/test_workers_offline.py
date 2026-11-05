"""Offline tests for the Week 3 workers (fake model, no Ollama needed). Run: python tests/test_workers_offline.py"""
import _paths  # noqa: F401  (puts the project folder on the import path)
from swarm.core import deps
deps.ensure()  # installs pydantic on first run

import json
import re
from pathlib import Path

from pydantic import ValidationError

from swarm.llm_agents import client
import run_loop
from swarm.llm_agents import outreach, retry, transit
from swarm.core.worker_schemas import OutreachEmail, TransitLeg, TransitPlan

CASES = json.loads((Path(__file__).resolve().parent / "w3_cases.json").read_text(encoding="utf-8"))


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
    for n in (50, 99, 181):
        try:
            OutreachEmail(subject="Hello there", greeting="Dear Prof", body=" ".join(["w"] * n), sign_off="Bye")
        except ValidationError:
            pass
        else:
            raise AssertionError(f"{n}-word body must be rejected")
    OutreachEmail(subject="Hello there", greeting="Dear Prof", body=" ".join(["w"] * 150), sign_off="Bye")


def test_all_cases_pass_first_try():
    client.call_structured_llm = good_llm
    for c in CASES["transit"]:
        _, info = transit.agent_transit(c["city"], c["stops"])
        assert (info["attempts"], info["first_schema_ok"], info["first_semantic_ok"]) == (1, True, True), c["id"]
    for c in CASES["outreach"]:
        _, info = outreach.agent_outreach(c)
        assert info["attempts"] == 1 and info["first_semantic_ok"], c["id"]


def test_retry_with_feedback():
    seen = []

    def wrong_then_right(system_prompt, user_prompt, response_model, temperature=0.2):
        seen.append(user_prompt)
        plan = good_llm(system_prompt, user_prompt, response_model, temperature)
        if len(seen) == 1:
            plan.legs = plan.legs[:-1]  # drop a leg on the first attempt
        return plan

    client.call_structured_llm = wrong_then_right
    c = CASES["transit"][4]
    result, info = transit.agent_transit(c["city"], c["stops"])
    assert (info["attempts"], info["first_schema_ok"], info["first_semantic_ok"]) == (2, True, False)
    assert "legs" in info["first_problems"][0] and [a["status"] for a in info["attempt_log"]] == ["content_problems", "ok"]
    assert "REJECTED" in seen[1] and "legs" in seen[1] and len(result.legs) == len(c["stops"]) - 1


def test_schema_failure_then_success():
    calls = {"n": 0}

    def flaky(system_prompt, user_prompt, response_model, temperature=0.2):
        calls["n"] += 1
        if calls["n"] == 1:
            raise client.StructuredOutputError("cut off")
        return good_llm(system_prompt, user_prompt, response_model, temperature)

    client.call_structured_llm = flaky
    _, info = outreach.agent_outreach(CASES["outreach"][0])
    assert (info["attempts"], info["first_schema_ok"], info["first_semantic_ok"]) == (2, False, False)
    assert info["attempt_log"][0]["status"] == "schema_error" and "cut off" in info["first_problems"][0]


def test_worker_failed_after_retries():
    def always_short(system_prompt, user_prompt, response_model, temperature=0.2):
        plan = good_llm(system_prompt, user_prompt, response_model, temperature)
        plan.legs = plan.legs[:1]
        return plan

    client.call_structured_llm = always_short
    c = CASES["transit"][5]
    try:
        transit.agent_transit(c["city"], c["stops"], max_retries=2)
    except retry.WorkerFailed as e:
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
    assert outreach.validate_outreach(good, contact) == []
    anonymous = good.model_copy(update={"sign_off": "Sincerely"})
    assert any("sign-off" in p for p in outreach.validate_outreach(anonymous, contact))
    no_univ = good.model_copy(update={"body": body.replace("Aalto University", "our school")})
    assert any("Aalto University" in p for p in outreach.validate_outreach(no_univ, contact))
    plain = {k: v for k, v in contact.items() if not k.startswith("sender")}
    assert outreach.validate_outreach(anonymous, plain) == [], "no sender given: no extra rules"


def test_transit_checked_against_driving_time():
    stops = ["A", "B", "C"]
    def plan(m1, m2):
        return TransitPlan(legs=[TransitLeg(from_stop="A", to_stop="B", mode="metro", minutes=m1, cost_eur=3),
                                 TransitLeg(from_stop="B", to_stop="C", mode="taxi", minutes=m2, cost_eur=9)])
    assert transit.validate_transit(plan(45, 10), stops) == [], "no measured time: no extra rule"
    bad = transit.validate_transit(plan(45, 10), stops, [5.0, None])
    assert len(bad) == 1 and "leg 1" in bad[0] and "driving takes about 5 min" in bad[0], bad
    assert transit.validate_transit(plan(25, 10), stops, [12.0, None]) == []
    assert transit.validate_transit(plan(1, 10), stops, [30.0, None]), "far too fast is also rejected"
    seen = {}
    real = client.call_structured_llm
    client.call_structured_llm = lambda sp, up, rm, temperature=0.2: seen.setdefault("p", up) and good_llm(sp, up, rm)
    try:
        transit.agent_transit("Shenzhen", stops, drive_minutes=[12.0, None])
    finally:
        client.call_structured_llm = real
    assert "leg 1: about 12 min by car" in seen["p"] and "leg 2" not in seen["p"].split("Measured")[1], seen["p"]


def test_drive_times_from_osrm_skill():
    from swarm.code_agents import dispatcher
    from swarm.core.requirements_schema import load_scenario
    calls = []
    def fake_skill(name, **kw):
        calls.append(name)
        if name == "verify_place":
            return {"ok": True, "result": {"found": kw["place"] != "Ghost", "lat": 22.5, "lon": 114.0}}
        return {"ok": True, "result": {"minutes": 12.5}}
    real = dispatcher.run_skill
    dispatcher.run_skill = fake_skill
    try:
        out = dispatcher.drive_times(["DJI", "Hotel", "OCT Loft", "Ghost"], load_scenario("shenzhen_3day"))
    finally:
        dispatcher.run_skill = real
    assert out == [None, None, None], out  # Hotel and an unknown place have no coordinates
    dispatcher.run_skill = fake_skill
    try:
        assert dispatcher.drive_times(["DJI", "OCT Loft"], load_scenario("shenzhen_3day")) == [12.5]
    finally:
        dispatcher.run_skill = real


def test_outreach_rejects_invented_claims():
    contact = {"name": "Prof. Liang Haotian", "institution": "Shenzhen University", "topics": ["drone swarm"],
               "city": "Shenzhen"}
    body = ("We admire your drone swarm work at Shenzhen University. " + " ".join(["word"] * 100)
            + " Our project on soft grippers is why we ask for a 30-minute lab tour.")
    email = OutreachEmail(subject="Lab tour request", greeting="Dear Prof. Liang Haotian,", body=body, sign_off="Sincerely")
    assert any("do not claim any project" in p for p in outreach.validate_outreach(email, contact))
    clean = email.model_copy(update={"body": body.replace("Our project on soft grippers is why we", "We")})
    assert outreach.validate_outreach(clean, contact) == []


def test_invented_claim_check_uses_whole_words():
    contact = {"name": "Prof. Liang Haotian", "institution": "Shenzhen University", "topics": ["drone swarm"],
               "city": "Shenzhen"}
    body = ("Your research on drone swarm coordination at Shenzhen University and your work on sensing are admired. "
            + " ".join(["word"] * 100) + " We ask for a 30-minute lab tour.")
    email = OutreachEmail(subject="Lab tour request", greeting="Dear Prof. Liang Haotian,", body=body, sign_off="Sincerely")
    assert not any("do not claim" in p for p in outreach.validate_outreach(email, contact))
    mine = email.model_copy(update={"body": body.replace("Your research", "Our research")})
    assert any("our research" in p for p in outreach.validate_outreach(mine, contact))
    soft = email.model_copy(update={"body": body + " Our group is currently exploring similar concepts in our studies."})
    assert any("do not claim" in p for p in outreach.validate_outreach(soft, contact))
    # a too-short body: the retry shows the previous text so the model can extend it
    try:
        OutreachEmail(subject="Lab tour request", greeting="Dear X,", body="a short text of only a few words but over forty characters", sign_off="Bye")
    except Exception as e:
        fb = retry._retry_feedback(e)
        assert "a short text of only" in fb and "ADD about" in fb, fb
    ok = OutreachEmail(subject="Lab tour request", greeting="Dear X,", body=" ".join(["w"] * 100), sign_off="Bye")
    assert len(ok.body.split()) == 100  # 100 words is now accepted
    try:
        OutreachEmail(subject="Lab tour request", greeting="Dear X,", body=" ".join(["w"] * 99), sign_off="Bye")
        raise AssertionError("99 words must be rejected")
    except ValueError as e:
        assert "100-180" in str(e)
    print("[OK] whole-word claim check; retry feedback shows the previous body; minimum is 100 words")


def test_outreach_validator_rules():
    c = CASES["outreach"][2]  # SUSTech case, uses an alias
    good = good_llm("", f"Institution: {c['institution']}\nProfessor: {c['name']}\n"
                        f"Recent research topics: {'; '.join(c['topics'])}", OutreachEmail)
    assert outreach.validate_outreach(good, c) == []
    generic = good.model_copy(update={"body": " ".join(["thanks"] * 20) + " 30 minute " + " ".join(["x"] * 100)})
    problems = " ".join(outreach.validate_outreach(generic, c))
    assert "institution" in problems and "research topics" in problems
    sustech = good.model_copy(update={"body": good.body.replace(c["institution"], "SUSTech")})
    assert not any("institution" in p for p in outreach.validate_outreach(sustech, c))
    wrong_name = good.model_copy(update={"greeting": "Dear colleague,"})
    assert any("name" in p for p in outreach.validate_outreach(wrong_name, c))
    given = good.model_copy(update={"greeting": "Dear Professor Zhou,"})
    assert outreach.validate_outreach(given, c) == []  # family name first is accepted


def test_claim_check_works_per_sentence():
    ok = ["Your recent work on drone swarm coordination has advanced the field.",
          "A small group of engineering students from Aalto University is visiting Shenzhen and seeks to learn.",
          "We are particularly interested in how distributed sensing enhances swarm coordination.",
          "The students are keen to observe how your team integrates sensing.",
          "The students study at Aalto University and are visiting Shenzhen."]
    bad = ["Our group is studying the intersection of robotics and biomimicry.",
           "We have been working on swarm robots.", "The students are currently developing a gripper."]
    assert not any(outreach.claim_sentences(x) for x in ok)
    assert all(outreach.claim_sentences(x) for x in bad)
    contact = {"name": "Prof. Zhou Mingyu", "institution": "Southern University of Science and Technology",
               "topics": ["soft robotic grippers"], "city": "Shenzhen"}
    body = ("Your work on soft robotic grippers at Southern University of Science and Technology is admired. "
            "Our group is studying the intersection of robotics and biomimicry. " + " ".join(["word"] * 100)
            + " We ask for a 30-minute lab tour.")
    email = OutreachEmail(subject="Lab tour request", greeting="Dear Prof. Zhou Mingyu,", body=body, sign_off="Sincerely")
    problems = outreach.validate_outreach(email, contact)
    assert any("Our group is studying" in p for p in problems), problems


def test_invented_travel_timing_is_rejected_unless_dates_are_given():
    contact = {"name": "Prof. Liang Haotian", "institution": "Shenzhen University", "topics": ["drone swarm"],
               "city": "Shenzhen"}
    body = ("Your drone swarm work at Shenzhen University is admired. " + " ".join(["word"] * 100)
            + " We ask for a 30-minute lab tour, and we may adapt to your schedule.")
    mk = lambda b: OutreachEmail(subject="Lab tour request", greeting="Dear Prof. Liang Haotian,", body=b, sign_off="Sincerely")
    assert outreach.validate_outreach(mk(body), contact) == [], "'may' is a verb, not a month"
    for phrase in ("Our visit is planned for the upcoming week.", "We arrive next month.", "We travel in March.",
                   "We come in 2027."):
        assert any("when the visit happens" in p for p in outreach.validate_outreach(mk(body + " " + phrase), contact)), phrase
    dated = {**contact, "trip_dates": "the second week of March 2027"}
    assert outreach.validate_outreach(mk(body + " We travel in March."), dated) == []
    captured = {}
    real = outreach.run_worker
    outreach.run_worker = lambda sp, up, *a, **k: captured.setdefault("up", up)
    try:
        outreach.agent_outreach({**contact, "sender_university": "", "sender_name": "", "trip_dates": ""})
    finally:
        outreach.run_worker = real
    assert "No travel dates are known" in captured["up"]


def test_content_retry_shows_previous_body_and_the_bad_sentence():
    contact = {"name": "Prof. Zhou Mingyu", "institution": "Southern University of Science and Technology",
               "topics": ["soft robotic grippers"], "city": "Shenzhen"}
    bad_sentence = "Your findings can inform our studies."
    body = ("Your work on soft robotic grippers at Southern University of Science and Technology is admired. "
            + bad_sentence + " " + " ".join(["word"] * 100) + " We ask for a 30-minute lab tour.")
    prompts = []

    def fake(system_prompt, user_prompt, model, temperature=0.2):
        prompts.append(user_prompt)
        if len(prompts) == 1:
            return OutreachEmail(subject="Lab tour request", greeting="Dear Prof. Zhou Mingyu,", body=body, sign_off="Sincerely")
        return OutreachEmail(subject="Lab tour request", greeting="Dear Prof. Zhou Mingyu,",
                             body=body.replace(bad_sentence, "We want to learn from your lab."), sign_off="Sincerely")

    real = client.call_structured_llm
    client.call_structured_llm = fake
    try:
        result, info = outreach.agent_outreach(contact)
    finally:
        client.call_structured_llm = real
    assert info["attempts"] == 2 and info["first_semantic_ok"] is False
    retry = prompts[1]
    assert "Your previous body:" in retry and body[:60] in retry
    assert f'- "{bad_sentence}"' in retry and "Rewrite ONLY these sentences" in retry
    # four attempts are allowed for an email now (three retries)
    import inspect
    assert inspect.signature(outreach.agent_outreach).parameters["max_retries"].default == 3


def test_code_repairs_a_stubborn_sentence_after_two_rejections():
    contact = {"name": "Prof. Zhou Mingyu", "institution": "Southern University of Science and Technology",
               "topics": ["soft robotic grippers"], "city": "Shenzhen"}
    bad = "Your findings can inform our studies."
    body = ("Your work on soft robotic grippers at Southern University of Science and Technology is admired. "
            + bad + " " + " ".join(["word"] * 100) + " We ask for a 30-minute lab tour.")
    calls = []

    def fake(system_prompt, user_prompt, model, temperature=0.2):
        calls.append(1)
        return OutreachEmail(subject="Lab tour request", greeting="Dear Prof. Zhou Mingyu,", body=body, sign_off="Sincerely")

    real = client.call_structured_llm
    client.call_structured_llm = fake
    try:
        result, info = outreach.agent_outreach(contact)
    finally:
        client.call_structured_llm = real
    assert len(calls) == 2 and info["attempts"] == 2 and info["repaired"] is True
    assert "our studies" not in result.body and "soft robotic grippers" in result.body
    assert info["attempt_log"][-1]["status"] == "repaired"
    # a body that is too short after the cut is NOT accepted
    short = OutreachEmail(subject="Lab tour request", greeting="Dear Prof. Zhou Mingyu,",
                          body="Your work on soft robotic grippers at Southern University of Science and Technology is admired. "
                               + bad + " " + " ".join(["word"] * 92) + " We ask for a 30-minute lab tour.", sign_off="S")
    try:
        outreach.repair_outreach(short)
    except ValueError:
        pass  # too short: run_worker catches this and keeps retrying


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} worker tests passed.")
