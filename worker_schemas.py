"""Output schemas for the Week 3 worker agents (Transit and Outreach)."""
from pydantic import BaseModel, Field, field_validator


# ---------- Transit agent ----------
class TransitLeg(BaseModel):
    from_stop: str
    to_stop: str
    mode: str = Field(description="e.g. Metro Line 1, Taxi, Walk, Caltrain, BART")
    minutes: int = Field(ge=1, le=300, description="Door-to-door travel time in minutes")
    cost_eur: float = Field(ge=0, description="Cost of this leg in EUR")


class TransitPlan(BaseModel):
    legs: list[TransitLeg] = Field(description="One leg per consecutive pair of stops, in order")


# ---------- Outreach agent ----------
class OutreachEmail(BaseModel):
    subject: str = Field(min_length=5)
    greeting: str
    body: str = Field(description="100-180 words, requesting a 30-minute lab tour")
    sign_off: str

    @field_validator("body")
    @classmethod
    def body_length(cls, v: str) -> str:
        n = len(v.split())
        if not 100 <= n <= 180:
            raise ValueError(f"body has {n} words, it must have 100-180")
        return v
