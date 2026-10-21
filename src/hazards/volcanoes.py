"""Volcanoes: official U.S. alert levels (USGS) and the global Holocene volcano catalogue (Smithsonian GVP).

- USGS Volcano Hazards Program, elevated volcanoes: https://volcanoes.usgs.gov/vsc/api/volcanoApi/elevated.
  Each record has the observatory's ground-based Volcano Alert Level (NORMAL, ADVISORY, WATCH, WARNING) and
  aviation colour code (GREEN, YELLOW, ORANGE, RED), with a link to the notice. U.S. volcanoes only.
- Smithsonian Global Volcanism Program, Volcanoes of the World (WFS): ~1,200 Holocene volcanoes with their
  last known eruption year. A reference catalogue, drawn as a context layer and never as current activity.
Global eruption reports come from GDACS (src.hazards.gdacs).
"""

from __future__ import annotations

import requests

from src.hazards import http
from src.hazards.schema import FeedResult, HazardEvent, utc, valid_coords

USGS_URL = "https://volcanoes.usgs.gov/vsc/api/volcanoApi/elevated"
GVP_URL = "https://webservices.volcano.si.edu/geoserver/GVP-VOTW/ows"
GVP_PAGE = "https://volcano.si.edu/volcano.cfm?vn={vnum}"
LEVEL_RANK = {"NORMAL": 1, "ADVISORY": 2, "WATCH": 3, "WARNING": 4}
COLOR_RANK = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4}
SEVERITY_DEFINITION = ("USGS Volcano Alert Level (ground hazards): Normal, Advisory (elevated unrest), Watch "
                       "(heightened unrest or a minor eruption), Warning (hazardous eruption under way or imminent). "
                       "Aviation colour code: Green, Yellow, Orange, Red.")


def parse_usgs(payload: list) -> FeedResult:
    if not isinstance(payload, list):
        raise http.FeedError("USGS volcano response is not a list")
    out = FeedResult()
    for v in payload:
        ll = valid_coords(v.get("lat"), v.get("long"))
        if ll is None or not v.get("vName"):
            out.warnings.append(f"skipped volcano notice {v.get('noticeId')!r}: no position or name")
            continue
        level, color = str(v.get("alertLevel") or "").upper(), str(v.get("colorCode") or "").upper()
        t = utc(v.get("sentUtc"))
        m = {"Volcano Alert Level": level.capitalize() or "—", "Aviation colour code": color.capitalize() or "—",
             "Observatory": str(v.get("obs") or "").upper() or "—"}
        if v.get("alertLevelPrev"):
            m["Previous level"] = f"{str(v['alertLevelPrev']).capitalize()} / {str(v.get('colorCodePrev') or '').capitalize()}"
        if v.get("nvewsThreat"):
            m["USGS threat ranking"] = str(v["nvewsThreat"])
        out.events.append(HazardEvent(
            id=f"usgs_volcano:{v.get('volcanoCd') or v.get('vnum')}", source="usgs_volcano", category="volcano",
            kind="warning", title=f"{v['vName']}: {level.capitalize()} / {color.capitalize()}", lat=ll[0], lon=ll[1],
            time=t, updated=t, severity_rank=max(LEVEL_RANK.get(level, 0), COLOR_RANK.get(color, 0)),
            severity_label=f"{level.capitalize()} / {color.capitalize()}", severity_definition=SEVERITY_DEFINITION,
            summary=str(v.get("noticeSynopsis") or ""), country="United States", url=v.get("noticeUrl"),
            official=True, measurements=m, extra={"vnum": v.get("vnum")}))
        if t and (out.data_time is None or t > out.data_time):
            out.data_time = t
    if not out.events:
        out.note = "No U.S. volcano is above normal."
    return out


def parse_gvp(payload: dict) -> FeedResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise http.FeedError("GVP response has no 'features' list")
    feats = []
    for f in payload["features"]:
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        c = g.get("coordinates") or []
        ll = valid_coords(c[1], c[0]) if len(c) >= 2 else None
        if ll is None or not p.get("Volcano_Name"):
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(ll[1], 4), round(ll[0], 4)]},
                      "properties": {"name": p["Volcano_Name"], "country": p.get("Country"),
                                     "last": p.get("Last_Eruption_Year"), "type": p.get("Primary_Volcano_Type"),
                                     "elev": p.get("Elevation"), "url": GVP_PAGE.format(vnum=p.get("Volcano_Number"))}})
    out = FeedResult()
    out.layers["volcano_catalogue"] = {"type": "FeatureCollection", "features": feats}
    out.note = f"{len(feats):,} Holocene volcanoes (reference catalogue, not current activity)."
    return out


def fetch_usgs(session: requests.Session | None = None) -> FeedResult:
    return parse_usgs(http.get(USGS_URL, session=session))


def fetch_gvp(session: requests.Session | None = None) -> FeedResult:
    return parse_gvp(http.get(GVP_URL, {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": "GVP-VOTW:Smithsonian_VOTW_Holocene_Volcanoes", "outputFormat": "application/json",
        "propertyName": "Volcano_Number,Volcano_Name,Country,Last_Eruption_Year,Primary_Volcano_Type,Elevation,"
                        "GeoLocation"}, session=session, timeout=40))
