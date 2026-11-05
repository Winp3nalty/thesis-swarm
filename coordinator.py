"""Coordinator: runs the Transit and Outreach workers after the Auditor has approved a plan.

It is plain code, not a model. For every day of the approved plan it asks the Transit agent for the
legs between that day's stops; for every outreach target of the scenario it asks the Outreach agent
for an email. All handovers go through handoff.py, so they are permission-checked and logged.
A worker that fails never fails the expedition plan: the failure is recorded and shown.
"""
from __future__ import annotations

from handoff import handoff
from skills import run_skill
from state_machine import TaskRecord

MAX_STOPS_PER_DAY = 6


def day_stops(day) -> list[str]:
    """Locations of one day in visiting order; a place repeated back to back is listed once."""
    stops: list[str] = []
    for act in day.activities:
        loc = act.location.strip()
        if loc and (not stops or stops[-1].lower() != loc.lower()):
            stops.append(loc)
    return stops[:MAX_STOPS_PER_DAY]


def drive_times(stops: list[str], req) -> list:
    """Driving minutes (OSRM) between consecutive stops; None where a stop has no coordinates (a hotel) or the
    web is unreachable. The Transit agent gets them as facts and its answers are checked against them."""
    from reality import _is_generic
    aliases = {k.lower(): v for k, v in req.place_search_names.items()}
    coords = []
    for s in stops:
        if _is_generic(s):
            coords.append(None)
            continue
        res = run_skill("verify_place", place=aliases.get(s.lower(), s), city=req.region)
        r = res.get("result", {}) if res["ok"] else {}
        coords.append((r["lat"], r["lon"]) if r.get("found") else None)
    out = []
    for a, b in zip(coords, coords[1:]):
        if not a or not b:
            out.append(None)
            continue
        res = run_skill("travel_minutes", lat1=a[0], lon1=a[1], lat2=b[0], lon2=b[1])
        out.append(res["result"].get("minutes") if res["ok"] else None)
    return out


def _run(kind: str, case: dict, call, record: TaskRecord, printer) -> dict:
    handoff(record, "coordinator", kind, case["id"], printer=printer)
    try:
        result, info = call()
        entry = {"case": case, "ok": True, "info": info, "result": result.model_dump(), "error": ""}
    except Exception as e:  # WorkerFailed, Ollama errors... a worker problem must not lose the plan
        info = getattr(e, "info", {"attempts": 0, "attempt_log": []})
        entry = {"case": case, "ok": False, "info": info, "result": None,
                 "error": f"{type(e).__name__}: {str(e)[:300]}"}
    handoff(record, kind, "coordinator", f"{case['id']} {'ok' if entry['ok'] else 'FAILED'}", printer=printer)
    return entry


def run_coordinator(req, plan, record: TaskRecord, printer=print) -> dict:
    """plan is an ExpeditionPlan. Returns {"transit": [...], "outreach": [...], "summary": {...}}."""
    import workers  # imported here because workers.py imports run_loop

    handoff(record, "auditor", "coordinator", "plan approved: request routes and outreach emails", printer=printer)
    transit, outreach = [], []
    for day in plan.days:
        stops = day_stops(day)
        if len(stops) < 2:
            continue
        case = {"id": f"day{day.day}", "day": day.day, "city": req.city, "stops": stops}
        transit.append(_run("transit", case,
                            lambda c=case: workers.agent_transit(c["city"], c["stops"], day=c["day"],
                                                                 drive_minutes=drive_times(c["stops"], req)),
                            record, printer))
    for i, target in enumerate(req.outreach, 1):
        case = {"id": f"o{i:02d}", "city": req.city, **req.team.model_dump(), **target.model_dump()}
        outreach.append(_run("outreach", case, lambda c=case: workers.agent_outreach(c), record, printer))
    handoff(record, "coordinator", "user", "routes and emails ready", printer=printer)

    legs = [leg for t in transit if t["ok"] for leg in t["result"]["legs"]]
    summary = {
        "transit_days": len(transit), "transit_ok": sum(t["ok"] for t in transit),
        "transit_minutes": sum(l["minutes"] for l in legs), "transit_cost_eur": round(sum(l["cost_eur"] for l in legs), 2),
        "outreach_emails": len(outreach), "outreach_ok": sum(o["ok"] for o in outreach),
    }
    printer(f"  [COORDINATOR] transit {summary['transit_ok']}/{summary['transit_days']} days, "
            f"outreach {summary['outreach_ok']}/{summary['outreach_emails']} emails")
    return {"transit": transit, "outreach": outreach, "summary": summary}
