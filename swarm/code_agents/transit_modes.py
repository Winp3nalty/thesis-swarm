"""
Transport modes per city (data/transit/<profile>.json) and what the Transit agent may answer.

The file lists the modes that exist for a visitor (Metro, Bus, Taxi, ... for Shenzhen; Caltrain, BART, VTA, ... for
the Bay Area) and a few aliases ("subway" is Metro). Plain code, no model: it checks the Transit agent's answer,
names the canonical mode, and writes the modes into the approved plan after Transit has run.
"""
from __future__ import annotations

import json

from swarm.core.paths import DATA_DIR

_DIR = DATA_DIR / "transit"


def load_transit_modes(profile: str) -> dict:
    """{"modes": [...], "aliases": {...}} for a city profile; {} when the city has no file (then no mode check)."""
    path = _DIR / f"{profile}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {"modes": list(data.get("modes", [])),
            "aliases": {k.lower(): v for k, v in data.get("aliases", {}).items()}}


def canonical_mode(mode: str, rules: dict) -> str | None:
    """The listed mode that an answer like 'Metro Line 1' or 'subway' means, or None if it matches none.
    Without rules every answer is accepted as it is."""
    if not rules or not rules.get("modes"):
        return mode.strip() or None
    low = mode.lower()
    for name in rules["modes"]:
        if name.lower() in low:
            return name
    for alias, name in rules.get("aliases", {}).items():
        if alias in low:
            return name
    return None


def mode_problem(mode: str, rules: dict, city: str, leg_no: int) -> str | None:
    """A rejection text for the retry loop, or None when the mode is acceptable."""
    if canonical_mode(mode, rules) is not None:
        return None
    return (f"leg {leg_no} uses '{mode}', which is not a transport mode for visitors in {city}; "
            f"use one of: {', '.join(rules['modes'])}")


def write_modes_into_plan(plan, day_number: int, stop_activities: list, legs: list, rules: dict) -> int:
    """Set each visit's transit_mode to the mode of the leg that reaches it. The first visit of the day is reached
    from the hotel (Transit plans no leg for it). Returns how many visits were set."""
    day = next((d for d in plan.days if d.day == day_number), None)
    if day is None or len(stop_activities) != len(legs) + 1:
        return 0
    stop_activities[0].transit_mode = "from hotel"
    for act, leg in zip(stop_activities[1:], legs):
        act.transit_mode = canonical_mode(leg["mode"], rules) or leg["mode"]
    return len(stop_activities)
