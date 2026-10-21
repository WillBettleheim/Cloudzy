"""U.S. Drought Monitor: weekly drought categories D0-D4 (United States only).

Maps are valid on Tuesday and released on Thursday at https://droughtmonitor.unl.edu/data/json/usdm_YYYYMMDD.json.
Cloudzy requests the most recent Tuesday's file and steps back a week at a time until one exists, so the map's
valid date is always known exactly (the "current" file doesn't carry one).

Categories (USDM definitions): D0 abnormally dry, D1 moderate, D2 severe, D3 extreme, D4 exceptional drought.
Polygons (~18 MB) are simplified to 0.02° (about 2 km) for display; the official map is linked.
"""

from __future__ import annotations

import pandas as pd
import requests

from src.hazards import http
from src.hazards.geometry import simplify_geometry
from src.hazards.schema import FeedResult

URL = "https://droughtmonitor.unl.edu/data/json/usdm_{date}.json"
MAP_PAGE = "https://droughtmonitor.unl.edu/CurrentMap.aspx"
CLASSES = {0: "D0 Abnormally dry", 1: "D1 Moderate drought", 2: "D2 Severe drought", 3: "D3 Extreme drought",
           4: "D4 Exceptional drought"}
TOLERANCE_DEG = 0.02
MAX_WEEKS_BACK = 3


def latest_tuesday(now: pd.Timestamp) -> pd.Timestamp:
    day = now.tz_convert("UTC").normalize()
    return day - pd.Timedelta(days=(day.weekday() - 1) % 7)


def parse(payload: dict, valid: pd.Timestamp) -> FeedResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise http.FeedError("USDM response has no 'features' list")
    feats = []
    for f in payload["features"]:
        dm = (f.get("properties") or {}).get("DM")
        if dm not in CLASSES or not f.get("geometry"):
            continue
        g = simplify_geometry(f["geometry"], TOLERANCE_DEG)
        if g:
            feats.append({"type": "Feature", "geometry": g, "properties": {"dm": dm, "label": CLASSES[dm]}})
    out = FeedResult(data_time=valid)
    out.layers["drought"] = {"type": "FeatureCollection", "features": feats, "valid": valid.strftime("%Y-%m-%d")}
    out.note = f"Map valid {valid:%a %d %b %Y} (released the following Thursday); {len(feats)} categories present."
    return out


def fetch(now: pd.Timestamp | None = None, session: requests.Session | None = None) -> FeedResult:
    tuesday = latest_tuesday(now or pd.Timestamp.now(tz="UTC"))
    last_error = None
    for weeks in range(MAX_WEEKS_BACK + 1):
        valid = tuesday - pd.Timedelta(weeks=weeks)
        try:
            payload = http.get(URL.format(date=valid.strftime("%Y%m%d")), session=session, timeout=40, retries=1)
        except http.FeedError as exc:          # not released yet (404) or temporarily unavailable
            last_error = exc
            continue
        return parse(payload, valid)
    raise http.FeedError(f"no U.S. Drought Monitor map found for the last {MAX_WEEKS_BACK + 1} weeks ({last_error})")
