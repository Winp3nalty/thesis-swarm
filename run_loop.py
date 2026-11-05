"""
The main program: runs the whole swarm on a scenario.

    python run_loop.py --scenario shenzhen_7day --modes none --repeats 5

Audit checks the scenario, the Dispatcher shares the work, the Tech Planner and Logistics draft the plan, Finance adds
the costs, Justice verifies it and the loop repeats until Justice approves (or the limits are reached). Afterwards
the Dispatcher runs Transit, Outreach and the closing agents and the results are written to outputs/.
"""
from swarm.core import deps

deps.ensure()  # installs pydantic on first run

from pydantic import ValidationError
from swarm.core.schemas import ExpeditionPlan
from swarm.core.state_machine import TaskRecord, Status, VALID_TRANSITIONS
from swarm.skills import reset_call_log, skills_summary
from swarm.code_agents.reality import load_cost_reference
from swarm.code_agents.plan_quality import fix_overnight_times, SOFT_MARKERS
from swarm.code_agents.audit import agent_audit
from swarm.code_agents.dispatcher import Dispatcher
from swarm.code_agents.finance import agent_finance
from swarm.code_agents.hotels import choose_hotel
from swarm.code_agents.meal_times import fix_meal_times, load_meals
from swarm.llm_agents import client
from swarm.llm_agents.tech_planner import agent_tech_planner
from swarm.llm_agents.logistics import agent_logistics_specialist
from swarm.llm_agents.justice import agent_justice, VERIFY_LOG, _missing_hubs
from swarm.core.requirements_schema import Requirements, load_scenario
from swarm.core.version_info import version_info, version_line
from swarm.output import batch_summary
import csv
import json
import os
import subprocess
import time
from datetime import datetime

RESULTS_FILE = "results.csv"
LOG_DIR = "logs"
CSV_HEADER = ["timestamp", "model", "inject_mode", "iterations",
              "status", "wall_clock_s", "schema_errors",
              "gpu_name", "prompt_tokens", "completion_tokens", "llm_seconds",
              "version", "git_commit", "rules_hash"]

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

    LAST_RUN.clear()
    LAST_RUN.update({"status": status, "iterations": iterations, "wall_clock_s": wall_clock_s})
    file_exists = os.path.exists(RESULTS_FILE)
    with open(RESULTS_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADER)
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            client.MODEL_NAME,
            inject_mode or "none",
            iterations,
            status,
            f"{wall_clock_s:.2f}",
            schema_errors,
            _gpu_name(),
            sum(s.get("prompt_tokens", 0) for s in client.CALL_STATS),
            sum(s.get("completion_tokens", 0) for s in client.CALL_STATS),
            f"{sum(s.get('seconds', 0) for s in client.CALL_STATS):.1f}",
            *[version_info()[k] for k in ("version", "git_commit", "rules_hash")],
        ])

LAST_RUN: dict = {}  # outcome of the most recent run, read by the --repeats summary


def _normalize_plan(plan: ExpeditionPlan, record: TaskRecord) -> ExpeditionPlan:
    """Overwrite model-reported summary fields with values computed from the activities."""
    actual = round(sum(act.cost_eur for day in plan.days for act in day.activities), 2)
    if abs(plan.total_budget_eur - actual) > 0.01:
        record.note("pipeline", f"model reported total_budget_eur={plan.total_budget_eur}, actual sum={actual}")
        plan.total_budget_eur = actual
    plan.duration_days = len(plan.days)
    return plan


SOFT_REJECTION_LIMIT = 3  # consecutive soft-only rejections before Justice approves with warnings


def is_soft_note(note: str) -> bool:
    """A soft problem can be waived with a warning (cost floor, repeats, filler); everything else cannot."""
    return any(marker in note for marker in SOFT_MARKERS)


# ==========================================
# SWARM ORCHESTRATOR (Targeted State Machine)
# ==========================================
def apply_fault_injection(
    detailed_plan: ExpeditionPlan,
    iteration: int,
    inject_mode: str = None,
    required_hubs: list[str] = (),
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
        if _missing_hubs(detailed_plan, required_hubs):
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


SAVE_OUTPUTS = os.environ.get("SWARM_SAVE_OUTPUTS", "1") != "0"


def _save_outputs(req, mode, status, iterations, plan, record, warnings=None, plausibility=None, skills_used=None,
                  coordination=None, dispatcher=None):
    """Write plan.md/plan.json/history.md under outputs/<run>/. Never lets saving break a run."""
    if not SAVE_OUTPUTS:
        return
    try:
        from swarm.code_agents.practical import load_practical
        from swarm.output import output_writer
        d = output_writer.write_swarm_run(req.request_text(), mode or "none", client.MODEL_NAME, _gpu_name(), status,
                                          iterations, plan, record.log_path, warnings=warnings,
                                          requirements=req.model_dump(), plausibility=plausibility, skills_used=skills_used,
                                          coordination=coordination, practical=load_practical(req.cost_profile),
                                          dispatcher=dispatcher)
        print(f"[OUTPUT] Readable results saved: {d / 'index.md'}")
    except Exception as e:
        print(f"[OUTPUT] Could not save readable results: {type(e).__name__}: {e}")


def _finish_with_dispatcher(dispatcher, plan, workers, warnings, status):
    """After approval the Dispatcher runs the workers (if asked), then the closing agents (practical notes, cost
    report, handbook) and hands the result to you. A problem here must never turn an approved plan into a crash."""
    coordination = None
    try:
        if workers:
            coordination = dispatcher.run_workers(plan)
        dispatcher.finalize(plan, coordination, warnings, status)
        dispatcher.finish("routes, emails and handbook ready" if workers else "plan and handbook ready")
    except Exception as e:
        print(f"[DISPATCHER] failed: {type(e).__name__}: {e}")
        dispatcher.record.note("dispatcher", f"failed: {type(e).__name__}: {e}")
        dispatcher.board.fail_running(str(e)[:120])
    return coordination


def run_swarm_loop(req: Requirements, max_iterations: int = 6, inject_mode: str = None, workers: bool = False):
    """workers=True: after approval the Dispatcher also runs the Transit and Outreach agents."""
    request = req.request_text()
    reset_call_log()
    LAST_RUN.clear()
    start_time = time.time()
    schema_errors = 0
    iterations_used = 0
    iterations_log = []
    final_status = "CRASH"
    final_plan = None
    warnings: list[str] = []
    coordination = None
    dispatcher = None
    soft_rejections = 0  # consecutive rejections caused only by soft (cost-level) problems

    task_id = f"{datetime.now():%Y%m%d_%H%M%S}_{inject_mode or 'none'}"
    record = TaskRecord(task_id, request, log_dir=LOG_DIR)

    print("=" * 65)
    print(f"[MISSION START] Request:\n{request}")
    print(f"[CONFIG] model={client.MODEL_NAME} | inject_mode={inject_mode} | max_iter={max_iterations}")
    print(f"[VERSION] {version_line()}")
    record.note("pipeline", f"version {version_line()} | model {client.MODEL_NAME}")
    print(f"[AUDIT LOG] {record.log_path}")
    print("=" * 65)

    plaus = None
    cost_ref = load_cost_reference(req.cost_profile)
    meals = load_meals(req.cost_profile)
    hotel = choose_hotel(req.cost_profile, req.hotel)  # one hotel for the whole trip, chosen by rule
    if hotel:
        record.note("pipeline", f"hotel: {hotel['name']}")
    critique_feedback = []
    rough_plan = None
    detailed_plan = None
    target_agent = "tech_planner"
    logistics_retries = 0
    retrying = False  # True right after a schema error: same agent runs again, no new handoff

    try:
        # Audit: is the scenario realistic? (code only, before any model call)
        plaus = agent_audit(req, record)
        if not plaus["ok"]:
            print("[REJECTED] The request is not plausible. Change the scenario and run again.")
            _fail(record, "request not plausible")
            final_status = "REJECTED_IMPLAUSIBLE"
            log_run(inject_mode, 0, final_status, time.time() - start_time, 0)
            return None
        dispatcher = Dispatcher(record, req, workers=workers)
        dispatcher.plan_tasks()
        board = dispatcher.board

        for iteration in range(1, max_iterations + 1):
            iterations_used = iteration
            print(f"\n>>> --- [Iteration {iteration}] Routing to: {target_agent.upper()} --- <<<")

            try:
                run_planner = target_agent == "tech_planner" or rough_plan is None

                if not retrying:
                    summary = ("scenario approved by Audit" if iteration == 1
                               else f"rework after Justice: {len(critique_feedback)} note(s)")
                    dispatcher.give("tech_planner" if run_planner else "logistics", summary)

                if run_planner:
                    _move(record, Status.PLANNING, "tech_planner")
                    board.start(dispatcher.t_plan)
                    rough_plan = agent_tech_planner(req, rough_plan, critique_feedback)
                    board.finish(dispatcher.t_plan, True, f"{len(rough_plan.days)} day(s)")
                    dispatcher.take("tech_planner", f"rough itinerary, {len(rough_plan.days)} day(s)")
                    dispatcher.give("logistics", "schedule the rough itinerary")
                    _move(record, Status.SCHEDULING, "logistics")
                    board.start(dispatcher.t_schedule)
                    detailed_plan = agent_logistics_specialist(rough_plan, req.budget_per_person, record=record,
                        cost_ref=cost_ref, meals=meals)
                    logistics_retries = 0
                else:
                    _move(record, Status.SCHEDULING, "logistics")
                    print(f"  [Router] Skipping Agent 1 -> Direct fix in Agent 2 (Attempt {logistics_retries})...")
                    board.start(dispatcher.t_schedule)
                    detailed_plan = agent_logistics_specialist(
                        rough_plan, req.budget_per_person, detailed_plan, critique_feedback, record=record, cost_ref=cost_ref,
                        meals=meals,
                        temperature=0.7  # a direct fix needs variety, or the model repeats its draft
                    )

                detailed_plan = apply_fault_injection(detailed_plan, iteration, inject_mode, req.must_visit)
                detailed_plan = _normalize_plan(detailed_plan, record)
                for note in fix_overnight_times(detailed_plan) + fix_meal_times(detailed_plan, meals):
                    record.note("pipeline", note)
                dispatcher.give("finance", "add lodging, meals and local transport")
                board.start(dispatcher.t_finance)
                fin_notes = agent_finance(detailed_plan, cost_ref, meals_windows=meals, chosen_hotel=hotel)
                board.finish(dispatcher.t_finance, True, fin_notes[0])
                for note in fin_notes:
                    record.note("finance", note)
                dispatcher.take("finance", f"plan total {detailed_plan.total_budget_eur:.2f} EUR")

                total_cost = sum(act.cost_eur for day in detailed_plan.days for act in day.activities)
                print(f"  [Pipeline Output] Generated {len(detailed_plan.days)} days | Summed Cost: {total_cost:.2f} EUR")

                board.finish(dispatcher.t_schedule, True, f"{total_cost:.2f} EUR")
                dispatcher.take("logistics", f"{len(detailed_plan.days)} day(s), {total_cost:.2f} EUR")
                dispatcher.give("justice", "verify the plan")
                _move(record, Status.REVIEWING, "justice")
                board.start(dispatcher.t_verify)
                audit = agent_justice(detailed_plan, req, rough_plan)
                dispatcher.take("justice", "approved" if audit.passed else f"rejected, blame={audit.failed_agent}")
                retrying = False
                iterations_log.append({
                    "iteration": iteration, "planner_ran": run_planner, "total_cost": total_cost,
                    "rough_plan": rough_plan.model_dump(), "detailed_plan": detailed_plan.model_dump(),
                    "audit": audit.model_dump(), "verification": list(VERIFY_LOG)})

            except (ValidationError, client.StructuredOutputError) as e:
                schema_errors += 1
                retrying = True
                print(f"  [Schema Error #{schema_errors}] Parsing failed on iteration {iteration}: {e}")
                record.note("pipeline", f"schema error #{schema_errors} on iteration {iteration}")
                iterations_log.append({"iteration": iteration, "error": f"{type(e).__name__}: {str(e)[:300]}"})
                critique_feedback = [f"Schema error: {str(e)}"]
                if schema_errors >= 3:
                    print("  [ABORTED] Too many schema errors - model is not producing valid output.")
                    _fail(record, "too many schema errors")
                    final_status = "SCHEMA_ABORT"
                    log_run(inject_mode, iterations_used, "SCHEMA_ABORT",
                            time.time() - start_time, schema_errors)
                    return None
                continue

            soft_only = (not audit.passed and bool(audit.critique_notes)
                         and all(is_soft_note(n) for n in audit.critique_notes))
            soft_rejections = soft_rejections + 1 if soft_only else 0
            if soft_only and (soft_rejections >= SOFT_REJECTION_LIMIT or iteration == max_iterations):
                # Adapted from Edict's review rule: after 3 rounds (or on the last round) the reviewer
                # approves and attaches its suggestions. Only soft problems qualify; missing hubs,
                # budget overruns, overlaps and unverified places are never waived.
                elapsed = time.time() - start_time
                warnings = list(audit.critique_notes)
                iterations_log[-1]["forced_approval"] = True
                board.finish(dispatcher.t_verify, True, f"approved with {len(warnings)} warning(s)")
                record.transition(Status.COMPLETED, "justice", "approved with warnings (only soft problems left)")
                print(f"\n[APPROVED WITH WARNINGS] Only soft problems remained after {soft_rejections} soft round(s) ({elapsed:.1f}s)")
                for w in warnings:
                    print(f"   ! {w}")
                print(f"[STATE PATH] {' -> '.join(record.path_taken())}")
                log_run(inject_mode, iterations_used, "SUCCESS_WITH_WARNINGS", elapsed, schema_errors)
                final_status, final_plan = "SUCCESS_WITH_WARNINGS", detailed_plan.model_dump()
                coordination = _finish_with_dispatcher(dispatcher, detailed_plan, workers, warnings, final_status)
                final_plan = detailed_plan.model_dump()  # Transit may have set the transport modes
                return detailed_plan

            if audit.passed:
                elapsed = time.time() - start_time
                board.finish(dispatcher.t_verify, True, "approved")
                record.transition(Status.COMPLETED, "justice", "plan approved")
                print(f"\n[MISSION SUCCESS] Plan APPROVED on Iteration {iteration}! ({elapsed:.1f}s)")
                print(f"[STATE PATH] {' -> '.join(record.path_taken())}")
                print(detailed_plan.model_dump_json(indent=2))
                log_run(inject_mode, iterations_used, "SUCCESS", elapsed, schema_errors)
                final_status, final_plan = "SUCCESS", detailed_plan.model_dump()
                coordination = _finish_with_dispatcher(dispatcher, detailed_plan, workers, warnings, final_status)
                final_plan = detailed_plan.model_dump()  # Transit may have set the transport modes
                return detailed_plan
            else:
                print(f"\n[REJECTED] Blame assigned to: [{audit.failed_agent.upper()}]")
                for note in audit.critique_notes:
                    print(f"   * {note}")
                record.note("justice", f"rejected, blame={audit.failed_agent}: " + " | ".join(audit.critique_notes))
                board.finish(dispatcher.t_verify, False, f"blame={audit.failed_agent}")
                target_agent, critique_feedback, logistics_retries = dispatcher.route_rejection(
                    audit, logistics_retries, soft_only)

        elapsed = time.time() - start_time
        print(f"\n[ABORTED] Could not converge within {max_iterations} iterations. ({elapsed:.1f}s)")
        print(f"[STATE PATH] {' -> '.join(record.path_taken())}")
        _fail(record, "max iterations reached")
        final_status = "ABORTED_MAX_ITER"
        log_run(inject_mode, iterations_used, "ABORTED_MAX_ITER", elapsed, schema_errors)
        return None

    except Exception as e:
        # Catch-all: network errors, Ollama disconnects, index errors in fault injection, etc.
        elapsed = time.time() - start_time
        print(f"\n[CRASHED] {type(e).__name__}: {e} ({elapsed:.1f}s)")
        _fail(record, f"crash: {type(e).__name__}: {e}")
        final_status = f"CRASH_{type(e).__name__}"
        log_run(inject_mode, iterations_used, f"CRASH_{type(e).__name__}", elapsed, schema_errors)
        return None

    finally:
        client._report_llm_stats(record)
        _save_outputs(req, inject_mode, final_status, iterations_log, final_plan, record, warnings, plaus,
                      skills_summary(), coordination, dispatcher)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run the expedition swarm on a saved scenario.")
    parser.add_argument("--scenario", default="shenzhen_3day", help="name in scenarios/ or a path to a .json file")
    parser.add_argument("--modes", nargs="*", default=["none", "missing_hub", "budget_spike", "saturation_escalation"],
                        help="fault-injection modes to run ('none' = a normal run)")
    parser.add_argument("--no-workers", action="store_true",
                        help="skip the Transit and Outreach agents after approval (faster, loop only)")
    parser.add_argument("--repeats", type=int, default=1, help="run every mode this many times and print a summary table")
    args = parser.parse_args()
    scenario = load_scenario(args.scenario)

    batch = []
    for n in range(1, args.repeats + 1):
        for mode in args.modes:
            print(f"\n\n########## RUNNING MODE: {mode} (repeat {n}/{args.repeats}) ##########")
            plan = run_swarm_loop(scenario, max_iterations=6, inject_mode=None if mode == "none" else mode,
                                  workers=not args.no_workers)
            batch.append(batch_summary.run_row(mode, LAST_RUN, plan))
    if args.repeats > 1 or len(batch) > 1:
        print(batch_summary.save_summary(args.scenario, client.MODEL_NAME, version_line(), batch))
