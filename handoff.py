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
    "user": {"tech_planner"},
    "tech_planner": {"logistics"},
    "logistics": {"auditor"},
    # The auditor can send work back to either agent, or release it to the user.
    "auditor": {"tech_planner", "logistics", "user", "coordinator"},
    # After approval the Coordinator (plain code) asks the two worker agents for routes and emails.
    "coordinator": {"transit", "outreach", "user"},
    "transit": {"coordinator"},
    "outreach": {"coordinator"},
}


class PermissionDenied(PermissionError):
    """Raised when an agent tries to hand work to an agent it is not allowed to."""


def handoff(record: TaskRecord, src: str, dst: str, summary: str = "", printer=print) -> None:
    if dst not in PERMISSIONS.get(src, set()):
        record.note(src, f"DENIED handoff to {dst}")
        raise PermissionDenied(f"{src} may not hand work to {dst}")
    record.note(src, f"handoff -> {dst}: {summary}")
    printer(f"  [HANDOFF] {src} -> {dst} | {summary}")
