"""Small text helpers that the Transit and Outreach validators share."""
from __future__ import annotations

import re


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _key_words(text: str, stop: set[str] | None = None) -> set[str]:
    return {w for w in re.findall(r"[a-z]{5,}", text.lower()) if w not in (stop or set())}
