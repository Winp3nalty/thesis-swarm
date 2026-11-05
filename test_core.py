"""Offline tests (no LLM, no network). Run:  python test_core.py"""
import deps

deps.ensure()  # installs pydantic on first run

import tempfile

from handoff import PermissionDenied, handoff
from skills import run_skill
from soul_loader import build_system_prompt, load_soul
from state_machine import InvalidTransition, Status, TaskRecord


def new_record(tmp):
    return TaskRecord("t1", "3-day Shenzhen expedition", log_dir=tmp)


def test_happy_path():
    with tempfile.TemporaryDirectory() as tmp:
        r = new_record(tmp)
        r.transition(Status.PLANNING, "tech_planner")
        r.transition(Status.SCHEDULING, "logistics")
        r.transition(Status.REVIEWING, "auditor")
        r.transition(Status.COMPLETED, "auditor")
        assert r.path_taken() == ["pending", "planning", "scheduling", "reviewing", "completed"]
        assert r.log_path.exists() and len(r.log_path.read_text().splitlines()) == 4


def test_rejection_and_escalation_path():
    with tempfile.TemporaryDirectory() as tmp:
        r = new_record(tmp)
        for s in (Status.PLANNING, Status.SCHEDULING, Status.REVIEWING,
                  Status.SCHEDULING, Status.REVIEWING, Status.PLANNING):
            r.transition(s, "x")
        assert r.status == Status.PLANNING


def test_invalid_transition_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        r = new_record(tmp)
        try:
            r.transition(Status.COMPLETED, "auditor")
        except InvalidTransition as e:
            assert "pending -> completed" in str(e)
        else:
            raise AssertionError("pending -> completed must be rejected")
        assert r.status == Status.PENDING


def test_handoff_permissions():
    with tempfile.TemporaryDirectory() as tmp:
        r = new_record(tmp)
        lines = []
        handoff(r, "dispatcher", "tech_planner", "scenario approved", printer=lines.append)
        handoff(r, "dispatcher", "logistics", "budget fix", printer=lines.append)
        assert len(lines) == 2 and "[HANDOFF] dispatcher -> tech_planner" in lines[0]
        try:
            handoff(r, "tech_planner", "justice", printer=lines.append)
        except PermissionDenied:
            pass
        else:
            raise AssertionError("tech_planner -> justice must be denied")
        assert any("DENIED" in e.get("message", "") for e in r.history)


def test_souls_load():
    for agent in ("tech_planner", "logistics", "justice"):
        assert load_soul(agent).startswith("# ")
    prompt = build_system_prompt("logistics", extra="Budget: 1200 EUR")
    assert load_soul("logistics") in prompt and "Budget: 1200 EUR" in prompt


def test_skills():
    b = run_skill("budget_check", costs=[500, 600, 200], cap=1200)["result"]
    assert b["total"] == 1300.0 and b["within_budget"] is False
    ok = run_skill("schedule_check", activities=[
        {"start_time": "09:00", "end_time": "11:00"}, {"start_time": "11:30", "end_time": "13:00"}])
    assert ok["result"]["ok"] is True
    bad = run_skill("schedule_check", activities=[
        {"start_time": "09:00", "end_time": "11:00"}, {"start_time": "10:30", "end_time": "10:00"}])
    assert bad["result"]["ok"] is False and len(bad["result"]["issues"]) == 2
    assert run_skill("nope")["ok"] is False
    assert run_skill("budget_check", costs=[1], cap=2)["ok"] is True
    assert run_skill("budget_check", costs=[1])["ok"] is False  # bad args reported, not raised


def test_live_exchange_rate():
    import os
    import tempfile
    import urllib.error
    from pathlib import Path
    from skills import web
    web._CACHE = Path(tempfile.mkdtemp()) / "fx_cache.json"
    os.environ.pop("SWARM_OFFLINE", None)
    # 1. live
    web._fetch_live = lambda: {"date": "2026-11-03", "rates": {"EUR": 1.0, "CNY": 8.2, "USD": 1.15}}
    r = run_skill("live_exchange_rate", amount=100, from_ccy="EUR", to_ccy="CNY")["result"]
    assert r["amount"] == 820.0 and r["source"] == "live" and r["rate_date"] == "2026-11-03"
    # 2. network down, cache still fresh -> cache
    def down():
        raise urllib.error.URLError("blocked")
    web._fetch_live = down
    assert run_skill("live_exchange_rate", amount=1, from_ccy="USD", to_ccy="CNY")["result"]["source"] == "cache"
    # 3. offline flag never calls the network
    os.environ["SWARM_OFFLINE"] = "1"
    web._fetch_live = lambda: (_ for _ in ()).throw(AssertionError("network used"))
    assert run_skill("live_exchange_rate", amount=1, from_ccy="EUR", to_ccy="USD")["result"]["source"] == "cache"
    # 4. no cache, no network -> static table
    web._CACHE = Path(tempfile.mkdtemp()) / "none.json"
    assert run_skill("live_exchange_rate", amount=1, from_ccy="EUR", to_ccy="USD")["result"]["source"] == "static"
    os.environ.pop("SWARM_OFFLINE")
    web._fetch_live = down
    assert run_skill("live_exchange_rate", amount=1, from_ccy="EUR", to_ccy="XXX")["ok"] is False


def test_model_choice():
    import importlib
    import os
    import tempfile
    from pathlib import Path
    import run_loop
    f = Path(tempfile.mkdtemp()) / "model.txt"
    f.write_text("# comment\n\n  qwen3:14b  \nignored\n", encoding="utf-8")
    assert run_loop._read_model_file(f) == "qwen3:14b"
    f.write_text("# only comments\n", encoding="utf-8")
    assert run_loop._read_model_file(f) is None
    assert run_loop._read_model_file(f.parent / "missing.txt") is None
    assert run_loop._read_model_file() == "qwen3:8b"  # the shipped model.txt
    os.environ["SWARM_MODEL"] = "qwen2.5:7b"
    try:
        assert importlib.reload(run_loop).MODEL_NAME == "qwen2.5:7b"  # env beats model.txt
    finally:
        os.environ.pop("SWARM_MODEL")
        importlib.reload(run_loop)


def test_version_info():
    import tempfile
    from pathlib import Path
    import version_info as vi
    f = Path(tempfile.mkdtemp()) / "version.txt"
    f.write_text("# comment\n\n v9.9-test \nignored\n", encoding="utf-8")
    assert vi.read_version(f) == "v9.9-test"
    assert vi.read_version(f.parent / "missing.txt") == "unversioned"
    assert vi.read_version() != "unversioned"  # the shipped version.txt
    info = vi.version_info()
    assert set(info) == {"version", "git_commit", "rules_hash"} and len(info["rules_hash"]) == 8
    root = Path(tempfile.mkdtemp())
    (root / "agents" / "a").mkdir(parents=True)
    (root / "agents" / "a" / "SOUL.md").write_text("one", encoding="utf-8")
    h1 = vi.rules_hash(root)
    (root / "agents" / "a" / "SOUL.md").write_text("two", encoding="utf-8")
    assert vi.rules_hash(root) != h1  # a prompt change changes the hash


def test_requirements_schema():
    import json
    import tempfile
    from pathlib import Path
    from requirements_schema import Requirements, load_scenario
    req = load_scenario("shenzhen_3day")
    assert req.days == 3 and req.budget_eur == 1200 and req.must_visit == ["DJI", "HAX", "Huaqiangbei"]
    assert req.city == "Shenzhen" and "1200 EUR" in req.request_text() and "DJI" in req.request_text()
    base = req.model_dump()
    d = Path(tempfile.mkdtemp())
    for label, change in [("zero budget", {"budget_eur": 0}), ("no hubs", {"must_visit": []}),
                          ("duplicate hub", {"must_visit": ["DJI", "dji"]}), ("too many days", {"days": 40})]:
        (d / "bad.json").write_text(json.dumps({**base, **change}), encoding="utf-8")
        try:
            load_scenario(d / "bad.json")
        except ValueError as e:
            assert "invalid scenario" in str(e), label
        else:
            raise AssertionError(f"{label} must be rejected")
    try:
        load_scenario("no_such_scenario")
    except ValueError as e:
        assert "cannot read" in str(e)
    else:
        raise AssertionError("missing scenario must be rejected")
    assert Requirements.model_validate(base) == req
    # derived values used by the loop
    assert (req.region, req.cost_profile, req.max_days_per_place) == ("Shenzhen, China", "shenzhen", 1)
    sv, sz7 = load_scenario("silicon_valley_7day"), load_scenario("shenzhen_7day")
    assert (sv.cost_profile, sv.region, sv.max_days_per_place) == ("silicon_valley", "California, USA", 1)
    assert (sz7.days, sz7.max_days_per_place, len(sz7.outreach)) == (7, 1, 2)  # 4 hubs + optional places cover 7 days -> no repeats


def test_deps_ensure():
    """pydantic is installed automatically when missing, and a failed install says what to run."""
    import importlib.util
    import subprocess
    import deps
    calls = []
    real_run, real_find = subprocess.run, importlib.util.find_spec
    subprocess.run = lambda cmd, **k: (calls.append(cmd), type("R", (), {"returncode": 0})())[1]
    try:
        importlib.util.find_spec = lambda name: object()
        deps.ensure()
        assert calls == [], "nothing to install when pydantic is present"
        importlib.util.find_spec = lambda name: None  # still missing after the "install"
        try:
            deps.ensure()
        except SystemExit as e:
            assert "pip install -r requirements.txt" in str(e)
        else:
            raise AssertionError("a failed install must stop with a message")
        assert calls and calls[0][1:4] == ["-m", "pip", "install"] and calls[0][-1] == str(deps.REQUIREMENTS)
    finally:
        subprocess.run, importlib.util.find_spec = real_run, real_find
    assert deps.REQUIREMENTS.read_text().strip().startswith("pydantic")


def test_state_table_consistent():
    """Idea from Edict's CI guard for its state tables: the table must stay complete and coherent."""
    from state_machine import Status, VALID_TRANSITIONS
    assert set(VALID_TRANSITIONS) == set(Status), "every status needs an entry in the table"
    for src, targets in VALID_TRANSITIONS.items():
        assert targets <= set(Status), f"{src} points to an unknown status"
    reachable, frontier = {Status.PENDING}, [Status.PENDING]
    while frontier:
        for nxt in VALID_TRANSITIONS[frontier.pop()]:
            if nxt not in reachable:
                reachable.add(nxt)
                frontier.append(nxt)
    assert reachable == set(Status), f"unreachable statuses: {set(Status) - reachable}"
    assert VALID_TRANSITIONS[Status.COMPLETED] == set(), "a completed task must be terminal"
    for st in (Status.PLANNING, Status.SCHEDULING, Status.REVIEWING):
        assert Status.FAILED in VALID_TRANSITIONS[st], f"{st} must be able to fail"


def test_skills_call_log_and_batch_summary():
    """Every run_skill call is logged per skill; the batch summary groups repeated runs by mode."""
    import tempfile
    import skills
    from schemas import ExpeditionPlan, DetailedDay, DetailedActivity
    import batch_summary
    skills.reset_call_log()
    skills.run_skill("budget_check", costs=[10, 20], cap=100)
    skills.run_skill("budget_check", costs=[1], cap=100)
    skills.run_skill("no_such_skill")
    rows = {r["skill"]: r for r in skills.skills_summary()}
    assert rows["budget_check"]["calls"] == 2 and rows["budget_check"]["failed"] == 0
    assert rows["no_such_skill"]["failed"] == 1
    skills.reset_call_log()
    assert skills.skills_summary() == []

    act = DetailedActivity(start_time="09:00", end_time="10:00", location="X", activity_description="d",
                           transit_mode="Walk", cost_eur=40.0)
    plan = ExpeditionPlan(destination="D", duration_days=1, total_budget_eur=100,
                          days=[DetailedDay(day=1, focus_topic="t", activities=[act])])
    ok = batch_summary.run_row("none", {"status": "SUCCESS", "iterations": 2, "wall_clock_s": 30.0}, plan)
    bad = batch_summary.run_row("none", {"status": "ABORTED_MAX_ITER", "iterations": 6, "wall_clock_s": 90.0}, None)
    assert ok["cost_eur"] == 40.0 and bad["cost_eur"] is None
    text = "\n".join(batch_summary.summarize([ok, bad]))
    assert "SUCCESS x1" in text and "ABORTED_MAX_ITER x1" in text and "| none | 2 |" in text
    with tempfile.TemporaryDirectory() as tmp:
        out = batch_summary.save_summary("shenzhen_7day", "m", "v", [ok, bad], root=tmp)
        assert list(__import__("pathlib").Path(tmp).glob("summary_shenzhen_7day_*.md")) and "[SUMMARY]" in out


def test_verify_place_rejects_unrelated_small_hit():
    """'Shenzhen Technology Park' once matched a bank branch: a small everyday place with an unrelated name."""
    import os
    import tempfile
    from pathlib import Path
    from skills import web
    old = (web._WEB_CACHE, web._nominatim, web.time.sleep, os.environ.get("SWARM_OFFLINE"))
    web._WEB_CACHE = Path(tempfile.mkdtemp()) / "cache.json"
    web.time.sleep = lambda s: None
    os.environ.pop("SWARM_OFFLINE", None)
    hits = {
        "Shenzhen Technology Park, Shenzhen": {"lat": "22.7", "lon": "114.0", "type": "bank",
                                                 "display_name": "Postal Savings Bank, Longhua, Shenzhen"},
        "Huaqiangbei, Shenzhen": {"lat": "22.5", "lon": "114.1", "type": "suburb", "display_name": "华强北, 福田区"},
        "Pizza Palace, Shenzhen": {"lat": "22.5", "lon": "114.1", "type": "restaurant",
                                    "display_name": "Pizza Palace, Futian, Shenzhen"},
    }
    web._nominatim = lambda q: [hits[q]]
    try:
        assert web.verify_place("Shenzhen Technology Park", "Shenzhen")["found"] is False
        assert web.verify_place("Huaqiangbei", "Shenzhen")["found"] is True  # transliteration, not a small place
        assert web.verify_place("Pizza Palace", "Shenzhen")["found"] is True  # small place, but the name matches
    finally:
        web._WEB_CACHE, web._nominatim, web.time.sleep = old[:3]
        if old[3] is not None:
            os.environ["SWARM_OFFLINE"] = old[3]


def test_max_days_per_place_and_team():
    from requirements_schema import load_scenario
    base = load_scenario("shenzhen_7day")
    few = base.model_copy(update={"optional_places": []})
    assert few.max_days_per_place == 2, "4 places for 7 days: a repeat is unavoidable"
    assert base.max_days_per_place == 1
    assert base.team.sender_university == "Aalto University" and base.team.sender_name == ""


def test_practical_notes():
    import practical
    from requirements_schema import load_scenario
    for name in ("shenzhen_7day", "silicon_valley_7day"):
        req = load_scenario(name)
        info = practical.load_practical(req.cost_profile)
        assert {"Visa and entry", "SIM and internet"} <= set(info["sections"]), name
        text = practical.practical_md(info)
        assert "verify before travel" in text and "NOT verified" in text, name
    assert practical.load_practical("nowhere") == {} and practical.practical_md({}) == ""
    assert load_scenario("shenzhen_7day").optional_places, "the 7-day scenarios offer optional places"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")
