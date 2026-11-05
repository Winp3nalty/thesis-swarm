"""
Task state machine with an audit trail.

Pattern adapted from the Edict project (cft0808/edict, MIT license): every task has a
status, only whitelisted transitions are allowed, and every change is appended to a
history / audit log. This is our own implementation of that idea, mapped to the
swarm's agents:

    pending -> planning (tech_planner) -> scheduling (logistics)
            -> reviewing (auditor) -> completed
    reviewing -> planning | scheduling   (auditor rejects and routes the fault)
"""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"        # Agent 1: tech planner drafts hubs per day
    SCHEDULING = "scheduling"    # Agent 2: logistics adds times, transit, cost
    REVIEWING = "reviewing"      # Agent 3: auditor checks and routes feedback
    COMPLETED = "completed"
    FAILED = "failed"


VALID_TRANSITIONS: dict[Status, set[Status]] = {
    Status.PENDING: {Status.PLANNING, Status.FAILED},  # FAILED: the request itself was not plausible
    Status.PLANNING: {Status.SCHEDULING, Status.FAILED},
    Status.SCHEDULING: {Status.REVIEWING, Status.PLANNING, Status.FAILED},
    Status.REVIEWING: {Status.COMPLETED, Status.PLANNING, Status.SCHEDULING, Status.FAILED},
    Status.COMPLETED: set(),
    Status.FAILED: {Status.PENDING},  # allow a manual retry
}


class InvalidTransition(ValueError):
    """Raised when code tries to move a task to a state that is not allowed."""


class TaskRecord:
    """One expedition request moving through the swarm, with a JSONL audit log."""

    def __init__(self, task_id: str, request: str, log_dir: str | Path = "logs"):
        self.task_id = task_id
        self.request = request
        self.status = Status.PENDING
        self.history: list[dict] = []
        self.log_path = Path(log_dir) / f"audit_{task_id}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _emit(self, event: dict) -> None:
        event["ts"] = datetime.now().isoformat(timespec="seconds")
        self.history.append(event)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def transition(self, new_status: Status | str, agent: str, note: str = "") -> None:
        new_status = Status(new_status)
        allowed = VALID_TRANSITIONS[self.status]
        if new_status not in allowed:
            names = sorted(s.value for s in allowed) or ["none"]
            raise InvalidTransition(
                f"Invalid transition: {self.status.value} -> {new_status.value}. "
                f"Allowed: {', '.join(names)}"
            )
        self._emit({"type": "transition", "from": self.status.value,
                    "to": new_status.value, "agent": agent, "note": note})
        self.status = new_status

    def note(self, agent: str, message: str) -> None:
        """Log an event that does not change state (retries, schema errors, handoffs)."""
        self._emit({"type": "note", "agent": agent, "message": message})

    def transition_count(self) -> int:
        return sum(1 for e in self.history if e["type"] == "transition")

    def path_taken(self) -> list[str]:
        """Ordered list of states visited, useful for thesis figures and tests."""
        path = [Status.PENDING.value]
        for e in self.history:
            if e["type"] == "transition":
                path.append(e["to"])
        return path
