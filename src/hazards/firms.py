"""NASA FIRMS active-fire detections: VIIRS 375 m on NOAA-20, last 24 hours, global.

The open CSV (no key needed) is https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/
J1_VIIRS_C2_Global_24h.csv. Columns: latitude, longitude, acq_date + acq_time (UTC, HHMM), frp (fire radiative
power, MW), confidence (low / nominal / high), daynight (D/N), satellite.

A detection is a thermal anomaly in one pixel during a satellite overpass. It is **not** a confirmed wildfire:
industrial heat sources, gas flares, volcanoes and agricultural burning are detected too, and clouds or smoke
can hide fires. The globe draws detections as their own layer and never calls them fires or incidents.

The ~100,000+ detections are written once to a compact static file (columns, rounded to the 375 m pixel)
instead of being sent with every page run.
"""

from __future__ import annotations

import csv
import io

import requests

from src.hazards import http
from src.hazards.schema import FeedResult, utc, valid_coords

CSV_URL = ("https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/"
           "J1_VIIRS_C2_Global_24h.csv")
CONFIDENCE = {"l": "low", "n": "nominal", "h": "high", "low": "low", "nominal": "nominal", "high": "high"}
REQUIRED = ("latitude", "longitude", "acq_date", "acq_time")


def parse(text: str) -> FeedResult:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not all(c in reader.fieldnames for c in REQUIRED):
        raise http.FeedError(f"FIRMS CSV is missing columns {REQUIRED}; got {reader.fieldnames}")
    lon, lat, frp, minute, conf, night = [], [], [], [], [], []
    skipped = 0
    newest = None
    for row in reader:
        ll = valid_coords(row.get("latitude"), row.get("longitude"))
        hhmm = (row.get("acq_time") or "").zfill(4)
        t = utc(f"{row.get('acq_date')} {hhmm[:2]}:{hhmm[2:]}") if hhmm.isdigit() else None
        if ll is None or t is None:
            skipped += 1
            continue
        try:
            p = round(float(row.get("frp") or "nan"), 1)
        except ValueError:
            p = float("nan")
        lat.append(round(ll[0] * 1000))               # thousandths of a degree (~110 m; pixels are 375 m)
        lon.append(round(ll[1] * 1000))
        frp.append(None if p != p else p)
        minute.append(int(t.timestamp() // 60))
        conf.append(CONFIDENCE.get(str(row.get("confidence") or "").lower(), "unknown")[0])   # l, n, h or u
        night.append(1 if row.get("daynight") == "N" else 0)
        newest = t if newest is None or t > newest else newest
    out = FeedResult(data_time=newest)
    base = min(minute) if minute else 0
    out.layers["fires"] = {"scale": 1000, "base_minute": base, "lon": lon, "lat": lat, "frp": frp,
                           "minute": [m - base for m in minute], "conf": "".join(conf), "night": night,
                           "satellite": "NOAA-20 VIIRS 375 m"}
    out.note = f"{len(lat):,} detections in the last 24 hours" + (f" ({skipped} malformed rows skipped)"
                                                                  if skipped else "")
    if skipped:
        out.warnings.append(f"{skipped} FIRMS rows had no valid position or time")
    return out


def fetch(session: requests.Session | None = None) -> FeedResult:
    return parse(http.get(CSV_URL, kind="text", session=session, timeout=40))
