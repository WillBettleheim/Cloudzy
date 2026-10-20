"""Build the Weather Explorer globe's Earth textures from NASA imagery (public domain).

- Day:   Blue Marble shaded relief and bathymetry  → static/vendor/earth/blue-marble-4096.jpg
- Night: VIIRS Black Marble 2016 (city lights)       → static/vendor/earth/black-marble-4096.jpg

One WMS request to NASA GIBS per image, for an equirectangular (EPSG:4326, -180..180, -90..90) image,
which maps directly onto a sphere's UV coordinates.

Usage:  python tools/build_globe_texture.py [day|night|all] [width]   (default: all 4096; height is width / 2)
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

EARTH = Path(__file__).resolve().parents[1] / "static" / "vendor" / "earth"
WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
TEXTURES = {
    "day": ("BlueMarble_ShadedRelief_Bathymetry", None, "blue-marble-{w}.jpg"),
    "night": ("VIIRS_Black_Marble", "2016-01-01", "black-marble-{w}.jpg"),
}


def fetch(kind: str, width: int = 4096) -> Path:
    layer, time, name = TEXTURES[kind]
    params = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0",
              "LAYERS": layer, "CRS": "EPSG:4326", "BBOX": "-90,-180,90,180",
              "WIDTH": width, "HEIGHT": width // 2, "FORMAT": "image/jpeg", "STYLES": ""}
    if time:
        params["TIME"] = time
    r = requests.get(WMS, params=params, timeout=120, headers={"User-Agent": "Cloudzy/1.0 (student project)"})
    r.raise_for_status()
    if r.headers.get("content-type") != "image/jpeg":
        raise SystemExit(f"Unexpected response for {layer}: {r.headers.get('content-type')}")
    out = EARTH / name.format(w=width)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)
    print(f"{out} {len(r.content) / 1e6:.2f} MB")
    return out


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 4096
    for k in (TEXTURES if which == "all" else [which]):
        fetch(k, w)
