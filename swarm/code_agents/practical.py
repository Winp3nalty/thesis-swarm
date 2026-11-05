"""Practical travel notes (visa, SIM, payments, transport) per city, from data/practical/<profile>.json.

The text is reference data, not a model answer, so it is the same on every run and carries a visible
"verify before travel" label. It is appended to plan.md next to the plan.
"""
from __future__ import annotations

import json
from swarm.core.paths import DATA_DIR

_DIR = DATA_DIR / "practical"


def load_practical(profile: str) -> dict:
    try:
        return json.loads((_DIR / f"{profile}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def practical_md(info: dict) -> str:
    if not info or not info.get("sections"):
        return ""
    lines = ["", "## Practical notes (reference data, verify before travel)", "",
             f"_{info.get('_note', 'Verify these notes before travel.')}_", ""]
    for title, items in info["sections"].items():
        lines += [f"### {title}", ""] + [f"- {x}" for x in items]
        src = info.get("sources", {}).get(title)
        if src:
            lines.append("- Official sources to check: " + "; ".join(f"[{x['name']}]({x['url']})" for x in src))
        lines.append("")
    return "\n".join(lines)
