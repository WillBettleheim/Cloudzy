"""Real thermometer readings for Delhi: routine METAR reports from the city's airport weather stations.

Why: Cloudzy Core is trained and scored against a reanalysis (ERA5-Land via Open-Meteo), which is itself model
output for a ~9 km grid cell. Station readings are independent measurements, so they show how Cloudzy's numbers
compare with what a thermometer actually recorded (src.station_eval).

Source: the Iowa Environmental Mesonet (IEM, Iowa State University) ASOS/METAR archive, which stores the METARs
that stations worldwide transmit on the WMO network. NOAA's ISD archive has the same stations but stopped
updating in October 2025 when checked on 2026-10-22, so it doesn't cover the 2026 test period.

| Station | ICAO | Position (IEM) | Distance from Cloudzy's grid point (28.6139 N, 77.209 E) |
|---|---|---|---|
| Indira Gandhi Intl (Palam) | VIDP | 28.5667 N, 77.1167 E | ~10.4 km (primary) |
| Safdarjung | VIDD | 28.5833 N, 77.2107 E | ~3.4 km (secondary, July 2026 on) |

Why Palam is primary although Safdarjung is closer: in the IEM archive Safdarjung sent almost no routine METARs
before July 2026 (under 2% of hours per month), so it can't cover the January-August 2026 test period. Palam
covers more than 99% of hours. This was decided from coverage alone, before any model was scored.

Data checks (2026-10-22)
- IEM's Palam values match NOAA ISD's copy of the same METARs (FM-15) exactly at 99.9% of 5,541 hours in 2025.
- The same station's 3-hourly SYNOP reports (FM-12, in tenths) differ from its METAR at the same hour by
  0.6 °C on average (1,842 hours): observation uncertainty is not zero, and no model can be expected to reach it.

Matching and quality control (documented, applied identically to every model)
- Only routine reports (IEM report_type 3); specials are left out.
- Hourly value = the report at the exact hour (:00); if there is none, the nearest within 10 minutes. No
  interpolation, no filling.
- Physically implausible values (outside -5..52 °C for Delhi) are dropped.
- Spikes are dropped: a value more than 6 °C away from *both* neighbouring hours, in the same direction.
- METAR temperatures are reported in whole degrees, so each reading carries up to ±0.5 °C rounding (an MAE of
  about 0.25 °C on its own if everything else were perfect). Every model is compared with the same readings.

Raw downloads are cached in data/raw/stations/ (git-ignored).
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from src import config
from src.hazards import http

logger = logging.getLogger(__name__)

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
CACHE_DIR = config.RAW_DIR / "stations"
PLAUSIBLE_C = (-5.0, 52.0)
SPIKE_C = 6.0
MATCH_MINUTES = 10
ATTRIBUTION = "Station observations: METAR via the Iowa Environmental Mesonet, Iowa State University"


@dataclass(frozen=True)
class Station:
    icao: str
    name: str
    lat: float
    lon: float


STATIONS = {s.icao: s for s in (
    Station("VIDD", "Safdarjung", 28.5833, 77.2107),
    Station("VIDP", "Indira Gandhi International (Palam)", 28.5667, 77.1167),
)}
PRIMARY = "VIDP"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def fetch_metar(icao: str, start: date, end: date, cache_dir: Path = CACHE_DIR,
                session: requests.Session | None = None) -> str:
    """Raw IEM CSV (station, valid, tmpc) for [start, end), downloaded once and cached."""
    path = cache_dir / f"{icao}_metar_{start}_{end}.csv"
    if path.exists():
        return path.read_text()
    params = {"station": icao, "data": "tmpc", "year1": start.year, "month1": start.month, "day1": start.day,
              "year2": end.year, "month2": end.month, "day2": end.day, "tz": "Etc/UTC", "format": "onlycomma",
              "latlon": "no", "missing": "M", "trace": "T", "direct": "no", "report_type": 3}
    text = http.get(IEM_URL, params, kind="text", session=session, timeout=120)
    parse_metar(text)                       # validate before caching
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def parse_metar(text: str) -> pd.DataFrame:
    """IEM CSV → DataFrame(valid UTC, tmpc float). Missing values ("M") become NaN."""
    df = pd.read_csv(io.StringIO(text), na_values=["M"])
    if not {"station", "valid", "tmpc"} <= set(df.columns):
        raise ValueError(f"unexpected IEM columns: {list(df.columns)[:6]}")
    df["valid"] = pd.to_datetime(df["valid"], utc=True)
    df["tmpc"] = pd.to_numeric(df["tmpc"], errors="coerce")
    return df[["station", "valid", "tmpc"]]


def hourly(df: pd.DataFrame) -> tuple[pd.Series, dict]:
    """Top-of-hour temperatures with quality control. Returns (series, counts of what was kept and dropped)."""
    obs = df.dropna(subset=["tmpc"]).copy()
    obs["hour"] = obs["valid"].dt.round("h")
    obs["off"] = (obs["valid"] - obs["hour"]).abs()
    obs = obs[obs["off"] <= pd.Timedelta(minutes=MATCH_MINUTES)].sort_values(["hour", "off"])
    s = obs.drop_duplicates("hour").set_index("hour")["tmpc"].astype(float)
    s.index.name = "time"
    counts = {"reports": len(df), "hours_matched": len(s)}
    lo, hi = PLAUSIBLE_C
    bad = (s < lo) | (s > hi)
    counts["dropped_implausible"] = int(bad.sum())
    s = s[~bad]
    full = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="h"))
    d_prev, d_next = full - full.shift(1), full - full.shift(-1)
    spike = ((d_prev > SPIKE_C) & (d_next > SPIKE_C)) | ((d_prev < -SPIKE_C) & (d_next < -SPIKE_C))
    counts["dropped_spikes"] = int(spike.sum())
    s = full[~spike].dropna()
    counts["hours_kept"] = len(s)
    return s, counts


def load(icao: str, start: date, end: date, cache_dir: Path = CACHE_DIR,
         session: requests.Session | None = None) -> tuple[pd.Series, dict]:
    series, counts = hourly(parse_metar(fetch_metar(icao, start, end, cache_dir, session)))
    st = STATIONS[icao]
    counts["distance_km_from_grid_point"] = round(distance_km(st.lat, st.lon, config.DELHI.latitude,
                                                             config.DELHI.longitude), 1)
    return series, counts


def coverage_by_month(series: pd.Series) -> dict[str, float]:
    """Share of hours with a kept reading, per calendar month (to spot gaps)."""
    months = series.groupby(series.index.tz_convert(None).to_period("M")).size()
    days = np.array([p.days_in_month for p in months.index])
    return {str(p): round(float(n / (24 * d)), 3) for p, n, d in zip(months.index, months.to_numpy(), days)}
