"""Dispatcher: the hub of the swarm. It shares the work between the agents and collects what they return.

Plain code, not a model. Every agent talks only to the Dispatcher (handoff.py checks and logs each handover):

    Audit approves the scenario  ->  Dispatcher keeps a task board and gives each task to its agent
    Tech Planner -> Dispatcher -> Logistics -> Dispatcher -> Justice -> Dispatcher
    Justice rejects              ->  the Dispatcher decides who must fix it (route_rejection)
    Justice approves             ->  the Dispatcher asks Transit for the routes of each day and Outreach for
                                     the emails, then hands everything to you.
A worker that fails never fails the expedition plan: the failure is recorded and shown.
(Edict's dispatching department, 尚书省; ARCHITECTURE.md section 3.)
"""
from __future__ import annotations

from handoff import handoff
from skills import run_skill
from state_machine import TaskRecord

MAX_STOPS_PER_DAY = 6


def day_stops(day) -> list[str]:
    """Real places of one day in visiting order; a place repeated back to back is listed once. A hotel or other
    generic stop is left out: its position is unknown, so any route to it would be an invented number."""
    from reality import _is_generic
    stops: list[str] = []
    for act in day.activities:
        loc = act.location.strip()
        if loc and not _is_generic(loc) and (not stops or stops[-1].lower() != loc.lower()):
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


class TaskBoard:
    """The Dispatcher's task list: who does what, after which task, how many rounds it took, how it ended."""

    def __init__(self):
        self.tasks: list[dict] = []

    def add(self, agent: str, label: str, after=(), key: str = "") -> str:
        tid = f"t{len(self.tasks) + 1:02d}"
        self.tasks.append({"id": tid, "key": key, "agent": agent, "task": label, "after": list(after),
                           "status": "pending", "rounds": 0, "note": ""})
        return tid

    def _get(self, tid: str) -> dict:
        return next(t for t in self.tasks if t["id"] == tid)

    def start(self, tid: str) -> None:
        t = self._get(tid)
        t["status"], t["rounds"] = "running", t["rounds"] + 1

    def finish(self, tid: str, ok: bool = True, note: str = "") -> None:
        t = self._get(tid)
        t["status"], t["note"] = ("done" if ok else "rejected"), note

    def fail_running(self, note: str) -> None:
        for t in self.tasks:
            if t["status"] == "running":
                t["status"], t["note"] = "error", note

    def key_id(self, key: str) -> str | None:
        return next((t["id"] for t in self.tasks if t["key"] == key), None)

    def as_list(self) -> list[dict]:
        return [dict(t) for t in self.tasks]


class Dispatcher:
    def __init__(self, record: TaskRecord, req, workers: bool = False, printer=print):
        self.record, self.req, self.workers, self.printer = record, req, workers, printer
        self.board = TaskBoard()
        self.t_plan = self.t_schedule = self.t_verify = ""

    # --- the board -------------------------------------------------------------------------------
    def plan_tasks(self) -> None:
        """Make the task list for this scenario. Routes depend on the approved plan, so they are added later."""
        b = self.board
        self.t_plan = b.add("tech_planner", "choose the places for each day")
        self.t_schedule = b.add("logistics", "schedule times and costs", after=[self.t_plan])
        self.t_verify = b.add("justice", "verify the plan against real data", after=[self.t_schedule])
        if self.workers:
            for i, target in enumerate(self.req.outreach, 1):
                b.add("outreach", f"email to {target.name}", after=[self.t_verify], key=f"o{i:02d}")
        self.record.note("dispatcher", f"task board: {len(b.tasks)} task(s) for "
                         + ", ".join(sorted({t['agent'] for t in b.tasks})))

    # --- handovers -------------------------------------------------------------------------------
    def give(self, agent: str, summary: str) -> None:
        handoff(self.record, "dispatcher", agent, summary, printer=self.printer)

    def take(self, agent: str, summary: str) -> None:
        handoff(self.record, agent, "dispatcher", summary, printer=self.printer)

    def finish(self, summary: str) -> None:
        self.give("user", summary)

    # --- routing a rejection ---------------------------------------------------------------------
    def route_rejection(self, audit, logistics_retries: int, soft_only: bool):
        """Decide who gets the work back. Justice names the agent to blame; a Logistics agent that failed twice
        on a hard problem is saturated, so the Planner gets a chance to change the hubs.
        Returns (agent, feedback notes, logistics retries so far)."""
        target = audit.failed_agent
        feedback = list(audit.critique_notes)
        if target == "logistics":
            logistics_retries += 1
            if logistics_retries >= 2 and not soft_only:
                self.printer("  [ESCALATION] Logistics saturated! Escalating the fault back to the Tech Planner...")
                target = "tech_planner"
                self.record.note("dispatcher", "escalation: logistics saturated, back to tech_planner")
                feedback.append("ESCALATION: Logistics could not fit your hubs within budget/time constraints. "
                                "Simplify or redistribute target_hubs across days.")
        self.record.note("dispatcher", f"routes the rework to {target}")
        return target, feedback, logistics_retries

    # --- the workers after approval --------------------------------------------------------------
    def _run(self, kind: str, case: dict, call, tid: str) -> dict:
        self.give(kind, case["id"])
        self.board.start(tid)
        try:
            result, info = call()
            entry = {"case": case, "ok": True, "info": info, "result": result.model_dump(), "error": ""}
        except Exception as e:  # a failing worker must not take the approved plan down
            info = getattr(e, "info", {"attempts": 0, "attempt_log": []})
            entry = {"case": case, "ok": False, "info": info, "result": None,
                     "error": f"{type(e).__name__}: {str(e)[:300]}"}
        self.board.finish(tid, entry["ok"], "" if entry["ok"] else entry["error"][:120])
        self.take(kind, f"{case['id']} {'ok' if entry['ok'] else 'FAILED'}")
        return entry

    def run_workers(self, plan) -> dict:
        """plan is an approved ExpeditionPlan. Returns {"transit": [...], "outreach": [...], "summary": {...}}."""
        import workers  # imported here because workers.py imports run_loop

        req = self.req
        transit, outreach = [], []
        for day in plan.days:
            stops = day_stops(day)
            if len(stops) < 2:
                continue
            case = {"id": f"day{day.day}", "day": day.day, "city": req.city, "stops": stops}
            tid = self.board.add("transit", f"route for day {day.day}", after=[self.t_verify], key=case["id"])
            transit.append(self._run("transit", case,
                                     lambda c=case: workers.agent_transit(c["city"], c["stops"], day=c["day"],
                                                                          drive_minutes=drive_times(c["stops"], req)),
                                     tid))
        for i, target in enumerate(req.outreach, 1):
            case = {"id": f"o{i:02d}", "city": req.city, **req.team.model_dump(), **target.model_dump()}
            outreach.append(self._run("outreach", case, lambda c=case: workers.agent_outreach(c),
                                      self.board.key_id(case["id"])))
        self.finish("routes and emails ready")

        legs = [leg for t in transit if t["ok"] for leg in t["result"]["legs"]]
        summary = {
            "transit_days": len(transit), "transit_ok": sum(t["ok"] for t in transit),
            "transit_minutes": sum(l["minutes"] for l in legs), "transit_cost_eur": round(sum(l["cost_eur"] for l in legs), 2),
            "outreach_emails": len(outreach), "outreach_ok": sum(o["ok"] for o in outreach),
        }
        self.printer(f"  [DISPATCHER] transit {summary['transit_ok']}/{summary['transit_days']} days, "
                     f"outreach {summary['outreach_ok']}/{summary['outreach_emails']} emails")
        return {"transit": transit, "outreach": outreach, "summary": summary}
