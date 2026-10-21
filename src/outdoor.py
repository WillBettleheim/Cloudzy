"""Cloudzy Outdoor Intelligence: a transparent rule engine for planning time outside.

For every forecast hour it checks air quality, heat, cold, sun, rain, storms and wind against
published guidance, and gives one of four results:

- **ok**: suitable conditions, given the rules below.
- **caution**: acceptable with care (shorter, easier, protection, or rain gear).
- **avoid**: better to postpone or move indoors.
- **unavailable**: an essential measurement is missing, so Cloudzy doesn't judge the hour.

An hour takes the *worst* result of its checks. A window of several hours takes the worst of its
hours. So a failed critical check (thunderstorm, very unhealthy air, dangerous heat) can never
be outweighed by pleasant temperatures or sunshine. There is no weighted score.

Windows with the same result are ranked by plain comparisons, in this order: fewer caution
factors, better air-quality category, lower mean PM2.5, lower heat index (or temperature), lower
UV for sun-exposed activities, lower rain chance, and then the earlier start. Nothing here is a
validated health or safety measure, and Cloudzy never says a time is safe.

Thresholds and their sources (details in docs/AIR_QUALITY.md):
- Air quality: the selected AQI standard's own guidance (src.aqi). The optional "more
  conservative" setting uses its guidance for sensitive groups.
- Heat: the US National Weather Service heat index (Rothfusz regression with NWS adjustments) and
  its categories: caution 80 °F (26.7 °C), extreme caution 90 °F (32.2 °C), danger 103 °F
  (39.4 °C), extreme danger 125 °F (51.7 °C). The heat index assumes shade and light wind; full
  sun can add up to about 8 °C. It is not WBGT, which Cloudzy has no reliable source for.
- Cold: the North American wind chill index (Environment and Climate Change Canada / NWS), using
  ECCC's risk bands: -10 to -27 uncomfortable, from -28 exposed skin can freeze in 10-30 minutes.
- UV: WHO UV index categories; protection from 3, extra protection from 8.
- Thunderstorms: forecast WMO codes 95, 96, 99 → avoid (NWS: "When thunder roars, go indoors").
  Freezing rain or drizzle → avoid (ice). Heavy rain (≥ 7.6 mm/h, the American Meteorological
  Society's heavy-rain rate) → caution.
- Wind: Beaufort scale descriptions (WMO): near gale 50-61 km/h makes walking against the wind
  difficult, gale 62-74 km/h impedes progress. Cyclists are affected earlier.

All inputs are metric, as the providers return them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from src import aqi
from src import units as U
from src.air_quality import AirQuality
from src.provider import ProviderForecast

OK, CAUTION, AVOID, UNAVAILABLE = "ok", "caution", "avoid", "unavailable"
RANK = {OK: 0, CAUTION: 1, AVOID: 2, UNAVAILABLE: 3}
LEVEL_LABELS = {OK: "Suitable", CAUTION: "Acceptable with caution", AVOID: "Better to postpone or move indoors",
                UNAVAILABLE: "Not enough data"}
LEVEL_CHIPS = {OK: ("ok", "check_circle"), CAUTION: ("warn", "error"), AVOID: ("risk", "block"),
               UNAVAILABLE: ("muted", "help")}
DISCLAIMER = ("Suggestions come from forecasts and published guidance, not from a medical assessment. Forecasts "
              "change and people react differently to heat and pollution, so no time can be promised as safe. "
              "Follow official warnings and your own health advice.")
SENSITIVITIES = ("standard", "conservative")
SENSITIVITY_HELP = ("More conservative guidance uses the air-quality standard's advice for sensitive groups (for "
                    "example people with asthma, heart or lung conditions, children and older adults), and starts "
                    "heat cautions one heat-index category earlier. Cloudzy doesn't ask why, and doesn't store "
                    "anything about your health.")
LONG_LEAD_H = 72


@dataclass(frozen=True)
class Activity:
    key: str
    name: str
    icon: str
    heavy: bool                 # heavy or prolonged exertion (AQI guidance distinguishes this)
    wind_sensitive: bool        # wind affects it more (cycling)
    indoor_alternative: str
    default_minutes: int = 60


ACTIVITIES = {
    "running": Activity("running", "Running", "directions_run", True, False,
                        "a treadmill or an indoor track", 45),
    "walking": Activity("walking", "Walking", "directions_walk", False, False,
                        "an indoor walk, such as a covered mall or a gym track", 60),
    "cycling": Activity("cycling", "Cycling", "directions_bike", True, True,
                        "an indoor trainer or a spin class", 60),
    "exercise": Activity("exercise", "Outdoor exercise", "fitness_center", True, False,
                         "an indoor workout, such as a gym or a home routine", 45),
    "casual": Activity("casual", "Casual time outside", "park", False, False,
                       "an indoor venue, such as a museum, library or café", 60),
}
DURATIONS_MIN = (30, 45, 60, 90, 120, 180)


@dataclass
class Factor:
    kind: str                     # air, heat, cold, uv, storm, rain, snow, ice, wind, data
    level: str                    # ok, caution, avoid, unavailable, or "info"
    text: str
    measurements: tuple[str, ...] = ()
    rule: str = ""


@dataclass
class HourAssessment:
    time: pd.Timestamp
    level: str
    factors: list[Factor]
    values: dict

    def limiting(self) -> list[Factor]:
        return [f for f in self.factors if f.level in (CAUTION, AVOID, UNAVAILABLE)]


@dataclass
class Window:
    start: pd.Timestamp
    end: pd.Timestamp
    level: str
    hours: list[HourAssessment]
    summary: dict
    supporting: list[str] = field(default_factory=list)
    limiting: list[Factor] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- Physical indices -------------------------------------------------------------------------------

HEAT_CATEGORIES = ((26.7, "Caution"), (32.2, "Extreme caution"), (39.4, "Danger"), (51.7, "Extreme danger"))


def heat_index_c(temp_c, rh) -> float | None:
    """NWS heat index in °C from air temperature (°C) and relative humidity (%)."""
    if temp_c is None or rh is None or pd.isna(temp_c) or pd.isna(rh):
        return None
    t = temp_c * 9 / 5 + 32
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        hi = simple
    else:
        hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh - 0.00683783 * t * t
              - 0.05481717 * rh * rh + 0.00122874 * t * t * rh + 0.00085282 * t * rh * rh
              - 0.00000199 * t * t * rh * rh)
        if rh < 13 and 80 <= t <= 112:
            hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
        elif rh > 85 and 80 <= t <= 87:
            hi += ((rh - 85) / 10) * ((87 - t) / 5)
    return (hi - 32) * 5 / 9


def heat_category(hi_c) -> int:
    """0 = below the NWS caution level, 1 caution, 2 extreme caution, 3 danger, 4 extreme danger."""
    if hi_c is None or pd.isna(hi_c):
        return 0
    return sum(hi_c >= lo for lo, _ in HEAT_CATEGORIES)


def heat_label(cat: int) -> str:
    return "Below caution level" if cat == 0 else HEAT_CATEGORIES[cat - 1][1]


def wind_chill_c(temp_c, wind_kmh) -> float | None:
    """Wind chill (°C) for T ≤ 10 °C and wind ≥ 4.8 km/h; None where the index isn't defined."""
    if temp_c is None or wind_kmh is None or pd.isna(temp_c) or pd.isna(wind_kmh):
        return None
    if temp_c > 10 or wind_kmh < 4.8:
        return None
    v = wind_kmh ** 0.16
    return 13.12 + 0.6215 * temp_c - 11.37 * v + 0.3965 * temp_c * v


def uv_label(uv) -> str:
    if uv is None or pd.isna(uv):
        return "unavailable"
    return "low" if uv < 3 else "moderate" if uv < 6 else "high" if uv < 8 else "very high" if uv < 11 else "extreme"


# --- Combining the two providers ----------------------------------------------------------------------

def combined_table(aq: AirQuality, forecast: ProviderForecast, standard: aqi.Standard) -> pd.DataFrame:
    """One hourly table (local time) with the weather forecast and air quality for the same instants.

    Only hours present in the weather forecast are kept; air-quality columns are missing (NaN) past
    the air-quality model's end, and those hours become "unavailable".
    """
    wx = forecast.hourly.copy()
    tz = wx.index.tz
    air = aq.hourly.tz_convert(tz)
    cols = [standard.column, "pm2_5", "pm10", "ozone", "nitrogen_dioxide"] + (
        ["naqi_open", "naqi_reason"] if standard is aqi.IN_NAQI else [])
    table = wx.join(air[cols].rename(columns={standard.column: "aqi"}), how="left")
    table["heat_index"] = [heat_index_c(t, h) for t, h in zip(table["temperature_2m"], table["relative_humidity_2m"])]
    table["wind_chill"] = [wind_chill_c(t, w) for t, w in zip(table["temperature_2m"], table["wind_speed_10m"])]
    return table


# --- One hour -----------------------------------------------------------------------------------------

STORM_CODES = {95, 96, 99}
FREEZING_CODES = {56, 57, 66, 67}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
HEAVY_SNOW_CODES = {75, 86}
HEAVY_RAIN_CODES = {65, 82}
HEAVY_RAIN_MM_H = 7.6
RAIN_LIKELY_PCT, RAIN_LIKELY_MM = 50, 0.5      # the same rain indicator as the Weather page
RAIN_POSSIBLE_PCT = 30


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


def _uv(v):
    """The UV index is reported as a whole number (WHO), so categories and text use the rounded value."""
    return None if v is None or pd.isna(v) else float(round(v))


def assess_hour(row: pd.Series, activity: Activity, standard: aqi.Standard, sensitivity: str = "standard",
                time: pd.Timestamp | None = None, units: U.Units = U.METRIC) -> HourAssessment:
    """Every rule for one hour. Inputs are metric; the result lists each check and its reason."""
    sensitive = sensitivity == "conservative"
    t = time if time is not None else row.name
    temp, rh, code = _num(row.get("temperature_2m")), _num(row.get("relative_humidity_2m")), _num(row.get("weather_code"))
    aqi_v = _num(row.get("aqi"))
    open_ended = bool(row.get("naqi_open")) if standard is aqi.IN_NAQI and aqi_v is not None else False
    values = {"aqi": aqi_v, "aqi_open": open_ended, "pm2_5": _num(row.get("pm2_5")), "temperature": temp,
              "humidity": rh, "apparent": _num(row.get("apparent_temperature")),
              "heat_index": _num(row.get("heat_index")), "wind_chill": _num(row.get("wind_chill")),
              "uv": _uv(row.get("uv_index")), "precip_prob": _num(row.get("precipitation_probability")),
              "precip": _num(row.get("precipitation")), "wind": _num(row.get("wind_speed_10m")),
              "gust": _num(row.get("wind_gusts_10m")), "code": code, "is_day": _num(row.get("is_day"))}
    factors: list[Factor] = []

    # Essential measurements: without them, Cloudzy doesn't judge the hour.
    missing = [name for name, v in (("air-quality index", aqi_v), ("temperature", temp), ("weather condition", code))
               if v is None]
    if missing:
        why = ""
        if aqi_v is None and standard is aqi.IN_NAQI and isinstance(row.get("naqi_reason"), str):
            why = f" (NAQI: {row.get('naqi_reason')})"
        elif aqi_v is None:
            why = " (the air-quality forecast doesn't cover this hour)"
        factors.append(Factor("data", UNAVAILABLE, "Missing " + ", ".join(missing) + why + ".",
                              ("aqi", "temperature_2m", "weather_code"), "Essential data check"))
        return HourAssessment(t, UNAVAILABLE, factors, values)

    # Air quality.
    cat = standard.category(aqi_v)
    assert cat is not None                       # every non-missing value falls in a category
    level = cat.guidance(activity.heavy, sensitive)
    advice = cat.sensitive if sensitive else cat.general
    factors.append(Factor("air", level, f"{standard.short} {aqi.index_text(standard, aqi_v, open_ended)} "
                                        f"({cat.label}). {advice}",
                          (standard.column,), f"{standard.source} guidance for "
                          f"{'sensitive groups' if sensitive else 'everyone'}, "
                          f"{'heavy' if activity.heavy else 'light'} exertion"))

    # Thunderstorms, ice, snow, rain.
    ic = int(code)
    if ic in STORM_CODES:
        factors.append(Factor("storm", AVOID, "Thunderstorms are forecast. Lightning is a risk outdoors.",
                              ("weather_code",), "NWS lightning safety: when thunder roars, go indoors"))
    elif ic in FREEZING_CODES:
        factors.append(Factor("ice", AVOID, "Freezing rain or drizzle is forecast: surfaces may ice over.",
                              ("weather_code",), "WMO weather codes 56, 57, 66, 67"))
    elif ic in SNOW_CODES:
        lvl = AVOID if activity.key == "cycling" and ic in HEAVY_SNOW_CODES else CAUTION
        factors.append(Factor("snow", lvl, "Snow is forecast: expect slippery surfaces and low visibility.",
                              ("weather_code",), "WMO weather codes 71-77, 85, 86"))
    precip, prob = values["precip"], values["precip_prob"]
    if ic not in STORM_CODES | FREEZING_CODES | SNOW_CODES:
        if ic in HEAVY_RAIN_CODES or (precip is not None and precip >= HEAVY_RAIN_MM_H):
            factors.append(Factor("rain", CAUTION, "Heavy rain is forecast, which reduces visibility and grip.",
                                  ("weather_code", "precipitation"), "AMS heavy rain: 7.6 mm per hour or more"))
        elif (prob is not None and prob >= RAIN_LIKELY_PCT) or (precip is not None and precip >= RAIN_LIKELY_MM):
            factors.append(Factor("rain", CAUTION, f"Rain is likely ({_pct(prob)} chance, {_mm(precip, units)}).",
                                  ("precipitation_probability", "precipitation"),
                                  "Cloudzy rain indicator: ≥ 50% chance or ≥ 0.5 mm in the hour"))
        elif prob is not None and prob >= RAIN_POSSIBLE_PCT:
            factors.append(Factor("rain", "info", f"Rain is possible ({_pct(prob)} chance).",
                                  ("precipitation_probability",), "30-49% chance"))

    # Heat.
    hi = values["heat_index"]
    if temp is not None and rh is None and temp >= HEAT_CATEGORIES[0][0]:
        factors.append(Factor("data", "info", "Humidity is missing, so the heat index can't be calculated.",
                              ("relative_humidity_2m",), "NWS heat index needs humidity"))
    hcat = heat_category(hi)
    shifted = min(hcat + 1, 4) if sensitive and hcat > 0 else hcat
    if shifted:
        if shifted >= 3:
            lvl = AVOID
        elif shifted == 2:
            lvl = CAUTION
        else:
            lvl = CAUTION if activity.heavy else "info"
        sun = f" Full sun can make it feel up to about {_dt(8, units)} hotter." if (values["uv"] or 0) >= 3 else ""
        factors.append(Factor("heat", lvl, f"Heat index {_t(hi, units)}: NWS '{heat_label(hcat)}'.{sun}",
                              ("temperature_2m", "relative_humidity_2m"),
                              "NWS heat index categories" + (", one category earlier (conservative)"
                                                             if shifted != hcat else "")))

    # Cold (wind chill where defined).
    wc = values["wind_chill"]
    cold = wc if wc is not None else (temp if temp is not None and temp <= 10 else None)
    if cold is not None and cold <= -10:
        lvl = AVOID if cold <= -28 else CAUTION
        what = "wind chill" if wc is not None else "temperature"
        text = (f"Very cold: {what} {_t(cold, units)}. Exposed skin can freeze in 10-30 minutes." if lvl == AVOID
                else f"Cold: {what} {_t(cold, units)}. Cover exposed skin and dress in layers.")
        factors.append(Factor("cold", lvl, text, ("temperature_2m", "wind_speed_10m"),
                              "ECCC wind chill risk bands"))

    # Sun.
    uv = values["uv"]
    if uv is not None and uv >= 3:
        lvl = CAUTION if uv >= 8 else "info"
        text = (f"UV index {uv:.0f} ({uv_label(uv)}): extra protection; avoid midday sun if you can."
                if uv >= 8 else f"UV index {uv:.0f} ({uv_label(uv)}): sun protection is advisable.")
        factors.append(Factor("uv", lvl, text, ("uv_index",), "WHO UV index guidance"))

    # Wind.
    wind, gust = values["wind"], values["gust"]
    if activity.wind_sensitive:
        if gust is not None and gust >= 62:
            factors.append(Factor("wind", AVOID, f"Gale-force gusts up to {_kmh(gust, units)} make cycling hazardous.",
                                  ("wind_gusts_10m",), "Beaufort 8 (gale) from 62 km/h"))
        elif (wind is not None and wind >= 39) or (gust is not None and gust >= 50):
            factors.append(Factor("wind", CAUTION, f"Strong wind for cycling ({_kmh(wind, units)}, gusts {_kmh(gust, units)}).",
                                  ("wind_speed_10m", "wind_gusts_10m"), "Beaufort 6-7 (39-61 km/h)"))
        elif wind is not None and wind < 20 and (gust is None or gust < 30):
            factors.append(Factor("wind", OK, f"Light wind ({_kmh(wind, units)}).", ("wind_speed_10m",),
                                  "Beaufort 3 or less"))
    else:
        if gust is not None and gust >= 75:
            factors.append(Factor("wind", AVOID, f"Severe gusts up to {_kmh(gust, units)}.", ("wind_gusts_10m",),
                                  "Beaufort 9 (strong gale) from 75 km/h"))
        elif gust is not None and gust >= 62:
            factors.append(Factor("wind", CAUTION, f"Gale-force gusts up to {_kmh(gust, units)}.", ("wind_gusts_10m",),
                                  "Beaufort 8 (gale) from 62 km/h"))
    if activity.wind_sensitive and wind is None and gust is None:
        factors.append(Factor("data", "info", "Wind data are missing for this hour.", ("wind_speed_10m",), ""))

    worst = max((f.level for f in factors if f.level in RANK), key=RANK.__getitem__, default=OK)
    return HourAssessment(t, worst, factors, values)


def _pct(v) -> str:
    return "—" if v is None else f"{v:.0f}%"


def _mm(v, units: U.Units = U.METRIC) -> str:
    if v is None:
        return "amount unknown"
    return f"{U.precipitation(v, units.precipitation):.{U.precipitation_digits(units.precipitation)}f} {units.precipitation}"


def _kmh(v, units: U.Units = U.METRIC) -> str:
    return "—" if v is None else f"{U.wind(v, units.wind):.0f} {units.wind}"


def _t(c, units: U.Units = U.METRIC) -> str:
    return "—" if c is None else f"{U.temperature(c, units.temperature):.0f} {units.temperature}"


def _dt(c, units: U.Units = U.METRIC) -> str:
    return f"{U.temperature_delta(c, units.temperature):.0f} {units.temperature}"


def assess(table: pd.DataFrame, activity: Activity, standard: aqi.Standard,
           sensitivity: str = "standard", units: U.Units = U.METRIC) -> list[HourAssessment]:
    return [assess_hour(row, activity, standard, sensitivity, t, units) for t, row in table.iterrows()]


# --- Windows ------------------------------------------------------------------------------------------

def _summary(hours: list[HourAssessment]) -> dict:
    def series(key):
        return [h.values[key] for h in hours if h.values.get(key) is not None]

    def stat(key, fn):
        s = series(key)
        return fn(s) if s else None
    worst_aqi = max(hours, key=lambda h: -1 if h.values["aqi"] is None else h.values["aqi"])
    return {"aqi_min": stat("aqi", min), "aqi_max": stat("aqi", max), "aqi_open": worst_aqi.values["aqi_open"],
            "pm2_5_mean": stat("pm2_5", lambda s: sum(s) / len(s)),
            "temp_min": stat("temperature", min), "temp_max": stat("temperature", max),
            "apparent_max": stat("apparent", max), "heat_index_max": stat("heat_index", max),
            "wind_chill_min": stat("wind_chill", min), "uv_max": stat("uv", max),
            "precip_prob_max": stat("precip_prob", max), "precip_sum": stat("precip", sum),
            "wind_max": stat("wind", max), "gust_max": stat("gust", max)}


def aqi_range(standard: aqi.Standard, summary: dict) -> str:
    """'42' or '42–55' (or '—') for a window's index values."""
    lo = aqi.index_text(standard, summary["aqi_min"])
    hi = aqi.index_text(standard, summary["aqi_max"], summary["aqi_open"])
    return hi if lo == hi or lo == "—" else f"{lo}–{hi}"


def _rank_key(w: Window, activity: Activity, standard: aqi.Standard):
    s = w.summary
    cat = standard.category(s["aqi_max"])
    cautions = len({f.kind for f in w.limiting})
    heat = s["heat_index_max"] if s["heat_index_max"] is not None else (s["temp_max"] or 0)
    uv = s["uv_max"] or 0
    return (RANK[w.level], cautions, cat.index if cat else 9, round(s["pm2_5_mean"] or 0),
            round(heat), round(uv), round(s["precip_prob_max"] or 0), w.start)


def build_window(hours: list[HourAssessment], standard: aqi.Standard, now: pd.Timestamp,
                 units: U.Units = U.METRIC) -> Window:
    level = max((h.level for h in hours), key=RANK.__getitem__)
    seen, limiting = set(), []
    for h in hours:
        for f in sorted(h.limiting(), key=lambda f: -RANK[f.level]):
            if f.kind not in seen:
                seen.add(f.kind)
                limiting.append(f)
    s = _summary(hours)
    supporting = []
    cat = standard.category(s["aqi_max"])
    if cat and all(f.kind != "air" for f in limiting):
        supporting.append(f"Air quality within guidance: {standard.short} {aqi_range(standard, s)} ({cat.label}).")
    if s["precip_prob_max"] is not None and s["precip_prob_max"] < RAIN_POSSIBLE_PCT and \
            all(f.kind not in ("rain", "storm", "snow", "ice") for f in limiting):
        supporting.append(f"Rain unlikely (at most {s['precip_prob_max']:.0f}% chance).")
    if s["heat_index_max"] is not None and heat_category(s["heat_index_max"]) == 0 and \
            s["temp_max"] is not None and s["temp_max"] >= 10:
        supporting.append("Heat index below the NWS caution level.")
    if s["uv_max"] is not None and s["uv_max"] < 3:
        supporting.append(f"Low UV (index {s['uv_max']:.0f}).")
    if any(f.kind == "wind" and f.level == OK for h in hours for f in h.factors):
        supporting.append(f"Light wind (up to {_kmh(s['wind_max'], units)}).")
    notes = []
    lead_h = (hours[0].time - now).total_seconds() / 3600
    if lead_h > LONG_LEAD_H:
        notes.append("More than 3 days ahead: forecasts this far out are more likely to change.")
    infos = {f.text for h in hours for f in h.factors if f.kind == "data" and f.level == "info"}
    notes.extend(sorted(infos))
    return Window(hours[0].time, hours[-1].time + pd.Timedelta(hours=1), level, hours, s, supporting, limiting, notes)


def windows(assessed: list[HourAssessment], duration_min: int, start_hour: int, end_hour: int,
            standard: aqi.Standard, now: pd.Timestamp, units: U.Units = U.METRIC) -> list[Window]:
    """Every run of consecutive hours long enough for the activity, inside the preferred local hours."""
    n = max(1, math.ceil(duration_min / 60))
    current = now.tz_convert(assessed[0].time.tz).floor("h") if assessed else None
    out = []
    for i in range(len(assessed) - n + 1):
        run = assessed[i:i + n]
        if run[0].time < current:
            continue
        if any(b.time - a.time != pd.Timedelta(hours=1) for a, b in zip(run, run[1:])):
            continue
        if not all(start_hour <= h.time.hour and h.time.hour + 1 <= end_hour for h in run):
            continue
        out.append(build_window(run, standard, now, units))
    return out


def best_windows(all_windows: list[Window], activity: Activity, standard: aqi.Standard) -> dict:
    """The best window per local day, the best overall in the next 24 hours, and the next suitable one."""
    if not all_windows:
        return {"by_day": [], "next_24h": None, "next_ok": None}
    first = all_windows[0].start
    by_day: dict = {}
    for w in all_windows:
        if w.level == UNAVAILABLE:
            continue
        d = w.start.date()
        if d not in by_day or _rank_key(w, activity, standard) < _rank_key(by_day[d], activity, standard):
            by_day[d] = w
    soon = [w for w in all_windows if w.start < first + pd.Timedelta(hours=24) and w.level != UNAVAILABLE]
    next_24h = min(soon, key=lambda w: _rank_key(w, activity, standard)) if soon else None
    next_ok = next((w for w in all_windows if w.level == OK), None)
    return {"by_day": [by_day[d] for d in sorted(by_day)], "next_24h": next_24h, "next_ok": next_ok}


def coverage_note(table: pd.DataFrame, duration_min: int, now: pd.Timestamp, start_hour: int,
                  end_hour: int) -> str | None:
    """Explain when the preferences can't be met by the forecast that exists."""
    usable = table.loc[now.tz_convert(table.index.tz).floor("h"):].dropna(subset=["aqi", "temperature_2m"])
    n = math.ceil(duration_min / 60)
    if usable.empty:
        return "The air-quality and weather forecasts have no overlapping hours left, so nothing can be planned."
    if end_hour - start_hour < n:
        return (f"Your time range ({start_hour:02d}:00–{end_hour:02d}:00) is shorter than the activity "
                f"({duration_min} minutes). Widen the range or shorten the activity.")
    if len(usable) < n:
        return (f"Only {len(usable)} hour(s) of combined forecast remain, less than the {duration_min} minutes "
                "you asked for.")
    return None


# --- Explanations ("environmental intelligence") -------------------------------------------------------

@dataclass
class Insight:
    text: str
    measurements: tuple[str, ...]
    basis: str          # "forecast" or "now (model estimate)"


def explain(table: pd.DataFrame, assessed: list[HourAssessment], best: Window | None, activity: Activity,
            standard: aqi.Standard, sensitivity: str, now: pd.Timestamp,
            units: U.Units = U.METRIC) -> list[Insight]:
    """Short, data-grounded sentences. Each names what it rests on and never claims a cause."""
    out: list[Insight] = []
    local_now = now.tz_convert(table.index.tz).floor("h")
    ahead = table.loc[local_now:local_now + pd.Timedelta(hours=12)]
    if ahead.empty or ahead["aqi"].dropna().empty or pd.isna(ahead["aqi"].iloc[0]):
        return [Insight("There isn't enough current air-quality and weather data to explain the outlook. "
                        "Check back after the next update.", ("aqi",), "now (model estimate)")]
    a0 = ahead["aqi"].iloc[0]
    c0 = standard.category(a0)
    peak_t, peak = ahead["aqi"].idxmax(), ahead["aqi"].max()
    low_t, low = ahead["aqi"].idxmin(), ahead["aqi"].min()
    cp, cl = standard.category(peak), standard.category(low)
    temps = ahead["temperature_2m"].dropna()
    if cp and c0 and cp.index > c0.index:
        txt = (f"Air quality is forecast to deteriorate, from {standard.short} {a0:.0f} ({c0.label}) now to "
               f"{peak:.0f} ({cp.label}) around {peak_t:%H:%M}")
        if len(temps) >= 2 and temps.loc[:peak_t].max() - temps.iloc[0] >= 2:
            txt += f", while temperatures rise to {_t(temps.loc[:peak_t].max(), units)}"
        out.append(Insight(txt + ".", (standard.column, "temperature_2m"), "forecast"))
    elif cl and c0 and cl.index < c0.index:
        out.append(Insight(f"Air quality is forecast to improve, from {standard.short} {a0:.0f} ({c0.label}) now "
                           f"to {low:.0f} ({cl.label}) around {low_t:%H:%M}.", (standard.column,), "forecast"))
    elif c0:
        out.append(Insight(f"Air quality is forecast to stay {c0.label.lower()} over the next 12 hours "
                           f"({standard.short} {low:.0f}–{peak:.0f}).", (standard.column,), "forecast"))

    now_h = next((h for h in assessed if h.time == local_now), None)
    if best is not None and now_h is not None and best.start > local_now:
        hi_now, hi_best = now_h.values.get("heat_index"), best.summary.get("heat_index_max")
        uv_best = best.summary.get("uv_max")
        if hi_now is not None and hi_best is not None and hi_now - hi_best >= 2:
            txt = (f"The {best.start:%a %H:%M} window has lower forecast heat stress (heat index up to {_t(hi_best, units)}, "
                   f"against {_t(hi_now, units)} now)")
            if uv_best is not None and uv_best >= 6:
                txt += f", but the UV index stays elevated (up to {uv_best:.0f})"
            out.append(Insight(txt + ".", ("temperature_2m", "relative_humidity_2m", "uv_index"), "forecast"))

    if activity.wind_sensitive and best is not None:
        light = any(f.kind == "wind" and f.level == OK for h in best.hours for f in h.factors)
        after = table.loc[best.end:best.end + pd.Timedelta(hours=6), "precipitation_probability"].dropna()
        if light and len(after) and after.max() >= RAIN_LIKELY_PCT:
            out.append(Insight(f"Wind is forecast to be light for cycling from {best.start:%H:%M}, although the "
                               f"chance of rain rises to {after.max():.0f}% by {after.idxmax():%H:%M}.",
                               ("wind_speed_10m", "precipitation_probability"), "forecast"))

    if c0 and sensitivity != "conservative" and c0.guidance(activity.heavy, False) == OK and \
            c0.guidance(activity.heavy, True) != OK:
        out.append(Insight(f"Air quality is acceptable under the {standard.name} now ({c0.label}), but its guidance "
                           "for sensitive groups is more conservative. Turn on 'More conservative guidance' if "
                           "that applies to you.", (standard.column,), "now (model estimate)"))
    return out


# --- Saved plans --------------------------------------------------------------------------------------

MAX_PLANS = 5


def plan_record(place, activity: Activity, window: Window, standard: aqi.Standard, sensitivity: str,
                saved_at_utc: str) -> dict:
    """What gets stored when someone saves a suggested window: enough to re-check it later."""
    return {"name": place.name, "latitude": round(float(place.latitude), 4), "longitude": round(float(place.longitude), 4),
            "timezone": place.timezone, "activity": activity.key, "start": window.start.isoformat(),
            "end": window.end.isoformat(), "level": window.level, "standard": standard.code,
            "sensitivity": sensitivity, "saved_at": saved_at_utc}


def recheck_plan(plan: dict, assessed: list[HourAssessment], standard: aqi.Standard, now: pd.Timestamp,
                 units: U.Units = U.METRIC) -> dict:
    """Re-evaluate a saved window with the latest forecast. Returns its new level and reasons."""
    start, end = pd.Timestamp(plan["start"]), pd.Timestamp(plan["end"])
    hours = [h for h in assessed if start <= h.time < end]
    expected = int((end - start) / pd.Timedelta(hours=1))
    if end <= now:
        return {"status": "past", "level": None, "reasons": [], "window": None}
    if len(hours) < expected:
        return {"status": "uncovered", "level": UNAVAILABLE, "window": None,
                "reasons": ["The latest forecast doesn't cover the whole window."]}
    w = build_window(hours, standard, now, units)
    worse = RANK[w.level] > RANK.get(str(plan.get("level")), 0)
    return {"status": "worse" if worse else "unchanged_or_better", "level": w.level, "window": w,
            "reasons": [f.text for f in w.limiting]}
