"""Offline tests (no LLM, no network). Run:  python test_core.py"""
import tempfile

from handoff import PermissionDenied, handoff
from skills import describe_skills, run_skill, run_skill_requests
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
        handoff(r, "user", "tech_planner", "request received", printer=lines.append)
        handoff(r, "auditor", "logistics", "budget fix", printer=lines.append)
        assert len(lines) == 2 and "[HANDOFF] user -> tech_planner" in lines[0]
        try:
            handoff(r, "tech_planner", "auditor", printer=lines.append)
        except PermissionDenied:
            pass
        else:
            raise AssertionError("tech_planner -> auditor must be denied")
        assert any("DENIED" in e.get("message", "") for e in r.history)


def test_souls_load():
    for agent in ("tech_planner", "logistics", "auditor"):
        assert load_soul(agent).startswith("# ")
    prompt = build_system_prompt("logistics", skill_names=["budget_check"], extra="Budget: 1200 EUR")
    assert "budget_check" in prompt and "Budget: 1200 EUR" in prompt


def test_skills():
    assert run_skill("currency_convert", amount=100, from_ccy="EUR", to_ccy="EUR")["result"]["amount"] == 100.0
    b = run_skill("budget_check", costs=[500, 600, 200], cap=1200)["result"]
    assert b["total"] == 1300.0 and b["within_budget"] is False
    ok = run_skill("schedule_check", activities=[
        {"start_time": "09:00", "end_time": "11:00"}, {"start_time": "11:30", "end_time": "13:00"}])
    assert ok["result"]["ok"] is True
    bad = run_skill("schedule_check", activities=[
        {"start_time": "09:00", "end_time": "11:00"}, {"start_time": "10:30", "end_time": "10:00"}])
    assert bad["result"]["ok"] is False and len(bad["result"]["issues"]) == 2
    assert run_skill("nope")["ok"] is False
    out = run_skill_requests([{"skill": "budget_check", "args": {"costs": [1], "cap": 2}},
                              {"skill": "currency_convert", "args": {"amount": 1}}])
    assert out[0]["ok"] is True and out[1]["ok"] is False  # bad args reported, not raised
    assert "schedule_check" in describe_skills()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")
