"""Local, account-free preferences: unit overrides, favourite places, alert thresholds.

Cloudzy runs locally, so preferences are stored in one small JSON file on this
machine (default `data/user/preferences.json`, which git ignores; override with
CLOUDZY_PREFS_PATH). Nothing is sent anywhere. Favourites store the coordinates
returned by geocoding, so two places with the same name never get mixed up.

Robustness: a missing file gives defaults. A corrupt or partly invalid file
also gives defaults for the bad parts, never a crash. Writes are atomic, so a
failed save can't leave a half-written file.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src import config
from src import units as U
from src.provider import Place

logger = logging.getLogger(__name__)

DEFAULT_PATH = config.DATA_DIR / "user" / "preferences.json"
MAX_FAVOURITES = 12


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


def prefs_path() -> Path:
    return Path(os.environ.get("CLOUDZY_PREFS_PATH", DEFAULT_PATH))


def load(path: Path | None = None) -> Preferences:
    path = path or prefs_path()
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return Preferences()
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable preferences file %s: %s", path, exc)
        return Preferences()
    if not isinstance(raw, dict):
        return Preferences()
    prefs = Preferences()
    if isinstance(raw.get("units"), dict):
        prefs.units = raw["units"]
        if prefs.unit_override() is None:
            prefs.units = None
    if isinstance(raw.get("favourites"), list):
        prefs.favourites = [f for f in raw["favourites"] if isinstance(f, dict)][:MAX_FAVOURITES]
    if isinstance(raw.get("alerts"), dict):
        defaults = AlertThresholds()
        for key, (lo, hi) in AlertThresholds.LIMITS.items():
            value = raw["alerts"].get(key)
            if isinstance(value, (int, float)) and lo <= value <= hi:
                setattr(defaults, key, float(value))
        prefs.alerts = defaults
    return prefs


def save(prefs: Preferences, path: Path | None = None) -> None:
    path = Path(path or prefs_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"units": prefs.units, "favourites": prefs.favourites,
            "alerts": {k: getattr(prefs.alerts, k) for k in AlertThresholds.LIMITS}}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)
