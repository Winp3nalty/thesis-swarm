"""
The hotel of the expedition, from data/hotels/<profile>.json. Plain code, no model.

One hotel for the whole trip, chosen by rule so that it never changes between nights or between runs: the scenario's
`hotel` if it names a listed hotel, otherwise the first one in the file. The names in the shipped files are
placeholders to be replaced with hotels checked on a booking site. Finance writes the name into the overnight rows.
"""
from __future__ import annotations

import json

from swarm.core.paths import DATA_DIR


def load_hotels(profile: str) -> list[dict]:
    """[{"name": ..., "area": ...}, ...] in file order; [] when the city has no file."""
    try:
        data = json.loads((DATA_DIR / "hotels" / f"{profile}.json").read_text(encoding="utf-8"))
        return [h for h in data.get("hotels", []) if h.get("name")]
    except (OSError, ValueError, TypeError):
        return []


def choose_hotel(profile: str, preferred: str = "") -> dict | None:
    """The scenario's preferred hotel when it is listed (case-insensitive), else the first hotel; None if none."""
    hotels = load_hotels(profile)
    for h in hotels:
        if preferred and h["name"].lower() == preferred.strip().lower():
            return h
    return hotels[0] if hotels else None


def hotel_label(hotel: dict | None) -> str:
    """Location text of an overnight row. It starts with 'Hotel', so every check treats it as lodging."""
    return f"Hotel: {hotel['name']}" if hotel else "Hotel"
