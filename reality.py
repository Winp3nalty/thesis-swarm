"""
Reality checks the Auditor runs on a finished plan, using the internet skills.

  planner issues   : a day with no real visit (only hotel / airport / filler)
  logistics issues : a place OpenStreetMap clearly cannot find, an activity whose location
                     contradicts its description, a travel gap shorter than half the driving
                     time, and costs below the reference table in data/cost_reference.json

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

_COST_FILE = Path(__file__).resolve().parent / "data" / "cost_reference.json"
# Not places to visit: lodging and transport. A day with only these has no real visit.
GENERIC_WORDS = ("hotel", "hostel", "airport", "station", "accommodation", "apartment", "lodging", "home",
                 "metro", "subway", "bus", "taxi", "transfer", "travel", "transit", "train")
STAY_WORDS = ("check-in", "check in", "check-out", "check out", "checkout", "checkin", "hotel", "hostel")
PACE_FACTOR = 0.5  # reject a travel gap only if it is shorter than half the driving time


def _is_generic(location: str) -> bool:
    """True for lodging/transport names. Whole words only: 'Business School' is not a bus."""
    words = set(re.findall(r"[a-z]+", location.lower()))
    return any(w in words for w in GENERIC_WORDS)


def _cost_reference() -> dict:
    try:
        return json.loads(_COST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def reality_check(plan, required_hubs: list[str]) -> dict:
    """Return {"planner_issues": [...], "logistics_issues": [...], "log": [...]}."""
    planner, logistics, log = [], [], []
    city = plan.destination
    required = {h.lower() for h in required_hubs}
    coords: dict[str, tuple[float, float]] = {}

    def locate(name: str):
        key = name.lower()
        if key not in coords:
            res = run_skill("verify_place", place=name, city=city)
            r = res.get("result", {}) if res["ok"] else {"found": None, "source": "error"}
            coords[key] = (r["lat"], r["lon"]) if r.get("found") else None
            found = r.get("found")
            log.append({"check": "place", "name": name, "found": found, "source": r.get("source"),
                        "detail": r.get("display_name", "")})
            if found is False and key not in required and not _is_generic(name):
                logistics.append(f"Place '{name}' was not found on OpenStreetMap in {city}. "
                                 "Use a real, existing venue name.")
        return coords[key]

    # 1. every day needs at least one real visit
    for day in plan.days:
        real = [a for a in day.activities if not _is_generic(a.location)]
        if day.activities and not real:
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

    # 4. costs against the reference table
    ref = _cost_reference()
    day_min, trip_min = ref.get("min_day_total_eur"), ref.get("min_trip_total_eur")
    hotel, meals, transport = (ref.get("hotel_night_eur"), ref.get("meals_per_day_eur"),
                               ref.get("local_transport_per_day_eur"))
    for day in plan.days:
        total = sum(a.cost_eur for a in day.activities)
        if day_min and day.activities and total < day_min:
            hint = (f" For example add meals of about {meals} EUR and local transport of about {transport} EUR."
                    if meals and transport else " Add realistic meal and transport costs.")
            logistics.append(f"Day {day.day}: total cost {total:.2f} EUR is below the realistic minimum of "
                             f"{day_min} EUR per day. Add about {day_min - total:.0f} EUR.{hint}")
    trip_total = sum(a.cost_eur for d in plan.days for a in d.activities)
    if trip_min and trip_total < trip_min:
        nights = max(len(plan.days) - 1, 1)
        hint = (f" For example add one 'Hotel' activity of {hotel} EUR for each of the {nights} nights "
                f"(lodging), if the plan has none yet." if hotel else " Add the missing lodging, meals and transport.")
        logistics.append(f"Trip total {trip_total:.2f} EUR is below the realistic minimum of {trip_min} EUR. "
                         f"Add about {trip_min - trip_total:.0f} EUR.{hint}")
    return {"planner_issues": planner, "logistics_issues": logistics, "log": log}


def cost_guide_text(num_days: int) -> str:
    """Prompt block that tells Logistics the cost expectations the Auditor will enforce."""
    ref = _cost_reference()
    hotel, meals, transport = ref.get("hotel_night_eur"), ref.get("meals_per_day_eur"), ref.get("local_transport_per_day_eur")
    if not (hotel and meals and transport):
        return ""
    day_min, trip_min = ref.get("min_day_total_eur"), ref.get("min_trip_total_eur")
    lines = ["", "Cost guide (the Auditor rejects plans that ignore it):",
             f"- Lodging: one 'Hotel' activity per night ({max(num_days - 1, 1)} nights), about {hotel} EUR each.",
             f"- Meals: about {meals} EUR per day, as part of the day's activities.",
             f"- Local transport: about {transport} EUR per day."]
    if day_min:
        lines.append(f"- Every day must total at least {day_min} EUR.")
    if trip_min:
        lines.append(f"- The whole trip must total at least {trip_min} EUR (and stay under the budget cap).")
    return "\n".join(lines) + "\n"
