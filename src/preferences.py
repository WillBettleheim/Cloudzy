"""Account-free preferences: units, favourite places, home city, alert settings.

Where they are stored (`store()`):
- **browser** (the default): each visitor's own browser storage, through
  `src.browser_prefs`. Visitors never see or change each other's settings, and
  nothing about them is kept on the server.
- **file**: one JSON file (`data/user/preferences.json`, ignored by git;
  override with CLOUDZY_PREFS_PATH). Only suitable for a single person running
  Cloudzy on their own computer, and used by the tests. Choose it with
  CLOUDZY_PREFS_STORE=file, or by setting CLOUDZY_PREFS_PATH.

Favourites store the coordinates returned by geocoding (never device
coordinates), so two places with the same name never get mixed up.

Robustness: stored settings are untrusted input (a browser's storage can be
edited by hand). Missing, corrupt or partly invalid data gives defaults for the
bad parts, never a crash. File writes are atomic.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from src import config
from src import units as U
from src.provider import Place

ALERT_KINDS = ("rain", "heat", "cold", "uv", "wind", "change")
SUMMARY_STYLES = ("detailed", "brief")

logger = logging.getLogger(__name__)

DEFAULT_PATH = config.DATA_DIR / "user" / "preferences.json"
MAX_FAVOURITES = 12
MAX_DISMISSED = 200
MAX_STORED_CHARS = 50_000   # far above any real settings; larger stored data is ignored


@dataclass
class AlertThresholds:
    """Thresholds for Cloudzy's own (unofficial) alerts. All in metric units."""
    rain_probability_pct: float = 60.0
    heat_feels_like_c: float = 40.0
    cold_feels_like_c: float = 0.0
    uv_index: float = 8.0
    wind_gust_kmh: float = 50.0
    day_to_day_change_c: float = 5.0

    LIMITS = {  # (min, max) accepted values
        "rain_probability_pct": (10, 100), "heat_feels_like_c": (25, 55), "cold_feels_like_c": (-40, 15),
        "uv_index": (3, 15), "wind_gust_kmh": (20, 150), "day_to_day_change_c": (2, 20),
    }


@dataclass
class Preferences:
    units: dict | None = None            # None = use the selected place's regional default
    favourites: list[dict] = field(default_factory=list)
    alerts: AlertThresholds = field(default_factory=AlertThresholds)
    auto_locate: bool = False            # opt-in; only this on/off choice is stored, never coordinates
    alert_categories: dict = field(default_factory=lambda: {k: True for k in ALERT_KINDS})
    dismissed_alerts: dict = field(default_factory=dict)   # alert key -> UTC time after which it may show again
    summary_style: str = "detailed"      # "detailed" or "brief"
    home: dict | None = None             # a saved favourite (city coordinates), shown when Cloudzy opens
    notifications: bool = False          # show the browser-notification control (permission asked on click)

    def enabled_alert_kinds(self) -> set[str]:
        return {k for k, on in self.alert_categories.items() if on and k in ALERT_KINDS}

    def dismiss(self, key: str, until_utc: str) -> None:
        self.dismissed_alerts[key] = until_utc

    def active_dismissals(self, now_utc) -> set[str]:
        import pandas as pd
        keep = {}
        for k, until in self.dismissed_alerts.items():
            try:
                if pd.Timestamp(until) > now_utc:
                    keep[k] = until
            except (ValueError, TypeError):
                continue
        self.dismissed_alerts = keep          # expired dismissals are dropped
        return set(keep)

    def home_place(self) -> Place | None:
        try:
            return Place(**self.home) if self.home else None
        except TypeError:
            return None

    def unit_override(self) -> U.Units | None:
        if not self.units:
            return None
        try:
            return U.Units(**self.units)
        except (TypeError, ValueError):
            return None

    def favourite_places(self) -> list[Place]:
        out = []
        for f in self.favourites:
            try:
                out.append(Place(**f))
            except TypeError:
                logger.warning("Skipping malformed favourite: %r", f)
        return out

    def is_favourite(self, place: Place) -> bool:
        return any(_same_place(place, p) for p in self.favourite_places())

    def toggle_favourite(self, place: Place) -> bool:
        """Add or remove `place`. Returns True if it is now a favourite."""
        if self.is_favourite(place):
            self.favourites = [asdict(p) for p in self.favourite_places() if not _same_place(place, p)]
            return False
        if len(self.favourites) >= MAX_FAVOURITES:
            raise ValueError(f"You can save up to {MAX_FAVOURITES} favourite places")
        self.favourites.append(asdict(place))
        return True


def _same_place(a: Place, b: Place) -> bool:
    return abs(a.latitude - b.latitude) < 0.01 and abs(a.longitude - b.longitude) < 0.01


def _clean_place(d) -> dict | None:
    """A stored place, checked field by field, or None if any part is unusable."""
    if not isinstance(d, dict):
        return None
    try:
        place = Place(**d)
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (place.latitude, place.longitude)):
            return None
        lat, lon = float(place.latitude), float(place.longitude)
        if not (isinstance(place.name, str) and place.name and len(place.name) <= 200):
            return None
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        ZoneInfo(place.timezone)                       # raises for unknown or malformed zones
        for opt in (place.country, place.admin1, place.country_code):
            if opt is not None and not (isinstance(opt, str) and len(opt) <= 200):
                return None
        if place.population is not None and not isinstance(place.population, int):
            return None
    except (TypeError, ValueError, KeyError, OSError):
        return None
    return asdict(place) | {"latitude": lat, "longitude": lon}


def from_dict(raw) -> Preferences:
    """Preferences from stored data. Anything missing or invalid falls back to its default."""
    prefs = Preferences()
    if not isinstance(raw, dict):
        return prefs
    if isinstance(raw.get("units"), dict):
        prefs.units = raw["units"]
        if prefs.unit_override() is None:
            prefs.units = None
    if isinstance(raw.get("favourites"), list):
        prefs.favourites = [c for c in (_clean_place(f) for f in raw["favourites"]) if c][:MAX_FAVOURITES]
    if isinstance(raw.get("alerts"), dict):
        defaults = AlertThresholds()
        for key, (lo, hi) in AlertThresholds.LIMITS.items():
            value = raw["alerts"].get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= value <= hi:
                setattr(defaults, key, float(value))
        prefs.alerts = defaults
    prefs.auto_locate = raw.get("auto_locate") is True
    if isinstance(raw.get("alert_categories"), dict):
        prefs.alert_categories = {k: raw["alert_categories"].get(k, True) is not False for k in ALERT_KINDS}
    if isinstance(raw.get("dismissed_alerts"), dict):
        prefs.dismissed_alerts = {str(k)[:200]: str(v)[:40]
                                  for k, v in list(raw["dismissed_alerts"].items())[:MAX_DISMISSED]}
    prefs.notifications = raw.get("notifications") is True
    if raw.get("summary_style") in SUMMARY_STYLES:
        prefs.summary_style = raw["summary_style"]
    prefs.home = _clean_place(raw.get("home"))
    return prefs


def to_dict(prefs: Preferences) -> dict:
    return {"units": prefs.units, "favourites": prefs.favourites,
            "alerts": {k: getattr(prefs.alerts, k) for k in AlertThresholds.LIMITS},
            "auto_locate": bool(prefs.auto_locate),
            "alert_categories": {k: bool(prefs.alert_categories.get(k, True)) for k in ALERT_KINDS},
            "dismissed_alerts": dict(list(prefs.dismissed_alerts.items())[-MAX_DISMISSED:]),
            "summary_style": prefs.summary_style if prefs.summary_style in SUMMARY_STYLES else "detailed",
            "home": prefs.home, "notifications": bool(prefs.notifications)}


def parse_json(text) -> Preferences:
    """Preferences from a stored JSON string (browser storage or file). Bad input gives defaults."""
    if not isinstance(text, str) or len(text) > MAX_STORED_CHARS:
        return Preferences()
    try:
        return from_dict(json.loads(text))
    except ValueError:
        return Preferences()


def store() -> str:
    """'browser' (each visitor's own settings) unless file storage was asked for."""
    chosen = os.environ.get("CLOUDZY_PREFS_STORE", "").strip().lower()
    if chosen in ("browser", "file"):
        return chosen
    return "file" if os.environ.get("CLOUDZY_PREFS_PATH") else "browser"


def prefs_path() -> Path:
    return Path(os.environ.get("CLOUDZY_PREFS_PATH", DEFAULT_PATH))


def load(path: Path | None = None) -> Preferences:
    if path is None and store() == "browser":
        from src import browser_prefs
        return from_dict(browser_prefs.current())
    path = path or prefs_path()
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return Preferences()
    except OSError as exc:
        logger.warning("Ignoring unreadable preferences file %s: %s", path, exc)
        return Preferences()
    return parse_json(text)


def save(prefs: Preferences, path: Path | None = None) -> None:
    data = to_dict(prefs)
    if path is None and store() == "browser":
        from src import browser_prefs
        browser_prefs.stage(data)
        return
    path = Path(path or prefs_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)
