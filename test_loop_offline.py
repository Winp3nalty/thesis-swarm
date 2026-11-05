"""
Offline test of the whole swarm loop with a FAKE model (no Ollama, no GPU needed).
It also works as a demo: it shows every fault mode being caught and corrected.

Run:  python test_loop_offline.py
Needs pydantic installed (the setup.ps1 venv has it).
"""
import deps

deps.ensure()  # installs pydantic on first run

from requirements_schema import load_scenario
import json
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

import run_loop
from schemas import AuditResult, ExpeditionPlan, RoughItinerary

HUBS = ["DJI", "HAX", "Huaqiangbei"]


def fake_llm(system_prompt, user_prompt, response_model, temperature=0.2):
    """Deterministic stand-in for the local model."""
    if response_model is RoughItinerary:
        return RoughItinerary.model_validate({
            "destination": "Shenzhen",
            "days": [{"day": i + 1, "focus_topic": h,
                      "target_hubs": [{"hub_name": h, "purpose": "robotics visit"}]}
                     for i, h in enumerate(HUBS)],
        })
    if response_model is ExpeditionPlan:
        return ExpeditionPlan.model_validate({
            "destination": "Shenzhen", "duration_days": 3, "total_budget_eur": 1150.0,  # model's wrong claim
            "days": [{"day": i + 1, "focus_topic": h, "activities": [
                {"start_time": "09:00", "end_time": "12:00", "location": h,
                 "activity_description": f"Visit {h}", "transit_mode": "Metro", "cost_eur": 100.0},
                {"start_time": "13:00", "end_time": "16:00", "location": h,
                 "activity_description": f"Workshop at {h}", "transit_mode": "Walk", "cost_eur": 100.0},
            ]} for i, h in enumerate(HUBS)],
        })
    if response_model is AuditResult:
        return AuditResult(passed=True, failed_agent="none", critique_notes=[])
    raise AssertionError(f"unexpected model {response_model}")


def last_path(mode):
    """State path of the newest audit log for this mode."""
    logs = sorted(Path(run_loop.LOG_DIR).glob(f"audit_*_{mode}.jsonl"))
    events = [json.loads(line) for line in logs[-1].read_text().splitlines()]
    return ["pending"] + [e["to"] for e in events if e["type"] == "transition"], events


def main():
    real_call = run_loop.call_structured_llm
    run_loop.call_structured_llm = fake_llm
    os.chdir(tempfile.mkdtemp())  # keep results.csv and logs/ out of the project
    os.environ["SWARM_OFFLINE"] = "1"  # tests never touch the network

    request = load_scenario("shenzhen_3day")
    P, S, R, C = "planning", "scheduling", "reviewing", "completed"

    expectations = {
        # mode: (state path the task must take)
        "none": ["pending", P, S, R, C],
        # hub wiped -> auditor blames planner -> planner reruns
        "missing_hub": ["pending", P, S, R, P, S, R, C],
        # budget spike -> auditor blames logistics -> logistics-only fix
        "budget_spike": ["pending", P, S, R, S, R, C],
        # budget locked bad 3 times -> logistics twice, escalate to planner, then recover
        "saturation_escalation": ["pending", P, S, R, S, R, P, S, R, S, R, C],
    }

    for mode, expected in expectations.items():
        print(f"\n########## OFFLINE MODE: {mode} ##########")
        result = run_loop.run_swarm_loop(request, max_iterations=6,
                                         inject_mode=None if mode == "none" else mode)
        assert result is not None, f"{mode}: loop did not converge"
        assert result.total_budget_eur == 600.0, f"{mode}: total not recomputed from activities"
        path, events = last_path(mode)
        assert path == expected, f"{mode}: expected {expected}, got {path}"
        denied = [e for e in events if "DENIED" in e.get("message", "")]
        assert not denied, f"{mode}: illegal handoff attempted"
        print(f"[OK] {mode}: {' -> '.join(path)}")

    # A schedule problem the LLM never mentions must still be caught by the skill.
    def overlapping_llm(system_prompt, user_prompt, response_model, temperature=0.2):
        plan = fake_llm(system_prompt, user_prompt, response_model, temperature)
        if response_model is ExpeditionPlan and "PREVIOUS SCHEDULE" not in user_prompt:
            plan.days[1].activities[1].start_time = "11:00"  # overlaps 09:00-12:00
        return plan

    run_loop.call_structured_llm = overlapping_llm
    print("\n########## OFFLINE: overlapping schedule ##########")
    assert run_loop.run_swarm_loop(request, max_iterations=4) is not None
    print("[OK] overlap caught by schedule_check and corrected")

    # An unparseable model reply (parsed=None) must be retried, not crash the loop.
    seen = {"n": 0}

    def flaky_llm(system_prompt, user_prompt, response_model, temperature=0.2):
        if response_model is ExpeditionPlan and seen["n"] == 0:
            seen["n"] += 1
            raise run_loop.StructuredOutputError("model returned no parseable ExpeditionPlan")
        return fake_llm(system_prompt, user_prompt, response_model, temperature)

    run_loop.call_structured_llm = flaky_llm
    print("\n########## OFFLINE: unparseable model reply ##########")
    assert run_loop.run_swarm_loop(request, max_iterations=4) is not None
    print("[OK] unparseable reply retried instead of crashing")

    # An empty day must be rejected by the Auditor, even if the model says it is fine.
    def empty_day_llm(system_prompt, user_prompt, response_model, temperature=0.2):
        plan = fake_llm(system_prompt, user_prompt, response_model, temperature)
        if response_model is ExpeditionPlan and "PREVIOUS SCHEDULE" not in user_prompt:
            # Keep every hub in the plan, but leave day 3 empty.
            plan.days[1].activities += plan.days[2].activities
            plan.days[2].activities = []
        return plan

    run_loop.call_structured_llm = empty_day_llm
    print("\n########## OFFLINE: empty day ##########")
    result = run_loop.run_swarm_loop(request, max_iterations=4)
    assert result is not None and all(d.activities for d in result.days)
    print("[OK] empty day caught and filled")

    # The real call_structured_llm talks to Ollama's native chat API. Test it with fake replies.
    sent = []

    def good_post(payload):
        sent.append(payload)
        body = AuditResult(passed=True, failed_agent="none", critique_notes=[]).model_dump_json()
        return {"message": {"content": body, "thinking": ""}, "done_reason": "stop",
                "prompt_eval_count": 50, "eval_count": 20}

    run_loop._post_chat = good_post
    assert real_call("sys", "usr", AuditResult, temperature=0.0).passed is True
    p = sent[-1]
    assert p["stream"] is False and p["think"] is False
    assert p["format"] == AuditResult.model_json_schema()
    assert p["options"] == {"temperature": 0.0, "num_ctx": run_loop.NUM_CTX}
    assert run_loop.CALL_STATS[-1]["completion_tokens"] == 20
    print("[OK] request has JSON schema, think=false, num_ctx; stats recorded")

    run_loop._post_chat = lambda payload: {"message": {"content": '{"passed": tr'}, "done_reason": "length"}
    try:
        real_call("s", "u", AuditResult)
    except run_loop.StructuredOutputError as e:
        assert "done_reason=length" in str(e)
    else:
        raise AssertionError("a cut-off reply must raise StructuredOutputError")
    print("[OK] cut-off reply raises StructuredOutputError with diagnostics")

    calls = []

    def picky_post(payload):
        calls.append(dict(payload))
        if "think" in payload:
            raise run_loop.OllamaHTTPError(400, '"model" does not support thinking')
        body = AuditResult(passed=False, failed_agent="logistics", critique_notes=["x"]).model_dump_json()
        return {"message": {"content": body}, "done_reason": "stop"}

    run_loop._post_chat = picky_post
    assert real_call("s", "u", AuditResult).failed_agent == "logistics"
    assert "think" in calls[0] and "think" not in calls[1]
    print("[OK] server that rejects 'think' is retried without it")

    run_loop._post_chat = lambda payload: {"message": {"content": "not json"}, "done_reason": "stop"}
    try:
        real_call("s", "u", AuditResult)
    except ValidationError:
        pass
    else:
        raise AssertionError("invalid JSON must raise ValidationError")
    print("[OK] invalid JSON raises ValidationError (retried by the loop)")

    rows = Path(run_loop.RESULTS_FILE).read_text().splitlines()
    assert len(rows) == 1 + 7 and all("SUCCESS" in r for r in rows[1:])
    assert rows[6].split(",")[6] == "1", "schema error was not counted"

    # results.csv: GPU name and token columns, and an old-format file is renamed, not lost.
    os.chdir(tempfile.mkdtemp())
    Path("results.csv").write_text("timestamp,model,inject_mode,iterations,status,wall_clock_s,schema_errors\n"
                                   "2026-11-03T15:29:52,qwen3:8b,none,1,SUCCESS,100.74,0\n")
    run_loop._gpu_name = lambda: "Test GPU 123"
    run_loop.CALL_STATS = [{"seconds": 1.25, "prompt_tokens": 100, "completion_tokens": 40},
                           {"seconds": 2.0, "prompt_tokens": 50, "completion_tokens": 10}]
    run_loop.log_run("none", 1, "SUCCESS", 3.5, 0)
    assert Path("results_old.csv").exists() and "100.74" in Path("results_old.csv").read_text()
    new_rows = Path("results.csv").read_text().splitlines()
    assert new_rows[0] == ",".join(run_loop.CSV_HEADER) and len(new_rows) == 2
    row = dict(zip(run_loop.CSV_HEADER, new_rows[1].split(",")))
    assert (row["gpu_name"], row["prompt_tokens"], row["completion_tokens"]) == ("Test GPU 123", "150", "50")
    assert row["llm_seconds"] in ("3.2", "3.3")
    assert row["version"] == run_loop.version_info()["version"] and row["rules_hash"], row
    run_loop.CALL_STATS = []
    print("[OK] results.csv has gpu_name and token columns; old file kept as results_old.csv")
    # Logistics gets real exchange rates from the internet skill, and the call is logged.
    from skills import web
    from state_machine import TaskRecord
    os.environ.pop("SWARM_OFFLINE")
    web._CACHE = Path(tempfile.mkdtemp()) / "fx.json"
    web._fetch_live = lambda: {"date": "2026-11-03", "rates": {"EUR": 1.0, "CNY": 8.2, "USD": 1.15}}
    rec = TaskRecord("fx_test", "req", log_dir=tempfile.mkdtemp())
    ctx = run_loop._exchange_rate_context(rec)
    assert "1 EUR = 8.2 CNY" in ctx and "1 EUR = 1.15 USD" in ctx, ctx
    log = Path(rec.log_path).read_text()
    assert "live_exchange_rate EUR->CNY" in log and "source=live" in log
    captured = {}
    run_loop.call_structured_llm = lambda sp, up, rm, temperature=0.2: captured.setdefault("p", up) and fake_llm(sp, up, rm)
    rough = fake_llm("", "", RoughItinerary)
    run_loop.agent_logistics_specialist(rough, 1200, record=rec)
    assert "1 EUR = 8.2 CNY" in captured["p"], "rates missing from the Logistics prompt"
    print("[OK] Logistics prompt contains live rates; skill call logged with its source")
    # Transport words are not visits: a day whose only "visit" is the metro is a filler day.
    import reality as _r
    metro_day = ExpeditionPlan.model_validate({"destination": "Shenzhen, China", "duration_days": 1,
        "total_budget_eur": 1.0, "days": [{"day": 1, "focus_topic": "x", "activities": [
            {"start_time": "09:00", "end_time": "10:00", "location": "Shenzhen Metro",
             "activity_description": "Ride", "transit_mode": "metro", "cost_eur": 10.0}]}]})
    os.environ["SWARM_OFFLINE"] = "1"
    assert any("no real visit" in i for i in _r.reality_check(metro_day, [])["planner_issues"])
    assert not _r._is_generic("Shenzhen Business School")  # whole words only
    os.environ.pop("SWARM_OFFLINE")
    # Reality checks with a fake OpenStreetMap: found / not found / unreachable / impossible gaps.
    import reality
    from skills import web
    web._WEB_CACHE = Path(tempfile.mkdtemp()) / "web_cache.json"
    os.environ.pop("SWARM_OFFLINE", None)
    places = {"dji, shenzhen": (22.54, 113.95), "huaqiangbei, shenzhen": (22.54, 114.09),
              "hax, shenzhen": None, "fake lab, shenzhen": None}
    state = {"down": False}

    def fake_nominatim(query):
        if state["down"]:
            raise OSError("blocked")
        key = query.lower().replace(", china", "")
        pos = places.get(key)
        return [{"lat": str(pos[0]), "lon": str(pos[1]), "display_name": query}] if pos else []

    web._nominatim = fake_nominatim
    web._osrm = lambda a, b, c, d: 50 * 60.0  # every drive takes 50 minutes
    web.time.sleep = lambda s: None

    def act(start, end, loc, desc="Visit", cost=100.0):
        return {"start_time": start, "end_time": end, "location": loc, "activity_description": desc,
                "transit_mode": "Taxi", "cost_eur": cost}

    def plan_with(days):
        return ExpeditionPlan.model_validate({"destination": "Shenzhen, China", "duration_days": len(days),
            "total_budget_eur": 1.0, "days": [{"day": i + 1, "focus_topic": "x", "activities": d}
                                              for i, d in enumerate(days)]})

    hubs = ["DJI", "HAX", "Huaqiangbei"]
    good = plan_with([[act("09:00", "11:00", "DJI")],
                      [act("09:00", "11:00", "HAX"), act("12:30", "14:00", "Huaqiangbei")],
                      [act("10:00", "12:00", "Huaqiangbei")]])
    r = reality.reality_check(good, hubs)
    assert r["planner_issues"] == [] and r["logistics_issues"] == [], r  # HAX not on the map, but exempt
    bad = plan_with([[act("09:00", "11:00", "Fake Lab"), act("11:10", "12:00", "DJI")],
                     [act("12:00", "13:00", "DJI", "Hotel check-in", 5.0)],
                     [act("10:00", "11:00", "Hotel", "Rest", 5.0)]])
    SZ = reality.load_cost_reference("shenzhen")
    r = reality.reality_check(bad, hubs, cost_ref=SZ)
    text = " ".join(r["logistics_issues"] + r["planner_issues"])
    assert "Fake Lab" in text and "not found" in text
    assert "only 10 min between Fake Lab" not in text  # Fake Lab has no coordinates, so no gap check
    assert "hotel activity" in text and "Day 3 (the last day) has no real visit" in text and "below the realistic minimum" in text
    gap = plan_with([[act("09:00", "10:00", "DJI"), act("10:10", "11:00", "Huaqiangbei")], [act("09:00", "10:00", "DJI", cost=300)],
                     [act("09:00", "10:00", "DJI", cost=300)]])
    assert any("only 10 min between DJI and Huaqiangbei" in i for i in reality.reality_check(gap, hubs)["logistics_issues"])
    # web unreachable and nothing cached -> unverified, never a rejection
    web._WEB_CACHE = Path(tempfile.mkdtemp()) / "empty.json"
    state["down"] = True
    r = reality.reality_check(bad, hubs)
    assert not any("not found" in i for i in r["logistics_issues"]), r
    assert all(v["found"] is None for v in r["log"] if v["check"] == "place")
    assert reality._is_generic("Shenzhen Metro") and reality._is_generic("Grand Hotel")
    assert not reality._is_generic("Shenzhen Business School") and not reality._is_generic("Training Center")
    metro_day = plan_with([[act("09:00", "11:00", "DJI")], [act("09:00", "11:00", "HAX")],
                           [act("09:00", "10:00", "Shenzhen Metro", "Use the metro", 40.0),
                            act("13:00", "14:00", "Hotel", "Lunch", 40.0)]])
    r = reality.reality_check(metro_day, hubs)
    assert any("Day 3 (the last day) has no real visit" in i for i in r["planner_issues"]), r
    assert all(run_loop.is_soft_note(i) for i in r["planner_issues"]), "a departure day without a visit is only a warning"
    middle = plan_with([[act("09:00", "11:00", "DJI")], [act("09:00", "10:00", "Shenzhen Metro", "Use the metro", 40.0)],
                        [act("09:00", "11:00", "HAX")]])
    mid = reality.reality_check(middle, hubs)["planner_issues"]
    assert any("Day 2 has no real visit" in i for i in mid) and not any(run_loop.is_soft_note(i) for i in mid), mid
    assert not any(v["name"] == "Shenzhen Metro" for v in r["log"]), "transport words must not be looked up as places"
    print("[OK] reality checks: real places, travel gaps, costs, filler days; unreachable web never rejects")
    # Notes are deduplicated; cost-too-low failures are not escalated to the planner; guide reaches Logistics.
    dn = run_loop._dedupe_notes(["Trip total 1 EUR is low.", "Reality Problems: ['x']", "Trip total 1 EUR is low.", "Other"])
    assert dn == ["Trip total 1 EUR is low.", "Other"], dn
    import reality
    guide = reality.cost_guide_text(3, SZ)
    assert "days 1 to 2" in guide and "40 EUR" in guide and "at least 185 EUR" in guide, guide  # 3 x 35 + 2 x 40
    captured2 = {}
    run_loop.call_structured_llm = lambda sp, up, rm, temperature=0.2: captured2.setdefault("p", up) and fake_llm(sp, up, rm)
    run_loop.agent_logistics_specialist(fake_llm("", "", RoughItinerary), 1200, cost_ref=SZ)
    assert "Cost guide" in captured2["p"]
    print("[OK] notes deduplicated; Logistics prompt contains the cost guide")
    # Specific cost feedback, higher temperature on direct fixes, and an identical plan is challenged.
    import reality as rl
    os.environ["SWARM_OFFLINE"] = "1"
    low = ExpeditionPlan.model_validate({"destination": "Shenzhen, China", "duration_days": 3, "total_budget_eur": 1.0,
        "days": [{"day": i + 1, "focus_topic": "x", "activities": [
            {"start_time": "09:00", "end_time": "10:00", "location": "DJI", "activity_description": "Visit",
             "transit_mode": "car", "cost_eur": 30.0}]} for i in range(3)]})
    notes = " ".join(rl.reality_check(low, [], cost_ref=SZ)["logistics_issues"])
    assert "Add about 5 EUR" in notes and "Add about 95 EUR" in notes and "Lodging is missing on day(s) 1, 2" in notes and "Overnight stay" in notes, notes
    assert "below the realistic minimum" in notes  # still recognised as cost-only by the escalation rule
    # Lodging already charged on every night: the hint asks for higher meal and transport costs instead.
    paid = ExpeditionPlan.model_validate({"destination": "Shenzhen, China", "duration_days": 3, "total_budget_eur": 1.0,
        "days": [{"day": i + 1, "focus_topic": "x", "activities": [
            {"start_time": "09:00", "end_time": "10:00", "location": "DJI", "activity_description": "Visit",
             "transit_mode": "car", "cost_eur": 5.0},
            {"start_time": "20:00", "end_time": "21:00", "location": "Hotel", "activity_description": "Overnight stay",
             "transit_mode": "walk", "cost_eur": 40.0 if i < 2 else 0.0}]} for i in range(3)]})
    notes2 = " ".join(rl.reality_check(paid, [], cost_ref=SZ)["logistics_issues"])
    assert "Lodging is complete" in notes2 and "Lodging is missing" not in notes2, notes2
    # The Planner is offered the scenario's optional places and asked for two visits a day.
    seen = {}
    run_loop.call_structured_llm = lambda sp, up, rm, temperature=0.2: seen.setdefault("p", up) and fake_llm(sp, up, rm)
    opt_req = load_scenario("shenzhen_3day").model_copy(update={"optional_places": ["Shenzhen University"]})
    run_loop.agent_tech_planner(opt_req)
    assert "two real visits per day" in seen["p"] and "Shenzhen University" in seen["p"], seen["p"]
    print("[OK] lodging hint names the missing nights; the Planner is offered optional places")
    os.environ.pop("SWARM_OFFLINE")
    calls3 = []

    prev = fake_llm("", "", ExpeditionPlan)
    run_loop.call_structured_llm = lambda sp, up, rm, temperature=0.2: (calls3.append((temperature, up)) or
        (prev if len(calls3) == 1 else prev.model_copy(deep=True).model_copy(
            update={"days": [prev.days[0].model_copy(update={"focus_topic": "changed"})] + prev.days[1:]})))
    out = run_loop.agent_logistics_specialist(fake_llm("", "", RoughItinerary), 1200, prev, ["fix it"], temperature=0.7)
    assert len(calls3) == 2 and calls3[0][0] == 0.7 and calls3[1][0] == 0.8, calls3
    assert "IDENTICAL" in calls3[1][1] and out.days[0].focus_topic == "changed"
    print("[OK] cost notes are specific; an unchanged plan is challenged and resampled")
    # Edict-style review rule: after 3 rounds of only soft (cost) problems, approve with warnings;
    # a hard problem (missing hub) is never waived.
    os.environ["SWARM_OFFLINE"] = "1"
    os.chdir(tempfile.mkdtemp())

    def cheap_llm(sp, up, rm, temperature=0.2):
        out = fake_llm(sp, up, rm)
        if rm is ExpeditionPlan:
            for d in out.days:
                for act in d.activities:
                    act.cost_eur = 10.0
        return out

    run_loop.call_structured_llm = cheap_llm
    res = run_loop.run_swarm_loop(load_scenario("shenzhen_3day"), max_iterations=6)
    assert res is not None, "soft-only rejections must end in approval with warnings"
    rows = Path("results.csv").read_text().splitlines()
    assert rows[-1].split(",")[4] == "SUCCESS_WITH_WARNINGS" and rows[-1].split(",")[3] == "3", rows[-1]
    run_dir = next(Path("outputs").iterdir())
    assert "Warnings" in (run_dir / "plan.md").read_text(encoding="utf-8")
    assert "SUCCESS_WITH_WARNINGS" in (run_dir / "index.md").read_text(encoding="utf-8")

    def hubless_llm(sp, up, rm, temperature=0.2):
        out = cheap_llm(sp, up, rm)
        if rm is ExpeditionPlan:
            out = ExpeditionPlan.model_validate_json(out.model_dump_json().replace("HAX", "Generic_Cafe"))
        return out

    run_loop.call_structured_llm = hubless_llm
    assert run_loop.run_swarm_loop(load_scenario("shenzhen_3day"), max_iterations=4) is None, "hard problems are never waived"
    print("[OK] soft-only rejections end as SUCCESS_WITH_WARNINGS after 3 rounds; hard problems are not waived")

    # v0.7: plan-quality checks (soft), the plausibility gate, per-city price files, search names.
    import plan_quality
    import reality
    from plausibility import plausibility_check
    os.chdir(tempfile.mkdtemp())
    os.environ["SWARM_OFFLINE"] = "1"
    sz3 = load_scenario("shenzhen_3day")

    def mkact(loc, desc="Visit", cost=50.0):
        return {"start_time": "09:00", "end_time": "10:00", "location": loc, "activity_description": desc,
                "transit_mode": "Taxi", "cost_eur": cost}

    def mkplan(days):
        return ExpeditionPlan.model_validate({"destination": "Shenzhen, China", "duration_days": len(days),
            "total_budget_eur": 1.0, "days": [{"day": i + 1, "focus_topic": "x", "activities": d}
                                              for i, d in enumerate(days)]})

    repeat = mkplan([[mkact("DJI")], [mkact("DJI")], [mkact("HAX")]])
    q = plan_quality.quality_check(repeat, reality._is_generic, 1)
    assert any("repeated visit (days 1, 2)" in n for n in q["planner_issues"]), q
    assert plan_quality.quality_check(repeat, reality._is_generic, 2)["planner_issues"] == []  # allowed with fewer hubs than days
    filler = mkplan([[mkact("DJI"), mkact("Hotel", "Check-in and rest"), mkact("Hotel", "Lunch"), mkact("Hotel", "Dinner")],
                     [mkact("HAX"), mkact("Hotel", "Check-in again")], [mkact("Huaqiangbei"), mkact("Hotel", "Check-out")]])
    q = plan_quality.quality_check(filler, reality._is_generic, 1)
    notes = q["logistics_issues"]
    assert any("Day 1 is mostly hotel time (3 of 4" in n for n in notes), notes
    assert any("more than one hotel check-in (days 1, 2)" in n for n in notes), notes
    repeat_notes = plan_quality.quality_check(repeat, reality._is_generic, 1)["planner_issues"]
    assert all(run_loop.is_soft_note(n) for n in notes + q["planner_issues"] + repeat_notes)
    assert not run_loop.is_soft_note("Missing mandatory hub: HAX") and not run_loop.is_soft_note("Budget exceeded: 5 EUR > 1 EUR")
    assert run_loop.is_soft_note("Place 'X' was not found on OpenStreetMap in Shenzhen, China.")  # weak evidence: a warning
    assert not run_loop.is_soft_note("Day 2: 'Hotel check-in' is a hotel activity but the location says 'DJI'.")
    print("[OK] quality checks: repeats, hotel filler and extra check-ins are found, and are soft")
    lodging = mkplan([[mkact("DJI"), mkact("Hotel", "Lodging", 110.0), mkact("Hotel", "Lodging", 110.0)],
                      [mkact("HAX"), mkact("Hotel", "Lodging", 110.0)], [mkact("Huaqiangbei"), mkact("Hotel", "Check-out", 0.0)]])
    ql = plan_quality.quality_check(lodging, reality._is_generic, 1, hotel_eur=110)["logistics_issues"]
    assert any("Lodging is charged 3 times (about 110 EUR each), but the trip has only 2 nights" in n for n in ql), ql
    assert all(run_loop.is_soft_note(n) for n in ql)
    assert not any("Lodging" in n for n in plan_quality.quality_check(lodging, reality._is_generic, 1)["logistics_issues"])
    print("[OK] lodging charged more than once per night is found (soft)")

    # price floors come from the city's file, not from constants
    sv = load_scenario("silicon_valley_7day")
    assert reality.cost_floors(reality.load_cost_reference("shenzhen"), 3) == (35, 185)
    assert reality.cost_floors(reality.load_cost_reference(sv.cost_profile), 7) == (65, 1115)  # 7 x 65 + 6 x 110
    assert reality.cost_floors({}, 3) == (None, None) and reality.load_cost_reference("atlantis") == {}

    # plausibility gate
    ok = plausibility_check(sz3)
    assert ok["ok"] and ok["floors"]["trip_eur"] == 185 and ok["warnings"] == [], ok
    cheap = plausibility_check(sz3.model_copy(update={"budget_eur": 150}))
    assert not cheap["ok"] and "below the cheapest realistic cost of 185 EUR" in cheap["hard"][0], cheap
    tight = plausibility_check(sz3.model_copy(update={"budget_eur": 200}))
    assert tight["ok"] and "tight" in tight["warnings"][0], tight
    crowded = plausibility_check(sz3.model_copy(update={"must_visit": [f"Lab {i}" for i in range(10)]}))
    assert not crowded["ok"] and "do not fit" in crowded["hard"][0], crowded
    nocost = plausibility_check(sz3.model_copy(update={"destination": "Atlantis, Sea"}))
    assert nocost["ok"] and "No price table" in nocost["warnings"][0], nocost
    os.environ.pop("SWARM_OFFLINE")
    web._WEB_CACHE = Path(tempfile.mkdtemp()) / "pl_cache.json"
    seen = []

    def alias_nominatim(query):
        seen.append(query)
        return [{"lat": "22.5", "lon": "114.0", "display_name": query}] if "hax shenzhen" in query.lower() else []

    web._nominatim = alias_nominatim
    r = plausibility_check(sz3)
    hax = next(x for x in r["log"] if x["name"] == "HAX")
    assert hax["found"] is False and any("place_search_names" in w and "'HAX'" in w for w in r["warnings"]), r
    r = plausibility_check(sz3.model_copy(update={"place_search_names": {"HAX": "HAX Shenzhen"}}))
    assert next(x for x in r["log"] if x["name"] == "HAX")["found"] is True and not any("HAX" in w for w in r["warnings"]), r
    assert "HAX Shenzhen, Shenzhen, China" in seen, seen
    os.environ["SWARM_OFFLINE"] = "1"

    # an implausible request stops before any model call and is recorded
    def no_llm(*a, **k):
        raise AssertionError("the model must not be called for an implausible request")

    run_loop.call_structured_llm = no_llm
    impossible = sz3.model_copy(update={"budget_eur": 100})
    assert run_loop.run_swarm_loop(impossible, max_iterations=3) is None
    rows = Path("results.csv").read_text().splitlines()
    assert rows[-1].split(",")[4] == "REJECTED_IMPLAUSIBLE", rows[-1]
    run_dir = sorted(Path("outputs").iterdir())[-1]
    index = (run_dir / "index.md").read_text(encoding="utf-8")
    assert "Plausibility check" in index and "REJECTED_IMPLAUSIBLE" in index and "cheapest realistic cost" in index
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["plausibility"]["ok"] is False
    path, _ = last_path("none")
    assert path == ["pending", "failed"], path
    print("[OK] plausibility gate: limits from the price file, search names work, implausible requests stop early")

    # v0.7.1: the model's own wording must not break the soft-problem rule.
    os.chdir(tempfile.mkdtemp())
    os.environ["SWARM_OFFLINE"] = "1"

    def chatty_llm(sp, up, rm, temperature=0.2):
        out = cheap_llm(sp, up, rm)
        if rm is AuditResult:  # the model rejects with its own wording, no marker phrase in it
            return AuditResult(passed=False, failed_agent="logistics",
                               critique_notes=["Day 1 has too many hotel activities and only one real visit"])
        return out

    run_loop.call_structured_llm = chatty_llm
    res = run_loop.run_swarm_loop(load_scenario("shenzhen_3day"), max_iterations=6)
    assert res is not None, "model-worded notes must not turn soft rejections into hard ones"
    rows = Path("results.csv").read_text().splitlines()
    assert rows[-1].split(",")[4] == "SUCCESS_WITH_WARNINGS" and rows[-1].split(",")[3] == "3", rows[-1]
    plan_md = (sorted(Path("outputs").iterdir())[-1] / "plan.md").read_text(encoding="utf-8")
    assert "too many hotel activities" not in plan_md and "below the realistic minimum" in plan_md
    # on the last round a soft-only plan is approved with warnings instead of being thrown away
    assert run_loop.run_swarm_loop(load_scenario("shenzhen_3day"), max_iterations=2) is not None
    assert Path("results.csv").read_text().splitlines()[-1].split(",")[3] == "2"
    print("[OK] soft problems are decided from the checks, not the model's wording; last round approves soft-only plans")

    # v0.7.2: an invented venue ends as a warning in plan.md, not as a rejection.
    os.chdir(tempfile.mkdtemp())
    web._WEB_CACHE = Path(tempfile.mkdtemp()) / "inv.json"
    os.environ.pop("SWARM_OFFLINE", None)
    web._nominatim = lambda q: [] if "night market" in q.lower() else [{"lat": "22.5", "lon": "114.0", "display_name": q}]
    web._osrm = lambda a, b, c, d: 600.0
    web.time.sleep = lambda s: None

    def market_llm(sp, up, rm, temperature=0.2):
        out = fake_llm(sp, up, rm)
        if rm is ExpeditionPlan:
            out = ExpeditionPlan.model_validate_json(out.model_dump_json().replace("HAX", "Night Market Lane"))
            out.days[1].activities[0].location = "Wangxia Night Market"
            for d in out.days:
                for a in d.activities:
                    a.cost_eur = 60.0
        if rm is RoughItinerary:
            out = RoughItinerary.model_validate_json(out.model_dump_json())
        return out

    run_loop.call_structured_llm = market_llm
    res = run_loop.run_swarm_loop(load_scenario("shenzhen_3day").model_copy(update={"must_visit": ["DJI", "Huaqiangbei"], "days": 3}),
                                  max_iterations=6)
    plan_md = (sorted(Path("outputs").iterdir())[-1] / "plan.md").read_text(encoding="utf-8")
    assert res is not None and "Wangxia Night Market" in plan_md and "Warnings" in plan_md, plan_md
    os.environ["SWARM_OFFLINE"] = "1"
    print("[OK] an unknown venue is a warning in plan.md, not a rejection")

    # v0.7.3: an identical Planner draft is challenged and resampled, like Logistics.
    pcalls = []
    prev_rough = fake_llm("", "", RoughItinerary)

    def planner_llm(sp, up, rm, temperature=0.2):
        pcalls.append((temperature, up))
        if len(pcalls) == 1:
            return prev_rough
        return RoughItinerary.model_validate({**prev_rough.model_dump(), "destination": "Shenzhen, China"})

    run_loop.call_structured_llm = planner_llm
    out = run_loop.agent_tech_planner(load_scenario("shenzhen_3day"), prev_rough, ["Day 3: fix it"])
    assert len(pcalls) == 2 and pcalls[0][0] == 0.2 and pcalls[1][0] == 0.8 and "IDENTICAL" in pcalls[1][1], pcalls
    assert out.destination == "Shenzhen, China"
    print("[OK] an unchanged Planner draft is challenged and resampled")
    print("\nAll offline loop tests passed.")


if __name__ == "__main__":
    main()
