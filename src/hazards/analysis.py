"""Filters, summaries and geographic context for hazard events.

Settlement context uses Natural Earth populated places (static/vendor/geo/places.json: 7,342 cities and towns
with `pop_max`, Natural Earth's metropolitan-area estimate drawn from several census years). It answers "which
mapped settlements are inside this warning polygon / near this point or forecast track". It never estimates how
many people are affected, damage or risk: being near an event doesn't mean being affected, and a missing event
doesn't mean an area is safe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from src import geo
from src.hazards.geometry import contains, distance_to_track_km, haversine_km
from src.hazards.schema import CATEGORIES, HazardEvent

POPULATION_NOTE = ("Settlements and populations: Natural Earth populated places (public domain). Populations are "
                   "metropolitan-area estimates from various census years, shown for orientation only.")
PROXIMITY_NOTE = ("Distance alone doesn't mean a place is affected, and places not listed aren't necessarily "
                  "unaffected. Follow the issuing authority for impacts and instructions.")
RADIUS_KM = {"earthquake": 100, "wildfire": 50, "volcano": 50, "flood": 75, "tsunami": 150, "drought": 0,
             "cyclone": 150, "severe": 50, "temperature": 50, "winter": 50, "other": 50, "fire_detection": 25}


@dataclass
class Place:
    name: str
    lat: float
    lon: float
    country_a3: str
    admin1: str
    population: int | None
    distance_km: float | None = None


@lru_cache(maxsize=1)
def _places() -> tuple[list, np.ndarray, np.ndarray]:
    data = json.loads((geo.GEO_DIR / "places.json").read_text())
    rows = data["places"]
    return rows, np.array([r[1] for r in rows], float), np.array([r[2] for r in rows], float)


def _place(row, d: float | None = None) -> Place:
    return Place(row[0], row[1], row[2], row[4], row[5], int(row[6]) if row[6] and row[6] > 0 else None,
                 None if d is None else round(float(d), 1))


def places_near(lat: float, lon: float, radius_km: float, limit: int = 8) -> list[Place]:
    """Mapped settlements within `radius_km` (great-circle), closest first."""
    rows, lats, lons = _places()
    d = haversine_km(lat, lon, lats, lons)
    idx = np.where(d <= radius_km)[0]
    return [_place(rows[i], d[i]) for i in idx[np.argsort(d[idx])][:limit]]


def places_inside(geometry: dict, limit: int = 12) -> list[Place]:
    """Mapped settlements inside a polygon (official warning area, forecast cone), most populous first."""
    rows, lats, lons = _places()
    inside = np.where(contains(geometry, lats, lons))[0]
    order = sorted(inside, key=lambda i: -(rows[i][6] or 0))
    return [_place(rows[i]) for i in order[:limit]]


def places_near_track(track: list[tuple[float, float]], radius_km: float, limit: int = 12) -> list[Place]:
    """Mapped settlements within `radius_km` of a polyline (e.g. a forecast track), closest first."""
    rows, lats, lons = _places()
    if not track:
        return []
    lat0, lat1 = min(p[0] for p in track) - 3, max(p[0] for p in track) + 3
    cand = np.where((lats >= lat0) & (lats <= lat1))[0]
    if len(cand) == 0:
        return []
    d = distance_to_track_km(lats[cand], lons[cand], track)
    keep = np.argsort(d)[: max(limit * 4, limit)]
    return [_place(rows[cand[i]], d[i]) for i in keep if d[i] <= radius_km][:limit]


def context(event: HazardEvent) -> dict:
    """Geographic context for one event, labelled by how it was computed."""
    out: dict = {"places": [], "method": "", "notes": [POPULATION_NOTE, PROXIMITY_NOTE]}
    if event.category == "cyclone" and event.extra.get("track"):
        fc = [(p["lat"], p["lon"]) for p in event.extra["track"] if p["status"] in ("current", "forecast")]
        if event.geometry:
            out["places"] = places_inside(event.geometry)
            out["method"] = "Settlements inside the official forecast cone (most populous first)."
        if not out["places"] and len(fc) >= 2:
            out["places"] = places_near_track(fc, 100)
            out["method"] = "Settlements within 100 km of the forecast track (closest first)."
        out["notes"].insert(0, "A forecast track and cone show where the centre may go, not a certain path; "
                               "wind, rain and surge can reach far beyond them.")
    elif event.geometry and event.geometry.get("type") in ("Polygon", "MultiPolygon", "GeometryCollection"):
        out["places"] = places_inside(event.geometry)
        out["method"] = "Settlements inside the event's area as the source drew it (most populous first)."
    elif event.lat is not None and event.lon is not None and RADIUS_KM.get(event.category, 50):
        r = RADIUS_KM.get(event.category, 50)
        out["places"] = places_near(event.lat, event.lon, r)
        out["method"] = f"Settlements within {r} km of the reported location (closest first)."
    return out


def nearby_events(event: HazardEvent, events: list[HazardEvent], radius_km: float = 300, limit: int = 8):
    """Other loaded events within `radius_km`, closest first (e.g. aftershocks near an earthquake)."""
    if event.lat is None or event.lon is None:
        return []
    cand = [e for e in events if e.id != event.id and e.lat is not None and e.lon is not None]
    if not cand:
        return []
    d = haversine_km(event.lat, event.lon, np.array([e.lat for e in cand]), np.array([e.lon for e in cand]))
    order = np.argsort(d)
    return [(cand[i], round(float(d[i]), 1)) for i in order if d[i] <= radius_km][:limit]


def events_near(lat: float, lon: float, events: list[HazardEvent], radius_km: float) -> list[HazardEvent]:
    """Events with a location within `radius_km` of a point (a searched city or address)."""
    located = [e for e in events if e.lat is not None and e.lon is not None]
    if not located:
        return []
    d = haversine_km(lat, lon, np.array([e.lat for e in located]), np.array([e.lon for e in located]))
    return [e for e, dist in zip(located, d) if dist <= radius_km]


# --- Filters --------------------------------------------------------------------------------------

def filter_events(events: list[HazardEvent], categories: set[str] | None = None, min_rank: int = 0,
                  official_only: bool = False, current_only: bool = False, start: pd.Timestamp | None = None,
                  end: pd.Timestamp | None = None, region: dict | None = None, text: str = "",
                  include_marine: bool = False) -> list[HazardEvent]:
    """Events matching every filter. `region` is a geo region dict (country or division) from src.geo."""
    out = []
    polys = _region_polys(region)
    needle = geo.fold(text) if text else ""
    for e in events:
        if categories is not None and e.category not in categories:
            continue
        if e.severity_rank < min_rank or (official_only and not e.official) or (current_only and not e.current):
            continue
        if not include_marine and e.extra.get("marine"):
            continue
        if (start is not None or end is not None) and e.time is not None:
            if (start is not None and (e.ends or e.time) < start) or (end is not None and e.time > end):
                continue
        if polys is not None:
            if e.lat is None or not geo.points_in_polys(np.array([e.lon]), np.array([e.lat]), polys)[0]:
                continue
        if needle and needle not in geo.fold(f"{e.title} {e.area or ''} {e.country or ''}"):
            continue
        out.append(e)
    return out


def _region_polys(region: dict | None):
    if not region:
        return None
    if region.get("kind") == "country":
        c = geo.country(region.get("a3", ""))
        return c["polys"] if c else None
    if region.get("kind") == "admin1":
        d = geo.division(region.get("a3", ""), region.get("id", ""))
        return d["polys"] if d else None
    return None


def summary(events: list[HazardEvent], statuses: dict, fire_count: int | None) -> list[dict]:
    """Counts by category, each with exactly what was counted and the sources' coverage."""
    def n(pred):
        return sum(1 for e in events if pred(e))
    rows = [
        {"label": "Earthquakes", "value": n(lambda e: e.category == "earthquake" and e.source == "usgs"),
         "definition": "USGS-located earthquakes in the selected window and magnitude range", "icon": "earthquake"},
        {"label": "Tropical cyclones", "value": n(lambda e: e.category == "cyclone" and e.current),
         "definition": "Active storms with an NHC advisory or a current GDACS record", "icon": "cyclone"},
        {"label": "Flood warnings and reports",
         "value": n(lambda e: e.category == "flood" and (e.source == "nws" or e.current)),
         "definition": "NWS flood warnings, watches and advisories (U.S.) plus current GDACS flood events",
         "icon": "flood"},
        {"label": "Fire detections", "value": fire_count,
         "definition": "VIIRS NOAA-20 satellite hotspots, last 24 h (not confirmed fires)", "icon": "local_fire_department"},
        {"label": "Volcanoes", "value": n(lambda e: e.category == "volcano"),
         "definition": "Elevated U.S. alert levels (USGS) plus GDACS volcano events", "icon": "volcano"},
        {"label": "Official warnings", "value": n(lambda e: e.official and e.kind == "warning" and e.current
                                                  and not e.extra.get("marine")),
         "definition": "NWS, NHC, USGS volcano and NOAA tsunami messages in force (U.S.-led coverage)",
         "icon": "campaign"},
    ]
    return rows


def category_counts(events: list[HazardEvent]) -> pd.DataFrame:
    counts = pd.Series([e.category for e in events]).value_counts() if events else pd.Series(dtype=int)
    return pd.DataFrame([{"Category": c.label, "Records": int(counts.get(k, 0))} for k, c in CATEGORIES.items()])
