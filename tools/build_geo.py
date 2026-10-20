"""Build the Weather Explorer's geographic data (boundaries, labels, places) from Natural Earth v5.1.2.

Natural Earth is public domain (https://www.naturalearthdata.com/about/terms-of-use/). Inputs, from
https://github.com/nvkelso/natural-earth-vector/tree/v5.1.2/geojson :
  ne_10m_admin_0_countries_ind.geojson           country outlines, India's point of view (worldview "IND")
  ne_10m_admin_1_states_provinces.geojson         first-level divisions (states, provinces, territories…)
  ne_10m_admin_1_states_provinces_lines.geojson   internal boundaries between those divisions
  ne_10m_populated_places_simple.geojson          cities and towns

Output: static/vendor/geo/ (countries.json, borders.json, index.json, places.json, admin1/<A3>.json). borders.json
holds the land borders between countries in this point of view, for the 2D map. Geometry is simplified
with Douglas-Peucker (countries 0.04°, dropping specks under 0.002 square degrees; divisions 0.01°) and rounded to 0.01° (about 1 km).

Disputed areas (India's point of view): a division whose label point lies inside another country's outline is
left out (for example Pakistan's Azad Kashmir and Gilgit-Baltistan), and division boundary lines are kept only
where they lie inside their own country's outline. Natural Earth has no Indian state polygons for areas that are
Indian in this view but not administered by India; those stay inside India's outline without a state.

Corrections: Natural Earth v5.1.2 lists Himachal Pradesh as a "Union Territory" and gives Gujarat no type; India's
division types are set from the constitutional list (28 states, 8 union territories) instead.

Usage:  python tools/build_geo.py <folder with the four .geojson files>
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.geo import points_in_polys

OUT = Path(__file__).resolve().parents[1] / "static" / "vendor" / "geo"
INDIA_UTS = {"Andaman and Nicobar", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
             "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry"}
NAME_FIXES = {("IND", "Andaman and Nicobar"): "Andaman and Nicobar Islands"}


def dp(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker simplification of an open polyline (iterative)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a], points[b]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1e-12
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            x, y = points[i]
            d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / n if (dx or dy) else math.hypot(x - x1, y - y1)
            if d > best:
                best, idx = d, i
        if best > tol and idx > 0:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep) if k]


def ring_area(r: list[tuple[float, float]]) -> float:
    return abs(sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1))) / 2


def simplify_polys(geom: dict, tol: float, min_area: float = 0.0) -> list[list[float]]:
    """Simplified rings; outer rings smaller than `min_area` (square degrees) are dropped, except the largest."""
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    rings: list[tuple[float, list[float]]] = []
    for poly in polys:
        for k, ring in enumerate(poly):
            pts = [(float(x), float(y)) for x, y in ring]
            area = ring_area(pts)
            s = dp(pts[:-1], tol) + [pts[0]] if len(pts) > 3 else pts
            s = [(round(x, 2), round(y, 2)) for x, y in s]
            s = [p for i, p in enumerate(s) if i == 0 or p != s[i - 1]]
            if len(s) < 4:
                if k == 0 and area > tol * tol:          # keep a tiny island as a small triangle-ish ring
                    s = [(round(x, 2), round(y, 2)) for x, y in pts[:: max(1, len(pts) // 4)]] + [s[0]]
                    s = [p for i, p in enumerate(s) if i == 0 or p != s[i - 1]]
                if len(s) < 4:
                    continue
            rings.append((area if k == 0 else math.inf, [c for p in s for c in p]))   # holes are always kept
    if not rings:
        return []
    biggest = max(a for a, _ in rings if a != math.inf) if any(a != math.inf for a, _ in rings) else 0
    return [r for a, r in rings if a >= min_area or a == biggest]


def bbox(polys: list[list[float]]) -> list[float]:
    xs = [v for r in polys for v in r[0::2]]
    ys = [v for r in polys for v in r[1::2]]
    return [min(xs), min(ys), max(xs), max(ys)]


def unit(lat: float, lon: float) -> np.ndarray:
    p, la = math.radians(lon), math.radians(lat)
    return np.array([math.cos(la) * math.cos(p), math.cos(la) * math.sin(p), math.sin(la)])


def angle(a: np.ndarray, b: np.ndarray) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, float(a @ b)))))


def radius(lat: float, lon: float, polys: list[list[float]]) -> float:
    """Angular radius (degrees) from the label point to the farthest outline vertex."""
    c = unit(lat, lon)
    return round(max((angle(c, unit(r[i + 1], r[i])) for r in polys for i in range(0, len(r), 2)), default=0.5), 2)


def land_borders(features: list[dict], tol: float = 0.02) -> list[list[float]]:
    """Land borders in this point of view: Natural Earth builds neighbouring countries from shared vertices, so
    an edge used by two countries' outlines is a land border. Edges are chained into lines, then simplified."""
    count: dict[tuple, int] = defaultdict(int)
    for f in features:
        g = f["geometry"]
        for poly in ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]):
            for ring in poly:
                pts = [(round(x, 6), round(y, 6)) for x, y in ring]
                for a, b in zip(pts, pts[1:]):
                    if a != b:
                        count[(min(a, b), max(a, b))] += 1
    adj: dict[tuple, list[tuple]] = defaultdict(list)
    for (a, b), n in count.items():
        if n >= 2:
            adj[a].append(b)
            adj[b].append(a)
    used: set[tuple] = set()
    lines_out = []

    def walk(start: tuple, nxt: tuple) -> list[tuple]:
        line, prev, cur = [start], start, nxt
        used.add((min(start, nxt), max(start, nxt)))
        while True:
            line.append(cur)
            options = [q for q in adj[cur] if (min(cur, q), max(cur, q)) not in used]
            if len(adj[cur]) != 2 or not options:
                return line
            prev, cur = cur, options[0]
            used.add((min(prev, cur), max(prev, cur)))

    for node in [n for n, ns in adj.items() if len(ns) != 2] + list(adj):   # ends and junctions first, then loops
        for q in adj[node]:
            if (min(node, q), max(node, q)) not in used:
                line = dp(walk(node, q), tol)
                pts = [(round(x, 2), round(y, 2)) for x, y in line]
                pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
                if len(pts) >= 2:
                    lines_out.append([c for p in pts for c in p])
    return lines_out


def main(src: str) -> None:
    d = Path(src)
    load = lambda n: json.loads((d / f"{n}.geojson").read_text())  # noqa: E731
    countries_fc = load("ne_10m_admin_0_countries_ind")
    adm1_fc = load("ne_10m_admin_1_states_provinces")
    lines_fc = load("ne_10m_admin_1_states_provinces_lines")
    places_fc = load("ne_10m_populated_places_simple")
    (OUT / "admin1").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "admin1").glob("*.json"):
        old.unlink()

    # Countries.
    countries: list[dict] = []
    outline: dict[str, list[list[float]]] = {}
    full: dict[str, list[list[float]]] = {}
    fine: dict[str, list[list[float]]] = {}
    for f in countries_fc["features"]:
        p = f["properties"]
        polys = simplify_polys(f["geometry"], 0.04, min_area=0.002)
        if not polys:
            continue
        a3 = p["ADM0_A3"]
        lat, lon = round(p["LABEL_Y"], 2), round(p["LABEL_X"], 2)
        countries.append({"a3": a3, "name": p["NAME"], "cont": p["CONTINENT"], "sub": p["SUBREGION"],
                          "lat": lat, "lon": lon, "r": radius(lat, lon, polys), "rank": p["LABELRANK"],
                          "minl": p["MIN_LABEL"], "bbox": bbox(polys), "polys": polys, "adm1": False})
        outline[a3] = polys
        g = f["geometry"]                                            # raw rings, for ownership tests
        full[a3] = [[float(v) for xy in ring for v in xy] for poly in
                    ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]) for ring in poly]
        fine[a3] = simplify_polys(f["geometry"], 0.01)              # for trimming division lines

    # Divisions, grouped by country. A division whose label point lies inside *another* country's outline is
    # a disputed area shown as that other country in this point of view (for example Azad Kashmir), so it's left out.
    boxes = {c["a3"]: c["bbox"] for c in countries}

    def owner(lon: float, lat: float, own: str) -> str | None:
        for a3, (x0, y0, x1, y1) in boxes.items():
            if a3 != own and x0 <= lon <= x1 and y0 <= lat <= y1 and \
                    points_in_polys(np.array([lon]), np.array([lat]), full[a3])[0]:
                return a3
        return None

    units: dict[str, list[dict]] = defaultdict(list)
    dropped = []
    for f in adm1_fc["features"]:
        p = f["properties"]
        a3 = p["adm0_a3"]
        if a3 not in outline or f["geometry"] is None or not p["name"]:
            continue                                                  # a handful of units have no name
        lat, lon = round(p["latitude"], 2), round(p["longitude"], 2)
        other = owner(float(p["longitude"]), float(p["latitude"]), a3)
        if other:
            dropped.append(f"{p['name']} ({a3}, inside {other})")
            continue
        polys = simplify_polys(f["geometry"], 0.01)
        if not polys:
            continue
        name = NAME_FIXES.get((a3, p["name"]), p["name"])
        kind = ("Union territory" if p["name"] in INDIA_UTS else "State") if a3 == "IND" else (p["type_en"] or "")
        units[a3].append({"id": p["adm1_code"], "name": name, "type": kind, "lat": lat, "lon": lon,
                          "r": radius(lat, lon, polys), "rank": p["labelrank"], "bbox": bbox(polys), "polys": polys})

    # Divisions that reach into an area shown as another country in this point of view (for example the Golan
    # Heights in Israel's HaZafon district): sampled on a 0.1° grid; more than 10% of the samples inside another
    # country's outline → "clip": the globe trims the division's highlight and outline to its own country.
    for a3, us in units.items():
        for u in us:
            x0, y0, x1, y1 = u["bbox"]
            gx, gy = np.meshgrid(np.arange(x0, x1, 0.1) + 0.05, np.arange(y0, y1, 0.1) + 0.05)
            gx, gy = gx.ravel(), gy.ravel()
            ins = points_in_polys(gx, gy, u["polys"])
            gx, gy = gx[ins], gy[ins]
            if len(gx) < 20:
                continue
            bad = np.zeros(len(gx), dtype=bool)
            for other, (a, b, c, d2) in boxes.items():
                if other != a3 and not (c < x0 or a > x1 or d2 < y0 or b > y1):
                    bad |= points_in_polys(gx, gy, full[other])
            if bad.mean() > 0.10 and bad.sum() >= 5:
                u["clip"] = True
                print(f"Clip: {u['name']} ({a3}): {bad.mean():.0%} of its area is shown as another country")

    # Internal division boundaries, kept only inside their own country's outline.
    lines: dict[str, list[list[float]]] = defaultdict(list)
    for f in lines_fc["features"]:
        a3 = f["properties"]["ADM0_A3"]
        if a3 not in units:
            continue
        g = f["geometry"]
        if g is None:
            continue
        parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        for part in parts:
            pts = [(round(x, 2), round(y, 2)) for x, y in dp([(float(x), float(y)) for x, y in part], 0.01)]
            pts = [q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]]
            if len(pts) < 2:
                continue
            arr = np.array(pts)
            mid = (arr[:-1] + arr[1:]) / 2
            ok = points_in_polys(mid[:, 0], mid[:, 1], fine[a3])
            run: list[float] = []
            for i, good in enumerate(ok):
                if good:
                    if not run:
                        run = [*pts[i]]
                    run += [*pts[i + 1]]
                elif run:
                    lines[a3].append(run)
                    run = []
            if run:
                lines[a3].append(run)

    index = []
    for c in countries:
        index.append({"kind": "country", "name": c["name"], "a3": c["a3"], "id": c["a3"], "lat": c["lat"],
                      "lon": c["lon"], "r": c["r"], "rank": c["rank"], "country": c["name"], "cont": c["cont"]})
        us = units.get(c["a3"], [])
        if len(us) >= 2:
            c["adm1"] = True
            (OUT / "admin1" / f"{c['a3']}.json").write_text(json.dumps(
                {"a3": c["a3"], "units": us, "lines": lines.get(c["a3"], [])}, separators=(",", ":")))
            for u in us:
                index.append({"kind": "admin1", "name": u["name"], "a3": c["a3"], "id": u["id"], "lat": u["lat"],
                              "lon": u["lon"], "r": u["r"], "rank": u["rank"], "type": u["type"],
                              "country": c["name"], "cont": c["cont"]})

    # Continent label points: derived from member countries' label points (weighted by the square root of
    # their extent), with an angular radius that covers the member countries' label points.
    conts = []
    for name in sorted({c["cont"] for c in countries if not c["cont"].startswith("Seven seas")}):
        mem = [c for c in countries if c["cont"] == name]
        v = sum(unit(c["lat"], c["lon"]) * math.sqrt(max(c["r"], 0.5)) for c in mem)
        v = v / np.linalg.norm(v)
        lat, lon = math.degrees(math.asin(v[2])), math.degrees(math.atan2(v[1], v[0]))
        r = max(angle(v, unit(c["lat"], c["lon"])) + min(c["r"], 15) for c in mem)
        conts.append({"name": name, "lat": round(lat, 2), "lon": round(lon, 2), "r": round(min(r, 75), 2)})

    places = []
    for f in places_fc["features"]:
        p = f["properties"]
        cap = 2 if p["adm0cap"] else (1 if p["featurecla"].startswith("Admin-1 capital") else 0)
        places.append([p["name"], round(p["latitude"], 3), round(p["longitude"], 3), p["scalerank"],
                       p["adm0_a3"], p["adm1name"] or "", int(p["pop_max"] or 0), cap])
    places.sort(key=lambda r: (r[3], -r[6]))

    meta = {"source": "Natural Earth v5.1.2 (public domain), naturalearthdata.com",
            "view": "India's point of view (Natural Earth worldview IND)"}
    borders = land_borders(countries_fc["features"])
    (OUT / "borders.json").write_text(json.dumps({**meta, "borders": borders}, separators=(",", ":")))
    (OUT / "countries.json").write_text(json.dumps({**meta, "continents": conts, "countries": countries},
                                                   separators=(",", ":")))
    (OUT / "index.json").write_text(json.dumps({**meta, "entries": index}, separators=(",", ":")))
    (OUT / "places.json").write_text(json.dumps({**meta, "fields": ["name", "lat", "lon", "scalerank", "a3", "admin1",
                                                                     "pop_max", "capital(2 country,1 admin-1)"],
                                                 "places": places}, separators=(",", ":")))
    total = sum(p.stat().st_size for p in OUT.rglob("*.json"))
    print(f"{len(countries)} countries, {sum(len(u) for u in units.values())} divisions in "
          f"{len(list((OUT / 'admin1').glob('*.json')))} files, {len(places)} places, {total / 1e6:.1f} MB")
    print("Left out (disputed: label point inside another country in this view):", ", ".join(dropped) or "none")


if __name__ == "__main__":
    main(sys.argv[1])
