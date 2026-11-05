"""
Permission matrix for agent-to-agent handovers.

Pattern adapted from Edict (cft0808/edict, MIT): an explicit table of which agent may
hand work to which, so wrong routing is caught in code instead of by luck.
Each allowed handover prints one clean line, which satisfies the Week 2 deliverable
("structured task handover log printed cleanly in CLI").
"""
from __future__ import annotations

from state_machine import TaskRecord

PERMISSIONS: dict[str, set[str]] = {
    # You give the scenario to Audit, which releases it to the Dispatcher or sends it back to you.
    "user": {"audit"},
    "audit": {"dispatcher", "user"},
    # Hub and spoke: the Dispatcher hands work to every agent and takes the result back.
    "dispatcher": {"tech_planner", "logistics", "justice", "transit", "outreach", "user"},
    "tech_planner": {"dispatcher"},
    "logistics": {"dispatcher"},
    "justice": {"dispatcher"},
    "transit": {"dispatcher"},
    "outreach": {"dispatcher"},
}


class PermissionDenied(PermissionError):
    """Raised when an agent tries to hand work to an agent it is not allowed to."""


def handoff(record: TaskRecord, src: str, dst: str, summary: str = "", printer=print) -> None:
    if dst not in PERMISSIONS.get(src, set()):
        record.note(src, f"DENIED handoff to {dst}")
        raise PermissionDenied(f"{src} may not hand work to {dst}")
    record.note(src, f"handoff -> {dst}: {summary}")
    printer(f"  [HANDOFF] {src} -> {dst} | {summary}")
