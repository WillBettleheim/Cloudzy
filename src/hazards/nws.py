"""NOAA National Weather Service active alerts (United States only), from https://api.weather.gov/alerts/active.

Alerts are CAP messages. Fields are used as the NWS defines them: `event` (e.g. "Flash Flood Warning"),
`severity` (Extreme, Severe, Moderate, Minor, Unknown), `certainty`, `urgency`, `onset`, `expires`,
`headline`, `description`, `instruction`, `areaDesc`, `senderName`.

Storm-based warnings (tornado, severe thunderstorm, flash flood…) come with a polygon and are drawn. Most other
alerts are issued for forecast zones without a polygon; they are listed with their area description and
counted, but not drawn (fetching every zone's outline would cost hundreds of requests).
Warning, watch, advisory and statement are different products; the event name says which.
"""

from __future__ import annotations

import requests

from src.hazards import http
from src.hazards.schema import FeedResult, HazardEvent, utc

ALERTS_URL = "https://api.weather.gov/alerts/active"
CAP_RANK = {"extreme": 4, "severe": 3, "moderate": 2, "minor": 1, "unknown": 0}
SEVERITY_DEFINITION = ("CAP severity set by the NWS: Extreme (extraordinary threat to life or property), Severe "
                       "(significant threat), Moderate (possible threat), Minor (minimal threat).")
MARINE = ("Small Craft", "Gale", "Hurricane Force Wind", "Special Marine", "Marine Weather", "Hazardous Seas",
          "Heavy Freezing Spray", "Low Water", "Brisk Wind")
# Marine products whose names are also endings of land products ("Winter Storm Warning", "Dust Storm Warning"):
# matched only as the whole event name.
MARINE_EXACT = {"Storm Warning", "Storm Watch"}
# First match wins, so the more specific names come first.
CATEGORY_RULES = (
    ("tsunami", ("Tsunami",)),
    ("cyclone", ("Hurricane Warning", "Hurricane Watch", "Tropical Storm", "Typhoon", "Storm Surge",
                 "Hurricane Local Statement", "Tropical Cyclone")),
    ("flood", ("Flood", "Hydrologic")),
    ("wildfire", ("Red Flag", "Fire Weather", "Fire Warning")),
    ("severe", ("Tornado", "Severe Thunderstorm", "Extreme Wind", "High Wind", "Wind Advisory", "Dust",
                "Severe Weather Statement")),
    ("temperature", ("Heat", "Extreme Cold", "Cold Weather", "Wind Chill", "Freeze", "Frost")),
    ("winter", ("Winter", "Blizzard", "Ice Storm", "Snow", "Lake Effect", "Avalanche", "Freezing Rain",
                "Freezing Fog")),
)


def is_marine(event: str) -> bool:
    return event in MARINE_EXACT or any(m in event for m in MARINE)


def category_for(event: str) -> str:
    if is_marine(event):
        return "other"
    for cat, names in CATEGORY_RULES:
        if any(n in event for n in names):
            return cat
    return "other"


def product_type(event: str) -> str:
    for word in ("Warning", "Watch", "Advisory", "Statement", "Emergency", "Outlook"):
        if word in event:
            return word
    return "Alert"


def centroid(geometry: dict | None) -> tuple[float, float] | None:
    """Mean of the outer-ring vertices (fine for the small storm-based polygons NWS issues)."""
    if not geometry:
        return None
    coords = geometry.get("coordinates") or []
    try:
        ring = coords[0] if geometry["type"] == "Polygon" else coords[0][0]
    except (KeyError, IndexError, TypeError):
        return None
    pts = [c for c in ring if isinstance(c, list) and len(c) >= 2]
    if not pts:
        return None
    return sum(c[1] for c in pts) / len(pts), sum(c[0] for c in pts) / len(pts)


def parse(payload: dict) -> FeedResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise http.FeedError("NWS response has no 'features' list")
    out = FeedResult(data_time=utc(payload.get("updated")))
    drawn = 0
    for f in payload["features"]:
        p = f.get("properties") or {}
        event = str(p.get("event") or "")
        if not p.get("id") or not event or p.get("status") not in (None, "Actual"):
            continue                              # exercises, tests and drafts are never shown
        geom = f.get("geometry") if (f.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon") else None
        c = centroid(geom)
        drawn += c is not None
        sev = str(p.get("severity") or "Unknown")
        m = {"Product": product_type(event), "CAP severity": sev, "Certainty": str(p.get("certainty") or "—"),
             "Urgency": str(p.get("urgency") or "—"), "Issued by": str(p.get("senderName") or "NWS")}
        if p.get("onset"):
            m["Onset"] = str(p["onset"])
        if p.get("expires"):
            m["Expires"] = str(p["expires"])
        if geom is None:
            m["Map"] = "Issued for forecast zones without a polygon; not drawn"
        summary = str(p.get("headline") or event)
        if p.get("instruction"):
            summary += " Instruction from the NWS: " + " ".join(str(p["instruction"]).split())[:600]
        out.events.append(HazardEvent(
            id=f"nws:{p['id']}", source="nws", category=category_for(event), kind="warning", title=event,
            lat=c[0] if c else None, lon=c[1] if c else None,
            time=utc(p.get("onset") or p.get("effective") or p.get("sent")), updated=utc(p.get("sent")),
            severity_rank=CAP_RANK.get(sev.lower(), 0), severity_label=f"{sev} (CAP)",
            severity_definition=SEVERITY_DEFINITION, summary=summary, country="United States",
            area=p.get("areaDesc"), url=p.get("@id") or f.get("id"), official=True, measurements=m,
            geometry=geom, ends=utc(p.get("ends") or p.get("expires")),
            extra={"description": " ".join(str(p.get("description") or "").split())[:1500],
                   "product": product_type(event), "marine": is_marine(event)}))
    out.note = f"{drawn} of {len(out.events)} active alerts have a polygon and are drawn; the rest are listed."
    return out


def fetch(session: requests.Session | None = None) -> FeedResult:
    return parse(http.get(ALERTS_URL, {"status": "actual"}, session=session,
                          headers={"Accept": "application/geo+json"}))


def point_page(lat: float, lon: float) -> str:
    """The NWS forecast page for a point, which lists the alerts in force there."""
    return f"https://forecast.weather.gov/MapClick.php?lat={lat:.4f}&lon={lon:.4f}"
