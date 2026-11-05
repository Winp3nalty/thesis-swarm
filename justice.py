"""Justice: checks a finished plan against real data and says who must fix what.

Plain checks, no opinion: budget, schedule overlaps, places on OpenStreetMap, driving times between visits,
cost floors, repeated visits, hotel filler and nights. The model only words the verdict; when a check found
something, the check's own notes are the verdict. A rejection names the agent that must fix it
(AuditResult.failed_agent), and the Dispatcher sends the work there.
(Edict's Justice ministry, 刑部; ARCHITECTURE.md section 3.)
"""
from __future__ import annotations

import re

from plan_quality import quality_check
from reality import reality_check, load_cost_reference, _is_generic
from requirements_schema import Requirements
from schemas import AuditResult, ExpeditionPlan, RoughItinerary
from skills import run_skill
from soul_loader import build_system_prompt


def blame_planner_for_chosen_places(reality: dict, rough_plan: RoughItinerary, req: Requirements) -> None:
    """A place that the Planner chose (it is in the rough itinerary) and OpenStreetMap does not know is the
    Planner's problem; Logistics cannot fix it. Places that only appear in the schedule stay with Logistics."""
    chosen = {h.hub_name.strip().lower() for d in rough_plan.days for h in d.target_hubs}
    keep = []
    for issue in reality["logistics_issues"]:
        m = re.match(r"Place '(.+?)' was not found on OpenStreetMap", issue)
        if m and m.group(1).strip().lower() in chosen:
            reality["planner_issues"].append(
                f"Place '{m.group(1)}' was not found on OpenStreetMap in {req.region}. Replace it with a different "
                "real place from the optional list or another well-known venue that exists.")
        else:
            keep.append(issue)
    reality["logistics_issues"] = keep


def agent_justice(plan: ExpeditionPlan, req: Requirements, rough_plan: RoughItinerary = None) -> AuditResult:
    """Verify a finished plan against real data and say who must fix what."""
    import run_loop as rl  # imported here: run_loop imports this module
    print("  [Justice] Verifying the plan against real data and routing feedback...")

    # --- Skill: budget_check ---
    all_costs = [act.cost_eur for day in plan.days for act in day.activities]
    budget = run_skill("budget_check", costs=all_costs, cap=req.budget_eur)["result"]
    actual_sum = budget["total"]

    # --- Skill: schedule_check (per day) ---
    schedule_issues = []
    if len(plan.days) != req.days:
        schedule_issues.append(f"Plan has {len(plan.days)} day(s) but {req.days} are required.")
    for day in plan.days:
        if not day.activities:
            schedule_issues.append(f"Day {day.day}: no activities planned. Every day needs at least one activity.")
            continue
        res = run_skill("schedule_check", activities=[a.model_dump() for a in day.activities])
        if not res["ok"]:
            schedule_issues.append(f"Day {day.day}: could not check times ({res['error']}). Use 24-hour HH:MM.")
        elif not res["result"]["ok"]:
            schedule_issues.extend(f"Day {day.day}: {issue}" for issue in res["result"]["issues"])

    missing_hubs = rl._missing_hubs(plan, req.must_visit)

    # --- Reality checks (internet skills): real places, possible travel times, plausible costs ---
    cost_ref = load_cost_reference(req.cost_profile)
    reality = reality_check(plan, req.must_visit, req.place_search_names, req.region, cost_ref,
                            req.place_coordinates)
    quality = quality_check(plan, _is_generic, req.max_days_per_place, cost_ref.get("hotel_night_eur"))
    reality["planner_issues"] += quality["planner_issues"]
    reality["logistics_issues"] += quality["logistics_issues"]
    if rough_plan is not None:
        blame_planner_for_chosen_places(reality, rough_plan, req)
    rl.VERIFY_LOG[:] = reality["log"]
    for item in reality["log"]:
        print(f"  [Verify] {item['check']}: {item['name']} -> found={item['found']} ({item['source']}) {item['detail']}")

    system_prompt = build_system_prompt("justice")
    user_prompt = (
        f"Max Allowed Budget: {req.budget_eur:g} EUR\n"
        f"Actual Summed Cost: {actual_sum:.2f} EUR\n"
        f"Currently Missing Hubs: {missing_hubs}\n"
        f"Schedule Problems: {schedule_issues or 'none'}\n"
        f"Reality Problems: {reality['planner_issues'] + reality['logistics_issues'] or 'none'}\n\n"
        f"Full Plan:\n{plan.model_dump_json(indent=2)}"
    )
    audit = rl.call_structured_llm(system_prompt, user_prompt, AuditResult, temperature=0.0)

    # Deterministic routing & safety net. When the checks found something, THEIR notes are the verdict:
    # the model's own wording is dropped, because paraphrases like "mostly transit and hotel time" would
    # not be recognised as soft problems and would restart the soft-rejection count.
    found = reality["planner_issues"] + reality["logistics_issues"]
    if missing_hubs:
        audit.passed = False
        audit.failed_agent = "tech_planner"
        audit.critique_notes = [f"Missing mandatory hub: {h}" for h in missing_hubs] + found
    elif reality["planner_issues"]:
        audit.passed = False
        audit.failed_agent = "tech_planner"
        audit.critique_notes = found
    elif not budget["within_budget"] or schedule_issues or reality["logistics_issues"]:
        audit.passed = False
        audit.failed_agent = "logistics"
        audit.critique_notes = list(reality["logistics_issues"])
        if not budget["within_budget"]:
            audit.critique_notes.append(
                f"Budget exceeded: {actual_sum:.2f} EUR > {req.budget_eur:g} EUR. Reduce activity or transit costs."
            )
        audit.critique_notes.extend(schedule_issues)

    audit.critique_notes = rl._dedupe_notes(audit.critique_notes)
    return audit


