"""Central configuration for Cloudzy Core experiments.

Everything that defines *which* data an experiment uses lives here, so a result
can always be traced back to an exact location, period, source and variable set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MODELS_DIR = PROJECT_ROOT / "models"
REPORTS_DIR = PROJECT_ROOT / "reports"

ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"
HISTORICAL_FORECAST_API_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
FORECAST_API_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class Location:
    slug: str
    name: str
    latitude: float
    longitude: float
    local_timezone: str  # used only for deriving local-time features later


DELHI = Location(
    slug="delhi",
    name="New Delhi, India",
    latitude=28.6139,
    longitude=77.2090,
    local_timezone="Asia/Kolkata",
)

# Three full calendar years. Fixed dates (not "today minus N") so that the
# dataset is identical every time the pipeline is re-run.
START_DATE = date(2023, 1, 1)
END_DATE = date(2025, 12, 31)

# A single reanalysis model instead of Open-Meteo's default "best_match" blend,
# so the data source is homogeneous across the whole period.
#   era5_seamless (default): ERA5-Land (~0.1°, ~9 km) for temperature and
#         humidity, which evolve smoothly hour to hour, and ERA5 for the
#         variables ERA5-Land does not provide through Open-Meteo (pressure,
#         wind, precipitation are all-null for plain "era5_land").
#   era5: ECMWF ERA5, ~0.25° (~25 km). At this grid cell its hourly 2 m
#         temperature steps unevenly at fixed UTC hours (see README), which
#         flatters any model that knows the hour of day. Kept for comparison.
# Override per run with:  CLOUDZY_WEATHER_MODEL=era5 python -m ...
SUPPORTED_WEATHER_MODELS = ("era5_seamless", "era5")
WEATHER_MODEL = os.environ.get("CLOUDZY_WEATHER_MODEL", "era5_seamless")
if WEATHER_MODEL not in SUPPORTED_WEATHER_MODELS:
    raise ValueError(f"CLOUDZY_WEATHER_MODEL must be one of {SUPPORTED_WEATHER_MODELS}, got {WEATHER_MODEL!r}")

# Prefix for every generated file, so runs on different sources never collide.
RUN_TAG = f"{DELHI.slug}_{WEATHER_MODEL}"


@dataclass(frozen=True)
class DataSource:
    """Where a historical dataset comes from. `model` is unique per source and
    is used in every file name."""
    model: str
    url: str
    lag_days: int  # how far behind real time the source is complete
    kind: str      # "reanalysis" or "nwp_forecast"


# Training data (Phase A).
REANALYSIS = DataSource(WEATHER_MODEL, ARCHIVE_API_URL, lag_days=6, kind="reanalysis")

# What a live service can actually get: ECMWF IFS output from the Open-Meteo
# Forecast API. The Historical Forecast API archives exactly the values the live
# API serves for past hours (verified to be identical over 144 h on 2026-10-19),
# so it lets us replay "live" inputs for 2023-2025. Pinned to ecmwf_ifs rather
# than best_match, whose underlying model can change without notice.
LIVE_MODEL = "ecmwf_ifs"
LIVE_ARCHIVE = DataSource(LIVE_MODEL, HISTORICAL_FORECAST_API_URL, lag_days=1, kind="nwp_forecast")

SOURCES = {s.model: s for s in (REANALYSIS, LIVE_ARCHIVE)}

# Open-Meteo variable name -> unit we expect the API to report.
# We request UTC ("GMT") timestamps to avoid any time-zone ambiguity in storage.
HOURLY_VARIABLES: dict[str, str] = {
    "temperature_2m": "°C",
    "relative_humidity_2m": "%",
    "surface_pressure": "hPa",
    "wind_speed_10m": "km/h",
    "precipitation": "mm",
}

# Physically plausible bounds for Delhi. Values outside these are treated as
# data errors (set to NaN and counted in the quality report), never silently
# clipped.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "temperature_2m": (-10.0, 55.0),
    "relative_humidity_2m": (0.0, 100.0),
    "surface_pressure": (900.0, 1050.0),
    "wind_speed_10m": (0.0, 200.0),
    "precipitation": (0.0, 300.0),
}
