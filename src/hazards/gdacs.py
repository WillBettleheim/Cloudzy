"""GDACS: global multi-hazard events (earthquakes, tropical cyclones, floods, volcanoes, droughts, forest fires).

GDACS (a UN–European Commission framework run with the JRC) publishes events through
https://www.gdacs.org/gdacsapi/. Its alert levels (Green, Orange, Red) are **automatic estimates of
humanitarian impact**, not official warnings; GDACS's own terms say notifications aren't reviewed before issue
and mustn't replace official alerts. Every GDACS record here is labelled that way.

- Event list: `events/geteventlist/SEARCH`, 100 per page. Each type is queried separately (so dozens of fire
  events can't crowd out a cyclone), up to MAX_PAGES pages.
- Geometry: `polygons/getgeometry` per event. Fetched up front for current tropical cyclones (track, forecast
  points and uncertainty cone) and on demand for other events when one is selected.
- Tropical cyclone track points carry "dd/mm HH:MM UTC" labels. Points after the latest advisory time (the
  episode's `todate`) are **forecast** positions; earlier ones are **observed** (best-track) positions.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from src.hazards import http
from src.hazards.schema import FeedResult, HazardEvent, utc, valid_coords

LIST_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
GEOMETRY_URL = "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry"
TYPES = {"EQ": "earthquake", "TC": "cyclone", "FL": "flood", "VO": "volcano", "DR": "drought", "WF": "wildfire"}
PAGE_SIZE = 100
MAX_PAGES = 3
ALERT_RANK = {"green": 1, "orange": 3, "red": 4}
SEVERITY_DEFINITION = ("GDACS alert level: an automatic estimate of potential humanitarian impact from the hazard's "
                       "size and the exposed population and vulnerability (Green, Orange, Red). Not an official "
                       "warning.")
LOOKBACK_DAYS = {"DR": 365}                    # droughts start months before they're current
# USGS already supplies every located earthquake, so GDACS earthquakes are only needed for their impact alerts.
LEVELS = {"EQ": "orange;red"}
_TRACK_LABEL = re.compile(r"^(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2})")


def parse_list(payload: dict) -> FeedResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise http.FeedError("GDACS response has no 'features' list")
    out = FeedResult()
    for f in payload["features"]:
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        cat = TYPES.get(str(p.get("eventtype")))
        coords = g.get("coordinates") or []
        ll = valid_coords(coords[1], coords[0]) if g.get("type") == "Point" and len(coords) >= 2 else None
        if cat is None or ll is None or p.get("eventid") is None:
            out.warnings.append(f"skipped GDACS record {p.get('eventtype')}/{p.get('eventid')}: unknown type or "
                                "no point location")
            continue
        level = str(p.get("alertlevel") or "").lower()
        sev = p.get("severitydata") or {}
        urls = p.get("url") or {}
        m = {"GDACS alert level": f"{level.capitalize() or 'Not given'} (automatic impact estimate)"}
        if sev.get("severitytext"):
            m["Hazard size (GDACS)"] = str(sev["severitytext"]).strip()
        if p.get("source"):
            m["Data source"] = str(p["source"]) + (" (flood model)" if p["source"] == "GLOFAS" else "")
        countries = ", ".join(c.get("countryname", "") for c in p.get("affectedcountries") or [] if c.get("countryname"))
        start, end = utc(p.get("fromdate")), utc(p.get("todate"))
        modified = utc(p.get("datemodified"))
        current = str(p.get("iscurrent")).lower() == "true"
        out.events.append(HazardEvent(
            id=f"gdacs:{p['eventtype']}{p['eventid']}", source="gdacs", category=cat, kind="impact_estimate",
            title=p.get("name") or p.get("description") or f"GDACS {cat}", lat=ll[0], lon=ll[1], time=start,
            updated=modified, severity_rank=ALERT_RANK.get(level, 0),
            severity_label=f"{level.capitalize()} alert (GDACS)" if level else "Not rated",
            severity_definition=SEVERITY_DEFINITION,
            summary=str(p.get("htmldescription") or p.get("description") or "").strip(),
            country=countries or p.get("country"), url=urls.get("report"), official=False, measurements=m,
            ends=end, current=current,
            extra={"gdacs_type": p["eventtype"], "eventid": p["eventid"], "episodeid": p.get("episodeid"),
                   "geometry_url": urls.get("geometry"), "source": p.get("source"),
                   "severity_value": sev.get("severity"), "severity_unit": sev.get("severityunit")}))
        if modified and (out.data_time is None or modified > out.data_time):
            out.data_time = modified
    return out


def _ring_centroid(coords: list) -> tuple[float, float] | None:
    ring = coords[0] if coords and isinstance(coords[0], list) and coords[0] and isinstance(coords[0][0], list) else coords
    pts = [(c[0], c[1]) for c in ring if isinstance(c, list) and len(c) >= 2]
    if not pts:
        return None
    return sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)


def track_time(label: str, reference: pd.Timestamp) -> pd.Timestamp | None:
    """'21/10 18:00 UTC' → a UTC time in the year of `reference` (the next year across New Year)."""
    m = _TRACK_LABEL.match(label or "")
    if not m:
        return None
    day, month, hour, minute = (int(x) for x in m.groups())
    year = reference.year + (1 if month < reference.month - 6 else 0) - (1 if month > reference.month + 6 else 0)
    try:
        return pd.Timestamp(year=year, month=month, day=day, hour=hour, minute=minute, tz="UTC")
    except ValueError:
        return None


def parse_cyclone_geometry(payload: dict, advisory_time: pd.Timestamp) -> dict:
    """Track points (observed/forecast), the uncertainty cone and wind-exposure areas from GDACS geometry."""
    points, cone, wind = [], None, []
    for f in (payload or {}).get("features", []):
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        cls, label = str(p.get("Class") or ""), str(p.get("polygonlabel") or "")
        if cls.startswith("Point_Polygon_Point"):
            c = _ring_centroid(g.get("coordinates") or [])
            t = track_time(label, advisory_time)
            if c and t is not None:
                points.append({"lon": round(c[0], 3), "lat": round(c[1], 3), "time": t,
                               "status": "forecast" if t > advisory_time else "observed"})
        elif cls == "Poly_Cones":
            cone = g
        elif cls.startswith("Poly_") and label.endswith("km/h"):
            wind.append({"label": f"Winds of {label} or more (GDACS estimate)", "geometry": g,
                         "level": cls.removeprefix("Poly_").lower()})
    points.sort(key=lambda x: x["time"])
    return {"points": points, "cone": cone, "wind": wind}


def _fetch_type(code: str, days: int, now: pd.Timestamp, session: requests.Session | None) -> FeedResult:
    start = now - pd.Timedelta(days=max(days, LOOKBACK_DAYS.get(code, 0)))
    out = FeedResult()
    for page in range(1, MAX_PAGES + 1):
        payload = http.get(LIST_URL, {"eventlist": code, "fromdate": start.strftime("%Y-%m-%d"),
                                      "todate": now.strftime("%Y-%m-%d"),
                                      "alertlevel": LEVELS.get(code, "green;orange;red"),
                                      "pagesize": PAGE_SIZE, "pagenumber": page}, session=session)
        if payload is None:                           # HTTP 204: no events of this type in the period
            break
        part = parse_list(payload)
        out.events += part.events
        out.warnings += part.warnings
        if part.data_time and (out.data_time is None or part.data_time > out.data_time):
            out.data_time = part.data_time
        if len(payload.get("features", [])) < PAGE_SIZE:
            break
    else:
        out.warnings.append(f"GDACS {code}: more than {MAX_PAGES * PAGE_SIZE} events; the oldest were not loaded")
    return out


def fetch(days: int = 30, session: requests.Session | None = None, now: pd.Timestamp | None = None) -> FeedResult:
    now = now or pd.Timestamp.now(tz="UTC")
    out = FeedResult()
    seen: set[str] = set()
    with ThreadPoolExecutor(max_workers=len(TYPES)) as pool:       # one request stream per hazard type
        parts = list(pool.map(lambda c: _fetch_type(c, days, now, session), TYPES))
    for part in parts:
        for e in part.events:
            if e.id not in seen:
                seen.add(e.id)
                out.events.append(e)
        out.warnings += part.warnings
        if part.data_time and (out.data_time is None or part.data_time > out.data_time):
            out.data_time = part.data_time
    out.events = [e for e in out.events if e.category != "drought" or e.current]
    # Current cyclones: attach track, forecast points and cone.
    for e in out.events:
        if e.category == "cyclone" and e.current and e.extra.get("geometry_url"):
            try:
                geom = parse_cyclone_geometry(http.get(e.extra["geometry_url"], session=session),
                                              e.ends or e.updated or now)
            except http.FeedError as exc:
                out.warnings.append(f"{e.title}: track unavailable ({exc})")
                continue
            e.extra["track"] = [{**pt, "time": pt["time"].isoformat()} for pt in geom["points"]]
            e.extra["wind_areas"] = geom["wind"]
            e.geometry = geom["cone"]
            e.measurements["Track"] = (f"{sum(p['status'] == 'observed' for p in geom['points'])} observed and "
                                       f"{sum(p['status'] == 'forecast' for p in geom['points'])} forecast positions")
    return out


def event_geometry(geometry_url: str, session: requests.Session | None = None) -> dict | None:
    """Polygons for one event (burnt area, affected area…), loaded only when that event is selected."""
    payload = http.get(geometry_url, session=session)
    polys = [f["geometry"] for f in payload.get("features", [])
             if (f.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon")
             and not str((f.get("properties") or {}).get("Class", "")).startswith("Point")]
    if not polys:
        return None
    return {"type": "GeometryCollection", "geometries": polys}
