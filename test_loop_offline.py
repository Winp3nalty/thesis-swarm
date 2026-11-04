"""
Offline test of the whole swarm loop with a FAKE model (no Ollama, no GPU needed).
It also works as a demo: it shows every fault mode being caught and corrected.

Run:  python test_loop_offline.py
Needs openai + pydantic installed (the setup.ps1 venv has them).
"""
import json
import os
import sys
import tempfile
import types
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

    request = "3-day Shenzhen robotics expedition, budget 1200 EUR"
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
    assert new_rows[1].split(",")[-4:] == ["Test GPU 123", "150", "50", "3.2"] or \
        new_rows[1].split(",")[-4:] == ["Test GPU 123", "150", "50", "3.3"]
    run_loop.CALL_STATS = []
    print("[OK] results.csv has gpu_name and token columns; old file kept as results_old.csv")
    print("\nAll offline loop tests passed.")


if __name__ == "__main__":
    main()
