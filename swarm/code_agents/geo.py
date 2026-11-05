"""
Is a place in the expedition's region? Plain code, no model.

data/geo/<profile>.json gives the centre of the city and the radius that a day trip can reach. A place whose
coordinates lie farther away is not a place of this expedition: the name 'Helsinki' is a real place, but not one
that a Shenzhen trip can visit.
"""
from __future__ import annotations

import json
import math

from swarm.core.paths import DATA_DIR


def load_geo(profile: str) -> dict:
    """{"center": [lat, lon], "radius_km": R} for a city profile; {} when the city has no file (then no check)."""
    try:
        data = json.loads((DATA_DIR / "geo" / f"{profile}.json").read_text(encoding="utf-8"))
        return {"center": (float(data["center"][0]), float(data["center"][1])), "radius_km": float(data["radius_km"])}
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return {}


def distance_km(a, b) -> float:
    """Great-circle distance between two (lat, lon) pairs."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def km_outside(coord, geo: dict) -> float | None:
    """How far the place is from the city centre when that is more than the radius; None when it is inside or
    when there is nothing to check (no coordinates, no region file)."""
    if not geo or not coord:
        return None
    d = distance_km(coord, geo["center"])
    return d if d > geo["radius_km"] else None


def region_problem(name: str, coord, geo: dict, city: str) -> str | None:
    d = km_outside(coord, geo)
    if d is None:
        return None
    return (f"Place '{name}' is {d:.0f} km from the centre of {city}, farther than the {geo['radius_km']:.0f} km "
            f"that an expedition day can reach. Use a real place in {city}.")
