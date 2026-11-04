from pydantic import BaseModel, ValidationError
from schemas import RoughItinerary, ExpeditionPlan, AuditResult
from state_machine import TaskRecord, Status, VALID_TRANSITIONS
from handoff import handoff
from soul_loader import build_system_prompt
from skills import run_skill
import csv
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

RESULTS_FILE = "results.csv"
LOG_DIR = "logs"
CSV_HEADER = ["timestamp", "model", "inject_mode", "iterations",
              "status", "wall_clock_s", "schema_errors",
              "gpu_name", "prompt_tokens", "completion_tokens", "llm_seconds"]

_GPU_NAME = None


def _gpu_name() -> str:
    """GPU model from nvidia-smi, or 'none' if no NVIDIA GPU/driver is found (cached)."""
    global _GPU_NAME
    if _GPU_NAME is None:
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10)
            lines = out.stdout.strip().splitlines()
            _GPU_NAME = lines[0].strip() if out.returncode == 0 and lines else "none"
        except (OSError, subprocess.SubprocessError):
            _GPU_NAME = "none"
    return _GPU_NAME


def log_run(inject_mode, iterations, status, wall_clock_s, schema_errors):
    """
    Append one row per run. Token and LLM-time columns are summed from this run's calls.
    If an existing results file has a different header, it is renamed (never overwritten).
    """
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, newline="") as f:
            first_line = f.readline().strip()
        if first_line != ",".join(CSV_HEADER):
            backup = "results_old.csv"
            if os.path.exists(backup):
                backup = f"results_old_{datetime.now():%Y%m%d_%H%M%S}.csv"
            os.replace(RESULTS_FILE, backup)
            print(f"[RESULTS] Old results file had a different format, renamed to {backup}")

    file_exists = os.path.exists(RESULTS_FILE)
    with open(RESULTS_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADER)
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            MODEL_NAME,
            inject_mode or "none",
            iterations,
            status,
            f"{wall_clock_s:.2f}",
            schema_errors,
            _gpu_name(),
            sum(s.get("prompt_tokens", 0) for s in CALL_STATS),
            sum(s.get("completion_tokens", 0) for s in CALL_STATS),
            f"{sum(s.get('seconds', 0) for s in CALL_STATS):.1f}",
        ])

OLLAMA_URL = "http://localhost:11434"
NUM_CTX = int(os.environ.get("SWARM_NUM_CTX", "8192"))
CALL_TIMEOUT_S = 900  # a cold model load plus generation can take minutes
CALL_STATS: list[dict] = []  # per-call timing and token counts, flushed into the audit log

# One setting for the model: override with  $env:SWARM_MODEL = "qwen3:30b-instruct"
MODEL_NAME = os.environ.get("SWARM_MODEL", "qwen3:8b")

MAX_BUDGET_EUR = 1200.0
REQUIRED_HUBS = ["DJI", "HAX", "Huaqiangbei"]
EXPECTED_DAYS = 3


def _missing_hubs(plan: ExpeditionPlan) -> list[str]:
    """Mandatory hubs that no activity actually takes place at (checked on 'location')."""
    locations = " | ".join(act.location for day in plan.days for act in day.activities).lower()
    return [h for h in REQUIRED_HUBS if h.lower() not in locations]


def _normalize_plan(plan: ExpeditionPlan, record: TaskRecord) -> ExpeditionPlan:
    """Overwrite model-reported summary fields with values computed from the activities."""
    actual = round(sum(act.cost_eur for day in plan.days for act in day.activities), 2)
    if abs(plan.total_budget_eur - actual) > 0.01:
        record.note("pipeline", f"model reported total_budget_eur={plan.total_budget_eur}, actual sum={actual}")
        plan.total_budget_eur = actual
    plan.duration_days = len(plan.days)
    return plan


class StructuredOutputError(ValueError):
    """The model answered, but the reply was cut off or unusable."""


class OllamaHTTPError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Ollama HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


def _post_chat(payload: dict) -> dict:
    """POST to Ollama's native /api/chat (kept separate so tests can replace it)."""
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=CALL_TIMEOUT_S) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise OllamaHTTPError(e.code, e.read().decode("utf-8", "replace"))


def call_structured_llm(
    system_prompt: str,
    user_prompt: str,
    response_model: type[BaseModel],
    temperature: float = 0.2,
):
    """
    One structured call through Ollama's native chat API.
    - 'format' is the Pydantic JSON schema, so the reply is constrained to valid JSON.
    - 'think': False switches off Qwen3's hidden reasoning, which otherwise burns
      thousands of tokens per call (slow, and can fill the context window).
    - num_ctx is set per request, so no environment variable is needed.
    """
    qwen3_hybrid = (MODEL_NAME.startswith("qwen3") and "instruct" not in MODEL_NAME
                    and "thinking" not in MODEL_NAME)
    if qwen3_hybrid:
        system_prompt += "\n\n/no_think"  # extra safety for older Ollama versions
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": response_model.model_json_schema(),
        "options": {"temperature": temperature, "num_ctx": NUM_CTX},
    }
    if MODEL_NAME.startswith("qwen3"):
        payload["think"] = False

    started = time.time()
    try:
        data = _post_chat(payload)
    except OllamaHTTPError as e:
        if "think" in payload and "think" in e.body.lower():
            payload.pop("think")  # this model/server does not support the switch
            data = _post_chat(payload)
        else:
            raise
    seconds = time.time() - started

    message = data.get("message") or {}
    content = message.get("content") or ""
    CALL_STATS.append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "schema": response_model.__name__,
        "seconds": round(seconds, 1),
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
        "thinking_chars": len(message.get("thinking") or ""),
        "done_reason": data.get("done_reason"),
    })

    if data.get("done_reason") == "length":
        head = content[:200].replace("\n", " ")
        raise StructuredOutputError(
            f"reply to {response_model.__name__} was cut off "
            f"(done_reason=length, num_ctx={NUM_CTX}, content starts: {head!r})"
        )
    return response_model.model_validate_json(content)  # ValidationError is retried by the loop


def _report_llm_stats(record: TaskRecord) -> None:
    """Write the per-call timing/token stats to the audit log and print a summary."""
    global CALL_STATS
    stats, CALL_STATS = CALL_STATS, []
    if not stats:
        return
    for s in stats:
        record.note("llm", json.dumps(s))
    print(f"[LLM STATS] calls={len(stats)} | llm_time={sum(s['seconds'] for s in stats):.1f}s"
          f" | prompt_tokens={sum(s['prompt_tokens'] for s in stats)}"
          f" | completion_tokens={sum(s['completion_tokens'] for s in stats)}"
          f" | thinking_chars={sum(s['thinking_chars'] for s in stats)}")


# ==========================================
# AGENT 1: Lead Route & Tech Planner
# (persona and rules live in agents/tech_planner/SOUL.md)
# ==========================================
def agent_tech_planner(
    user_request: str,
    previous_plan: RoughItinerary = None,
    critique_feedback: list[str] = None,
) -> RoughItinerary:
    print("  [Agent 1: Tech Planner] Designing high-level technical itinerary...")
    system_prompt = build_system_prompt("tech_planner")
    user_prompt = (
        f"User Request: {user_request}\n"
        f"Mandatory hubs (each one must be visited; use these exact names): {', '.join(REQUIRED_HUBS)}\n"
    )

    if previous_plan and critique_feedback:
        user_prompt += (
            f"\nYOUR PREVIOUS DRAFT:\n{previous_plan.model_dump_json(indent=2)}\n"
            "\nFIX THESE HUB ERRORS WITHOUT REMOVING EXISTING VALID HUBS:\n"
            + "\n".join(f"- {note}" for note in critique_feedback)
        )
    return call_structured_llm(system_prompt, user_prompt, RoughItinerary)


# ==========================================
# AGENT 2: Logistics & Budget Specialist
# (persona and rules live in agents/logistics/SOUL.md)
# ==========================================
def agent_logistics_specialist(
    rough_plan: RoughItinerary,
    budget_cap: float,
    previous_detailed: ExpeditionPlan = None,
    critique_feedback: list[str] = None,
) -> ExpeditionPlan:
    print("  [Agent 2: Logistics Specialist] Enforcing time slots, transit, and EUR costs...")
    system_prompt = build_system_prompt(
        "logistics",
        extra=f"Keep total costs strictly under {budget_cap} EUR.",
    )
    user_prompt = f"Rough Outline to Follow:\n{rough_plan.model_dump_json(indent=2)}\n"

    if previous_detailed and critique_feedback:
        user_prompt += (
            f"\nYOUR PREVIOUS SCHEDULE DRAFT:\n{previous_detailed.model_dump_json(indent=2)}\n"
            "\nFIX THESE LOGISTICS/BUDGET ERRORS (DO NOT DROP ANY HUBS):\n"
            + "\n".join(f"- {note}" for note in critique_feedback)
        )
    return call_structured_llm(system_prompt, user_prompt, ExpeditionPlan)


# ==========================================
# AGENT 3: Chief Auditor Agent (Router + Gatekeeper)
# (persona and rules live in agents/auditor/SOUL.md)
# The hard facts come from deterministic skills, not from the LLM's arithmetic.
# ==========================================
def agent_auditor(plan: ExpeditionPlan) -> AuditResult:
    print("  [Agent 3: Auditor Agent] Evaluating feasibility and routing feedback...")

    # --- Skill: budget_check ---
    all_costs = [act.cost_eur for day in plan.days for act in day.activities]
    budget = run_skill("budget_check", costs=all_costs, cap=MAX_BUDGET_EUR)["result"]
    actual_sum = budget["total"]

    # --- Skill: schedule_check (per day) ---
    schedule_issues = []
    if len(plan.days) != EXPECTED_DAYS:
        schedule_issues.append(f"Plan has {len(plan.days)} day(s) but {EXPECTED_DAYS} are required.")
    for day in plan.days:
        if not day.activities:
            schedule_issues.append(f"Day {day.day}: no activities planned. Every day needs at least one activity.")
            continue
        res = run_skill("schedule_check", activities=[a.model_dump() for a in day.activities])
        if not res["ok"]:
            schedule_issues.append(f"Day {day.day}: could not check times ({res['error']}). Use 24-hour HH:MM.")
        elif not res["result"]["ok"]:
            schedule_issues.extend(f"Day {day.day}: {issue}" for issue in res["result"]["issues"])

    missing_hubs = _missing_hubs(plan)

    system_prompt = build_system_prompt("auditor")
    user_prompt = (
        f"Max Allowed Budget: {MAX_BUDGET_EUR} EUR\n"
        f"Actual Summed Cost: {actual_sum:.2f} EUR\n"
        f"Currently Missing Hubs: {missing_hubs}\n"
        f"Schedule Problems: {schedule_issues or 'none'}\n\n"
        f"Full Plan:\n{plan.model_dump_json(indent=2)}"
    )
    audit = call_structured_llm(system_prompt, user_prompt, AuditResult, temperature=0.0)

    # Deterministic routing & safety net
    if missing_hubs:
        audit.passed = False
        audit.failed_agent = "tech_planner"
        for h in missing_hubs:
            if not any(h.lower() in note.lower() for note in audit.critique_notes):
                audit.critique_notes.append(f"Missing mandatory hub: {h}")
    elif not budget["within_budget"] or schedule_issues:
        audit.passed = False
        audit.failed_agent = "logistics"
        if not budget["within_budget"]:
            audit.critique_notes.append(
                f"Budget exceeded: {actual_sum:.2f} EUR > {MAX_BUDGET_EUR} EUR. Reduce activity or transit costs."
            )
        audit.critique_notes.extend(schedule_issues)

    return audit


# ==========================================
# SWARM ORCHESTRATOR (Targeted State Machine)
# ==========================================
def apply_fault_injection(
    detailed_plan: ExpeditionPlan,
    iteration: int,
    inject_mode: str = None,
) -> ExpeditionPlan:
    """
    Artificially injects controlled faults into the state object to test
    closed-loop disturbance rejection and routing logic.
    """
    if not inject_mode:
        return detailed_plan

    # Test 1: Force a missing hub on Iteration 1 -> Should route to TECH_PLANNER
    if inject_mode == "missing_hub" and iteration == 1:
        print("  [FAULT INJECTED] Sabotaging plan: Wiping 'HAX' from Iteration 1 to test Outer Loop...")
        raw_str = detailed_plan.model_dump_json()
        corrupted_str = raw_str.replace("HAX", "Generic_Cafe").replace("hax", "generic_cafe")
        return ExpeditionPlan.model_validate_json(corrupted_str)

    # Test 2: Force a budget overrun on Iteration 1 -> Should skip Agent 1 & route to LOGISTICS
    if inject_mode == "budget_spike" and iteration == 1:
        if _missing_hubs(detailed_plan):
            print("  [FAULT SKIPPED] Base plan already invalid - not isolating budget fault.")
            return detailed_plan  # let organic failure dominate; don't pretend to test budget
        detailed_plan = detailed_plan.model_copy(deep=True)
        detailed_plan.days[0].activities[0].cost_eur = 5000.0
        return detailed_plan

    # Test 3: Force budget overrun on Iterations 1 & 2 -> Should trigger ESCALATION to Agent 1
    if inject_mode == "saturation_escalation" and iteration in (1, 2, 3):
        print(f"  [FAULT INJECTED] Sabotaging plan (Iter {iteration}): Locking cost at 5000 EUR to force Escalation...")
        detailed_plan.days[0].activities[0].cost_eur = 5000.0
        return detailed_plan

    return detailed_plan


def _move(record: TaskRecord, status: Status, agent: str, note: str = "") -> None:
    """Transition unless the task is already in that state (e.g. after a retry)."""
    if record.status != status:
        record.transition(status, agent, note)


def _fail(record: TaskRecord, note: str) -> None:
    """Mark the task FAILED if its current state allows it."""
    if Status.FAILED in VALID_TRANSITIONS[record.status]:
        record.transition(Status.FAILED, "pipeline", note)


def run_swarm_loop(request: str, max_iterations: int = 6, inject_mode: str = None):
    start_time = time.time()
    schema_errors = 0
    iterations_used = 0

    task_id = f"{datetime.now():%Y%m%d_%H%M%S}_{inject_mode or 'none'}"
    record = TaskRecord(task_id, request, log_dir=LOG_DIR)

    print("=" * 65)
    print(f"[MISSION START] Request:\n{request}")
    print(f"[CONFIG] model={MODEL_NAME} | inject_mode={inject_mode} | max_iter={max_iterations}")
    print(f"[AUDIT LOG] {record.log_path}")
    print("=" * 65)

    critique_feedback = []
    rough_plan = None
    detailed_plan = None
    target_agent = "tech_planner"
    logistics_retries = 0
    retrying = False  # True right after a schema error: same agent runs again, no new handoff

    try:
        for iteration in range(1, max_iterations + 1):
            iterations_used = iteration
            print(f"\n>>> --- [Iteration {iteration}] Routing to: {target_agent.upper()} --- <<<")

            try:
                run_planner = target_agent == "tech_planner" or rough_plan is None

                if not retrying:
                    src = "user" if iteration == 1 else "auditor"
                    summary = ("new expedition request" if iteration == 1
                               else f"rejected with {len(critique_feedback)} note(s)")
                    handoff(record, src, "tech_planner" if run_planner else "logistics", summary)

                if run_planner:
                    _move(record, Status.PLANNING, "tech_planner")
                    rough_plan = agent_tech_planner(request, rough_plan, critique_feedback)
                    handoff(record, "tech_planner", "logistics",
                            f"rough itinerary, {len(rough_plan.days)} day(s)")
                    _move(record, Status.SCHEDULING, "logistics")
                    detailed_plan = agent_logistics_specialist(rough_plan, MAX_BUDGET_EUR)
                    logistics_retries = 0
                else:
                    _move(record, Status.SCHEDULING, "logistics")
                    print(f"  [Router] Skipping Agent 1 -> Direct fix in Agent 2 (Attempt {logistics_retries})...")
                    detailed_plan = agent_logistics_specialist(
                        rough_plan, MAX_BUDGET_EUR, detailed_plan, critique_feedback
                    )

                detailed_plan = apply_fault_injection(detailed_plan, iteration, inject_mode)
                detailed_plan = _normalize_plan(detailed_plan, record)

                total_cost = sum(act.cost_eur for day in detailed_plan.days for act in day.activities)
                print(f"  [Pipeline Output] Generated {len(detailed_plan.days)} days | Summed Cost: {total_cost:.2f} EUR")

                handoff(record, "logistics", "auditor",
                        f"{len(detailed_plan.days)} day(s), {total_cost:.2f} EUR")
                _move(record, Status.REVIEWING, "auditor")
                audit = agent_auditor(detailed_plan)
                retrying = False

            except (ValidationError, StructuredOutputError) as e:
                schema_errors += 1
                retrying = True
                print(f"  [Schema Error #{schema_errors}] Parsing failed on iteration {iteration}: {e}")
                record.note("pipeline", f"schema error #{schema_errors} on iteration {iteration}")
                critique_feedback = [f"Schema error: {str(e)}"]
                if schema_errors >= 3:
                    print("  [ABORTED] Too many schema errors - model is not producing valid output.")
                    _fail(record, "too many schema errors")
                    log_run(inject_mode, iterations_used, "SCHEMA_ABORT",
                            time.time() - start_time, schema_errors)
                    return None
                continue

            if audit.passed:
                elapsed = time.time() - start_time
                handoff(record, "auditor", "user", "plan approved")
                record.transition(Status.COMPLETED, "auditor", "plan approved")
                print(f"\n[MISSION SUCCESS] Plan APPROVED on Iteration {iteration}! ({elapsed:.1f}s)")
                print(f"[STATE PATH] {' -> '.join(record.path_taken())}")
                print(detailed_plan.model_dump_json(indent=2))
                log_run(inject_mode, iterations_used, "SUCCESS", elapsed, schema_errors)
                return detailed_plan
            else:
                print(f"\n[REJECTED] Blame assigned to: [{audit.failed_agent.upper()}]")
                for note in audit.critique_notes:
                    print(f"   * {note}")
                record.note("auditor", f"rejected, blame={audit.failed_agent}: " + " | ".join(audit.critique_notes))

                critique_feedback = audit.critique_notes
                target_agent = audit.failed_agent

                if target_agent == "logistics":
                    logistics_retries += 1
                    if logistics_retries >= 2:
                        print("  [ESCALATION] Agent 2 saturated! Escalating fault back to Agent 1...")
                        target_agent = "tech_planner"
                        record.note("pipeline", "escalation: logistics saturated, back to tech_planner")
                        critique_feedback.append(
                            "ESCALATION: Logistics could not fit your hubs within budget/time constraints. "
                            "Simplify or redistribute target_hubs across days."
                        )

        elapsed = time.time() - start_time
        print(f"\n[ABORTED] Could not converge within {max_iterations} iterations. ({elapsed:.1f}s)")
        print(f"[STATE PATH] {' -> '.join(record.path_taken())}")
        _fail(record, "max iterations reached")
        log_run(inject_mode, iterations_used, "ABORTED_MAX_ITER", elapsed, schema_errors)
        return None

    except Exception as e:
        # Catch-all: network errors, Ollama disconnects, index errors in fault injection, etc.
        elapsed = time.time() - start_time
        print(f"\n[CRASHED] {type(e).__name__}: {e} ({elapsed:.1f}s)")
        _fail(record, f"crash: {type(e).__name__}: {e}")
        log_run(inject_mode, iterations_used, f"CRASH_{type(e).__name__}", elapsed, schema_errors)
        return None

    finally:
        _report_llm_stats(record)


if __name__ == "__main__":
    test_request = (
        "Organize a 3-day robotics expedition to Shenzhen. "
        "Strict budget ceiling is 1200 EUR."
    )

    for mode in [None, "missing_hub", "budget_spike", "saturation_escalation"]:
        print(f"\n\n########## RUNNING MODE: {mode} ##########")
        run_swarm_loop(test_request, max_iterations=6, inject_mode=mode)
