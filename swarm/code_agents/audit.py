"""Audit: the gatekeeper for the scenario. It judges the request, never the plan.

Is the request realistic? Budget against the cheapest realistic trip, number of must-visit places against
the days, whether the named places exist online. All of it is code (plausibility.py), so it runs before
any model call and costs nothing. A pass hands the scenario to the Dispatcher; a fail goes back to you with
the reason. (Edict's review department, 门下省; ARCHITECTURE.md section 3.)
The checks of the finished plan belong to Justice (justice.py).
"""
from __future__ import annotations

from swarm.core.handoff import handoff
from swarm.code_agents.plausibility import plausibility_check
from swarm.core.state_machine import TaskRecord


def agent_audit(req, record: TaskRecord, printer=print) -> dict:
    """Review the scenario. Returns the plausibility result: {"ok", "hard", "warnings", "log", "floors"}."""
    handoff(record, "user", "audit", "scenario received", printer=printer)
    result = plausibility_check(req)
    for item in result["hard"]:
        printer(f"  [Audit] problem: {item}")
    for item in result["warnings"]:
        printer(f"  [Audit] warning: {item}")
    record.note("audit", f"plausibility: {'ok' if result['ok'] else 'REJECTED'} | "
                + " | ".join(result["hard"] + result["warnings"]))
    if result["ok"]:
        handoff(record, "audit", "dispatcher", f"scenario plausible, {len(result['warnings'])} warning(s)",
                printer=printer)
    else:
        handoff(record, "audit", "user", f"scenario rejected: {len(result['hard'])} problem(s)", printer=printer)
    return result
