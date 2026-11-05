"""Order and clustering checks for the places of one day (used by the Dispatcher for the Transit agent).

Pure functions on a matrix of measured driving minutes (OSRM through the travel_minutes skill):
  - best_order: the visiting order with the least total driving (brute force, a day has at most 6 stops;
    the first stop stays first, because the morning start is fixed by the schedule)
  - route_report: the current order against the best one, and the legs that are too long to be one cluster
Report only: it never changes the approved plan's times. It tells the team whether a day is worth re-ordering.
"""
from __future__ import annotations

from itertools import permutations

FAR_MINUTES = 45  # two consecutive stops further apart than this are not one cluster


def _total(order, matrix):
    total = 0.0
    for a, b in zip(order, order[1:]):
        d = matrix[a][b]
        if d is None:
            return None
        total += d
    return total


def best_order(matrix) -> tuple[list[int], float] | None:
    """(order as stop indexes, total minutes) with the first stop fixed; None if a needed time is unknown."""
    n = len(matrix)
    if n < 3:
        return None
    best = None
    for rest in permutations(range(1, n)):
        order = [0, *rest]
        t = _total(order, matrix)
        if t is None:
            return None
        if best is None or t < best[1] - 1e-9:
            best = (order, t)
    return best


def route_report(stops: list[str], matrix) -> dict:
    """{"current_minutes", "best_minutes", "best_order" (names), "saves_minutes", "far_legs": [...], "known"}"""
    n = len(stops)
    current = _total(list(range(n)), matrix) if n >= 2 else None
    far = [f"{stops[i]} to {stops[i + 1]} takes about {matrix[i][i + 1]:.0f} min"
           for i in range(n - 1) if matrix[i][i + 1] is not None and matrix[i][i + 1] > FAR_MINUTES]
    best = best_order(matrix)
    report = {"known": current is not None, "current_minutes": round(current, 1) if current is not None else None,
              "best_minutes": None, "best_order": None, "saves_minutes": 0.0, "far_legs": far}
    if best and current is not None:
        order, total = best
        report.update(best_minutes=round(total, 1), best_order=[stops[i] for i in order],
                      saves_minutes=round(max(0.0, current - total), 1))
    return report


def route_note(report: dict) -> str:
    """One line for the task board and the Transit file."""
    if not report["known"]:
        return "driving times unknown (web unreachable or a place not found): order not checked"
    parts = [f"driving {report['current_minutes']:.0f} min in the planned order"]
    if report["saves_minutes"] >= 10:
        parts.append(f"{' > '.join(report['best_order'])} would save {report['saves_minutes']:.0f} min")
    else:
        parts.append("no better order saves 10 min or more")
    if report["far_legs"]:
        parts.append("not one cluster: " + "; ".join(report["far_legs"]))
    return "; ".join(parts)
