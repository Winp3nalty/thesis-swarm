"""
Mandatory meal times, from data/culture/<profile>.json. Plain code, no model.

Breakfast, lunch and dinner are windows in which no visit takes place. Code trims a visit that runs into a window
(the model ignored such rules when only asked), and the check reports what is left. The Finance row for meals
is placed in the lunch window.
"""
from __future__ import annotations

import json

from swarm.code_agents.finance import is_visit
from swarm.core.paths import DATA_DIR

MIN_VISIT = 30  # a trimmed visit shorter than this is not repaired (the check reports it instead)


def _m(t: str) -> int:
    h, mi = t.split(":")
    return int(h) * 60 + int(mi)


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def load_meals(profile: str) -> dict:
    """{"breakfast": (start, end) in minutes, ...} sorted by time; {} when the city has no file (no meal rule)."""
    try:
        raw = json.loads((DATA_DIR / "culture" / f"{profile}.json").read_text(encoding="utf-8"))["meals"]
        meals = {name: (_m(w[0]), _m(w[1])) for name, w in raw.items()}
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return {}
    return dict(sorted(meals.items(), key=lambda kv: kv[1]))


def meal_prompt_text(meals: dict) -> str:
    if not meals:
        return ""
    return ("\nMandatory meal times (no visit may take place in these windows; end a visit before the window or "
            "start it after the window):\n"
            + "\n".join(f"- {n}: {_hhmm(s)}-{_hhmm(e)}" for n, (s, e) in meals.items()) + "\n")


def fix_meal_times(plan, meals: dict) -> list[str]:
    """Trim every visit that runs into a meal window, keeping the longer side of the visit. A visit that would
    be shorter than MIN_VISIT after trimming is left alone. Returns a note for each change."""
    notes = []
    for d in plan.days:
        for a in d.activities:
            if not is_visit(a):
                continue
            for name, (ws, we) in meals.items():
                try:
                    s, e = _m(a.start_time), _m(a.end_time)
                except (ValueError, IndexError):
                    break
                if not (s < we and e > ws):
                    continue
                before, after = ws - s, e - we
                if before >= after and before >= MIN_VISIT:
                    notes.append(f"day {d.day}: '{a.location}' now ends at {_hhmm(ws)} ({name})")
                    a.end_time = _hhmm(ws)
                elif after > before and after >= MIN_VISIT:
                    notes.append(f"day {d.day}: '{a.location}' now starts at {_hhmm(we)} ({name})")
                    a.start_time = _hhmm(we)
    return notes


def meal_problems(plan, meals: dict) -> list[str]:
    """Visits that still overlap a meal window. The wording contains 'overlaps a meal time' (a soft marker)."""
    out = []
    for d in plan.days:
        for a in d.activities:
            if not is_visit(a):
                continue
            try:
                s, e = _m(a.start_time), _m(a.end_time)
            except (ValueError, IndexError):
                continue
            for name, (ws, we) in meals.items():
                if s < we and e > ws:
                    out.append(f"Day {d.day}: '{a.location}' ({a.start_time}-{a.end_time}) overlaps a meal time "
                               f"({name} {_hhmm(ws)}-{_hhmm(we)}). End the visit by {_hhmm(ws)} or start it at "
                               f"{_hhmm(we)} or later.")
    return out
