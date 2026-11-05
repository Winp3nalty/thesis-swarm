"""
Reality checks the Auditor runs on a finished plan, using the internet skills.

  planner issues   : a day with no real visit (only hotel / airport / filler)
  logistics issues : a place OpenStreetMap clearly cannot find, an activity whose location
                     contradicts its description, a travel gap shorter than half the driving
                     time, and costs below the floors derived from data/cost/<city>.json

Rule (chosen by the user): reject only on CLEAR evidence. When the web is unreachable a
check is logged as "unverified" and never rejects. Required hubs are exempt from the place
check, because short brand names ("HAX") are often missing from OpenStreetMap.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from skills import run_skill
from skills.builtin import _minutes

_COST_DIR = Path(__file__).resolve().parent / "data" / "cost"
# Not places to visit: lodging and transport. A day with only these has no real visit.
GENERIC_WORDS = ("hotel", "hostel", "airport", "station", "accommodation", "apartment", "lodging", "home",
                 "metro", "subway", "bus", "taxi", "transfer", "travel", "transit", "train")
STAY_WORDS = ("check-in", "check in", "check-out", "check out", "checkout", "checkin", "hotel", "hostel")
PACE_FACTOR = 0.5  # reject a travel gap only if it is shorter than half the driving time


def _is_generic(location: str) -> bool:
    """True for lodging/transport names. Whole words only: 'Business School' is not a bus."""
    words = set(re.findall(r"[a-z]+", location.lower()))
    return any(w in words for w in GENERIC_WORDS)


def load_cost_reference(profile: str) -> dict:
    """Price assumptions for a city (data/cost/<profile>.json); {} when the file is missing."""
    try:
        return json.loads((_COST_DIR / f"{profile}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cost_floors(ref: dict, days: int):
    """(cheapest realistic day, cheapest realistic trip) derived from the price table, or (None, None)."""
    hotel, meals, transport = (ref.get("hotel_night_eur"), ref.get("meals_per_day_eur"),
                               ref.get("local_transport_per_day_eur"))
    if hotel is None or meals is None or transport is None:
        return None, None
    day_min = meals + transport
    return day_min, day_min * days + hotel * max(days - 1, 0)


def reality_check(plan, required_hubs: list[str], search_names: dict | None = None,
                  region: str | None = None, cost_ref: dict | None = None) -> dict:
    """Return {"planner_issues": [...], "logistics_issues": [...], "log": [...]}."""
    planner, logistics, log = [], [], []
    city = region or plan.destination
    aliases = {k.lower(): v for k, v in (search_names or {}).items()}
    required = {h.lower() for h in required_hubs}
    coords: dict[str, tuple[float, float]] = {}

    def locate(name: str):
        key = name.lower()
        if key not in coords:
            res = run_skill("verify_place", place=aliases.get(key, name), city=city)
            r = res.get("result", {}) if res["ok"] else {"found": None, "source": "error"}
            coords[key] = (r["lat"], r["lon"]) if r.get("found") else None
            found = r.get("found")
            log.append({"check": "place", "name": name, "found": found, "source": r.get("source"),
                        "detail": r.get("display_name", "")})
            if found is False and key not in required and not _is_generic(name):
                logistics.append(f"Place '{name}' was not found on OpenStreetMap in {city}. If it is a meal, put "
                                 "its cost into a visit row instead; otherwise use the real name of an existing venue.")
        return coords[key]

    # 1. every day needs at least one real visit
    last_day = max((d.day for d in plan.days), default=0) if len(plan.days) > 1 else None
    for day in plan.days:
        real = [a for a in day.activities if not _is_generic(a.location)]
        if day.activities and not real:
            if day.day == last_day:  # a departure day is normal, so this one is only a warning
                planner.append(f"Day {day.day} (the last day) has no real visit, only hotel or travel. A departure "
                               "day is acceptable; a morning visit to a real place is better.")
            else:
                planner.append(f"Day {day.day} has no real visit (only hotel or travel). Plan a visit to a real "
                               "company, lab, university or market for this day.")

    # 2. place existence + description/location consistency
    for day in plan.days:
        for a in day.activities:
            if not _is_generic(a.location):
                locate(a.location)
            desc = a.activity_description.lower()
            if any(w in desc for w in STAY_WORDS) and not _is_generic(a.location):
                logistics.append(f"Day {day.day}: '{a.activity_description}' is a hotel activity but the "
                                 f"location says '{a.location}'. Set the location to the hotel.")

    # 3. travel gaps between consecutive activities at different real places
    for day in plan.days:
        acts = sorted(day.activities, key=lambda a: _minutes(a.start_time))
        for prev, nxt in zip(acts, acts[1:]):
            if prev.location.lower() == nxt.location.lower() or _is_generic(prev.location) or _is_generic(nxt.location):
                continue
            c1, c2 = locate(prev.location), locate(nxt.location)
            if not c1 or not c2:
                continue
            res = run_skill("travel_minutes", lat1=c1[0], lon1=c1[1], lat2=c2[0], lon2=c2[1])
            minutes = res["result"].get("minutes") if res["ok"] else None
            gap = _minutes(nxt.start_time) - _minutes(prev.end_time)
            log.append({"check": "travel", "name": f"{prev.location} -> {nxt.location}", "found": minutes is not None,
                        "source": res["result"].get("source") if res["ok"] else "error",
                        "detail": f"driving {minutes} min, gap {gap} min"})
            if minutes is not None and gap < PACE_FACTOR * minutes:
                logistics.append(f"Day {day.day}: only {gap} min between {prev.location} and {nxt.location}, but "
                                 f"the drive alone takes about {minutes:.0f} min. Leave more travel time.")

    # 4. costs against the floors derived from the city's price table
    ref = cost_ref or {}
    hotel, meals, transport = (ref.get("hotel_night_eur"), ref.get("meals_per_day_eur"),
                               ref.get("local_transport_per_day_eur"))
    day_min, trip_min = cost_floors(ref, len(plan.days))
    for day in plan.days:
        total = sum(a.cost_eur for a in day.activities)
        if day_min and day.activities and total < day_min:
            logistics.append(f"Day {day.day}: total cost {total:.2f} EUR is below the realistic minimum of "
                             f"{day_min:g} EUR per day (meals {meals:g} + local transport {transport:g}). "
                             f"Add about {day_min - total:.0f} EUR.")
    trip_total = sum(a.cost_eur for d in plan.days for a in d.activities)
    if trip_min and trip_total < trip_min:
        nights = max(len(plan.days) - 1, 0)
        hint = (f" For example add one 'Hotel' activity of {hotel:g} EUR for each of the {nights} nights "
                f"(lodging), if the plan has none yet." if nights else "")
        logistics.append(f"Trip total {trip_total:.2f} EUR is below the realistic minimum of {trip_min:g} EUR. "
                         f"Add about {trip_min - trip_total:.0f} EUR.{hint}")
    return {"planner_issues": planner, "logistics_issues": logistics, "log": log}


def cost_guide_text(num_days: int, ref: dict | None) -> str:
    """Prompt block that tells Logistics the cost expectations the Auditor will enforce."""
    ref = ref or {}
    day_min, trip_min = cost_floors(ref, num_days)
    if day_min is None:
        return ""
    nights = max(num_days - 1, 0)
    lines = ["", "Cost guide (the Auditor rejects plans that ignore it):"]
    if nights:
        lines.append(f"- Lodging: one 'Hotel' activity per night ({nights} nights), about {ref['hotel_night_eur']:g} EUR each.")
    lines += [f"- Meals: about {ref['meals_per_day_eur']:g} EUR per day, as part of the day's activities.",
              f"- Local transport: about {ref['local_transport_per_day_eur']:g} EUR per day.",
              f"- Every day must total at least {day_min:g} EUR.",
              f"- The whole trip must total at least {trip_min:g} EUR (and stay under the budget cap)."]
    return "\n".join(lines) + "\n"
