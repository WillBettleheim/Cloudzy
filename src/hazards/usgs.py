"""USGS earthquakes: GeoJSON summary feeds for recent windows, the FDSN event service for chosen dates.

Feeds (https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php): all_day, 2.5_week, 4.5_month, regenerated
every minute. FDSN (https://earthquake.usgs.gov/fdsnws/event/1/) answers date-range queries for playback.
Fields used exactly as USGS defines them: `mag` and `magType`, depth (the third coordinate, km), `time` and
`updated` (epoch ms), `status` (automatic/reviewed), PAGER `alert` (green/yellow/orange/red: estimated
losses), `tsunami` (1 = the event is in a region where NOAA's tsunami centres evaluate, *not* that a tsunami
occurred), `felt`, `cdi`, `mmi`, `sig`.
"""

from __future__ import annotations

import pandas as pd
import requests

from src.hazards import http
from src.hazards.schema import FeedResult, HazardEvent, utc, valid_coords

FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{feed}.geojson"
FDSN_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
WINDOWS = {"day": ("all_day", 0.0), "week": ("2.5_week", 2.5), "month": ("4.5_month", 4.5)}
FDSN_LIMIT = 4000
PAGER_RANK = {"green": 1, "yellow": 2, "orange": 3, "red": 4}
SEVERITY_DEFINITION = ("Magnitude is the size of the earthquake at its source. PAGER alert (when issued) is USGS's "
                       "estimate of fatalities and economic losses: green, yellow, orange, red.")


def magnitude_rank(mag) -> int:
    if mag is None:
        return 0
    return 1 if mag < 4 else 2 if mag < 6 else 3 if mag < 7 else 4


def parse(payload: dict, now: pd.Timestamp | None = None) -> FeedResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise http.FeedError("USGS response has no 'features' list")
    out = FeedResult(data_time=utc((payload.get("metadata") or {}).get("generated"), "ms"))
    for f in payload["features"]:
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        coords = g.get("coordinates") or []
        ll = valid_coords(coords[1], coords[0]) if len(coords) >= 2 else None
        t = utc(p.get("time"), "ms")
        if ll is None or t is None or not f.get("id"):
            out.warnings.append(f"skipped earthquake {f.get('id')!r}: missing id, time or valid coordinates")
            continue
        if p.get("type") not in (None, "earthquake"):
            continue                            # quarry blasts, explosions, ice quakes: not earthquakes
        mag = p.get("mag")
        mag = float(mag) if isinstance(mag, (int, float)) else None
        depth = float(coords[2]) if len(coords) > 2 and isinstance(coords[2], (int, float)) else None
        alert = p.get("alert") if p.get("alert") in PAGER_RANK else None
        m: dict[str, str] = {}
        if mag is not None:
            m["Magnitude"] = f"{mag:.1f} {p.get('magType') or ''}".strip()
        if depth is not None:
            m["Depth"] = f"{depth:.1f} km"
        m["Review status"] = str(p.get("status") or "not given")
        if alert:
            m["PAGER alert"] = alert.capitalize()
        if p.get("felt"):
            m["Felt reports (DYFI)"] = f"{int(p['felt']):,}"
        if isinstance(p.get("mmi"), (int, float)):
            m["Max. estimated intensity (MMI)"] = f"{p['mmi']:.1f}"
        if p.get("tsunami") == 1:
            m["Tsunami flag"] = "In a region NOAA tsunami centres evaluate (not a tsunami report)"
        title = p.get("title") or f"M {mag} earthquake"
        out.events.append(HazardEvent(
            id=f"usgs:{f['id']}", source="usgs", category="earthquake", kind="observation", title=title,
            lat=ll[0], lon=ll[1], time=t, updated=utc(p.get("updated"), "ms"),
            severity_rank=max(magnitude_rank(mag), PAGER_RANK.get(alert or "", 0)),
            severity_label=(f"M {mag:.1f}" if mag is not None else "Magnitude not available")
                           + (f" · PAGER {alert}" if alert else ""),
            severity_definition=SEVERITY_DEFINITION,
            summary=f"{title}, depth {depth:.0f} km." if depth is not None else title,
            area=p.get("place"), url=p.get("url"), official=True, measurements=m,
            extra={"mag": mag, "depth_km": depth, "status": p.get("status"), "sig": p.get("sig"),
                   "net": p.get("net")}))
    return out


def fetch(window: str = "week", min_mag: float = 0.0, start: pd.Timestamp | None = None,
          end: pd.Timestamp | None = None, session: requests.Session | None = None) -> FeedResult:
    """A summary feed for a recent window, or an FDSN query when `start`/`end` are given."""
    if start is not None and end is not None:
        payload = http.get(FDSN_URL, {"format": "geojson", "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                                      "endtime": end.strftime("%Y-%m-%dT%H:%M:%S"),
                                      "minmagnitude": max(min_mag, 2.5), "orderby": "time", "limit": FDSN_LIMIT},
                           session=session)
        res = parse(payload)
        if len(payload.get("features", [])) >= FDSN_LIMIT:
            res.note = f"Only the {FDSN_LIMIT:,} most recent matching earthquakes were returned; raise the magnitude."
        res.data_time = None                    # an archive query, not a live feed
        return res
    feed, floor = WINDOWS[window]
    res = parse(http.get(FEED_URL.format(feed=feed), session=session))
    if min_mag > floor:
        res.events = [e for e in res.events if (e.extra.get("mag") or 0) >= min_mag]
    return res
