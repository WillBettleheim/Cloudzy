"""Disaster situations: the globe's selector, presets, record layers and per-situation filters.

A *situation* is what a person looks for ("tornadoes", "extreme heat"). Each loaded record belongs to exactly one,
decided from its feed category and, for official warnings, the product name the agency used (so a "Tornado
Warning" is a tornado, a "Dust Storm Warning" a dust storm). Situations with no connected source stay in the list,
marked "not connected", so the absence of markers is never mistaken for the absence of events. Nothing here creates
records.

Record layers say what a record *is*, never mixed: observed events (measured or analysed), official warnings,
provider forecasts and automatic estimates (GDACS, forecast tracks), Cloudzy ML predictions, and historical
analysis (reference catalogues).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.hazards.schema import HazardEvent


@dataclass(frozen=True)
class Situation:
    key: str
    label: str
    icon: str                       # Material Symbols name
    color: str
    shape: str                      # marker shape (see src.earth_view)
    sources: tuple[str, ...]        # connected feeds that can report it ("" → not connected)
    covers: str                     # what is included, in words
    default_on: bool = True
    missing: str = ""               # for a situation with no connected source: why, and where to look instead


SITUATIONS: dict[str, Situation] = {s.key: s for s in (
    Situation("earthquake", "Earthquakes", "earthquake", "#e8a33d", "circle", ("usgs", "gdacs"),
              "USGS-located earthquakes worldwide; GDACS orange/red alerts"),
    Situation("flood", "Floods", "flood", "#4fa3d9", "drop", ("nws", "gdacs"),
              "NWS flood and flash-flood warnings, watches and advisories (U.S.); GDACS flood alerts"),
    Situation("cyclone", "Tropical cyclones", "cyclone", "#7ab8ff", "hurricane", ("nhc", "gdacs", "nws"),
              "NHC advisories, tracks and cones; GDACS cyclone alerts; NWS hurricane and storm-surge warnings"),
    Situation("drought", "Drought", "water_drop", "#c99a5b", "square", ("usdm", "gdacs"),
              "U.S. Drought Monitor map; GDACS drought alerts"),
    Situation("wildfire", "Wildfires", "local_fire_department", "#ef6a45", "triangle", ("firms", "gdacs", "nws"),
              "VIIRS satellite fire detections; GDACS forest-fire alerts; NWS red-flag and fire-weather warnings"),
    Situation("thunderstorm", "Severe thunderstorms", "thunderstorm", "#b58cf2", "diamond", ("nws",),
              "NWS severe thunderstorm warnings and watches, severe weather statements (U.S.)"),
    Situation("tornado", "Tornadoes", "tornado", "#e2566a", "diamond", ("nws",),
              "NWS tornado warnings and watches (U.S.)"),
    Situation("heat", "Extreme heat", "thermostat", "#f0b35b", "hexagon", ("nws",),
              "NWS heat warnings, watches and advisories (U.S.)"),
    Situation("cold", "Extreme cold", "severe_cold", "#8fc3f0", "hexagon", ("nws",),
              "NWS extreme-cold, wind-chill, freeze and frost products (U.S.)"),
    Situation("rain", "Heavy rainfall", "rainy", "#5c8fe6", "drop", (), "",
              missing="No heavy-rainfall warning feed is connected. Flooding caused by rain is under Floods; "
                      "rain forecasts are on the Weather page."),
    Situation("wind", "Severe wind", "air", "#9fb7d9", "diamond", ("nws",),
              "NWS high-wind, extreme-wind and wind advisories (U.S.)"),
    Situation("tsunami", "Tsunamis", "tsunami", "#3fc1c9", "wave", ("tsunami", "nws"),
              "NOAA tsunami warning centre messages; NWS tsunami products"),
    Situation("volcano", "Volcanoes", "volcano", "#d9584f", "triangle_down", ("usgs_volcano", "gdacs"),
              "USGS elevated alert levels (U.S.); GDACS volcano alerts"),
    Situation("landslide", "Landslides", "landslide", "#a47c55", "square", (), "",
              missing="No landslide feed is connected (NASA's landslide catalogue is reported after the fact and "
                      "isn't real time)."),
    Situation("snow", "Snowstorms and blizzards", "ac_unit", "#bcd5f5", "star", ("nws",),
              "NWS winter storm, blizzard, ice storm, snow, freezing rain and avalanche products (U.S.)"),
    Situation("dust", "Dust storms", "foggy", "#c9a46b", "square", ("nws",),
              "NWS dust storm warnings and blowing-dust advisories (U.S.)"),
    Situation("lightning", "Lightning", "bolt", "#f5d76e", "star", (), "",
              missing="No openly licensed real-time lightning feed is available to connect."),
    Situation("other", "Other official warnings", "warning", "#9aa7bb", "square", ("nws",),
              "Other NWS products (air quality, fog, coastal, special statements…)", default_on=False),
)}

DEFAULT_ON = frozenset(k for k, s in SITUATIONS.items() if s.default_on and s.sources)

PRESETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "all": ("All available", tuple(k for k, s in SITUATIONS.items() if s.sources)),
    "geological": ("Geological", ("earthquake", "volcano", "tsunami", "landslide")),
    "storms": ("Storms", ("cyclone", "thunderstorm", "tornado", "wind", "snow", "dust", "lightning")),
    "water": ("Water hazards", ("flood", "cyclone", "tsunami", "rain")),
    "heat": ("Climate and heat", ("heat", "cold", "drought")),
    "fire": ("Fire and dryness", ("wildfire", "drought", "heat")),
}
# "My selection" is the viewer's saved set, kept in their browser (src.earth_view).

LAYERS = {
    "observed": "Observed events",
    "official": "Official warnings",
    "provider": "Provider forecasts and automatic estimates",
    "ml": "Cloudzy ML predictions",
    "historical": "Historical analysis",
}
LAYER_OF_KIND = {"observation": "observed", "detection": "observed", "assessment": "observed",
                 "warning": "official", "forecast": "provider", "impact_estimate": "provider",
                 "catalogue": "historical"}

# Situation from an official product name; first match wins, so the specific names come first.
_SEVERE_RULES = (("tornado", ("Tornado",)), ("dust", ("Dust",)),
                 ("thunderstorm", ("Thunderstorm", "Severe Weather Statement")),
                 ("wind", ("Wind",)))


def situation_for(e: HazardEvent) -> str:
    c = e.category
    if c in ("earthquake", "flood", "cyclone", "drought", "wildfire", "tsunami", "volcano"):
        return c
    if c == "severe":
        return next((k for k, names in _SEVERE_RULES if any(n in e.title for n in names)), "thunderstorm")
    if c == "temperature":
        return "heat" if "Heat" in e.title else "cold"
    if c == "winter":
        return "snow"
    return "other"


def layer_for(e: HazardEvent) -> str:
    return LAYER_OF_KIND.get(e.kind, "observed")


# --- Per-situation attributes used by the filters ---------------------------------------------------

def basin(e: HazardEvent) -> str | None:
    """Tropical-cyclone basin: NHC's own bin (AT/EP/CP), otherwise the basin containing the position, by the usual
    longitude bounds (approximate near Central America, where the Atlantic and Eastern Pacific meet)."""
    if e.category != "cyclone":
        return None
    area = (e.area or "").upper()
    for code, name in (("AT", "North Atlantic"), ("EP", "Eastern Pacific"), ("CP", "Central Pacific")):
        if area.startswith(code):
            return name
    if e.lat is None or e.lon is None:
        return None
    lat, lon = e.lat, e.lon
    if lat >= 0:
        if 40 <= lon < 100:
            return "North Indian"
        if lon >= 100:
            return "Western Pacific"
        if lon < -140:
            return "Central Pacific"
        if lon < -100 or (lon < -80 and lat < 15):
            return "Eastern Pacific"
        return "North Atlantic"
    if 20 <= lon < 90:
        return "South-West Indian"
    if 90 <= lon < 160:
        return "Australian region"
    return "South Pacific" if lon >= 160 or lon < -120 else "South Atlantic"


BASINS = ("North Atlantic", "Eastern Pacific", "Central Pacific", "Western Pacific", "North Indian",
          "South-West Indian", "Australian region", "South Pacific", "South Atlantic")


def product(e: HazardEvent) -> str | None:
    """The warning product type (Warning, Watch, Advisory, Statement…), for official records."""
    return e.extra.get("product") if e.source == "nws" else ("Warning" if e.kind == "warning" else None)


def active(e: HazardEvent, now: pd.Timestamp) -> bool:
    return e.current and (e.ends is None or e.ends > now)


def flood_type(e: HazardEvent) -> str | None:
    if e.category != "flood":
        return None
    return "official" if e.official else "automatic"


@dataclass
class View:
    """The globe's current selector state, mirrored by the page's table and summaries."""
    on: set[str] = field(default_factory=lambda: set(DEFAULT_ON))
    layers: set[str] = field(default_factory=lambda: set(LAYERS))
    rank: int = 0
    official: bool = False
    current: bool = False
    eq_min_mag: float = 0.0
    eq_depths: set[str] = field(default_factory=lambda: {"shallow", "intermediate", "deep"})
    tc_basin: str = ""
    tc_rank: int = 0
    flood: str = ""                # "" | official | automatic
    severe_product: str = ""       # "" | Warning | Watch | Advisory | Statement
    severe_active: bool = False

    @classmethod
    def from_json(cls, d: dict | None) -> View:
        v = cls()
        if not d:
            return v
        v.on = {k for k in d.get("on", v.on) if k in SITUATIONS}
        v.layers = {k for k in d.get("layers", v.layers) if k in LAYERS}
        f = d.get("f") or {}
        v.rank = int(f.get("rank") or 0)
        v.official, v.current = bool(f.get("official")), bool(f.get("current"))
        v.eq_min_mag = float(f.get("eqMag") or 0)
        v.eq_depths = set(f.get("eqDepth") or v.eq_depths)
        v.tc_basin, v.flood = str(f.get("tcBasin") or ""), str(f.get("flood") or "")
        v.tc_rank = int(f.get("tcRank") or 0)
        v.severe_product, v.severe_active = str(f.get("svProd") or ""), bool(f.get("svActive"))
        return v


# The globe's filter keys that change which *records* are shown (mirrored by `matches`). The other filters in the
# globe (fire power, confidence and age; drought category) apply to the fire and drought overlays only.
MIRRORED_FILTERS = ("rank", "official", "current", "eqMag", "eqDepth", "tcBasin", "tcRank", "flood", "svProd", "svActive")
OVERLAY_FILTERS = ("fireFrp", "fireConf", "fireHours", "dmMin")


def depth_band(depth_km) -> str | None:
    if depth_km is None:
        return None
    return "shallow" if depth_km < 70 else "intermediate" if depth_km < 300 else "deep"


WARNING_SITUATIONS = frozenset({"thunderstorm", "tornado", "wind", "dust", "heat", "cold", "snow", "other"})


def matches(e: HazardEvent, v: View, now: pd.Timestamp) -> bool:
    """Same rules as the globe's `visible()` at "Now" (src.earth_view)."""
    sit = situation_for(e)
    if sit not in v.on or layer_for(e) not in v.layers:
        return False
    if e.severity_rank < v.rank or (v.official and not e.official) or (v.current and not e.current):
        return False
    if sit == "earthquake" and e.source == "usgs":
        mag = e.extra.get("mag")
        if mag is not None and mag < v.eq_min_mag:
            return False
        band = depth_band(e.extra.get("depth_km"))
        if band is not None and band not in v.eq_depths:
            return False
    if sit == "cyclone" and ((v.tc_basin and basin(e) != v.tc_basin) or e.severity_rank < v.tc_rank):
        return False
    if sit == "flood" and v.flood and flood_type(e) != v.flood:
        return False
    if sit in WARNING_SITUATIONS:
        if v.severe_product and product(e) != v.severe_product:
            return False
        if v.severe_active and not active(e, now):
            return False
    return True


def counts(events: list[HazardEvent]) -> dict[str, int]:
    out = dict.fromkeys(SITUATIONS, 0)
    for e in events:
        out[situation_for(e)] += 1
    return out


def selector_data(events: list[HazardEvent]) -> list[dict]:
    """What the globe's selector lists: every situation, with its loaded count and connection status."""
    n = counts(events)
    return [{"key": s.key, "label": s.label, "icon": s.icon, "color": s.color, "shape": s.shape,
             "connected": bool(s.sources), "on": s.key in DEFAULT_ON, "n": n[s.key],
             "covers": s.covers or s.missing} for s in SITUATIONS.values()]
