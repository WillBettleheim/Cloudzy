"""The shared hazard-event schema, category definitions and honest freshness labels."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

# --- Categories (the globe's layers) ---------------------------------------------------------------

@dataclass(frozen=True)
class Category:
    key: str
    label: str
    icon: str          # Material Symbols name
    color: str         # restrained accent; always paired with a marker shape and a text label
    shape: str         # marker shape drawn on the globe: circle, triangle, diamond, square, hexagon, ...


CATEGORIES = {c.key: c for c in (
    Category("earthquake", "Earthquakes", "earthquake", "#e8a33d", "circle"),
    Category("cyclone", "Tropical cyclones", "cyclone", "#7ab8ff", "hurricane"),
    Category("flood", "Floods", "flood", "#4fa3d9", "drop"),
    Category("drought", "Drought", "water_drop", "#c99a5b", "square"),
    Category("wildfire", "Wildfires", "local_fire_department", "#ef6a45", "triangle"),
    Category("severe", "Severe storms and wind", "thunderstorm", "#b58cf2", "diamond"),
    Category("temperature", "Extreme heat and cold", "thermostat", "#f0b35b", "hexagon"),
    Category("winter", "Snow, ice and blizzards", "ac_unit", "#bcd5f5", "star"),
    Category("volcano", "Volcanoes", "volcano", "#d9584f", "triangle_down"),
    Category("tsunami", "Tsunamis", "tsunami", "#3fc1c9", "wave"),
    Category("other", "Other official warnings", "warning", "#9aa7bb", "square"),
)}

# What a record *is*. These are never mixed up in the interface.
KINDS = {
    "observation": "Observed",                 # measured or analysed by the source (e.g. a located earthquake)
    "forecast": "Forecast",                    # a prediction (forecast track, GloFAS flood forecast)
    "warning": "Official warning",             # issued by a warning authority (NWS, NHC, tsunami centres, USGS)
    "impact_estimate": "Automatic alert",      # GDACS: an automatic, model-based impact estimate, not official
    "detection": "Satellite detection",        # FIRMS hotspot: not a confirmed fire
    "assessment": "Assessment",                # USDM weekly drought assessment
    "catalogue": "Reference catalogue",        # GVP Holocene volcano list: not current activity
}

# Severity ranks 0-4, one shared scale for sorting and filtering. Each source's own label and definition are
# always shown next to it; the rank is a documented mapping (docs/EARTH_INTELLIGENCE.md), not a new measure.
RANK_LABELS = {0: "Not rated", 1: "Minor", 2: "Moderate", 3: "Severe", 4: "Extreme"}


@dataclass
class HazardEvent:
    id: str                         # "<source>:<source's own id>" — stable across refreshes
    source: str                     # key in src.hazards.sources.SOURCES
    category: str                   # key in CATEGORIES
    kind: str                       # key in KINDS
    title: str
    lat: float | None
    lon: float | None
    time: pd.Timestamp | None       # event time, onset or detection time (UTC)
    updated: pd.Timestamp | None    # the source's last update of this record (UTC)
    severity_rank: int = 0
    severity_label: str = ""        # the source's own words, e.g. "M 6.2", "Orange (GDACS)", "Severe (CAP)"
    severity_definition: str = ""   # what that label means, from the source
    summary: str = ""
    country: str | None = None
    area: str | None = None         # the source's own area description
    url: str | None = None          # the authoritative record
    official: bool = False          # issued by an official warning or monitoring authority
    measurements: dict[str, str] = field(default_factory=dict)   # label → value with unit, exactly as reported
    geometry: dict | None = None    # GeoJSON geometry beyond the point (polygon, track…), when the source has one
    ends: pd.Timestamp | None = None
    current: bool = True            # the source says it's ongoing/active
    related: list[str] = field(default_factory=list)   # ids of the same event in other sources
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.url = safe_link(self.url)       # links come from the feeds: only plain web links are kept

    def to_json(self) -> dict:
        d = asdict(self)
        for k in ("time", "updated", "ends"):
            d[k] = iso(getattr(self, k))
        return d


@dataclass
class FeedResult:
    """One provider's parsed response."""
    events: list[HazardEvent] = field(default_factory=list)
    layers: dict[str, Any] = field(default_factory=dict)   # large overlays written to static files (fires, drought)
    data_time: pd.Timestamp | None = None                   # the feed's own timestamp
    note: str | None = None
    warnings: list[str] = field(default_factory=list)       # skipped or malformed records, with reasons


def safe_link(url) -> str | None:
    """A feed-supplied link if it is a plain http(s) web address, else None (never javascript:, data: etc.)."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    parts = urlsplit(url.strip())
    return url.strip() if parts.scheme in ("http", "https") and parts.hostname and not parts.username else None


def iso(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert("UTC").isoformat().replace("+00:00", "Z")


# --- Validation helpers -------------------------------------------------------------------------------

def valid_coords(lat, lon) -> tuple[float, float] | None:
    """Finite WGS84 coordinates, or None. Longitudes are wrapped into [-180, 180]."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or not -90 <= lat <= 90 or not -540 <= lon <= 540:
        return None
    lon = ((lon + 180) % 360) - 180 if not -180 <= lon <= 180 else lon
    return lat, lon


def utc(value, unit: str | None = None) -> pd.Timestamp | None:
    """A UTC timestamp from epoch numbers (unit='ms' or 's') or ISO text; naive text is taken as UTC."""
    if value is None or value == "":
        return None
    try:
        t = pd.Timestamp(value, unit=unit) if unit else pd.Timestamp(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if pd.isna(t):
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# --- Feed status and freshness --------------------------------------------------------------------------

LIVE, RECENT, DELAYED, HISTORICAL, UNAVAILABLE = ("Live feed", "Recently updated", "Delayed data",
                                                  "Historical dataset", "Temporarily unavailable")


@dataclass
class FeedStatus:
    source: str
    ok: bool                              # the last refresh attempt succeeded
    fetched_at: pd.Timestamp | None       # when Cloudzy last got data successfully
    data_time: pd.Timestamp | None        # the feed's own timestamp (generated/updated/newest record)
    count: int = 0
    error: str | None = None
    note: str | None = None               # e.g. "11 of 354 alerts have a polygon"
    using_previous: bool = False          # refresh failed; showing the last good data
    label: str = UNAVAILABLE

    def to_json(self) -> dict:
        d = asdict(self)
        d["fetched_at"], d["data_time"] = iso(self.fetched_at), iso(self.data_time)
        return d


def freshness(ok: bool, has_data: bool, data_time, now: pd.Timestamp, cadence: timedelta,
              historical: bool = False, using_previous: bool = False, event_driven: bool = False,
              fetched_at=None) -> str:
    """The status label, from the data's own age against the source's documented update cadence.

    For event-driven feeds (bulletins issued only when something happens), a quiet feed isn't late, so the age
    is that of Cloudzy's last successful check (`fetched_at`) instead of the newest record.

    - no data at all → "Temporarily unavailable"
    - a reference catalogue or a fixed archive query → "Historical dataset"
    - refresh failed but older data is shown, or age > 3 × cadence → "Delayed data"
    - age ≤ 2 × cadence for a feed that updates every 15 minutes or faster → "Live feed"
    - otherwise → "Recently updated"
    """
    if not has_data:
        return UNAVAILABLE
    if historical:
        return HISTORICAL
    if using_previous or not ok:
        return DELAYED
    reference = fetched_at if event_driven else data_time
    if reference is None:
        return RECENT
    age = now - reference
    if age > 3 * cadence:
        return DELAYED
    if cadence <= timedelta(minutes=15) and age <= 2 * cadence:
        return LIVE
    return RECENT


def age_text(t, now: pd.Timestamp) -> str:
    if t is None:
        return "time unknown"
    s = max((now - t).total_seconds(), 0)
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{s / 60:.0f} min ago"
    if s < 172800:
        return f"{s / 3600:.0f} h ago"
    return f"{s / 86400:.0f} days ago"
