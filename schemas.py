from typing import Literal
from pydantic import BaseModel, Field


class TechHubVisit(BaseModel):
    hub_name: str
    purpose: str


class DayOutline(BaseModel):
    day: int
    focus_topic: str
    target_hubs: list[TechHubVisit]


class RoughItinerary(BaseModel):
    destination: str
    days: list[DayOutline]


class DetailedActivity(BaseModel):
    start_time: str = Field(description="e.g. 09:30")
    end_time: str = Field(description="e.g. 12:00")
    location: str
    activity_description: str
    transit_mode: str = Field(description="e.g. Metro Line 1, Taxi, Walk")
    cost_eur: float


class DetailedDay(BaseModel):
    day: int
    focus_topic: str
    activities: list[DetailedActivity]


class ExpeditionPlan(BaseModel):
    destination: str
    duration_days: int
    total_budget_eur: float
    days: list[DetailedDay]


class AuditResult(BaseModel):
    passed: bool
    failed_agent: Literal["tech_planner", "logistics", "none"] = Field(
        default="none",
        description="Set to 'tech_planner' if hubs are missing, 'logistics' if budget/time fails, or 'none' if passed."
    )
    critique_notes: list[str]