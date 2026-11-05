"""
The contract between Intake and the rest of the swarm (see ARCHITECTURE.md section 4).

A Requirements object holds everything the user decides: destination, days, budget, must-visit
places. The loop, the Planner and the Auditor read these values; no limit is hard-coded.
Tests and the benchmark load a saved scenario (scenarios/<name>.json), which has this same format.
The future Intake agent will produce the same object by interviewing the user.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"


class OutreachTarget(BaseModel):
    name: str
    institution: str
    topics: list[str] = Field(min_length=1)


class Requirements(BaseModel):
    destination: str = Field(min_length=2, description="City, country, e.g. 'Shenzhen, China'")
    days: int = Field(ge=1, le=14)
    budget_eur: float = Field(gt=0, description="Ceiling for the whole trip")
    comfort: Literal["budget", "standard"] = "budget"
    must_visit: list[str] = Field(min_length=1, description="Each one must be a real visit")
    interests: str = ""
    outreach: list[OutreachTarget] = Field(default_factory=list)
    notes: str = ""

    @field_validator("must_visit")
    @classmethod
    def _no_blank_or_duplicate(cls, v: list[str]) -> list[str]:
        names = [x.strip() for x in v]
        if any(not n for n in names):
            raise ValueError("must_visit contains an empty name")
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("must_visit contains a duplicate")
        return names

    @property
    def city(self) -> str:
        return self.destination.split(",")[0].strip()

    def request_text(self) -> str:
        """The request as plain text for the prompts, the audit log and the output files."""
        text = (f"Organize a {self.days}-day tech expedition to {self.destination}. "
                f"Strict budget ceiling is {self.budget_eur:g} EUR ({self.comfort} travel). "
                f"Must visit: {', '.join(self.must_visit)}.")
        if self.interests:
            text += f" Interests: {self.interests}."
        if self.notes:
            text += f" Notes: {self.notes}"
        return text


def load_scenario(name_or_path: str | Path) -> Requirements:
    """Load scenarios/<name>.json (or any path) and validate it. Raises ValueError with a clear message."""
    p = Path(name_or_path)
    if not p.suffix:
        p = SCENARIO_DIR / f"{name_or_path}.json"
    try:
        return Requirements.model_validate(json.loads(p.read_text(encoding="utf-8")))
    except OSError as e:
        raise ValueError(f"cannot read scenario {p}: {e}") from e
    except ValueError as e:  # bad JSON or failed validation (pydantic's error is a ValueError)
        raise ValueError(f"invalid scenario {p}: {e}") from e
