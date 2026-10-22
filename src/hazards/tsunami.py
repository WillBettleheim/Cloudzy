"""NOAA tsunami bulletins from the U.S. Tsunami Warning Centers (Atom feeds at https://www.tsunami.gov/).

- NTWC (Palmer, Alaska): https://www.tsunami.gov/events/xml/PAAQAtom.xml
- PTWC (Honolulu): https://www.tsunami.gov/events/xml/PHEBAtom.xml
Each entry is a bulletin with a category, exactly as the centre issues it: Warning, Advisory, Watch, Threat,
or Information (an earthquake happened, with no tsunami threat to the area the centre covers). Bulletins are
official; their coverage is the centres' areas of responsibility, not the whole world.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

import pandas as pd
import requests

from src.hazards import http
from src.hazards.schema import FeedResult, HazardEvent, utc, valid_coords

FEEDS = {"NTWC": "https://www.tsunami.gov/events/xml/PAAQAtom.xml",
         "PTWC": "https://www.tsunami.gov/events/xml/PHEBAtom.xml"}
CATEGORY_RANK = {"information": 1, "advisory": 2, "watch": 3, "threat": 4, "warning": 4}
SEVERITY_DEFINITION = ("Tsunami bulletin category: Warning (dangerous tsunami expected), Threat (hazardous waves "
                       "possible, international), Advisory (strong currents), Watch (danger level not yet known), "
                       "Information (no threat, or a threat elsewhere).")
CURRENT_HOURS = 24
NS = {"a": "http://www.w3.org/2005/Atom", "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#"}


def _field(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, flags=re.I)
    return m.group(1).strip() if m else None


def parse(xml_text: str, centre: str, now: pd.Timestamp) -> FeedResult:
    try:
        root = safe_fromstring(xml_text)          # no entity expansion or external entities
    except (ET.ParseError, DefusedXmlException) as exc:
        raise http.FeedError(f"{centre} feed is not valid XML ({exc})") from exc
    out = FeedResult(data_time=utc((root.findtext("a:updated", namespaces=NS) or "").strip()))
    for entry in root.findall("a:entry", NS):
        ll = valid_coords(entry.findtext("geo:lat", namespaces=NS), entry.findtext("geo:long", namespaces=NS))
        t = utc((entry.findtext("a:updated", namespaces=NS) or "").strip())
        summary_el = entry.find("a:summary", NS)
        text = " ".join(" ".join(summary_el.itertext()).split()) if summary_el is not None else ""
        category = _field(text, r"Category:\s*(\w+)") or "Unknown"
        if ll is None or t is None:
            out.warnings.append(f"skipped {centre} bulletin without position or time")
            continue
        region = (entry.findtext("a:title", namespaces=NS) or "").strip()
        link = next((lk.get("href") for lk in entry.findall("a:link", NS) if lk.get("href")), None)
        note = _field(text, r"Note:\s*\*?\s*(.*?)\s*(?:Definition:|$)")
        magnitude = _field(text, r"Preliminary Magnitude:\s*([\d.]+\s*\(\w+\))")
        m = {"Bulletin category": category, "Issued by": centre}
        if magnitude:
            m["Preliminary magnitude"] = magnitude
        out.events.append(HazardEvent(
            id=f"tsunami:{centre}:{t.isoformat()}:{region}", source="tsunami", category="tsunami", kind="warning",
            title=f"Tsunami {category.lower()}: {region}", lat=ll[0], lon=ll[1], time=t, updated=t,
            severity_rank=CATEGORY_RANK.get(category.lower(), 0), severity_label=f"{category} ({centre})",
            severity_definition=SEVERITY_DEFINITION, summary=note or "", area=region,
            url=link or "https://www.tsunami.gov/", official=True, measurements=m,
            current=now - t <= pd.Timedelta(hours=CURRENT_HOURS)))
    return out


def fetch(session: requests.Session | None = None, now: pd.Timestamp | None = None) -> FeedResult:
    now = now or pd.Timestamp.now(tz="UTC")
    out = FeedResult()
    errors = []
    for centre, url in FEEDS.items():
        try:
            part = parse(http.get(url, kind="text", session=session), centre, now)
        except http.FeedError as exc:
            errors.append(f"{centre}: {exc}")
            continue
        out.events += part.events
        out.warnings += part.warnings
        if part.data_time and (out.data_time is None or part.data_time > out.data_time):
            out.data_time = part.data_time
    if errors and not out.events and len(errors) == len(FEEDS):
        raise http.FeedError("; ".join(errors))
    out.warnings += errors
    return out
