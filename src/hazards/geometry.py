"""Geographic helpers: great-circle distances, point-in-polygon on GeoJSON, distance to a track, and line
simplification for large overlays. Pure Python and NumPy, so the hazard layer adds no geospatial dependency."""

from __future__ import annotations

import math

import numpy as np

EARTH_RADIUS_KM = 6371.0088          # IUGG mean radius


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance (km); works element-wise on NumPy arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def rings(geometry: dict | None) -> list[list[list[float]]]:
    """Every ring of a Polygon, MultiPolygon or GeometryCollection, as lists of [lon, lat]."""
    if not geometry:
        return []
    t, c = geometry.get("type"), geometry.get("coordinates")
    if t == "Polygon":
        return list(c or [])
    if t == "MultiPolygon":
        return [r for poly in c or [] for r in poly]
    if t == "GeometryCollection":
        return [r for g in geometry.get("geometries") or [] for r in rings(g)]
    return []


def contains(geometry: dict | None, lat, lon) -> np.ndarray:
    """Even-odd point-in-polygon over all rings (holes and multi-part areas work). Arrays in, booleans out."""
    lat, lon = np.atleast_1d(np.asarray(lat, float)), np.atleast_1d(np.asarray(lon, float))
    inside = np.zeros(lat.shape, bool)
    for ring in rings(geometry):
        pts = np.asarray([p[:2] for p in ring], float)
        if len(pts) < 3:
            continue
        x0, y0 = pts[:, 0], pts[:, 1]
        x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
        for a, b, c, d in zip(x0, y0, x1, y1):
            crosses = ((b > lat) != (d > lat)) & (lon < (c - a) * (lat - b) / ((d - b) or 1e-12) + a)
            inside ^= crosses
    return inside


def bbox(geometry: dict | None) -> list[float] | None:
    pts = [p for r in rings(geometry) for p in r]
    if geometry and geometry.get("type") == "LineString":
        pts = geometry.get("coordinates") or []
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def distance_to_track_km(lat, lon, track: list[tuple[float, float]]) -> np.ndarray:
    """Distance from points to a polyline of (lat, lon) vertices: the closest of the vertices and of points
    every ~10 km along each segment (accurate to a few km at the scales used for "near a forecast track")."""
    lat, lon = np.atleast_1d(np.asarray(lat, float)), np.atleast_1d(np.asarray(lon, float))
    samples: list[tuple[float, float]] = []
    for (a_lat, a_lon), (b_lat, b_lon) in zip(track, track[1:]):
        n = max(1, int(float(haversine_km(a_lat, a_lon, b_lat, b_lon)) // 10))
        samples += [(a_lat + (b_lat - a_lat) * i / n, a_lon + (b_lon - a_lon) * i / n) for i in range(n)]
    if track:
        samples.append(track[-1])
    if not samples:
        return np.full(lat.shape, np.inf)
    s = np.asarray(samples)
    d = haversine_km(lat[:, None], lon[:, None], s[None, :, 0], s[None, :, 1])
    return d.min(axis=1)


def simplify(points: list[list[float]], tolerance: float) -> list[list[float]]:
    """Douglas-Peucker in degrees, keeping the first and last vertex (rings stay closed)."""
    if len(points) < 3:
        return points
    keep = np.zeros(len(points), bool)
    keep[0] = keep[-1] = True
    arr = np.asarray(points, float)[:, :2]
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = arr[i], arr[j]
        seg = b - a
        norm = math.hypot(*seg)
        rel = arr[i + 1:j] - a
        d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / norm if norm else np.hypot(rel[:, 0], rel[:, 1])
        k = int(np.argmax(d))
        if d[k] > tolerance:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return [[round(x, 4), round(y, 4)] for x, y in arr[keep]]


def simplify_geometry(geometry: dict, tolerance: float) -> dict | None:
    """Simplify a Polygon/MultiPolygon; rings that collapse below 4 vertices are dropped."""
    def ring(r):
        s = simplify(r, tolerance)
        return s if len(s) >= 4 else None

    def poly(p):
        out = [r for r in (ring(x) for x in p) if r]
        return out if out else None
    if geometry.get("type") == "Polygon":
        p = poly(geometry["coordinates"])
        return {"type": "Polygon", "coordinates": p} if p else None
    if geometry.get("type") == "MultiPolygon":
        ps = [p for p in (poly(x) for x in geometry["coordinates"]) if p]
        return {"type": "MultiPolygon", "coordinates": ps} if ps else None
    return None
