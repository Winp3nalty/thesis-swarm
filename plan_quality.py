"""
Quality checks on a finished plan that cost and place checks cannot see: repeated visits,
days filled with hotel time, and more than one hotel check-in or check-out.

All three are SOFT problems (see SOFT_MARKERS): the Auditor keeps asking for a fix, but after
three rounds of only soft problems the plan is approved with warnings. The wording of each note
contains one of the markers, because run_loop.is_soft_note() recognises soft notes by text.
"""
from __future__ import annotations

# A note containing one of these is soft. The first two are written by reality.py (cost floors, places
# OpenStreetMap does not know: its free-text search misses many restaurants and markets, so that is
# weak evidence and ends as a warning, never as a rejection).
SOFT_MARKERS = ("below the realistic minimum", "was not found on OpenStreetMap", "(the last day) has no real visit",
                "repeated visit", "mostly hotel time", "more than one hotel check-", "Lodging is charged")

_CHECK_IN = ("check-in", "check in", "checkin")
_CHECK_OUT = ("check-out", "check out", "checkout")
_LODGING_WORDS = ("hotel", "hostel", "lodging", "apartment", "accommodation")


def lodging_charge_days(plan, hotel_eur: float) -> set[int]:
    """Days that carry a lodging charge (a row at a hotel-like location costing about one night)."""
    return {d.day for d in plan.days for a in d.activities
            if any(w in a.location.lower() for w in _LODGING_WORDS) and a.cost_eur >= 0.8 * hotel_eur}


def quality_check(plan, is_generic, max_days_per_place: int = 1, hotel_eur: float | None = None) -> dict:
    """Return {"planner_issues": [...], "logistics_issues": [...]}; is_generic(name) is True for hotel/transport."""
    planner, logistics = [], []

    # 1. the same real place on too many days
    days_at: dict[str, set[int]] = {}
    shown: dict[str, str] = {}
    for day in plan.days:
        for a in day.activities:
            if not is_generic(a.location):
                key = a.location.strip().lower()
                days_at.setdefault(key, set()).add(day.day)
                shown.setdefault(key, a.location.strip())
    for key, days in days_at.items():
        if len(days) > max_days_per_place:
            planner.append(f"'{shown[key]}' is a repeated visit (days {', '.join(map(str, sorted(days)))}). "
                           f"Visit it on at most {max_days_per_place} day(s) and plan a different real place "
                           "for the other days.")

    # 2. days where hotel time outweighs real visits
    for day in plan.days:
        real = sum(1 for a in day.activities if not is_generic(a.location))
        generic = len(day.activities) - real
        if real >= 1 and generic > real + 1:
            logistics.append(f"Day {day.day} is mostly hotel time ({generic} of {len(day.activities)} activities "
                             f"are at the hotel or in transit, {real} real visit). Remove the extra hotel rows: put "
                             "meal costs into the visit rows instead of adding new venues.")

    # 3. one stay has one check-in and one check-out
    for words, label in ((_CHECK_IN, "check-in"), (_CHECK_OUT, "check-out")):
        days = [d.day for d in plan.days for a in d.activities
                if any(w in a.activity_description.lower() for w in words)]
        if len(days) > 1:
            logistics.append(f"The plan has more than one hotel {label} (days {', '.join(map(str, days))}). "
                             f"A stay has one {label}; remove the extras.")

    # 4. lodging is charged once per night, not once per hotel row
    if hotel_eur:
        nights = max(len(plan.days) - 1, 0)
        charges = [a for d in plan.days for a in d.activities
                   if any(w in a.location.lower() for w in _LODGING_WORDS) and a.cost_eur >= 0.8 * hotel_eur]
        if nights and len(charges) > nights:
            logistics.append(f"Lodging is charged {len(charges)} times (about {hotel_eur:g} EUR each), but the trip "
                             f"has only {nights} nights. Charge each night once.")
    return {"planner_issues": planner, "logistics_issues": logistics}
