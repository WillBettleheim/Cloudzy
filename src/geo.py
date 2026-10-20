"""Geographic boundaries, labels and places for the Weather Explorer (Natural Earth, public domain).

Data (built by tools/build_geo.py from Natural Earth v5.1.2; see static/vendor/geo/README.md):
- countries.json: every country's outline (India's official point of view, Natural Earth "IND" worldview),
  continent, subregion, label point, angular radius and bounding box, plus derived continent label points.
- admin1/<A3>.json: first-level divisions (states, provinces, territories…) of one country, with their
  internal boundary lines; loaded only when a country is explored.
- index.json: names of countries and divisions, for search (no geometry).
- places.json: populated places (cities and towns) with Natural Earth's scale rank.

Polygons are stored as lists of rings, each a flat [lon0, lat0, lon1, lat1, …] list. Point-in-polygon uses the
even-odd rule over all rings of a feature, so holes and multi-part features work.
"""

from __future__ import annotations

import json
import math
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from src import config

GEO_DIR = config.PROJECT_ROOT / "static" / "vendor" / "geo"
STATIC_URL = "app/static/vendor/geo"
MIN_CELLS = 3          # fewer 1° grid cells than this inside a region → no regional aggregate


@lru_cache(maxsize=1)
def countries() -> dict[str, Any]:
    return json.loads((GEO_DIR / "countries.json").read_text())


@lru_cache(maxsize=1)
def index() -> list[dict[str, Any]]:
    return json.loads((GEO_DIR / "index.json").read_text())["entries"]


@lru_cache(maxsize=64)
def admin1(a3: str) -> dict[str, Any] | None:
    path = GEO_DIR / "admin1" / f"{a3}.json"
    if not a3.isalnum() or not path.exists():
        return None
    return json.loads(path.read_text())


def country(a3: str) -> dict[str, Any] | None:
    return next((c for c in countries()["countries"] if c["a3"] == a3), None)


def division(a3: str, unit_id: str) -> dict[str, Any] | None:
    data = admin1(a3)
    return next((u for u in data["units"] if u["id"] == unit_id), None) if data else None


def points_in_polys(lon: np.ndarray, lat: np.ndarray, polys: list[list[float]]) -> np.ndarray:
    """Even-odd point-in-polygon for many points against all rings of one feature (vectorised over points
    and edges, in chunks so memory stays small)."""
    lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    crossings = np.zeros(lon.shape, dtype=np.int64)
    for ring in polys:
        xs, ys = np.asarray(ring[0::2], dtype=float), np.asarray(ring[1::2], dtype=float)
        x1, y1, x2, y2 = xs, ys, np.roll(xs, 1), np.roll(ys, 1)
        keep = y1 != y2
        x1, y1, x2, y2 = x1[keep], y1[keep], x2[keep], y2[keep]
        slope = (x2 - x1) / (y2 - y1)
        step = max(1, 2_000_000 // max(1, len(x1)))
        for s in range(0, lon.size, step):
            px, py = lon.ravel()[s:s + step, None], lat.ravel()[s:s + step, None]
            hit = ((y1 > py) != (y2 > py)) & (px < x1 + (py - y1) * slope)
            crossings.ravel()[s:s + step] += hit.sum(axis=1)
    return (crossings % 2) == 1


def fold(text: str) -> str:
    """Lower case without accents, so 'sao paulo' finds 'São Paulo'."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).lower()


def search(query: str, limit: int = 6) -> list[dict[str, Any]]:
    """Countries and first-level divisions whose name starts with (or else contains) the query."""
    q = fold(query.strip())
    if len(q) < 2:
        return []
    rows = [e for e in index() if e.get("name")]
    starts = [e for e in rows if fold(e["name"]).startswith(q)]
    contains = [e for e in rows if q in fold(e["name"]) and e not in starts]
    order = {"country": 0, "admin1": 1}
    return sorted(starts, key=lambda e: (order[e["kind"]], e.get("rank", 9)))[:limit] + \
        sorted(contains, key=lambda e: (order[e["kind"]], e.get("rank", 9)))[:max(0, limit - len(starts))]


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """'28.61, 77.21' or '28.61 77.21' (latitude first) → (lat, lon), or None."""
    parts = text.replace(",", " ").split()
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def breadcrumb(kind: str, a3: str | None = None, unit_id: str | None = None) -> list[str]:
    """Earth → continent → country → division, as names."""
    trail = ["Earth"]
    c = country(a3) if a3 else None
    if c:
        if c["cont"] and not c["cont"].startswith("Seven seas"):
            trail.append(c["cont"])
        trail.append(c["name"])
        if kind == "admin1" and unit_id:
            u = division(a3 or "", unit_id)
            if u:
                trail.append(u["name"])
    return trail


def region_mask(kind: str, a3: str, unit_id: str | None = None) -> tuple[np.ndarray, int] | None:
    """Which 1° grid cells (181 × 360, the GFS frame layout) have their centre inside the region.

    A division's cells are also limited to its country's outline, so a division that Natural Earth draws
    across a disputed area never takes in cells shown as another country. Returns (mask, cell count)."""
    c = country(a3)
    if c is None:
        return None
    feature = c if kind == "country" else division(a3, unit_id or "")
    if feature is None:
        return None
    lat = 90.0 - np.arange(181.0)
    lon = np.arange(360.0)
    lon = np.where(lon > 180, lon - 360, lon)
    glon, glat = np.meshgrid(lon, lat)
    x0, y0, x1, y1 = feature["bbox"]
    sel = (glat >= y0) & (glat <= y1) & (glon >= x0) & (glon <= x1)
    mask = np.zeros(glon.shape, dtype=bool)
    if sel.any():
        inside = points_in_polys(glon[sel], glat[sel], feature["polys"])
        if kind != "country":
            inside &= points_in_polys(glon[sel], glat[sel], c["polys"])
        mask[sel] = inside
    return mask, int(mask.sum())


def area_weights(mask: np.ndarray) -> np.ndarray:
    """cos(latitude) weights (a 1° cell's area is proportional to it), zero outside the mask."""
    lat = 90.0 - np.arange(181.0)
    w = np.cos(np.radians(lat))[:, None] * np.ones((1, 360))
    return np.where(mask, w, 0.0)


def angular_distance_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def data_files_present(directory: Path | None = None) -> bool:
    d = Path(directory or GEO_DIR)
    return (d / "countries.json").exists() and (d / "index.json").exists()


COAST_TOLERANCE_DEG = 0.15   # simplified outlines can cut a coastal point off; within this, it still counts


def distance_to_rings_deg(lon: float, lat: float, polys: list[list[float]]) -> float:
    """Smallest distance (in degrees, planar) from a point to any edge of the rings."""
    best = math.inf
    for ring in polys:
        xs, ys = np.asarray(ring[0::2]), np.asarray(ring[1::2])
        x1, y1, x2, y2 = xs[:-1], ys[:-1], xs[1:], ys[1:]
        dx, dy = x2 - x1, y2 - y1
        t = np.clip(((lon - x1) * dx + (lat - y1) * dy) / np.maximum(dx * dx + dy * dy, 1e-12), 0, 1)
        if len(t):
            best = min(best, float(np.min(np.hypot(x1 + t * dx - lon, y1 + t * dy - lat))))
    return best


def country_at(lat: float, lon: float) -> dict[str, Any] | None:
    """The country containing a point, or, failing that, one whose outline is within COAST_TOLERANCE_DEG."""
    pt_lon, pt_lat = np.array([lon]), np.array([lat])
    near: list[tuple[float, dict]] = []
    m = COAST_TOLERANCE_DEG
    for c in countries()["countries"]:
        x0, y0, x1, y1 = c["bbox"]
        if not (x0 - m <= lon <= x1 + m and y0 - m <= lat <= y1 + m):
            continue
        if points_in_polys(pt_lon, pt_lat, c["polys"])[0]:
            return c
        d = distance_to_rings_deg(lon, lat, c["polys"])
        if d <= m:
            near.append((d, c))
    return min(near, key=lambda x: x[0])[1] if near else None


def region_at(lat: float, lon: float) -> dict[str, Any] | None:
    """The first-level division (or, without one, the country) containing a point, as a region dict."""
    pt_lon, pt_lat = np.array([lon]), np.array([lat])
    for c in [x for x in [country_at(lat, lon)] if x]:
        data = admin1(c["a3"]) if c.get("adm1") else None
        near: list[tuple[float, dict]] = []
        for u in (data or {}).get("units", []):
            ux0, uy0, ux1, uy1 = u["bbox"]
            if not (ux0 - 0.1 <= lon <= ux1 + 0.1 and uy0 - 0.1 <= lat <= uy1 + 0.1):
                continue
            if points_in_polys(pt_lon, pt_lat, u["polys"])[0]:
                return {"kind": "admin1", "a3": c["a3"], "id": u["id"], "name": u["name"]}
            d = distance_to_rings_deg(lon, lat, u["polys"])
            if d <= 0.1:
                near.append((d, u))
        if near:
            u = min(near, key=lambda x: x[0])[1]
            return {"kind": "admin1", "a3": c["a3"], "id": u["id"], "name": u["name"]}
        return {"kind": "country", "a3": c["a3"], "id": c["a3"], "name": c["name"]}
    return None


def clean_region(value: Any) -> dict[str, Any] | None:
    """A region sent by the browser, checked against the data (anything unknown → None)."""
    if not isinstance(value, dict):
        return None
    kind, name = value.get("kind"), value.get("name")
    fhr = value.get("fhr")
    fhr = int(fhr) if isinstance(fhr, (int, float)) and 0 <= fhr <= 384 else None
    if kind == "continent":
        k = next((c for c in countries()["continents"] if c["name"] == name), None)
        return {"kind": "continent", "a3": None, "id": k["name"], "name": k["name"], "fhr": fhr} if k else None
    a3 = value.get("a3")
    c = country(a3) if isinstance(a3, str) and a3.isalnum() else None
    if c is None:
        return None
    if kind == "country":
        return {"kind": "country", "a3": a3, "id": a3, "name": c["name"], "fhr": fhr}
    if kind == "admin1" and isinstance(value.get("id"), str):
        u = division(c["a3"], value["id"])
        return {"kind": "admin1", "a3": a3, "id": u["id"], "name": u["name"], "fhr": fhr} if u else None
    return None
