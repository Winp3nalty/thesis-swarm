"""Finance: owns every cost that is not a visit. Plain code, no model (Edict's finance ministry, 户部).

Logistics plans the visits and their times and prices. Finance then adds, by rule and from the city's price
table (data/cost/<city>.json):
  - one overnight row per night (day 1 to N-1): the hotel
  - one daily row for meals and local transport
The hotel and meal rows the model invented are removed first, so the model cannot get them wrong and Justice
never has to send the plan back because of them. After approval (and after Transit has run) Finance writes the
cost report: visits, meals, transport, lodging, the total against the budget, EUR and the local currency.
"""
from __future__ import annotations

from swarm.core.schemas import DetailedActivity
from swarm.skills import run_skill

LODGING_WORDS = ("hotel", "hostel", "lodging", "apartment", "accommodation")
DAILY_LOCATION = "Meals and local transport"


def _m(t: str) -> int:
    h, mi = t.split(":")
    return int(h) * 60 + int(mi)


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def is_lodging(a) -> bool:
    return any(w in a.location.lower() for w in LODGING_WORDS)


def is_daily(a) -> bool:
    return a.location == DAILY_LOCATION


def is_visit(a) -> bool:
    return not is_lodging(a) and not is_daily(a)


def _free_slot(acts, length: int = 30) -> tuple[str, str]:
    """A gap of `length` minutes in the day, as close to 12:30 as possible (the meals row)."""
    try:
        busy = sorted((_m(a.start_time), _m(a.end_time)) for a in acts)
    except (ValueError, IndexError):
        return "00:00", _hhmm(length)
    gaps, cur = [], 6 * 60
    for s, e in busy:
        if s - cur >= length:
            gaps.append((cur, s))
        cur = max(cur, e)
    if 24 * 60 - cur >= length:
        gaps.append((cur, 24 * 60))
    if not gaps:
        return "00:00", _hhmm(length)
    best = min(((min(max(gs, 750), ge - length)) for gs, ge in gaps), key=lambda s: abs(s - 750))
    return _hhmm(best), _hhmm(best + length)


def agent_finance(plan, cost_ref: dict, printer=print) -> list[str]:
    """Rewrite the plan's non-visit rows in place. Returns what changed (for the audit log)."""
    notes = []
    hotel, meals, transport = (cost_ref.get("hotel_night_eur"), cost_ref.get("meals_per_day_eur"),
                               cost_ref.get("local_transport_per_day_eur"))
    if hotel is None or meals is None or transport is None:
        printer("  [Finance] no price table for this city: the plan is left as Logistics wrote it.")
        return ["no price table: nothing added"]
    removed = 0
    for d in plan.days:
        before = len(d.activities)
        d.activities = [a for a in d.activities if is_visit(a)]
        removed += before - len(d.activities)
    nights = len(plan.days) - 1
    for i, d in enumerate(plan.days):
        if not d.activities:  # an empty day stays empty so that Justice sends it back to Logistics
            continue
        if i < nights:
            what = "Hotel check-in and overnight stay" if i == 0 else "Overnight stay"
            d.activities.append(DetailedActivity(start_time="20:00", end_time="23:59", location="Hotel",
                                                 activity_description=what, transit_mode="none", cost_eur=float(hotel)))
        start, end = _free_slot(d.activities)
        d.activities.append(DetailedActivity(
            start_time=start, end_time=end, location=DAILY_LOCATION,
            activity_description="Meals and local transport for the day (estimate from the price table)",
            transit_mode="metro/taxi", cost_eur=float(meals + transport)))
        d.activities.sort(key=lambda a: _m(a.start_time) if ":" in a.start_time else 0)
    plan.total_budget_eur = round(sum(a.cost_eur for d in plan.days for a in d.activities), 2)
    plan.duration_days = len(plan.days)
    notes.append(f"removed {removed} model-written hotel/meal row(s); added {max(nights, 0)} overnight row(s) "
                 f"and {sum(1 for d in plan.days if d.activities)} daily row(s)")
    printer(f"  [Finance] {notes[0]}; plan total {plan.total_budget_eur:.2f} EUR")
    return notes


def exchange_rate(cost_ref: dict) -> dict | None:
    """EUR -> the city's local currency through the live_exchange_rate skill (cache or static when offline)."""
    ccy = cost_ref.get("local_currency")
    if not ccy:
        return None
    res = run_skill("live_exchange_rate", amount=1, from_ccy="EUR", to_ccy=ccy)
    if not res["ok"]:
        return None
    r = res["result"]
    return {"currency": ccy, "rate": r["rate"], "source": r["source"], "date": r.get("rate_date") or ""}


def finance_report(plan, req, cost_ref: dict, transit: list | None = None) -> dict:
    """The cost table of the approved plan. `transit` is the Dispatcher's list of Transit results; where Transit
    estimated more than the daily local-transport allowance, the difference is added (never less)."""
    meals, transport = cost_ref.get("meals_per_day_eur", 0.0), cost_ref.get("local_transport_per_day_eur", 0.0)
    transit_by_day = {}
    for t in transit or []:
        if t.get("ok"):
            transit_by_day[t["case"]["day"]] = round(sum(l["cost_eur"] for l in t["result"]["legs"]), 2)
    days = []
    for d in plan.days:
        visits = round(sum(a.cost_eur for a in d.activities if is_visit(a)), 2)
        lodging = round(sum(a.cost_eur for a in d.activities if is_lodging(a)), 2)
        daily = round(sum(a.cost_eur for a in d.activities if is_daily(a)), 2)
        m = min(meals, daily) if daily else 0.0
        allowance = round(daily - m, 2)
        t_est = transit_by_day.get(d.day)
        extra = round(max(0.0, t_est - allowance), 2) if t_est is not None else 0.0
        days.append({"day": d.day, "visits": visits, "meals": round(m, 2), "transport": allowance,
                     "transit_estimate": t_est, "transit_extra": extra, "lodging": lodging,
                     "total": round(visits + lodging + daily + extra, 2)})
    total = round(sum(x["total"] for x in days), 2)
    rate = exchange_rate(cost_ref)
    return {"days": days, "total_eur": total, "budget_eur": req.budget_eur,
            "left_eur": round(req.budget_eur - total, 2), "within_budget": total <= req.budget_eur,
            "transit_checked_days": len(transit_by_day), "rate": rate,
            "total_local": round(total * rate["rate"], 0) if rate else None}


def finance_md(report: dict, title: str = "Cost report (Finance)") -> str:
    rate = report["rate"]
    ccy = rate["currency"] if rate else None
    head = ["Day", "Visits", "Meals", "Local transport", "Transit extra", "Lodging", "Total (EUR)"]
    if ccy:
        head.append(f"Total ({ccy})")
    lines = [f"## {title}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for x in report["days"]:
        row = [str(x["day"]), f"{x['visits']:.2f}", f"{x['meals']:.2f}", f"{x['transport']:.2f}",
               f"{x['transit_extra']:.2f}", f"{x['lodging']:.2f}", f"{x['total']:.2f}"]
        if ccy:
            row.append(f"{x['total'] * rate['rate']:.0f}")
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", f"- **Total:** {report['total_eur']:.2f} EUR"
              + (f" (about {report['total_local']:.0f} {ccy})" if ccy else "")
              + f", budget {report['budget_eur']:g} EUR, "
              + (f"{report['left_eur']:.2f} EUR left" if report["within_budget"] else f"OVER by {-report['left_eur']:.2f} EUR"),
              "- Meals and local transport are allowances from the city price table (data/cost/), not measured prices.",
              f"- Transit estimates were compared with the transport allowance on {report['transit_checked_days']} day(s); "
              "a higher estimate is added in 'Transit extra'."]
    if rate:
        lines.append(f"- Exchange rate: 1 EUR = {rate['rate']} {ccy} ({rate['source']}{', ' + rate['date'] if rate['date'] else ''}).")
    return "\n".join(lines) + "\n"
