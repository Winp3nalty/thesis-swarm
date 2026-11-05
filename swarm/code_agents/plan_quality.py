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
                "repeated visit", "mostly hotel time", "more than one hotel check-", "Lodging is charged",
                "'Overnight stay' row starts at", "has no overnight stay")

_CHECK_IN = ("check-in", "check in", "checkin")
_CHECK_OUT = ("check-out", "check out", "checkout")
_LODGING_WORDS = ("hotel", "hostel", "lodging", "apartment", "accommodation")


def lodging_charge_days(plan, hotel_eur: float) -> set[int]:
    """Days that carry a lodging charge (a row at a hotel-like location costing about one night)."""
    return {d.day for d in plan.days for a in d.activities
            if any(w in a.location.lower() for w in _LODGING_WORDS) and a.cost_eur >= 0.8 * hotel_eur}


def _is_overnight(a) -> bool:
    return "overnight" in a.activity_description.lower() and any(w in a.location.lower() for w in _LODGING_WORDS)


def fix_overnight_times(plan) -> list[str]:
    """Code, not the model, sets the time of the 'Overnight stay' row: 20:00-23:59 when every other row of the
    day ends by 20:00. The model ignored this rule in every round, so asking again only used up iterations.
    Returns a note for each day that was changed."""
    notes = []
    for d in plan.days:
        for a in d.activities:
            if not _is_overnight(a):
                continue
            try:
                hour = int(a.start_time.split(":")[0])
                others = [int(o.end_time.split(":")[0]) * 60 + int(o.end_time.split(":")[1])
                          for o in d.activities if o is not a]
            except (ValueError, IndexError):
                continue
            if hour < 20 and all(e <= 20 * 60 for e in others):
                notes.append(f"day {d.day}: overnight row moved from {a.start_time} to 20:00")
                a.start_time, a.end_time = "20:00", "23:59"
    return notes


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

    # 4b. a trip of N days needs N-1 nights: report the days without an overnight stay
    if hotel_eur and len(plan.days) > 1:
        slept = {d.day for d in plan.days for a in d.activities if _is_overnight(a)}
        missing = [d.day for d in plan.days[:-1] if d.day not in slept]
        if missing:
            logistics.append(f"Day(s) {', '.join(map(str, missing))} has no overnight stay. Add one 'Hotel' row "
                             "described as 'Overnight stay' on each of these days (the last day needs none).")

    # 5. an overnight stay belongs to the evening
    for d in plan.days:
        for a in d.activities:
            if "overnight" in a.activity_description.lower() and any(w in a.location.lower() for w in _LODGING_WORDS):
                try:
                    hour = int(a.start_time.split(":")[0])
                except ValueError:
                    continue
                if hour < 18:
                    logistics.append(f"Day {d.day}: the 'Overnight stay' row starts at {a.start_time}. Schedule it in "
                                     "the evening (20:00 or later), after the last visit.")
    return {"planner_issues": planner, "logistics_issues": logistics}
