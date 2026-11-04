"""
Internet skills. They are the only skills that touch the network, and they always
degrade gracefully: live -> cached copy (data/fx_cache.json) -> static table.
Every result says which source it came from, so the thesis can report it honestly.

Set SWARM_OFFLINE=1 to forbid network access (reproducible benchmark runs, tests).
Data source: Frankfurter (free, no key, European Central Bank reference rates).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import skill
from .builtin import _rates

_CACHE = Path(__file__).resolve().parent.parent / "data" / "fx_cache.json"
FX_URL = "https://api.frankfurter.dev/v1/latest?base=EUR"
TIMEOUT_S = 8
CACHE_MAX_AGE_S = 24 * 3600


def _fetch_live() -> dict:
    """Return {"date": "YYYY-MM-DD", "rates": {ccy: per 1 EUR}} from the web."""
    req = urllib.request.Request(FX_URL, headers={"User-Agent": "thesis-swarm/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    rates = {k.upper(): float(v) for k, v in data["rates"].items()}
    rates["EUR"] = 1.0
    return {"date": data.get("date", ""), "rates": rates}


def _read_cache():
    try:
        return json.loads(_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(entry: dict) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    except OSError:
        pass  # a read-only folder must not break a run


def _load_rates():
    """Return (rates, source, date) using live data, then cache, then the static table."""
    cache = _read_cache()
    offline = os.environ.get("SWARM_OFFLINE", "0") == "1"
    fresh = cache and time.time() - cache.get("fetched_at", 0) < CACHE_MAX_AGE_S
    if cache and (offline or fresh):
        return cache["rates"], "cache", cache.get("date", "")
    if not offline:
        try:
            live = _fetch_live()
            _write_cache({**live, "fetched_at": time.time()})
            return live["rates"], "live", live["date"]
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            pass
    if cache:
        return cache["rates"], "cache", cache.get("date", "")
    return _rates(), "static", ""


@skill("live_exchange_rate",
       "Look up the current exchange rate from the internet (ECB reference rates). "
       "Falls back to a cached or static rate when offline; the result states its source.",
       "amount: float, from_ccy: str, to_ccy: str")
def live_exchange_rate(amount: float, from_ccy: str, to_ccy: str) -> dict:
    rates, source, date = _load_rates()
    f, t = from_ccy.upper(), to_ccy.upper()
    if f not in rates or t not in rates:
        raise ValueError(f"unsupported currency (known: {', '.join(sorted(rates))})")
    rate = rates[t] / rates[f]
    return {"amount": round(float(amount) * rate, 2), "currency": t, "rate": round(rate, 6),
            "source": source, "rate_date": date}


# ==========================================================================
# Reality-check skills: does this place exist, and is the travel time possible?
# Free OpenStreetMap services. Every answer is cached in data/web_cache.json, so
# a run can be repeated offline (SWARM_OFFLINE=1) with exactly the same facts.
# Results are tri-state: found True / False, or None when the web was unreachable
# ("unverified"). Callers must never treat None as evidence against a plan.
# ==========================================================================
_WEB_CACHE = Path(__file__).resolve().parent.parent / "data" / "web_cache.json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
_last_nominatim_call = 0.0


def _cache_get(key: str):
    try:
        return json.loads(_WEB_CACHE.read_text(encoding="utf-8")).get(key)
    except (OSError, ValueError):
        return None


def _cache_put(key: str, value: dict) -> None:
    try:
        try:
            data = json.loads(_WEB_CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[key] = value
        _WEB_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _WEB_CACHE.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "thesis-swarm/1.0 (student research)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _nominatim(query: str):
    """Return a list of result dicts from OpenStreetMap Nominatim (max 1 request/second)."""
    global _last_nominatim_call
    wait = 1.1 - (time.time() - _last_nominatim_call)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim_call = time.time()
    return _get_json(f"{NOMINATIM_URL}?q={urllib.parse.quote(query)}&format=jsonv2&limit=1")


def _osrm(lat1: float, lon1: float, lat2: float, lon2: float):
    data = _get_json(f"{OSRM_URL}/{lon1},{lat1};{lon2},{lat2}?overview=false")
    return float(data["routes"][0]["duration"])  # seconds


def _offline() -> bool:
    return os.environ.get("SWARM_OFFLINE", "0") == "1"


@skill("verify_place",
       "Check on OpenStreetMap that a named place exists in a city. found=true/false, or null if the web "
       "could not be reached (unverified). Returns coordinates.",
       "place: str, city: str")
def verify_place(place: str, city: str) -> dict:
    query = f"{place}, {city}"
    key = "place:" + query.lower()
    hit = _cache_get(key)
    if hit is not None:
        return {**hit, "source": "cache"}
    if _offline():
        return {"found": None, "source": "offline"}
    try:
        results = _nominatim(query)
    except (urllib.error.URLError, OSError, ValueError):
        return {"found": None, "source": "unreachable"}
    if results:
        r = results[0]
        entry = {"found": True, "lat": float(r["lat"]), "lon": float(r["lon"]),
                 "display_name": r.get("display_name", "")[:120]}
    else:
        entry = {"found": False}
    _cache_put(key, entry)
    return {**entry, "source": "live"}


@skill("travel_minutes",
       "Driving time in minutes between two coordinates (OpenStreetMap routing, no traffic). "
       "minutes=null if the web could not be reached.",
       "lat1: float, lon1: float, lat2: float, lon2: float")
def travel_minutes(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    key = f"route:{lat1:.4f},{lon1:.4f};{lat2:.4f},{lon2:.4f}"
    hit = _cache_get(key)
    if hit is not None:
        return {**hit, "source": "cache"}
    if _offline():
        return {"minutes": None, "source": "offline"}
    try:
        seconds = _osrm(lat1, lon1, lat2, lon2)
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError):
        return {"minutes": None, "source": "unreachable"}
    entry = {"minutes": round(seconds / 60, 1)}
    _cache_put(key, entry)
    return {**entry, "source": "live"}
