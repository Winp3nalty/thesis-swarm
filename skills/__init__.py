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
CALL_LOG: list[dict] = []  # one entry per run_skill() call, so a run can report which skills it used


def reset_call_log() -> None:
    CALL_LOG.clear()


def skills_summary() -> list[dict]:
    """Per skill: how many calls, how many failed, and where the answers came from (live, cache, offline...)."""
    out: dict[str, dict] = {}
    for c in CALL_LOG:
        row = out.setdefault(c["skill"], {"skill": c["skill"], "calls": 0, "failed": 0, "sources": {}})
        row["calls"] += 1
        row["failed"] += 0 if c["ok"] else 1
        row["sources"][c["source"]] = row["sources"].get(c["source"], 0) + 1
    return sorted(out.values(), key=lambda r: r["skill"])


def skill(name: str, description: str, params: str):
    """Decorator that registers a function as a skill."""
    def decorator(fn: Callable):
        SKILLS[name] = {"fn": fn, "description": description, "params": params}
        return fn
    return decorator


def run_skill(name: str, **kwargs) -> dict:
    if name not in SKILLS:
        CALL_LOG.append({"skill": name, "ok": False, "source": "unknown"})
        return {"skill": name, "ok": False, "error": f"unknown skill '{name}'"}
    try:
        result = SKILLS[name]["fn"](**kwargs)
    except Exception as e:  # a bad call must be reported, not crash the loop
        CALL_LOG.append({"skill": name, "ok": False, "source": "error"})
        return {"skill": name, "ok": False, "error": f"{type(e).__name__}: {e}"}
    source = result.get("source", "local") if isinstance(result, dict) else "local"
    CALL_LOG.append({"skill": name, "ok": True, "source": str(source)})
    return {"skill": name, "ok": True, "result": result}


from . import builtin  # noqa: E402,F401  (registers the built-in skills)
from . import web  # noqa: E402,F401  (registers the internet skills)
