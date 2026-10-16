from openai import OpenAI
from pydantic import BaseModel, ValidationError
from schemas import RoughItinerary, ExpeditionPlan, AuditResult
import csv
import os
import time
from datetime import datetime

RESULTS_FILE = "results.csv"
CSV_HEADER = ["timestamp", "model", "inject_mode", "iterations",
              "status", "wall_clock_s", "schema_errors"]


def log_run(inject_mode, iterations, status, wall_clock_s, schema_errors):
    """Append one row per run. Creates the file with a header on first write."""
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
        ])

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
MODEL_NAME = "qwen2.5:7b"

MAX_BUDGET_EUR = 1200.0
REQUIRED_HUBS = ["DJI", "HAX", "Huaqiangbei"]


def call_structured_llm(
    system_prompt: str,
    user_prompt: str,
    response_model: type[BaseModel],
    temperature: float = 0.2,
):
    completion = client.beta.chat.completions.parse(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=response_model,
        temperature=temperature,
    )
    return completion.choices[0].message.parsed


# ==========================================
# AGENT 1: Lead Route & Tech Planner
# ==========================================
def agent_tech_planner(
    user_request: str,
    previous_plan: RoughItinerary = None,
    critique_feedback: list[str] = None,
) -> RoughItinerary:
    print("  [Agent 1: Tech Planner] Designing high-level technical itinerary...")
    system_prompt = (
        "You are the Lead Technical Route Planner for engineering expeditions. "
        "Select high-impact robotics labs and hardware hubs for each day of the trip."
    )
    user_prompt = f"User Request: {user_request}\n"

    if previous_plan and critique_feedback:
        user_prompt += (
            f"\nYOUR PREVIOUS DRAFT:\n{previous_plan.model_dump_json(indent=2)}\n"
            "\nFIX THESE HUB ERRORS WITHOUT REMOVING EXISTING VALID HUBS:\n"
            + "\n".join(f"- {note}" for note in critique_feedback)
        )
    return call_structured_llm(system_prompt, user_prompt, RoughItinerary)


# ==========================================
# AGENT 2: Logistics & Budget Specialist
# ==========================================
def agent_logistics_specialist(
    rough_plan: RoughItinerary,
    budget_cap: float,
    previous_detailed: ExpeditionPlan = None,
    critique_feedback: list[str] = None,
) -> ExpeditionPlan:
    print("  [Agent 2: Logistics Specialist] Enforcing time slots, transit, and EUR costs...")
    system_prompt = (
        "You are the Logistics & Budget Specialist. "
        "Expand the rough itinerary into a detailed daily schedule with start_time, end_time, "
        f"transit_mode, and cost_eur. Keep total costs strictly under {budget_cap} EUR."
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
# ==========================================
def agent_auditor(plan: ExpeditionPlan) -> AuditResult:
    print("  [Agent 3: Auditor Agent] Evaluating feasibility and routing feedback...")
    actual_sum = sum(act.cost_eur for day in plan.days for act in day.activities)
    plan_text = plan.model_dump_json().lower()
    missing_hubs = [h for h in REQUIRED_HUBS if h.lower() not in plan_text]

    system_prompt = (
        "You are the Chief Expedition Auditor Agent. "
        "Review the expedition plan and telemetry facts.\n"
        "- If mandatory hubs are missing, set passed=False and failed_agent='tech_planner'.\n"
        "- If all hubs are present but budget or schedule fails, set passed=False and failed_agent='logistics'.\n"
        "- If everything is satisfied, set passed=True and failed_agent='none'."
    )
    user_prompt = (
        f"Max Allowed Budget: {MAX_BUDGET_EUR} EUR\n"
        f"Actual Summed Cost: {actual_sum:.2f} EUR\n"
        f"Currently Missing Hubs: {missing_hubs}\n\n"
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
    elif actual_sum > MAX_BUDGET_EUR:
        audit.passed = False
        audit.failed_agent = "logistics"
        audit.critique_notes.append(
            f"Budget exceeded: {actual_sum:.2f} EUR > {MAX_BUDGET_EUR} EUR. Reduce activity or transit costs."
        )

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
        plan_text = detailed_plan.model_dump_json().lower()
        if any(h.lower() not in plan_text for h in REQUIRED_HUBS):
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


def run_swarm_loop(request: str, max_iterations: int = 6, inject_mode: str = None):
    start_time = time.time()
    schema_errors = 0
    iterations_used = 0

    print("=" * 65)
    print(f"[MISSION START] Request:\n{request}")
    print(f"[CONFIG] model={MODEL_NAME} | inject_mode={inject_mode} | max_iter={max_iterations}")
    print("=" * 65)

    critique_feedback = []
    rough_plan = None
    detailed_plan = None
    target_agent = "tech_planner"
    logistics_retries = 0

    try:
        for iteration in range(1, max_iterations + 1):
            iterations_used = iteration
            print(f"\n>>> --- [Iteration {iteration}] Routing to: {target_agent.upper()} --- <<<")

            try:
                if target_agent == "tech_planner" or rough_plan is None:
                    rough_plan = agent_tech_planner(request, rough_plan, critique_feedback)
                    detailed_plan = agent_logistics_specialist(rough_plan, MAX_BUDGET_EUR)
                    logistics_retries = 0
                else:
                    print(f"  [Router] Skipping Agent 1 -> Direct fix in Agent 2 (Attempt {logistics_retries})...")
                    detailed_plan = agent_logistics_specialist(
                        rough_plan, MAX_BUDGET_EUR, detailed_plan, critique_feedback
                    )

                detailed_plan = apply_fault_injection(detailed_plan, iteration, inject_mode)

                total_cost = sum(act.cost_eur for day in detailed_plan.days for act in day.activities)
                print(f"  [Pipeline Output] Generated {len(detailed_plan.days)} days | Summed Cost: {total_cost:.2f} EUR")

                audit = agent_auditor(detailed_plan)

            except ValidationError as e:
                schema_errors += 1
                print(f"  [Schema Error #{schema_errors}] Parsing failed on iteration {iteration}: {e}")
                critique_feedback = [f"Schema error: {str(e)}"]
                if schema_errors >= 3:
                    print("  [ABORTED] Too many schema errors - model is not producing valid output.")
                    log_run(inject_mode, iterations_used, "SCHEMA_ABORT",
                            time.time() - start_time, schema_errors)
                    return None
                continue

            if audit.passed:
                elapsed = time.time() - start_time
                print(f"\n[MISSION SUCCESS] Plan APPROVED on Iteration {iteration}! ({elapsed:.1f}s)")
                print(detailed_plan.model_dump_json(indent=2))
                log_run(inject_mode, iterations_used, "SUCCESS", elapsed, schema_errors)
                return detailed_plan
            else:
                print(f"\n[REJECTED] Blame assigned to: [{audit.failed_agent.upper()}]")
                for note in audit.critique_notes:
                    print(f"   * {note}")

                critique_feedback = audit.critique_notes
                target_agent = audit.failed_agent

                if target_agent == "logistics":
                    logistics_retries += 1
                    if logistics_retries >= 2:
                        print("  [ESCALATION] Agent 2 saturated! Escalating fault back to Agent 1...")
                        target_agent = "tech_planner"
                        critique_feedback.append(
                            "ESCALATION: Logistics could not fit your hubs within budget/time constraints. "
                            "Simplify or redistribute target_hubs across days."
                        )

        elapsed = time.time() - start_time
        print(f"\n[ABORTED] Could not converge within {max_iterations} iterations. ({elapsed:.1f}s)")
        log_run(inject_mode, iterations_used, "ABORTED_MAX_ITER", elapsed, schema_errors)
        return None

    except Exception as e:
        # Catch-all: network errors, Ollama disconnects, index errors in fault injection, etc.
        elapsed = time.time() - start_time
        print(f"\n[CRASHED] {type(e).__name__}: {e} ({elapsed:.1f}s)")
        log_run(inject_mode, iterations_used, f"CRASH_{type(e).__name__}", elapsed, schema_errors)
        return None


if __name__ == "__main__":
    test_request = (
        "Organize a 3-day robotics expedition to Shenzhen. "
        "Strict budget ceiling is 1200 EUR."
    )

    for mode in [None, "missing_hub", "budget_spike", "saturation_escalation"]:
        print(f"\n\n########## RUNNING MODE: {mode} ##########")
        run_swarm_loop(test_request, max_iterations=6, inject_mode=mode)