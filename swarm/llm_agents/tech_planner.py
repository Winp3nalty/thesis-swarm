"""
Tech Planner (Agent 1): chooses the places to visit each day (persona and rules in prompts/tech_planner/SOUL.md).
It returns a rough itinerary (hubs per day); Logistics adds times and costs.
"""
from __future__ import annotations

from swarm.core.requirements_schema import Requirements
from swarm.core.schemas import RoughItinerary
from swarm.core.soul_loader import build_system_prompt
from swarm.llm_agents import client


def agent_tech_planner(
    req: Requirements,
    previous_plan: RoughItinerary = None,
    critique_feedback: list[str] = None,
    temperature: float = 0.2,
) -> RoughItinerary:
    print("  [Agent 1: Tech Planner] Designing high-level technical itinerary...")
    system_prompt = build_system_prompt("tech_planner")
    user_prompt = (
        f"User Request: {req.request_text()}\n"
        f"Mandatory hubs (each one must be visited; use these exact names): {', '.join(req.must_visit)}\n"
        f"Plan exactly {req.days} days. Every day needs a real visit, and a place may appear on at most "
        f"{req.max_days_per_place} day(s); a 'revisit' of a place already used on another day does not count as "
        "a visit, so a day with one new place is better than a day with a repeated one. If there are fewer mandatory hubs than days, add other real "
        f"companies, labs or universities in {req.region}.\n"
        "Aim for two real visits per day (one in the morning, one in the afternoon) in the same part of the city: "
        "list both in target_hubs for that day. A day with a single visit is acceptable only when nothing "
        "suitable is left.\n"
    )
    pool = list(dict.fromkeys(req.must_visit + req.optional_places))
    if len(pool) < 2 * req.days:
        user_prompt += (f"You have {len(pool)} distinct places for {2 * req.days} visit slots. Use every place at most "
                        "once. When the places run out, give the remaining days a single visit; never repeat a place "
                        "to fill a slot.\n")
    if req.optional_places:
        user_prompt += ("Real places you may add as extra visits (use these exact names; other places only if you "
                        f"are sure they exist): {', '.join(req.optional_places)}\n")

    if previous_plan and critique_feedback:
        user_prompt += (
            f"\nYOUR PREVIOUS DRAFT:\n{previous_plan.model_dump_json(indent=2)}\n"
            "\nFIX THESE HUB ERRORS WITHOUT REMOVING EXISTING VALID HUBS:\n"
            + "\n".join(f"- {note}" for note in critique_feedback)
        )
        used = {h.hub_name.strip().lower() for d in previous_plan.days for h in d.target_hubs}
        unused = [p for p in pool if p.lower() not in used]
        user_prompt += ("\nPlaces from the lists not used yet: " + ", ".join(unused) + "\n") if unused else \
            "\nEvery listed place is already used; give a day a single visit rather than repeating a place.\n"
    plan = client.call_structured_llm(system_prompt, user_prompt, RoughItinerary, temperature=temperature)
    if previous_plan and plan.model_dump() == previous_plan.model_dump():
        # Same prompt, same feedback, same answer: say so and sample more freely (as Logistics does).
        print("  [Agent 1] Draft identical to the previous one. Asking again with a warning.")
        plan = client.call_structured_llm(
            system_prompt,
            user_prompt + "\nYOUR NEW DRAFT WAS IDENTICAL TO THE PREVIOUS ONE, SO THE LISTED PROBLEMS ARE STILL "
                          "THERE. Change the plan so that each listed problem is fixed.",
            RoughItinerary, temperature=min(temperature + 0.6, 1.0))
    return plan
