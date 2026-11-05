"""
Transit agent: the route between the stops of one day (persona and rules in prompts/transit/SOUL.md).

The Dispatcher gives it the ordered stops and, when OpenStreetMap routing knows them, the measured driving minutes.
"""
from __future__ import annotations

from swarm.code_agents.transit_modes import mode_problem
from swarm.core.soul_loader import build_system_prompt
from swarm.core.worker_schemas import TransitPlan
from swarm.llm_agents.retry import run_worker
from swarm.llm_agents.textcheck import _norm


def validate_transit(plan: TransitPlan, stops: list[str], drive_minutes: list | None = None,
                     mode_rules: dict | None = None, city: str = "this city") -> list[str]:
    problems = []
    if len(plan.legs) != len(stops) - 1:
        problems.append(f"expected {len(stops) - 1} legs (one per consecutive pair), got {len(plan.legs)}")
    for i, leg in enumerate(plan.legs[: len(stops) - 1]):
        if _norm(leg.from_stop) != _norm(stops[i]):
            problems.append(f"leg {i + 1} must start at '{stops[i]}'")
        if _norm(leg.to_stop) != _norm(stops[i + 1]):
            problems.append(f"leg {i + 1} must end at '{stops[i + 1]}'")
        if mode_rules and mode_rules.get("modes"):  # the city has a list of modes: the answer must be one of them
            problem = mode_problem(leg.mode, mode_rules, city, i + 1)
            if problem:
                problems.append(problem)
        d = drive_minutes[i] if drive_minutes and i < len(drive_minutes) else None
        if d is not None:  # a measured driving time exists: a trip far outside it is a made-up number
            lo, hi = max(2.0, 0.4 * d), 3 * d + 20
            if not lo <= leg.minutes <= hi:
                problems.append(f"leg {i + 1} ({stops[i]} to {stops[i + 1]}) takes {leg.minutes} min, but driving takes "
                                f"about {d:.0f} min; give a time between {lo:.0f} and {hi:.0f} min")
    return problems


def agent_transit(city: str, stops: list[str], day: int = 1, max_retries: int = 2,
                  drive_minutes: list | None = None, mode_rules: dict | None = None):
    if len(stops) < 2:
        raise ValueError("need at least two stops")
    print(f"  [Transit Agent] Routing {len(stops)} stops in {city}...")
    system_prompt = build_system_prompt("transit")
    user_prompt = (
        f"City: {city}\nDay: {day}\nOrdered stops:\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(stops))
        + f"\n\nReturn exactly {len(stops) - 1} leg(s), one between each consecutive pair of stops, in this exact order. "
        "Do not add a leg to a hotel or back to the start."
    )
    if mode_rules and mode_rules.get("modes"):
        user_prompt += (f"\nTransport modes for visitors in {city} (the mode of every leg must be one of these, "
                        f"named as written): {', '.join(mode_rules['modes'])}.\n")
    known = [(i, d) for i, d in enumerate(drive_minutes or []) if d is not None]
    if known:
        user_prompt += ("\nMeasured driving time (OpenStreetMap routing, no traffic), use it to judge the trip time:\n"
                        + "\n".join(f"- leg {i + 1}: about {d:.0f} min by car" for i, d in known) + "\n")
    return run_worker(system_prompt, user_prompt, TransitPlan,
                      lambda plan: validate_transit(plan, stops, drive_minutes, mode_rules, city), max_retries)


