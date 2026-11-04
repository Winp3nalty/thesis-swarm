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

try:
    import openai  # noqa: F401
except ImportError:  # the fake model never touches the real client
    stub = types.ModuleType("openai")
    stub.OpenAI = lambda **kwargs: object()
    sys.modules["openai"] = stub

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
            "destination": "Shenzhen", "duration_days": 3, "total_budget_eur": 600.0,
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

    rows = Path(run_loop.RESULTS_FILE).read_text().splitlines()
    assert len(rows) == 1 + 5 and all("SUCCESS" in r for r in rows[1:])
    print("\nAll offline loop tests passed.")


if __name__ == "__main__":
    main()
