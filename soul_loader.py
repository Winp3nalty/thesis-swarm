"""
Loads each agent's persona and rules from agents/<agent_id>/SOUL.md.

Pattern adapted from Edict (cft0808/edict, MIT): an agent's behaviour lives in a
plain Markdown file instead of a string buried in code, so prompts can be edited,
versioned and cited in the thesis without touching Python.
"""
from __future__ import annotations

from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent / "agents"


def load_soul(agent_id: str) -> str:
    path = AGENTS_DIR / agent_id / "SOUL.md"
    if not path.exists():
        raise FileNotFoundError(f"No SOUL.md for agent '{agent_id}' at {path}")
    return path.read_text(encoding="utf-8").strip()


def build_system_prompt(agent_id: str, skill_names: list[str] | None = None, extra: str = "") -> str:
    """SOUL.md text, plus an optional list of skills the agent may request, plus extras."""
    parts = [load_soul(agent_id)]
    if skill_names:
        from skills import describe_skills
        parts.append(describe_skills(skill_names))
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)
