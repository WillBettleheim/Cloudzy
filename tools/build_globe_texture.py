"""Build the Weather Explorer globe's Earth texture from NASA Blue Marble (public domain).

One WMS request to NASA GIBS for an equirectangular (EPSG:4326, -180..180, -90..90) image, which maps
directly onto a sphere's UV coordinates.

Usage:  python tools/build_globe_texture.py [width]      (default 4096; height is width / 2)
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parents[1] / "static" / "vendor" / "earth" / "blue-marble-4096.jpg"
WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"


def main(width: int = 4096) -> None:
    params = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0",
              "LAYERS": "BlueMarble_ShadedRelief_Bathymetry", "CRS": "EPSG:4326", "BBOX": "-90,-180,90,180",
              "WIDTH": width, "HEIGHT": width // 2, "FORMAT": "image/jpeg", "STYLES": ""}
    r = requests.get(WMS, params=params, timeout=120, headers={"User-Agent": "Cloudzy/1.0 (student project)"})
    r.raise_for_status()
    if r.headers.get("content-type") != "image/jpeg":
        raise SystemExit(f"Unexpected response: {r.headers.get('content-type')}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(r.content)
    print(f"{OUT} {len(r.content) / 1e6:.2f} MB")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 4096)
