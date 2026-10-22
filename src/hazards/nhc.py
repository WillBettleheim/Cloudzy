"""NOAA National Hurricane Center: active tropical cyclones in the Atlantic and eastern/central North Pacific.

- `CurrentStorms.json` lists active storms with the latest advisory: position, classification, intensity (kt,
  1-minute sustained wind as NHC defines it), pressure (mb), movement, and links to products.
- Per storm, three KMZ files from the same advisory: the forecast cone ("the entire track can be expected to
  remain within the cone roughly 60-70% of the time", NHC), the forecast track points (each "N hr Forecast,
  Valid at …"), and the best track (observed positions every 6 hours, ATCF date-time groups).

Observed positions (best track and the current advisory position) and forecast positions are kept apart; a
forecast track is never shown as a path the storm will certainly take.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

import pandas as pd
import requests

from src.hazards import http
from src.hazards.schema import FeedResult, HazardEvent, utc, valid_coords

CURRENT_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
CLASSIFICATION = {"TD": "Tropical Depression", "TS": "Tropical Storm", "HU": "Hurricane", "MH": "Major Hurricane",
                  "STD": "Subtropical Depression", "STS": "Subtropical Storm", "PTC": "Post-Tropical Cyclone",
                  "PC": "Potential Tropical Cyclone", "TY": "Typhoon"}
CONE_NOTE = ("NHC: the cone is the probable track of the centre. The whole track stays inside it roughly 60-70% "
             "of the time, and impacts can extend well beyond it.")
SEVERITY_DEFINITION = ("Saffir-Simpson scale from NHC's maximum sustained wind (1-minute average): tropical storm "
                       "34-63 kt, hurricane 64 kt and above, major hurricane 96 kt and above.")
TZ_OFFSETS = {"UTC": 0, "GMT": 0, "AST": -4, "ADT": -3, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7,
              "MDT": -6, "PST": -8, "PDT": -7, "HST": -10, "AKST": -9, "AKDT": -8, "ChST": 10}
_KML = re.compile(r"\{.*?\}")


def wind_rank(kt) -> int:
    if kt is None:
        return 0
    return 1 if kt < 34 else 2 if kt < 64 else 3 if kt < 96 else 4


MAX_KML_BYTES = 25_000_000   # uncompressed; NHC cones and tracks are a few hundred kB


def _kml(kmz: bytes) -> ET.Element:
    """The KML inside an NHC KMZ, size-checked before unzipping and parsed without entity expansion."""
    with zipfile.ZipFile(io.BytesIO(kmz)) as z:
        name = next(n for n in z.namelist() if n.endswith(".kml"))
        if z.getinfo(name).file_size > MAX_KML_BYTES:
            raise http.FeedError(f"KML too large to unpack ({z.getinfo(name).file_size:,} bytes)")
        return safe_fromstring(z.read(name))


def _local(tag: str) -> str:
    return _KML.sub("", tag)


def _iter(root: ET.Element, name: str):
    return (e for e in root.iter() if _local(e.tag) == name)


def _coords(text: str) -> list[list[float]]:
    out = []
    for chunk in (text or "").split():
        parts = chunk.split(",")
        if len(parts) >= 2:
            out.append([round(float(parts[0]), 4), round(float(parts[1]), 4)])
    return out


def parse_cone(kmz: bytes) -> dict | None:
    polys = [[_coords(c.text or "")] for poly in _iter(_kml(kmz), "Polygon")
             for c in _iter(poly, "coordinates")]
    polys = [p for p in polys if len(p[0]) >= 4]
    if not polys:
        return None
    return {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1 else \
        {"type": "MultiPolygon", "coordinates": polys}


def parse_valid_time(text: str) -> pd.Timestamp | None:
    """'12:00 AM CST October 22, 2026' → UTC."""
    m = re.search(r"(\d{1,2}:\d{2} [AP]M) ([A-Za-z]+) ([A-Za-z]+ \d{1,2}, \d{4})", text or "")
    if not m or m.group(2) not in TZ_OFFSETS:
        return None
    try:
        local = pd.Timestamp(f"{m.group(3)} {m.group(1)}")
    except ValueError:
        return None
    return (local - pd.Timedelta(hours=TZ_OFFSETS[m.group(2)])).tz_localize("UTC")


def parse_forecast_points(kmz: bytes) -> list[dict]:
    pts = []
    for pm in _iter(_kml(kmz), "Placemark"):
        point = next(_iter(pm, "Point"), None)
        desc = next(_iter(pm, "description"), None)
        text = " ".join(re.sub(r"<[^>]+>", " ", (desc.text or "") if desc is not None else "").split())
        hours = re.search(r"(\d+) hr Forecast", text)
        if point is None or not hours:
            continue
        c = _coords(next(_iter(point, "coordinates")).text or "")
        wind = re.search(r"Maximum Wind: (\d+) knots", text)
        found = re.search(r"Valid at:\s*(.*?)\s*(?:Location|$)", text)
        valid = parse_valid_time(found.group(1)) if found else None
        if c and valid is not None:
            pts.append({"lon": c[0][0], "lat": c[0][1], "time": valid, "status": "forecast",
                        "hours": int(hours.group(1)), "wind_kt": int(wind.group(1)) if wind else None})
    return sorted(pts, key=lambda p: p["time"])


def parse_best_track(kmz: bytes) -> list[dict]:
    pts = []
    for pm in _iter(_kml(kmz), "Placemark"):
        fields = {_local(e.tag): (e.text or "").strip() for e in pm.iter()}
        dtg = fields.get("atcfdtg")
        ll = valid_coords(fields.get("lat"), fields.get("lon"))
        if not dtg or ll is None:
            continue
        t = utc(pd.to_datetime(dtg, format="%Y%m%d%H", errors="coerce"))
        if t is None:
            continue
        kt = fields.get("intensity")
        pts.append({"lon": ll[1], "lat": ll[0], "time": t, "status": "observed", "type": fields.get("stormType"),
                    "wind_kt": int(kt) if kt and kt.isdigit() else None})
    return sorted(pts, key=lambda p: p["time"])


def parse_current(payload: dict) -> FeedResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("activeStorms"), list):
        raise http.FeedError("NHC response has no 'activeStorms' list")
    out = FeedResult()
    for s in payload["activeStorms"]:
        ll = valid_coords(s.get("latitudeNumeric"), s.get("longitudeNumeric"))
        if ll is None or not s.get("id"):
            out.warnings.append(f"skipped NHC storm {s.get('id')!r}: no position")
            continue
        try:
            kt = int(s.get("intensity"))
        except (TypeError, ValueError):
            kt = None
        try:
            mb = int(s.get("pressure"))
        except (TypeError, ValueError):
            mb = None
        cls = CLASSIFICATION.get(s.get("classification"), s.get("classification") or "Tropical cyclone")
        label = "Major Hurricane" if kt is not None and kt >= 96 and s.get("classification") == "HU" else cls
        t = utc(s.get("lastUpdate"))
        m = {"Classification (NHC)": label}
        if kt is not None:
            m["Maximum sustained wind"] = f"{kt} kt ({kt * 1.852:.0f} km/h), 1-minute average"
        if mb is not None:
            m["Minimum central pressure"] = f"{mb} mb"
        if s.get("movementSpeed") is not None and s.get("movementDir") is not None:
            m["Movement"] = f"toward {s['movementDir']}° at {s['movementSpeed']} mph"
        adv = s.get("publicAdvisory") or {}
        if adv.get("advNum"):
            m["Advisory"] = f"#{adv['advNum']}, issued {pd.Timestamp(adv['issuance']):%d %b %H:%M} UTC" \
                if adv.get("issuance") else f"#{adv['advNum']}"
        name = f"{label} {s.get('name') or ''}".strip()
        out.events.append(HazardEvent(
            id=f"nhc:{s['id']}", source="nhc", category="cyclone", kind="warning", title=name, lat=ll[0], lon=ll[1],
            time=t, updated=t, severity_rank=wind_rank(kt), severity_label=label,
            severity_definition=SEVERITY_DEFINITION,
            summary=f"{name}: position at the latest NHC advisory; see the public advisory for watches and warnings.",
            area=f"{s.get('binNumber')} basin" if s.get("binNumber") else None,
            url=(s.get("forecastGraphics") or {}).get("url") or adv.get("url"), official=True, measurements=m,
            extra={"wind_kt": kt, "pressure_mb": mb, "name": s.get("name"), "advisory_url": adv.get("url"),
                   "cone_kmz": (s.get("trackCone") or {}).get("kmzFile"),
                   "track_kmz": (s.get("forecastTrack") or {}).get("kmzFile"),
                   "best_kmz": (s.get("bestTrackGIS") or {}).get("kmzFile")}))
        if t and (out.data_time is None or t > out.data_time):
            out.data_time = t
    return out


def fetch(session: requests.Session | None = None) -> FeedResult:
    out = parse_current(http.get(CURRENT_URL, session=session))
    if not out.events:
        out.note = "No active storms in the Atlantic or eastern/central North Pacific."
    for e in out.events:
        track: list[dict] = []
        for key, parser in (("best_kmz", parse_best_track), ("track_kmz", parse_forecast_points)):
            if e.extra.get(key):
                try:
                    track += parser(http.get(e.extra[key], kind="bytes", session=session))
                except (http.FeedError, zipfile.BadZipFile, ET.ParseError, DefusedXmlException, StopIteration) as exc:
                    out.warnings.append(f"{e.title}: {key.removesuffix('_kmz')} track unavailable ({exc})")
        if e.time is not None:
            track.append({"lon": e.lon, "lat": e.lat, "time": e.time, "status": "current",
                          "wind_kt": e.extra.get("wind_kt")})
        track.sort(key=lambda p: p["time"])
        e.extra["track"] = [{**p, "time": p["time"].isoformat()} for p in track]
        if e.extra.get("cone_kmz"):
            try:
                e.geometry = parse_cone(http.get(e.extra["cone_kmz"], kind="bytes", session=session))
                e.extra["cone_note"] = CONE_NOTE
            except (http.FeedError, zipfile.BadZipFile, ET.ParseError, DefusedXmlException, StopIteration) as exc:
                out.warnings.append(f"{e.title}: cone unavailable ({exc})")
    return out
