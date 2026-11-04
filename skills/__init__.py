"""
Skill registry: small deterministic Python functions the agents can request.

Small local models are unreliable at native function calling, so skills are used
the prompt-based way: an agent returns a JSON list such as
    [{"skill": "currency_convert", "args": {"amount": 100, "from_ccy": "EUR", "to_ccy": "CNY"}}]
and the orchestrator runs them with run_skill_requests() and feeds the results back
into the next prompt. Skills never call the network, so results are reproducible.
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


def describe_skills(names: list[str] | None = None) -> str:
    chosen = names or list(SKILLS)
    lines = ["## Available skills",
             "To use a skill, include it in skill_requests as {\"skill\": name, \"args\": {...}}."]
    for n in chosen:
        info = SKILLS[n]
        lines.append(f"- {n}({info['params']}): {info['description']}")
    return "\n".join(lines)


def run_skill(name: str, **kwargs) -> dict:
    if name not in SKILLS:
        return {"skill": name, "ok": False, "error": f"unknown skill '{name}'"}
    try:
        return {"skill": name, "ok": True, "result": SKILLS[name]["fn"](**kwargs)}
    except Exception as e:  # a bad request from the model must not crash the loop
        return {"skill": name, "ok": False, "error": f"{type(e).__name__}: {e}"}


def run_skill_requests(requests: list[dict]) -> list[dict]:
    return [run_skill(r.get("skill", ""), **(r.get("args") or {})) for r in requests]


from . import builtin  # noqa: E402,F401  (registers the built-in skills)
