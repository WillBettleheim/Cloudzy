"""Historical weather for any place (Cloudzy Lab's history explorer).

Source: Open-Meteo Historical Weather API (archive), default model blend.
These are **reanalysis** values: a model-based reconstruction of past weather
that blends observations into a weather model on a grid of roughly 9–25 km.
They are not readings from a particular station. For the most recent days,
before ERA5 is published, the archive fills in from operational weather-model
analyses. That is why the page labels them separately from forecasts and from
Cloudzy's predictions.

Limits, checked before any request is made:
- dates from 1940-01-01 (the API's documented start) up to today;
- at most 366 days per request, which keeps calls small and charts quick.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.data_collection import DataCollectionError, fetch_json
from src.provider import Place, ProviderError

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MIN_DATE = date(1940, 1, 1)
MAX_SPAN_DAYS = 366
RECENT_DAYS_NOTE = 7  # days for which values may come from weather-model analyses rather than ERA5
VARIABLES = {
    "temperature_2m": "°C", "relative_humidity_2m": "%", "precipitation": "mm",
    "wind_speed_10m": "km/h", "wind_gusts_10m": "km/h", "cloud_cover": "%", "weather_code": "wmo code",
}
LABEL = "Historical reanalysis (model-based reconstruction of past weather, not station observations)"


def validate_range(start: date, end: date, today: date) -> None:
    """Raise ValueError with a user-facing message when the range can't be requested."""
    if start > end:
        raise ValueError("The start date is after the end date.")
    if start < MIN_DATE:
        raise ValueError(f"Historical data starts on {MIN_DATE:%d %B %Y}.")
    if end > today:
        raise ValueError("The end date is in the future. For upcoming days, use the forecast.")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise ValueError(f"Please choose at most {MAX_SPAN_DAYS} days at a time.")


def parse_history(payload: dict) -> pd.DataFrame:
    if not isinstance(payload, dict) or payload.get("error"):
        raise ProviderError(f"Archive API error: {payload.get('reason') if isinstance(payload, dict) else payload}")
    hourly, units = payload.get("hourly"), payload.get("hourly_units", {})
    if not isinstance(hourly, dict) or not hourly.get("time"):
        raise ProviderError("The archive returned no hourly data for this range.")
    if units.get("time") != "unixtime":
        raise ProviderError("Unexpected time format from the archive")
    n = len(hourly["time"])
    cols = {}
    for var, unit in VARIABLES.items():
        values = hourly.get(var)
        if values is None or all(v is None for v in values):
            cols[var] = [None] * n  # unavailable: kept empty, never filled
            continue
        if units.get(var) != unit:
            raise ProviderError(f"{var}: expected unit {unit!r}, got {units.get(var)!r}")
        if len(values) != n:
            raise ProviderError(f"{var} length does not match time")
        cols[var] = values
    tz = payload.get("timezone") or "UTC"
    df = pd.DataFrame(cols, dtype="float64")
    df.index = pd.to_datetime(hourly["time"], unit="s", utc=True).tz_convert(tz)
    df.index.name = "time"
    if not df.index.is_unique or not df.index.is_monotonic_increasing:
        raise ProviderError("Archive timestamps are not in order")
    if df["temperature_2m"].isna().all():
        raise ProviderError("The archive has no temperature data for this place and range.")
    return df


def fetch_history(place: Place, start: date, end: date, today: date | None = None, session=None) -> pd.DataFrame:
    validate_range(start, end, today or date.today())
    params = {"latitude": place.latitude, "longitude": place.longitude, "start_date": start.isoformat(),
              "end_date": end.isoformat(), "hourly": ",".join(VARIABLES), "timezone": "auto",
              "timeformat": "unixtime"}
    try:
        payload = fetch_json(ARCHIVE_URL, params, session=session, timeout=25, retries=2, max_wait_s=5)
    except DataCollectionError as exc:
        raise ProviderError(f"Couldn't load historical data: {exc}") from exc
    return within_local_dates(parse_history(payload), start, end)


def within_local_dates(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Keep hours whose *local* calendar date is in [start, end].

    With unix timestamps the archive anchors the range using one fixed UTC
    offset (the place's offset at request time). Across a daylight-saving
    change, that puts an hour of the previous evening at the start (or drops
    one at the end). Trimming by local date fixes both.
    """
    dates = pd.Series(df.index.date, index=df.index)
    return df[(dates >= start) & (dates <= end)]


def daily_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Daily aggregates in the place's local calendar (metric units). Missing stays missing."""
    g = df.groupby(df.index.date)
    out = pd.DataFrame({
        "temp_mean_c": g["temperature_2m"].mean(), "temp_min_c": g["temperature_2m"].min(),
        "temp_max_c": g["temperature_2m"].max(),
        "precipitation_mm": g["precipitation"].sum(min_count=1),
        "humidity_mean_pct": g["relative_humidity_2m"].mean(),
        "wind_mean_kmh": g["wind_speed_10m"].mean(), "gust_max_kmh": g["wind_gusts_10m"].max(),
        "hours": g["temperature_2m"].count(),
    })
    out.index = pd.to_datetime(out.index)
    out.index.name = "date"
    return out


def overview(daily: pd.DataFrame) -> dict:
    """Headline numbers for the selected period."""
    if daily.empty:
        return {}
    hottest, coldest = daily["temp_max_c"].idxmax(), daily["temp_min_c"].idxmin()
    wettest = daily["precipitation_mm"].idxmax() if daily["precipitation_mm"].notna().any() else None
    return {
        "days": int(len(daily)),
        "mean_c": float(daily["temp_mean_c"].mean()),
        "hottest": (hottest, float(daily.loc[hottest, "temp_max_c"])),
        "coldest": (coldest, float(daily.loc[coldest, "temp_min_c"])),
        "total_precip_mm": float(daily["precipitation_mm"].sum(min_count=1)) if daily["precipitation_mm"].notna().any() else None,
        "wet_days": int((daily["precipitation_mm"] >= 1).sum()),
        "wettest": (wettest, float(daily.loc[wettest, "precipitation_mm"])) if wettest is not None else None,
    }
