"""
The contract between Intake and the rest of the swarm (see ARCHITECTURE.md section 4).

A Requirements object holds everything the user decides: destination, days, budget, must-visit
places. The loop, the Planner and the Auditor read these values; no limit is hard-coded.
Tests and the benchmark load a saved scenario (scenarios/<name>.json), which has this same format.
The future Intake agent will produce the same object by interviewing the user.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from swarm.core.paths import SCENARIO_DIR


class OutreachTarget(BaseModel):
    name: str
    institution: str
    topics: list[str] = Field(min_length=1)


class TeamInfo(BaseModel):
    """Who is travelling. Used in the Outreach emails; every field is optional."""
    sender_name: str = ""
    sender_university: str = ""
    trip_dates: str = Field(default="", description="free text, e.g. 'the second week of March 2027'")


class Requirements(BaseModel):
    destination: str = Field(min_length=2, description="City, country, e.g. 'Shenzhen, China'")
    days: int = Field(ge=1, le=14)
    budget_eur: float = Field(gt=0, description="Ceiling for the whole trip and the whole group")
    hotel: str = Field(default="", description="A hotel named in data/hotels/<city>.json; empty = the first one listed")
    group_size: int = Field(default=1, ge=1, le=50, description="Travellers. All prices in the plan are per person")
    comfort: Literal["budget", "standard"] = "budget"
    must_visit: list[str] = Field(min_length=1, description="Each one must be a real visit")
    interests: str = ""
    outreach: list[OutreachTarget] = Field(default_factory=list)
    team: TeamInfo = Field(default_factory=TeamInfo)
    notes: str = ""
    search_region: str = Field(default="", description="Region added to online place lookups; defaults to destination")
    optional_places: list[str] = Field(
        default_factory=list, description="Real places the Planner may add as a second visit on a day")
    place_search_names: dict[str, str] = Field(
        default_factory=dict, description="must_visit name -> better OpenStreetMap search text, e.g. {'HAX': 'HAX Shenzhen'}")

    place_coordinates: dict[str, tuple[float, float]] = Field(
        default_factory=dict,
        description="name -> [lat, lon] for places OpenStreetMap does not find, e.g. {'HAX': [22.54, 113.95]}")

    @field_validator("place_coordinates")
    @classmethod
    def _coordinates_in_range(cls, v: dict) -> dict:
        for name, (lat, lon) in v.items():
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"place_coordinates['{name}'] = [{lat}, {lon}] is not a latitude/longitude pair")
        return v

    def known_coords(self, name: str):
        """(lat, lon) the scenario gives for a place, or None. Names match without regard to case."""
        for k, v in self.place_coordinates.items():
            if k.lower() == name.strip().lower():
                return (v[0], v[1])
        return None

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

    @property
    def region(self) -> str:
        return self.search_region or self.destination

    @property
    def cost_profile(self) -> str:
        """Name of the price file data/cost/<name>.json, taken from the city: 'Silicon Valley' -> silicon_valley."""
        return re.sub(r"[^a-z0-9]+", "_", self.city.lower()).strip("_")

    @property
    def budget_per_person(self) -> float:
        return self.budget_eur / self.group_size

    @property
    def max_days_per_place(self) -> int:
        """A place may be visited on this many days. One, whenever the must-visit and optional places are at
        least as many as the days; more only if there are fewer places than days."""
        places = len(self.must_visit) + len(self.optional_places)
        return 1 if places >= self.days else max(1, math.ceil(self.days / places))

    def request_text(self) -> str:
        """The request as plain text for the prompts, the audit log and the output files."""
        if self.group_size > 1:
            money = (f"Group of {self.group_size} travellers. Strict budget ceiling is {self.budget_eur:g} EUR for the "
                     f"group, {self.budget_per_person:g} EUR per person ({self.comfort} travel). "
                     "Every price in the plan is per person.")
        else:
            money = f"Strict budget ceiling is {self.budget_eur:g} EUR ({self.comfort} travel)."
        text = (f"Organize a {self.days}-day tech expedition to {self.destination}. {money} "
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
