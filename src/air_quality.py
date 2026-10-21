"""Air-quality data from the Open-Meteo Air Quality API (CAMS model forecasts).

What the data are, and what they aren't:
- **Source:** Copernicus Atmosphere Monitoring Service (CAMS) forecasts, served by Open-Meteo.
  Open-Meteo picks the domain for each location: CAMS European (0.1°, about 11 km, hourly,
  4-day forecast, updated every 24 h) inside Europe, otherwise CAMS Global (0.4°, about 45 km,
  3-hourly values interpolated to hours, 5-day forecast, updated every 12 h). The response
  doesn't say which one was used, so the app names both.
- **Model estimates, not observations.** Every value, including "now" and the past hours, comes
  from the model. Past hours are the model's values for those hours, not station readings. The
  app never calls them observations.
- **Coverage:** the API is asked for 7 forecast days, but the models stop earlier (about 4-5
  days). Hours past the model's end come back empty; they are trimmed and the real end of the
  forecast is reported as `coverage_end`.
- **Indices:** US AQI and European AQI come from Open-Meteo. India's NAQI is calculated by
  src.aqi from the concentrations.

Time handling follows src.provider: `timeformat=unixtime`, so hourly times are unambiguous
instants, converted to the location's time zone (correct across daylight-saving changes).

Units are checked exactly as the API reports them ("μg/m³", "USAQI", "EAQI"); an unexpected unit
on a variable with values is an error, never converted by guesswork.

Run:  python -m src.air_quality "New Delhi"
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import requests

from src import aqi
from src.data_collection import INTERACTIVE, DataCollectionError, fetch_json
from src.provider import Place

AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
SOURCE_LABEL = "CAMS air-quality forecast via Open-Meteo"
DOMAIN_NOTE = ("CAMS European model (about 11 km) inside Europe, otherwise CAMS Global (about 45 km); "
               "Open-Meteo chooses the domain for each location.")
ATTRIBUTION = ("Air quality: Copernicus Atmosphere Monitoring Service (CAMS) forecasts, via Open-Meteo.com "
               "(CC BY 4.0). Contains modified Copernicus Atmosphere Monitoring Service information.")
KIND_NOTE = "Model estimates for this location, not monitoring-station readings."
PAST_DAYS = 2          # 24-hour averages (India NAQI) need the previous day; also shows recent model values
FORECAST_DAYS = 7      # the most the API allows; the models themselves end after about 4-5 days

UG = "μg/m³"
CONCENTRATIONS = ("pm2_5", "pm10", "nitrogen_dioxide", "ozone", "sulphur_dioxide", "carbon_monoxide", "dust")
HOURLY_UNITS = {
    **{c: UG for c in CONCENTRATIONS},
    "us_aqi": "USAQI", **{col: "USAQI" for col in aqi.US_EPA.sub_columns.values()},
    "european_aqi": "EAQI", **{col: "EAQI" for col in aqi.EU_EAQI.sub_columns.values()},
}
CURRENT_UNITS = {"us_aqi": "USAQI", "european_aqi": "EAQI",
                 **{c: UG for c in CONCENTRATIONS if c != "dust"}}
# Physical plausibility only (dust storms push PM10 into thousands). Values outside become missing.
BOUNDS = {"pm2_5": (0, 5000), "pm10": (0, 10000), "dust": (0, 10000), "nitrogen_dioxide": (0, 5000),
          "ozone": (0, 2000), "sulphur_dioxide": (0, 10000), "carbon_monoxide": (0, 200000)}
AQI_BOUNDS = (0, 3000)
DUST_NOTABLE_UGM3 = 50   # mentioned in the explanation from here (the WHO 24-hour PM10 guideline is 45)
# The micro sign has two Unicode spellings (U+03BC and U+00B5); both mean the same unit.
_UNIT_ALIASES = {"µg/m³": UG, "ug/m3": UG}


class AirQualityError(RuntimeError):
    """The air-quality response can't be used."""


@dataclass
class AirQuality:
    place: Place
    retrieved_at_utc: str
    grid_latitude: float | None
    grid_longitude: float | None
    hourly: pd.DataFrame            # tz-aware local index; concentrations, provider indices and NAQI columns
    current: dict[str, Any]
    coverage_end: pd.Timestamp      # last hour with a forecast value
    warnings: list[str] = field(default_factory=list)
    source_label: str = SOURCE_LABEL
    attribution: str = ATTRIBUTION

    def forecast_hours(self, now: pd.Timestamp) -> pd.DataFrame:
        """Rows from the current hour to the end of the model's forecast."""
        start = now.tz_convert(self.hourly.index.tz).floor("h")
        return self.hourly.loc[start:self.coverage_end]

    def past_hours(self, now: pd.Timestamp) -> pd.DataFrame:
        """Model values for hours before the current one (not observations)."""
        start = now.tz_convert(self.hourly.index.tz).floor("h")
        return self.hourly.loc[:start - pd.Timedelta(hours=1)]

    def at(self, ts: pd.Timestamp) -> pd.Series | None:
        """The row for the hour containing `ts`, or None outside the data."""
        t = ts.tz_convert(self.hourly.index.tz).floor("h")
        return self.hourly.loc[t] if t in self.hourly.index and t <= self.coverage_end else None


def _unit(u) -> str | None:
    return _UNIT_ALIASES.get(u, u) if isinstance(u, str) else None


def _all_missing(values) -> bool:
    return values is None or (isinstance(values, list) and all(v is None for v in values))


def parse_air_quality(payload: dict, place: Place, retrieved_at: str) -> AirQuality:
    """Validate an Air Quality API response and turn it into a tidy, time-zone-aware table."""
    if not isinstance(payload, dict):
        raise AirQualityError("Air-quality response is not a JSON object")
    if payload.get("error"):
        raise AirQualityError(f"Air Quality API error: {payload.get('reason', 'no reason given')}")
    raw = payload.get("hourly")
    if not isinstance(raw, dict):
        raise AirQualityError("Air-quality response has no hourly data")
    units = payload.get("hourly_units") or {}
    if units.get("time") != "unixtime":
        raise AirQualityError(f"hourly.time: expected unixtime, got {units.get('time')!r}")
    times = raw.get("time")
    if not isinstance(times, list) or not times:
        raise AirQualityError("Air-quality response contains no hourly timestamps")
    tz = payload.get("timezone") or place.timezone
    warnings: list[str] = []

    data: dict[str, list] = {}
    for var, unit in HOURLY_UNITS.items():
        values = raw.get(var)
        if _all_missing(values):
            data[var] = [None] * len(times)
            warnings.append(f"{var}: not available for this location")
            continue
        if not isinstance(values, list) or len(values) != len(times):
            raise AirQualityError(f"hourly.{var} length does not match time")
        if _unit(units.get(var)) != unit:
            raise AirQualityError(f"hourly.{var}: expected unit {unit!r}, got {units.get(var)!r}")
        data[var] = values
    hourly = pd.DataFrame(data, dtype=float)
    hourly.index = pd.to_datetime(times, unit="s", utc=True).tz_convert(tz)
    hourly.index.name = "time"
    if not hourly.index.is_unique or not ((hourly.index[1:] - hourly.index[:-1]) == pd.Timedelta("1h")).all():
        raise AirQualityError("Air-quality timestamps are not a consecutive hourly sequence")

    for col in hourly.columns:
        lo, hi = BOUNDS.get(col, AQI_BOUNDS)
        bad = hourly[col].notna() & ~hourly[col].between(lo, hi)
        if bad.any():
            warnings.append(f"{col}: {int(bad.sum())} implausible values removed")
            hourly.loc[bad, col] = float("nan")

    # The forecast ends where the model ends: trim trailing hours with no pollutant values at all.
    has_any = hourly[["pm2_5", "pm10", "us_aqi", "european_aqi"]].notna().any(axis=1)
    if not has_any.any():
        raise AirQualityError("The air-quality model has no values for this location")
    coverage_end = has_any[has_any].index.max()
    hourly = hourly.loc[:coverage_end]
    gaps = int((~has_any.loc[:coverage_end]).sum())
    if gaps:
        warnings.append(f"{gaps} hour(s) inside the forecast have no values; they're shown as gaps")

    hourly = hourly.join(aqi.naqi_frame(hourly))

    cur_raw: dict = payload["current"] if isinstance(payload.get("current"), dict) else {}
    cur_units = payload.get("current_units") or {}
    current: dict[str, Any] = {"time": None, "interval_seconds": cur_raw.get("interval")}
    if "time" in cur_raw:
        current["time"] = pd.Timestamp(cur_raw["time"], unit="s", tz="UTC").tz_convert(tz).isoformat()
    for var, unit in CURRENT_UNITS.items():
        val = cur_raw.get(var)
        if val is not None and _unit(cur_units.get(var)) != unit:
            raise AirQualityError(f"current.{var}: expected unit {unit!r}, got {cur_units.get(var)!r}")
        lo, hi = BOUNDS.get(var, AQI_BOUNDS)
        if val is not None and not (lo <= val <= hi):
            warnings.append(f"current.{var}: implausible value {val} removed")
            val = None
        current[var] = val

    return AirQuality(place=place, retrieved_at_utc=retrieved_at, grid_latitude=payload.get("latitude"),
                      grid_longitude=payload.get("longitude"), hourly=hourly, current=current,
                      coverage_end=coverage_end, warnings=warnings)


def fetch_air_quality(place: Place, session: requests.Session | None = None) -> AirQuality:
    params = {
        "latitude": place.latitude, "longitude": place.longitude,
        "hourly": ",".join(HOURLY_UNITS), "current": ",".join(CURRENT_UNITS),
        "timezone": "auto", "timeformat": "unixtime", "past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS,
    }
    try:
        payload = fetch_json(AIR_QUALITY_URL, params, session=session, **INTERACTIVE)
    except DataCollectionError as exc:
        raise AirQualityError(f"Could not reach the air-quality provider: {exc}") from exc
    return parse_air_quality(payload, place, datetime.now(UTC).isoformat(timespec="seconds"))


# --- Reading the data ---------------------------------------------------------------------------

@dataclass
class Reading:
    """One index value, ready to show: never a number without its standard and time."""
    standard: aqi.Standard
    value: float | None
    open_ended: bool
    category: aqi.Category | None
    dominant: str | None
    time: pd.Timestamp | None
    note: str = ""

    @property
    def text(self) -> str:
        return aqi.index_text(self.standard, self.value, self.open_ended)


def reading(data: AirQuality, standard: aqi.Standard, now: pd.Timestamp) -> Reading:
    """The index for the current hour (the hourly table, so all three standards use the same hour)."""
    row = data.at(now)
    if row is None:
        return Reading(standard, None, False, None, None, None,
                       "The air-quality data don't cover the current hour.")
    value = row.get(standard.column)
    open_ended = bool(row.get("naqi_open")) if standard is aqi.IN_NAQI else False
    note = ""
    if pd.isna(value):
        value = None
        note = (f"India's NAQI can't be calculated for this hour: {row.get('naqi_reason')}."
                if standard is aqi.IN_NAQI else f"{standard.short} is not available for this hour.")
    return Reading(standard, value, open_ended, standard.category(value), aqi.dominant_pollutant(standard, row),
                   now.tz_convert(data.hourly.index.tz).floor("h"), note)


def rolling_mean(data: AirQuality, pollutant: str, hours: int, at: pd.Timestamp) -> float | None:
    """Mean of the `hours` hourly model values up to and including the hour of `at` (≥ 75% present)."""
    t = at.tz_convert(data.hourly.index.tz).floor("h")
    s = data.hourly[pollutant].loc[t - pd.Timedelta(hours=hours - 1):t]
    if len(s) < hours or s.notna().sum() < 0.75 * hours:
        return None
    return float(s.mean())


def daily_summary(data: AirQuality, standard: aqi.Standard, now: pd.Timestamp) -> pd.DataFrame:
    """Per local day from today: lowest and highest hourly index, the worst category, dominant pollutant,
    mean PM2.5 and how many hours the forecast covers (a partial day is labelled as such)."""
    future = data.forecast_hours(now)
    rows = []
    for day, g in future.groupby(future.index.date):
        vals = g[standard.column].dropna()
        if vals.empty:
            continue
        worst_t = vals.idxmax()
        open_ended = bool(g.loc[worst_t].get("naqi_open")) if standard is aqi.IN_NAQI else False
        cat = standard.category(vals.max())
        rows.append({"date": day, "hours": len(g), "low": vals.min(), "high": vals.max(), "high_open": open_ended,
                     "worst_category": cat.label if cat else None, "worst_index": cat.index if cat else None,
                     "worst_time": worst_t, "dominant": aqi.dominant_pollutant(standard, g.loc[worst_t]),
                     "pm2_5_mean": g["pm2_5"].mean()})
    return pd.DataFrame(rows)


def category_changes(data: AirQuality, standard: aqi.Standard, now: pd.Timestamp) -> list[dict]:
    """Every forecast change of category from the current hour on: when, from, to, and the values.

    These are the 'improving' and 'deteriorating' periods; each is a fact read from the forecast.
    """
    future = data.forecast_hours(now)[standard.column]
    out: list[dict] = []
    prev_cat, prev_t, prev_v = None, None, None
    for t, v in future.items():
        cat = standard.category(v)
        if cat is None:
            continue
        if prev_cat is not None and cat.index != prev_cat.index:
            out.append({"time": t, "from": prev_cat, "to": cat, "from_value": prev_v, "to_value": v,
                        "direction": "worse" if cat.index > prev_cat.index else "better", "since": prev_t})
        prev_cat, prev_t, prev_v = cat, t, v
    return out


def current_factors(data: AirQuality, standard: aqi.Standard, now: pd.Timestamp) -> list[tuple[str, str, str]]:
    """Short facts about what sets the index now and how it's forecast to move: (icon, text, basis).

    Each sentence restates values from the data. None of them claims a cause (no "because of wind").
    """
    out: list[tuple[str, str, str]] = []
    row = data.at(now)
    if row is None:
        return [("help", "The air-quality data don't cover the current hour, so there's nothing to explain.", "Data")]
    subs = {p: row.get(c) for p, c in standard.sub_columns.items()}
    subs = {p: float(v) for p, v in subs.items() if v is not None and pd.notna(v)}
    dom = aqi.dominant_pollutant(standard, row)
    if dom and len(subs) >= 2:
        ranked = sorted(subs.items(), key=lambda kv: -kv[1])
        second = ranked[1]
        out.append(("science", f"{aqi.POLLUTANTS[dom].symbol} sets the {standard.short} now (sub-index "
                               f"{ranked[0][1]:.0f}; next highest {aqi.POLLUTANTS[second[0]].symbol} at {second[1]:.0f}).",
                    "Now · model estimate"))
    elif row.get(standard.column) is not None and pd.notna(row.get(standard.column)):
        out.append(("science", "The pollutant sub-indices don't single out one main pollutant this hour.",
                    "Now · model estimate"))
    # CAMS models dust as its own field (it can exceed PM10, as it includes larger particles), so it is
    # reported on its own and never as a share of PM10.
    dust = row.get("dust")
    if dust is not None and pd.notna(dust) and dust >= DUST_NOTABLE_UGM3:
        out.append(("landscape", f"The model also shows desert dust near the ground ({dust:,.0f} μg/m³, a separate "
                                 "CAMS estimate).", "Now · model estimate"))
    nxt = data.forecast_hours(now).iloc[:24]
    pm = nxt["pm2_5"].dropna()
    if len(pm) >= 6:
        out.append(("show_chart", f"Hourly PM2.5 is forecast to be lowest around {pm.idxmin():%a %H:%M} "
                                  f"({pm.min():,.0f} μg/m³) and highest around {pm.idxmax():%a %H:%M} "
                                  f"({pm.max():,.0f} μg/m³) in the next 24 hours.", "Forecast"))
    changes = [c for c in category_changes(data, standard, now) if c["time"] <= nxt.index.max()] if len(nxt) else []
    for c in changes[:3]:
        word = "worsen" if c["direction"] == "worse" else "improve"
        out.append(("trending_up" if c["direction"] == "worse" else "trending_down",
                    f"Forecast to {word} from {c['from'].label} to {c['to'].label} around {c['time']:%a %H:%M} "
                    f"({standard.short} {c['from_value']:.0f} → {c['to_value']:.0f}).", "Forecast"))
    if len(nxt) and not changes:
        cat = standard.category(row.get(standard.column))
        if cat:
            out.append(("trending_flat", f"No change of category is forecast in the next {len(nxt)} hours; it stays "
                                         f"{cat.label}.", "Forecast"))
    if standard is aqi.US_EPA:
        out.append(("info", "The US AQI uses 24-hour averages for particles, so it moves slowly; the hourly PM2.5 "
                            "values show shorter swings.", "Method"))
    elif standard is aqi.IN_NAQI:
        out.append(("info", "India's NAQI uses 24-hour averages (8-hour for ozone and CO), so it moves slowly; the "
                            "hourly PM2.5 values show shorter swings.", "Method"))
    return out


def main() -> None:
    from src.provider import search_locations
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("place", nargs="?", default="New Delhi")
    args = parser.parse_args()
    places = search_locations(args.place)
    if not places:
        print(f"No place found for {args.place!r}.")
        return
    data = fetch_air_quality(places[0])
    now = pd.Timestamp.now(tz="UTC")
    standard, note = aqi.default_standard(places[0].country_code)
    r = reading(data, standard, now)
    print(f"\n{data.source_label} — {data.place.label}\n  {KIND_NOTE}")
    print(f"  now: {standard.name} {r.text} ({r.category.label if r.category else 'unavailable'})  {note}")
    print(f"  forecast until {data.coverage_end:%a %d %b %H:%M} local time")
    for w in data.warnings:
        print("  note:", w)
    print(f"\n  {data.attribution}")


if __name__ == "__main__":
    main()
