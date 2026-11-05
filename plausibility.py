"""
Plausibility gate: is the REQUEST realistic? It runs before any model call (see ARCHITECTURE.md).

  hard problems  : budget below the cheapest realistic cost of the trip, more must-visit
                   places than the days can hold. The run stops and says why.
  warnings       : tight budget, no price table for the city, a must-visit place that
                   OpenStreetMap cannot find (the plan is not stopped for this).

Limits come from the scenario and from data/cost/<city>.json, not from constants in the code.
Try it on its own:   python plausibility.py shenzhen_7day
"""
from __future__ import annotations

import deps

deps.ensure()  # installs pydantic on first run

from reality import cost_floors, load_cost_reference
from skills import run_skill

MAX_HUBS_PER_DAY = 3
TIGHT_FACTOR = 1.25  # warn when the budget is less than 25% above the cheapest realistic trip


def plausibility_check(req) -> dict:
    hard, warnings, log = [], [], []
    day_min, trip_min = cost_floors(load_cost_reference(req.cost_profile), req.days)
    if trip_min is None:
        warnings.append(f"No price table for '{req.cost_profile}' (data/cost/{req.cost_profile}.json): "
                        "budget and cost checks are off.")
    elif req.budget_eur < trip_min:
        hard.append(f"The budget of {req.budget_eur:g} EUR is below the cheapest realistic cost of "
                    f"{trip_min:g} EUR for {req.days} day(s) in {req.city} (lodging, meals, local transport). "
                    "Raise the budget or shorten the trip.")
    elif req.budget_eur < TIGHT_FACTOR * trip_min:
        warnings.append(f"The budget of {req.budget_eur:g} EUR is tight: the cheapest realistic trip costs "
                        f"{trip_min:g} EUR.")
    if len(req.must_visit) > req.days * MAX_HUBS_PER_DAY:
        hard.append(f"{len(req.must_visit)} must-visit places do not fit into {req.days} day(s) "
                    f"(at most {MAX_HUBS_PER_DAY} per day).")
    aliases = {k.lower(): v for k, v in req.place_search_names.items()}
    for hub in req.must_visit:
        query = aliases.get(hub.lower(), hub)
        if req.known_coords(hub):
            log.append({"check": "place", "name": hub, "found": True, "source": "scenario",
                        "detail": "coordinates given in the scenario (place_coordinates)"})
            continue
        res = run_skill("verify_place", place=query, city=req.region)
        r = res.get("result", {}) if res["ok"] else {"found": None, "source": "error"}
        log.append({"check": "place", "name": hub, "found": r.get("found"), "source": r.get("source"),
                    "detail": f"searched '{query}, {req.region}'"})
        if r.get("found") is False:
            warnings.append(f"'{hub}' was not found on OpenStreetMap (searched '{query}, {req.region}'). "
                            "Check the spelling or add a better search text under place_search_names in the scenario.")
    for place in req.optional_places:
        query = aliases.get(place.lower(), place)
        if req.known_coords(place):
            log.append({"check": "optional place", "name": place, "found": True, "source": "scenario",
                        "detail": "coordinates given in the scenario (place_coordinates)"})
            continue
        res = run_skill("verify_place", place=query, city=req.region)
        r = res.get("result", {}) if res["ok"] else {"found": None, "source": "error"}
        log.append({"check": "optional place", "name": place, "found": r.get("found"), "source": r.get("source"),
                    "detail": f"searched '{query}, {req.region}'"})
        if r.get("found") is False:
            warnings.append(f"Optional place '{place}' was not found on OpenStreetMap. Remove it from optional_places "
                            "or find a better spelling, so the Planner is not offered a place the Auditor will flag.")
    return {"ok": not hard, "hard": hard, "warnings": warnings, "log": log,
            "floors": {"day_eur": day_min, "trip_eur": trip_min}}


if __name__ == "__main__":
    import json
    import sys
    from requirements_schema import load_scenario
    result = plausibility_check(load_scenario(sys.argv[1] if len(sys.argv) > 1 else "shenzhen_3day"))
    print(json.dumps(result, indent=2, ensure_ascii=False))
