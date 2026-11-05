"""Offline test of the Dispatcher (fake model, no Ollama). Run: python test_dispatcher_offline.py"""
import deps

deps.ensure()  # installs pydantic on first run

import json
import os
import tempfile
from pathlib import Path

import dispatcher
import run_loop
import test_loop_offline as tl
import test_workers_offline as tw
from handoff import PermissionDenied, handoff
from requirements_schema import load_scenario
from schemas import DetailedActivity, DetailedDay, ExpeditionPlan
from state_machine import TaskRecord
from worker_schemas import OutreachEmail, TransitPlan


def llm(system_prompt, user_prompt, response_model, temperature=0.2):
    if response_model in (TransitPlan, OutreachEmail):
        return tw.good_llm(system_prompt, user_prompt, response_model, temperature)
    result = tl.fake_llm(system_prompt, user_prompt, response_model, temperature)
    if isinstance(result, ExpeditionPlan):  # a second place per day, so that every day has a route
        for day in result.days:
            day.activities[1].location = f"{day.activities[0].location} Annex"
    return result


def scenario_with_outreach():
    from requirements_schema import OutreachTarget
    req = load_scenario("shenzhen_3day")
    return req.model_copy(update={"outreach": [
        OutreachTarget(name="Prof. Zhou Mingyu", institution="Southern University of Science and Technology",
                       topics=["soft robotic grippers"]),
        OutreachTarget(name="Prof. Liang Haotian", institution="Shenzhen University", topics=["drone swarm coordination"])]})


def test_day_stops():
    acts = [DetailedActivity(start_time="09:00", end_time="10:00", location=loc, activity_description="x",
                             transit_mode="Walk", cost_eur=1) for loc in ["Hotel", "DJI", "dji", "HAX", "Hotel"] + [f"P{i}" for i in range(8)]]
    stops = dispatcher.day_stops(DetailedDay(day=1, focus_topic="t", activities=acts))
    assert stops[:3] == ["DJI", "HAX", "P0"], "hotels are left out; a repeated place back to back is listed once"
    assert len(stops) == dispatcher.MAX_STOPS_PER_DAY


def test_permissions():
    with tempfile.TemporaryDirectory() as tmp:
        r = TaskRecord("t", "req", log_dir=tmp)
        for a, b in [("user", "audit"), ("audit", "dispatcher"), ("dispatcher", "transit"), ("transit", "dispatcher"),
                     ("dispatcher", "outreach"), ("outreach", "dispatcher"), ("dispatcher", "user"), ("audit", "user")]:
            handoff(r, a, b, printer=lambda *_: None)
        for a, b in [("transit", "outreach"), ("outreach", "user"), ("user", "dispatcher"), ("audit", "logistics"), ("justice", "user")]:
            try:
                handoff(r, a, b, printer=lambda *_: None)
            except PermissionDenied:
                pass
            else:
                raise AssertionError(f"{a} -> {b} must be denied")


def test_loop_with_workers():
    real = run_loop.call_structured_llm
    run_loop.call_structured_llm = llm
    old = os.getcwd()
    os.chdir(tempfile.mkdtemp())
    os.environ["SWARM_OFFLINE"] = "1"
    try:
        req = scenario_with_outreach()
        plan = run_loop.run_swarm_loop(req, inject_mode=None, workers=True)
        assert plan is not None
        run = sorted(Path("outputs").glob("*_swarm_*"))[-1]
        data = json.loads((run / "run.json").read_text())
        s = data["coordination"]
        assert s["transit_days"] == 3 and s["transit_ok"] == 3, s
        assert s["outreach_emails"] == len(req.outreach) and s["outreach_ok"] == len(req.outreach), s
        for name in ["transit_day1.md", "transit_day3.md"] + [f"outreach_o{i:02d}.md" for i in range(1, len(req.outreach) + 1)]:
            assert (run / name).exists(), name
        assert "outreach_o01.md" in (run / "index.md").read_text()
        # v0.9b: closing agents and their files
        agents = {t["agent"] for t in data["dispatcher"]}
        assert {"finance", "local_logistics", "static_web", "transit", "outreach"} <= agents, agents
        assert data["finance"]["total_eur"] > 0 and data["finance"]["currency"] == "CNY", data["finance"]
        assert "Dispatcher task board" in (run / "index.md").read_text()
        hb = (run / "handbook.md").read_text(encoding="utf-8")
        assert "Expedition handbook" in hb and "Cost report" in hb and "Outreach emails" in hb
        assert json.loads((run / "handbook.json").read_text(encoding="utf-8"))["plan"]["days"]
        assert "Cost report" in (run / "plan.md").read_text(encoding="utf-8")
        assert "Order check" in (run / "transit_day1.md").read_text(encoding="utf-8")
        assert "Practical notes" in (run / "plan.md").read_text(encoding="utf-8"), "visa and SIM section missing"
        events = [json.loads(l) for l in sorted(Path("logs").glob("audit_*.jsonl"))[-1].read_text().splitlines()]
        notes = " ".join(e.get("message", "") for e in events)
        assert "handoff -> dispatcher" in notes and "handoff -> audit" in notes and "handoff -> justice" in notes and "handoff -> transit" in notes and "handoff -> outreach" in notes

        # workers are optional: the default run has no coordination and no extra files
        run_loop.run_swarm_loop(req, inject_mode=None)
        run2 = sorted(Path("outputs").glob("*_swarm_*"))[-1]
        assert json.loads((run2 / "run.json").read_text())["coordination"] is None
        assert not list(run2.glob("transit_*.md"))
    finally:
        run_loop.call_structured_llm = real
        os.chdir(old)


def test_worker_failure_does_not_lose_plan():
    real = run_loop.call_structured_llm

    def broken(sp, up, rm, temperature=0.2):
        if rm in (TransitPlan, OutreachEmail):
            raise ConnectionError("ollama down")
        return llm(sp, up, rm, temperature)

    run_loop.call_structured_llm = broken
    old = os.getcwd()
    os.chdir(tempfile.mkdtemp())
    os.environ["SWARM_OFFLINE"] = "1"
    try:
        plan = run_loop.run_swarm_loop(scenario_with_outreach(), inject_mode=None, workers=True)
        assert plan is not None, "an approved plan must survive failing workers"
        run = sorted(Path("outputs").glob("*_swarm_*"))[-1]
        s = json.loads((run / "run.json").read_text())["coordination"]
        assert s["transit_days"] == 3 and s["transit_ok"] == 0 and s["outreach_ok"] == 0, s
        assert "FAILED" in (run / "index.md").read_text()
    finally:
        run_loop.call_structured_llm = real
        os.chdir(old)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} dispatcher tests passed.")
