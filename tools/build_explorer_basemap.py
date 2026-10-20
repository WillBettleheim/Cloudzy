"""Build the Weather Explorer's basemap file from Natural Earth (public domain).

Input: Natural Earth v5.1.2 GeoJSON (https://github.com/nvkelso/natural-earth-vector):
  ne_50m_coastline, ne_50m_admin_0_boundary_lines_land, ne_110m_populated_places_simple.
Output: static/vendor/naturalearth/basemap.json with coordinates rounded to 0.01° and only the fields the map uses.

Usage:  python tools/build_explorer_basemap.py <folder with the three .geojson files>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "static" / "vendor" / "naturalearth" / "basemap.json"


def lines(path: Path) -> list[list[list[float]]]:
    out = []
    for f in json.loads(path.read_text())["features"]:
        g = f["geometry"]
        parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        for part in parts:
            pts = [[round(x, 2), round(y, 2)] for x, y in part]
            dedup = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
            if len(dedup) > 1:
                out.append(dedup)
    return out


def cities(path: Path) -> list[list]:
    rows = []
    for f in json.loads(path.read_text())["features"]:
        p, (x, y) = f["properties"], f["geometry"]["coordinates"]
        rows.append([p["name"], round(y, 2), round(x, 2), int(p.get("scalerank", 9))])
    return sorted(rows, key=lambda r: r[3])


def main(src: str) -> None:
    d = Path(src)
    data = {"source": "Natural Earth v5.1.2 (public domain), naturalearthdata.com",
            "coast": lines(d / "ne_50m_coastline.geojson"),
            "borders": lines(d / "ne_50m_admin_0_boundary_lines_land.geojson"),
            "cities": cities(d / "ne_110m_populated_places_simple.geojson")}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    print(f"{OUT} {OUT.stat().st_size / 1e6:.2f} MB: {len(data['coast'])} coast lines, "
          f"{len(data['borders'])} borders, {len(data['cities'])} cities")


if __name__ == "__main__":
    main(sys.argv[1])
