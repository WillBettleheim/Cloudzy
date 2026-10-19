"""Provider forecasts and location search from Open-Meteo (Phase C).

Everything in this module is the *provider's* data, never Cloudzy's own
predictions. That keeps the two strictly apart: Cloudzy predictions come only
from src.service, and src.briefing puts both side by side with labels.

- Forecast: Open-Meteo Forecast API, `models=best_match`. This is Open-Meteo's
  default blend of the best models for each location. For Delhi it gives the
  same values as ECMWF IFS, and it also fills fields IFS lacks (such as UV
  index). It returns current conditions (15-minute model values, not station
  observations), hourly data for 7 days, and a daily summary for 7 days.
- Location search: Open-Meteo Geocoding API (GeoNames-based).

Time handling: data are requested with `timeformat=unixtime`, so hourly
timestamps are unambiguous instants, including across daylight-saving changes.
Open-Meteo builds daily `time` values with one fixed UTC offset (the one in
effect at request time). After a DST change these instants land an hour off
local midnight, so daily *dates* are decoded with that same fixed offset.
Sunrise and sunset are true instants and use the location's real time zone.

Run:  python -m src.provider "New Delhi"
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import requests

from src.data_collection import INTERACTIVE, DataCollectionError, fetch_json

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
PROVIDER_MODEL = "best_match"
SOURCE_LABEL = "Open-Meteo forecast (best_match model blend)"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
FORECAST_DAYS = 7

# Variables requested, with the units the app expects. All values are fetched in
# metric units, and src.units converts them for display, so one cached response
# serves every unit choice.
CURRENT_UNITS = {
    "temperature_2m": "°C", "relative_humidity_2m": "%", "apparent_temperature": "°C",
    "is_day": "", "precipitation": "mm", "weather_code": "wmo code", "cloud_cover": "%",
    "wind_speed_10m": "km/h", "wind_direction_10m": "°", "wind_gusts_10m": "km/h",
    "uv_index": "", "pressure_msl": "hPa", "visibility": "m", "dew_point_2m": "°C",
}
HOURLY_UNITS = {
    "temperature_2m": "°C", "relative_humidity_2m": "%", "apparent_temperature": "°C",
    "precipitation_probability": "%", "precipitation": "mm", "weather_code": "wmo code",
    "wind_speed_10m": "km/h", "wind_direction_10m": "°", "is_day": "",
    "uv_index": "", "cloud_cover": "%", "wind_gusts_10m": "km/h",
}
DAILY_UNITS = {
    "weather_code": "wmo code", "temperature_2m_max": "°C", "temperature_2m_min": "°C",
    "precipitation_sum": "mm", "precipitation_probability_max": "%", "wind_speed_10m_max": "km/h",
    "sunrise": "unixtime", "sunset": "unixtime", "uv_index_max": "",
    "wind_direction_10m_dominant": "°", "daylight_duration": "s",
}
# Without these the forecast is unusable. Any other variable may be missing for a
# location (some models don't provide UV or visibility, for example). It is then
# shown as unavailable, never filled in.
REQUIRED = {
    "current": {"temperature_2m", "weather_code"},
    "hourly": {"temperature_2m", "weather_code"},
    "daily": {"weather_code", "temperature_2m_max", "temperature_2m_min", "sunrise", "sunset"},
}
# Physical bounds valid anywhere on Earth. Values outside them become None and are reported.
BOUNDS = {
    "temperature_2m": (-90, 60), "apparent_temperature": (-100, 75), "relative_humidity_2m": (0, 100),
    "precipitation": (0, 500), "precipitation_probability": (0, 100), "cloud_cover": (0, 100),
    "wind_speed_10m": (0, 400), "wind_gusts_10m": (0, 500), "wind_direction_10m": (0, 360),
    "temperature_2m_max": (-90, 60), "temperature_2m_min": (-90, 60), "precipitation_sum": (0, 2000),
    "precipitation_probability_max": (0, 100), "wind_speed_10m_max": (0, 400), "uv_index_max": (0, 20),
    "uv_index": (0, 20), "pressure_msl": (850, 1090), "visibility": (0, 200_000), "dew_point_2m": (-90, 40),
    "wind_direction_10m_dominant": (0, 360), "daylight_duration": (0, 86_400),
}

# WMO weather interpretation codes as documented by Open-Meteo.
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snowfall", 73: "Moderate snowfall", 75: "Heavy snowfall", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 97: "Heavy thunderstorm",
    99: "Thunderstorm with heavy hail",
}

# Rain indicator thresholds (documented in the README; not an official warning).
RAIN_PROBABILITY_PCT = 50
RAIN_AMOUNT_MM = 0.5


class ProviderError(RuntimeError):
    """The provider response cannot be used."""


def describe_weather(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return WMO_CODES.get(int(code), f"Unknown (WMO {int(code)})")


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    timezone: str
    country: str | None = None
    admin1: str | None = None
    population: int | None = None
    country_code: str | None = None  # ISO 3166-1 alpha-2, used for regional unit defaults

    @property
    def label(self) -> str:
        return ", ".join(p for p in (self.name, self.admin1, self.country) if p)


@dataclass
class ProviderForecast:
    place: Place
    model: str
    retrieved_at_utc: str
    grid_latitude: float
    grid_longitude: float
    elevation_m: float | None
    current: dict[str, Any]
    hourly: pd.DataFrame  # index: tz-aware local time
    daily: pd.DataFrame   # index: local calendar date
    warnings: list[str] = field(default_factory=list)
    source_label: str = SOURCE_LABEL
    attribution: str = ATTRIBUTION

    def to_dict(self) -> dict:
        def records(df: pd.DataFrame, key: str) -> list[dict]:
            out = df.reset_index().rename(columns={"index": key})
            for col in out.columns:
                if pd.api.types.is_datetime64_any_dtype(out[col]) or col == "date":
                    out[col] = out[col].map(lambda t: t.isoformat())
            return out.astype(object).where(out.notna(), None).to_dict(orient="records")

        return {
            "place": asdict(self.place) | {"label": self.place.label},
            "source": self.source_label, "model": self.model, "attribution": self.attribution,
            "retrieved_at_utc": self.retrieved_at_utc,
            "grid": {"latitude": self.grid_latitude, "longitude": self.grid_longitude, "elevation_m": self.elevation_m},
            "current": self.current,
            "hourly": records(self.hourly, "time"),
            "daily": records(self.daily, "date"),
            "warnings": self.warnings,
        }


# --- Location search -------------------------------------------------------------

def search_locations(name: str, count: int = 5, session: requests.Session | None = None) -> list[Place]:
    """Places matching `name`, most relevant first. Empty list when nothing matches."""
    query = (name or "").strip()
    if len(query) < 2:
        raise ValueError("Type at least 2 characters to search for a place")
    payload = fetch_json(GEOCODING_URL, {"name": query, "count": count, "language": "en", "format": "json"},
                         session=session, **INTERACTIVE)
    if not isinstance(payload, dict) or payload.get("error"):
        raise ProviderError(f"Geocoding failed: {payload.get('reason') if isinstance(payload, dict) else payload}")
    places = []
    for r in payload.get("results", []):  # the key is absent when nothing matches
        try:
            places.append(Place(
                name=r["name"], latitude=float(r["latitude"]), longitude=float(r["longitude"]),
                timezone=r.get("timezone") or "UTC", country=r.get("country"), admin1=r.get("admin1"),
                population=r.get("population"), country_code=r.get("country_code"),
            ))
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping malformed geocoding result: %r", r)
    return places


# --- Forecast --------------------------------------------------------------------

def _all_missing(value) -> bool:
    if value is None:
        return True
    return isinstance(value, list) and all(v is None for v in value)


def _check_units(section: str, got: dict, expected: dict, data: dict, warnings: list[str]) -> set[str]:
    """Check each variable's unit. Returns optional variables that are unavailable.

    A wrong unit on a variable that has values is fatal: converting it would
    be a guess. An optional variable the model doesn't provide (missing, or
    all null with a placeholder unit) is reported and left empty.
    """
    if got.get("time") != "unixtime":
        raise ProviderError(f"{section}.time: expected unixtime, got {got.get('time')!r}")
    unavailable = set()
    for var, unit in expected.items():
        if got.get(var) == unit:
            continue
        if var not in REQUIRED[section] and _all_missing(data.get(var)):
            unavailable.add(var)
            warnings.append(f"{section}.{var}: not available for this location")
            continue
        raise ProviderError(f"{section}.{var}: expected unit {unit!r}, got {got.get(var)!r}")
    return unavailable


def _clean(df: pd.DataFrame, section: str, warnings: list[str], skip: set[str] = frozenset()) -> pd.DataFrame:
    for col in df.columns:
        if col in ("sunrise", "sunset"):
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if col in skip:  # already reported as unavailable
            continue
        if col in BOUNDS:
            lo, hi = BOUNDS[col]
            bad = df[col].notna() & ~df[col].between(lo, hi)
            if bad.any():
                warnings.append(f"{section}.{col}: {int(bad.sum())} implausible values removed")
                df.loc[bad, col] = float("nan")
        missing = int(df[col].isna().sum())
        if missing:
            warnings.append(f"{section}.{col}: {missing} of {len(df)} values missing")
    return df


def parse_forecast(payload: dict, place: Place, retrieved_at: str) -> ProviderForecast:
    """Validate a Forecast API payload and convert it to tidy, time-zone-aware tables."""
    if not isinstance(payload, dict):
        raise ProviderError("Forecast response is not a JSON object")
    if payload.get("error"):
        raise ProviderError(f"Forecast API error: {payload.get('reason', 'no reason given')}")
    for section in ("current", "hourly", "daily"):
        if not isinstance(payload.get(section), dict):
            raise ProviderError(f"Forecast response has no '{section}' section")
    tz = payload.get("timezone") or place.timezone
    offset = payload.get("utc_offset_seconds")
    if not isinstance(offset, int):
        raise ProviderError("Forecast response has no utc_offset_seconds")
    warnings: list[str] = []
    gone = {
        section: _check_units(section, payload.get(f"{section}_units", {}), units, payload[section], warnings)
        for section, units in (("current", CURRENT_UNITS), ("hourly", HOURLY_UNITS), ("daily", DAILY_UNITS))
    }

    # Hourly: consecutive instants, shown in local time.
    hourly_raw = payload["hourly"]
    n = len(hourly_raw.get("time", []))
    if n == 0:
        raise ProviderError("Forecast contains no hourly data")
    for var in HOURLY_UNITS:
        if var not in gone["hourly"] and len(hourly_raw.get(var, [])) != n:
            raise ProviderError(f"hourly.{var} length does not match time")
    hourly = pd.DataFrame({v: [None] * n if v in gone["hourly"] else hourly_raw[v] for v in HOURLY_UNITS})
    hourly.index = pd.to_datetime(hourly_raw["time"], unit="s", utc=True).tz_convert(tz)
    hourly.index.name = "time"
    if not hourly.index.is_unique or not ((hourly.index[1:] - hourly.index[:-1]) == pd.Timedelta("1h")).all():
        raise ProviderError("Hourly timestamps are not a consecutive hourly sequence")
    hourly = _clean(hourly, "hourly", warnings, skip=gone["hourly"])
    if hourly["temperature_2m"].isna().all():
        raise ProviderError("Hourly temperature is entirely missing")

    # Daily: dates decoded with the API's own fixed offset (see module docstring).
    daily_raw = payload["daily"]
    m = len(daily_raw.get("time", []))
    if m == 0:
        raise ProviderError("Forecast contains no daily data")
    for var in DAILY_UNITS:
        if var not in gone["daily"] and len(daily_raw.get(var, [])) != m:
            raise ProviderError(f"daily.{var} length does not match time")
    daily = pd.DataFrame({v: [None] * m if v in gone["daily"] else daily_raw[v] for v in DAILY_UNITS})
    fixed = pd.to_datetime(daily_raw["time"], unit="s", utc=True) + pd.Timedelta(seconds=offset)
    daily.index = pd.Index(fixed.date, name="date")
    if not daily.index.is_unique:
        raise ProviderError("Daily dates are not unique")
    for col in ("sunrise", "sunset"):
        daily[col] = pd.to_datetime(daily[col], unit="s", utc=True).dt.tz_convert(tz)
    daily = _clean(daily, "daily", warnings, skip=gone["daily"])
    daily["description"] = daily["weather_code"].map(lambda c: describe_weather(None if pd.isna(c) else c))

    # Current: one 15-minute model value.
    cur = dict(payload["current"])
    if "time" not in cur:
        raise ProviderError("Current conditions have no time")
    current = {"time": pd.Timestamp(cur["time"], unit="s", tz="UTC").tz_convert(tz).isoformat(),
               "interval_seconds": cur.get("interval")}
    for var in CURRENT_UNITS:
        val = None if var in gone["current"] else cur.get(var)
        lo_hi = BOUNDS.get(var)
        if val is not None and lo_hi and not (lo_hi[0] <= val <= lo_hi[1]):
            warnings.append(f"current.{var}: implausible value {val} removed")
            val = None
        current[var] = val
    current["description"] = describe_weather(current.get("weather_code"))

    return ProviderForecast(
        place=place, model=PROVIDER_MODEL, retrieved_at_utc=retrieved_at,
        grid_latitude=payload.get("latitude"), grid_longitude=payload.get("longitude"),
        elevation_m=payload.get("elevation"),
        current=current, hourly=hourly, daily=daily, warnings=warnings,
    )


def fetch_forecast(place: Place, days: int = FORECAST_DAYS, session: requests.Session | None = None) -> ProviderForecast:
    params = {
        "latitude": place.latitude, "longitude": place.longitude,
        "current": ",".join(CURRENT_UNITS), "hourly": ",".join(HOURLY_UNITS), "daily": ",".join(DAILY_UNITS),
        "timezone": "auto", "timeformat": "unixtime", "forecast_days": days, "models": PROVIDER_MODEL,
    }
    try:
        payload = fetch_json(FORECAST_URL, params, session=session, **INTERACTIVE)
    except DataCollectionError as exc:
        raise ProviderError(f"Could not reach the forecast provider: {exc}") from exc
    return parse_forecast(payload, place, datetime.now(UTC).isoformat(timespec="seconds"))


def upcoming(forecast: ProviderForecast, now: pd.Timestamp, hours: int) -> pd.DataFrame:
    """Hourly rows from the current hour onward."""
    start = now.tz_convert(forecast.hourly.index.tz).floor("h")
    return forecast.hourly.loc[start:].iloc[:hours]


def rain_outlook(forecast: ProviderForecast, now: pd.Timestamp, hours: int = 24) -> dict:
    """Simple rain indicator from the provider forecast. Not an official warning.

    Rain is flagged for an hour when precipitation probability >= 50% or
    forecast precipitation >= 0.5 mm.
    """
    window = upcoming(forecast, now, hours)
    flagged = window[(window["precipitation_probability"] >= RAIN_PROBABILITY_PCT)
                     | (window["precipitation"] >= RAIN_AMOUNT_MM)]
    return {
        "hours_checked": len(window),
        "rain_expected": not flagged.empty,
        "first_rain_hour_local": flagged.index[0].isoformat() if not flagged.empty else None,
        "rainy_hours": len(flagged),
        "max_probability_pct": None if window.empty else float(window["precipitation_probability"].max()),
        "total_precipitation_mm": round(float(window["precipitation"].sum()), 1),
        "rule": f"probability >= {RAIN_PROBABILITY_PCT}% or amount >= {RAIN_AMOUNT_MM} mm in an hour",
        "label": "Derived from the provider forecast; not an official weather warning.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("place", nargs="?", default="New Delhi")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    places = search_locations(args.place)
    if not places:
        print(f"No place found for {args.place!r}.")
        return
    f = fetch_forecast(places[0])
    now = pd.Timestamp.now(tz="UTC")
    c = f.current
    print(f"\n{f.source_label} — {f.place.label}")
    print(f"  now ({c['time'][11:16]} local): {c['temperature_2m']} °C, feels {c['apparent_temperature']} °C, "
          f"{c['description']}, humidity {c['relative_humidity_2m']}%, wind {c['wind_speed_10m']} km/h")
    print("\n  next 12 hours")
    for t, r in upcoming(f, now, 12).iterrows():
        print(f"   {t:%H:%M}  {r.temperature_2m:5.1f} °C  rain {r.precipitation_probability:3.0f}%  "
              f"{describe_weather(r.weather_code)}")
    print("\n  7 days")
    for d, r in f.daily.iterrows():
        print(f"   {d:%a %d %b}  {r.temperature_2m_min:5.1f} – {r.temperature_2m_max:4.1f} °C  "
              f"rain {r.precipitation_probability_max:3.0f}%  {r.description}")
    rain = rain_outlook(f, now)
    print(f"\n  rain next 24 h: {'yes, from ' + rain['first_rain_hour_local'][11:16] if rain['rain_expected'] else 'not expected'}"
          f"  ({rain['label']})")
    for w in f.warnings:
        print("  warning:", w)
    print(f"\n  {f.attribution}")


if __name__ == "__main__":
    main()
