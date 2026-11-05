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

from swarm.core.handoff import handoff
from swarm.code_agents.transit_modes import FIRST_VISIT_LABEL, load_transit_modes, write_modes_into_plan
from swarm.llm_agents.outreach import agent_outreach
from swarm.llm_agents.transit import agent_transit
from swarm.skills import run_skill
from swarm.code_agents.route_order import route_note, route_report
from swarm.core.state_machine import TaskRecord

MAX_STOPS_PER_DAY = 6


def stop_activities(day) -> list:
    """The activity of each real place of one day, in visiting order (a place repeated back to back counts once)."""
    from swarm.code_agents.reality import _is_generic
    acts: list = []
    for act in day.activities:
        loc = act.location.strip()
        if loc and not _is_generic(loc) and (not acts or acts[-1].location.strip().lower() != loc.lower()):
            acts.append(act)
    return acts[:MAX_STOPS_PER_DAY]


def day_stops(day) -> list[str]:
    """Real places of one day in visiting order; a place repeated back to back is listed once. A hotel or other
    generic stop is left out: its position is unknown, so any route to it would be an invented number."""
    return [act.location.strip() for act in stop_activities(day)]


def _coords(stops: list[str], req) -> list:
    """(lat, lon) of each stop, or None for a hotel / unknown place / unreachable web."""
    from swarm.code_agents.reality import _is_generic
    aliases = {k.lower(): v for k, v in req.place_search_names.items()}
    coords = []
    for s in stops:
        if _is_generic(s):
            coords.append(None)
            continue
        if req.known_coords(s):
            coords.append(req.known_coords(s))
            continue
        res = run_skill("verify_place", place=aliases.get(s.lower(), s), city=req.region)
        r = res.get("result", {}) if res["ok"] else {}
        coords.append((r["lat"], r["lon"]) if r.get("found") else None)
    return coords


def _minutes(a, b):
    if not a or not b:
        return None
    res = run_skill("travel_minutes", lat1=a[0], lon1=a[1], lat2=b[0], lon2=b[1])
    return res["result"].get("minutes") if res["ok"] else None


def drive_times(stops: list[str], req) -> list:
    """Driving minutes (OSRM) between consecutive stops; None where a stop has no coordinates (a hotel) or the
    web is unreachable. The Transit agent gets them as facts and its answers are checked against them."""
    coords = _coords(stops, req)
    return [_minutes(a, b) for a, b in zip(coords, coords[1:])]


def drive_matrix(stops: list[str], req) -> list[list]:
    """Driving minutes between every pair of stops (symmetric: each pair is asked once)."""
    coords = _coords(stops, req)
    n = len(stops)
    m = [[0.0 if i == j else None for j in range(n)] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            m[i][j] = m[j][i] = _minutes(coords[i], coords[j])
    return m


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
        self.final: dict = {}
        self.t_plan = self.t_schedule = self.t_finance = self.t_verify = ""

    # --- the board -------------------------------------------------------------------------------
    def plan_tasks(self) -> None:
        """Make the task list for this scenario. Routes depend on the approved plan, so they are added later."""
        b = self.board
        self.t_plan = b.add("tech_planner", "choose the places for each day")
        self.t_schedule = b.add("logistics", "schedule times and costs", after=[self.t_plan])
        self.t_finance = b.add("finance", "add lodging, meals and local transport", after=[self.t_schedule])
        self.t_verify = b.add("justice", "verify the plan against real data", after=[self.t_finance])
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
        note = "" if entry["ok"] else entry["error"][:120]
        if kind == "transit" and entry["ok"] and case.get("order"):
            note = route_note(case["order"])[:160]
        self.board.finish(tid, entry["ok"], note)
        self.take(kind, f"{case['id']} {'ok' if entry['ok'] else 'FAILED'}")
        return entry

    def run_workers(self, plan) -> dict:
        """plan is an approved ExpeditionPlan. Returns {"transit": [...], "outreach": [...], "summary": {...}}."""
        req = self.req
        mode_rules = load_transit_modes(req.cost_profile)
        transit, outreach = [], []
        for day in plan.days:
            stops = day_stops(day)
            if len(stops) < 2:
                continue
            case = {"id": f"day{day.day}", "day": day.day, "city": req.city, "stops": stops}
            try:
                case["order"] = route_report(stops, drive_matrix(stops, req))
            except Exception as e:  # the order check is advice; it must never stop the route
                case["order"] = None
                self.record.note("dispatcher", f"order check failed for day {day.day}: {type(e).__name__}: {e}")
            tid = self.board.add("transit", f"route for day {day.day}", after=[self.t_verify], key=case["id"])
            transit.append(self._run("transit", case,
                                     lambda c=case: agent_transit(c["city"], c["stops"], day=c["day"],
                                                                  drive_minutes=drive_times(c["stops"], req),
                                                                  mode_rules=mode_rules),
                                     tid))
        for i, target in enumerate(req.outreach, 1):
            case = {"id": f"o{i:02d}", "city": req.city, **req.team.model_dump(), **target.model_dump()}
            outreach.append(self._run("outreach", case, lambda c=case: agent_outreach(c),
                                      self.board.key_id(case["id"])))

        set_modes = 0  # Transit owns the transport mode: write it into the plan, so plan and routes agree
        for t in transit:
            if t["ok"]:
                day = next(d for d in plan.days if d.day == t["case"]["day"])
                set_modes += write_modes_into_plan(plan, t["case"]["day"], stop_activities(day), t["result"]["legs"],
                                                   mode_rules)
        for day in plan.days:  # a day with one visit gets no Transit run, but its first visit is reached from the hotel too
            acts = stop_activities(day)
            if acts:
                acts[0].transit_mode = FIRST_VISIT_LABEL
        if set_modes:
            self.record.note("dispatcher", f"transit modes written into the plan for {set_modes} visit(s)")
        legs = [leg for t in transit if t["ok"] for leg in t["result"]["legs"]]
        summary = {
            "transit_days": len(transit), "transit_ok": sum(t["ok"] for t in transit),
            "transit_minutes": sum(l["minutes"] for l in legs), "transit_cost_eur": round(sum(l["cost_eur"] for l in legs), 2),
            "outreach_emails": len(outreach), "outreach_ok": sum(o["ok"] for o in outreach),
            "transit_modes_set": set_modes,
        }
        self.printer(f"  [DISPATCHER] transit {summary['transit_ok']}/{summary['transit_days']} days, "
                     f"outreach {summary['outreach_ok']}/{summary['outreach_emails']} emails")
        return {"transit": transit, "outreach": outreach, "summary": summary}

    # --- the closing agents: Local Logistics, Finance report, Static Web -------------------------------
    def _step(self, kind: str, label: str, summary: str, call):
        """One closing agent: handover, task on the board, result back. A failure is recorded, never fatal."""
        tid = self.board.add(kind, label, after=[self.t_verify])
        self.give(kind, summary)
        self.board.start(tid)
        try:
            result = call()
            self.board.finish(tid, True)
        except Exception as e:
            result = None
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            self.board.finish(tid, False, msg[:120])
            self.record.note(kind, f"failed: {msg}")
            self.printer(f"  [{kind.upper()}] failed: {msg}")
        self.take(kind, "done" if result is not None else "FAILED")
        return result

    def finalize(self, plan, coordination, warnings, status: str) -> dict:
        """After approval (and after the workers): practical notes, cost report, handbook.
        plan is the approved ExpeditionPlan. The result is kept in self.final for the output writer."""
        from swarm.code_agents.finance import finance_report
        from swarm.code_agents.local_logistics import agent_local_logistics
        from swarm.code_agents.reality import load_cost_reference
        from swarm.code_agents.static_web import agent_static_web
        from swarm.core.version_info import version_line

        req = self.req
        cost_ref = load_cost_reference(req.cost_profile)
        local = self._step("local_logistics", "visa, SIM, payments and transport with official sources",
                           "practical notes", lambda: agent_local_logistics(req.cost_profile, self.printer))
        fin = self._step("finance", "cost report with Transit and exchange rate", "cost report",
                         lambda: finance_report(plan, req, cost_ref, (coordination or {}).get("transit")))
        site = self._step("static_web", "build handbook.md and handbook.json", "build the handbook",
                          lambda: agent_static_web(plan.model_dump(), req, status, fin, local, coordination,
                                                   list(warnings), version_line(), self.printer))
        self.final = {"practical": local, "finance": fin, "site": site}
        return self.final
