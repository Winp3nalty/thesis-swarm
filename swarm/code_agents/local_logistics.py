"""Local Logistics: visa, SIM, payments and local transport for the destination (Edict's 吏部/刑部 rules desk).

Code, not a model: these are entry rules, so the answer must be the same on every run and must name where to
check it. The text lives in data/practical/<city>.json; every section must name at least one official source.
A section without a source is reported as a problem (shown in the output), never silently shipped.
"""
from __future__ import annotations

from swarm.code_agents.practical import load_practical


def agent_local_logistics(profile: str, printer=print) -> dict:
    """Returns {"info": the city's notes, "problems": [...], "sources": number of named sources}."""
    info = load_practical(profile)
    if not info or not info.get("sections"):
        printer(f"  [Local Logistics] no practical notes for '{profile}'.")
        return {"info": {}, "problems": [f"no practical notes for '{profile}'"], "sources": 0}
    sources = info.get("sources", {})
    problems = [f"section '{title}' names no official source" for title in info["sections"] if not sources.get(title)]
    n = sum(len(v) for v in sources.values())
    printer(f"  [Local Logistics] {len(info['sections'])} section(s), {n} named source(s), {len(problems)} problem(s)")
    return {"info": info, "problems": problems, "sources": n}
