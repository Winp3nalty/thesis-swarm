"""Built-in skills. All pure functions over local data, no network access."""
from __future__ import annotations

import json
from pathlib import Path

from . import skill

_DATA = Path(__file__).resolve().parent.parent / "data"

# PLACEHOLDER rates (units of currency per 1 EUR). Put real, dated values in
# data/fx_rates.json, for example {"EUR": 1.0, "CNY": <rate>, "USD": <rate>}.
_DEFAULT_RATES = {"EUR": 1.0, "CNY": 8.0, "USD": 1.1}


def _rates() -> dict:
    path = _DATA / "fx_rates.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return _DEFAULT_RATES


def _minutes(hhmm: str) -> int:
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


@skill("currency_convert",
       "Convert an amount between currencies using the static rate table.",
       "amount: float, from_ccy: str, to_ccy: str")
def currency_convert(amount: float, from_ccy: str, to_ccy: str) -> dict:
    rates = _rates()
    f, t = from_ccy.upper(), to_ccy.upper()
    if f not in rates or t not in rates:
        raise ValueError(f"unsupported currency (known: {', '.join(rates)})")
    value = float(amount) / rates[f] * rates[t]
    return {"amount": round(value, 2), "currency": t}


@skill("budget_check",
       "Sum a list of EUR costs and compare with a budget cap.",
       "costs: list[float], cap: float")
def budget_check(costs: list[float], cap: float) -> dict:
    total = round(sum(float(c) for c in costs), 2)
    return {"total": total, "cap": float(cap),
            "within_budget": total <= float(cap),
            "remaining": round(float(cap) - total, 2)}


@skill("schedule_check",
       "Find problems in one day's activities: end not after start, or overlapping slots.",
       "activities: list[{start_time: 'HH:MM', end_time: 'HH:MM'}]")
def schedule_check(activities: list[dict]) -> dict:
    issues: list[str] = []
    slots = []
    for i, a in enumerate(activities):
        s, e = _minutes(a["start_time"]), _minutes(a["end_time"])
        if e <= s:
            issues.append(f"activity {i + 1}: end time is not after start time")
        slots.append((s, e, i + 1))
    slots.sort()
    for (s1, e1, i1), (s2, e2, i2) in zip(slots, slots[1:]):
        if s2 < e1:
            issues.append(f"activities {i1} and {i2} overlap")
    return {"ok": not issues, "issues": issues}
