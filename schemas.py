from pydantic import BaseModel, Field
from typing import List

class ActivityItem(BaseModel):
    time: str = Field(description="e.g. Morning, Afternoon, Evening")
    description: str
    cost_eur: float

class DayPlan(BaseModel):
    day: int
    focus: str
    activities: List[ActivityItem]

class ExpeditionPlan(BaseModel):
    destination: str
    duration_days: int
    total_budget_eur: float
    days: List[DayPlan]

class AuditResult(BaseModel):
    passed: bool
    critique_notes: List[str] = Field(default_factory=list)