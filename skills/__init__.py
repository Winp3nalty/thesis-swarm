"""
Skill registry: small Python functions with a name, used by the orchestrator.

Small local models are unreliable at calling tools themselves, so the orchestrator (the
Auditor, the Logistics step and reality.py) calls skills directly with run_skill() and gives the
results to the agents as facts. Most skills are offline and deterministic (skills/builtin.py).
Skills in skills/web.py use the internet, cache their answers in data/ and fall back to cached
or static data.
"""
from __future__ import annotations

from typing import Callable

SKILLS: dict[str, dict] = {}


def skill(name: str, description: str, params: str):
    """Decorator that registers a function as a skill."""
    def decorator(fn: Callable):
        SKILLS[name] = {"fn": fn, "description": description, "params": params}
        return fn
    return decorator


def run_skill(name: str, **kwargs) -> dict:
    if name not in SKILLS:
        return {"skill": name, "ok": False, "error": f"unknown skill '{name}'"}
    try:
        return {"skill": name, "ok": True, "result": SKILLS[name]["fn"](**kwargs)}
    except Exception as e:  # a bad call must be reported, not crash the loop
        return {"skill": name, "ok": False, "error": f"{type(e).__name__}: {e}"}


from . import builtin  # noqa: E402,F401  (registers the built-in skills)
from . import web  # noqa: E402,F401  (registers the internet skills)
