"""Synthetic Open-Meteo Air Quality API payloads, shaped like the real response (checked live on 2026-10-22):
unixtime instants, "μg/m³"/"USAQI"/"EAQI" units, and trailing nulls after the model's forecast ends."""

from __future__ import annotations

import pandas as pd

from src.air_quality import CURRENT_UNITS, HOURLY_UNITS

AQ_DEFAULTS = {"pm2_5": 20.0, "pm10": 40.0, "nitrogen_dioxide": 20.0, "ozone": 60.0, "sulphur_dioxide": 5.0,
               "carbon_monoxide": 400.0, "dust": 1.0,
               "us_aqi": 68.0, "us_aqi_pm2_5": 68.0, "us_aqi_pm10": 37.0, "us_aqi_nitrogen_dioxide": 18.0,
               "us_aqi_ozone": 30.0, "us_aqi_sulphur_dioxide": 3.0, "us_aqi_carbon_monoxide": 4.0,
               "european_aqi": 38.0, "european_aqi_pm2_5": 38.0, "european_aqi_pm10": 30.0,
               "european_aqi_nitrogen_dioxide": 15.0, "european_aqi_ozone": 25.0, "european_aqi_sulphur_dioxide": 2.0}


def aq_payload(start: str = "2026-10-17T00:00", tz: str = "Asia/Kolkata", days: int = 9, model_hours: int = 24 * 7,
               overrides: dict | None = None, current_hour: int = 60) -> dict:
    """`days` of hourly rows from local `start`; values only for the first `model_hours`, then nulls.

    `overrides` maps a variable to a scalar or a full list of values (None for a missing hour)."""
    local = pd.date_range(pd.Timestamp(start).tz_localize(tz), periods=24 * days, freq="h")
    epochs = [int(t.timestamp()) for t in local]
    n = len(epochs)
    hourly: dict = {"time": epochs}
    for var in HOURLY_UNITS:
        value = (overrides or {}).get(var, AQ_DEFAULTS[var])
        values = list(value) if isinstance(value, list) else [value] * n
        hourly[var] = [v if i < model_hours else None for i, v in enumerate(values)]
    units = {"time": "unixtime", **HOURLY_UNITS}
    current = {"time": epochs[current_hour], "interval": 3600,
               **{v: hourly[v][current_hour] for v in CURRENT_UNITS}}
    return {"latitude": 28.6, "longitude": 77.2, "generationtime_ms": 1.0, "utc_offset_seconds": 19800,
            "timezone": tz, "timezone_abbreviation": "GMT+5:30", "elevation": 217.0,
            "current_units": {"time": "unixtime", "interval": "seconds", **CURRENT_UNITS}, "current": current,
            "hourly_units": units, "hourly": hourly}


def aq_today_payload(tz: str = "Asia/Kolkata", overrides: dict | None = None) -> dict:
    """Starts two local days before today (as past_days=2 does) and covers about 4.5 days ahead."""
    start = (pd.Timestamp.now(tz=tz).normalize() - pd.Timedelta(days=2)).strftime("%Y-%m-%dT00:00")
    return aq_payload(start=start, tz=tz, days=9, model_hours=24 * 2 + 24 * 4 + 12, overrides=overrides,
                      current_hour=48 + pd.Timestamp.now(tz=tz).hour)
