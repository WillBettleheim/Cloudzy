"""Weather intelligence: summaries, unofficial alerts, recommendations and a daily briefing.

Transparent rules applied to the provider forecast, with no language model.
Principles:
- Every sentence is computed from forecast values for identifiable local times,
  and quotes the numbers it rests on.
- Current values are called model estimates "now"; everything later is
  "forecast". Wording stays hedged ("likely", "may"), because forecasts are
  uncertain.
- Missing data drops a sentence instead of guessing. If too little data is
  left, the summary says so.
- Alerts are Cloudzy's own threshold checks. They are never presented as
  official warnings.

All inputs are metric (as the provider returns them). `units` only affects how
numbers are written.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from src import units as U
from src.preferences import AlertThresholds
from src.provider import ProviderForecast, upcoming

PERIODS = (("night", 0, 6), ("morning", 6, 12), ("afternoon", 12, 18), ("evening", 18, 24))
OFFICIAL_NOTE = "Cloudzy alert (automatic threshold check, not an official weather warning)"

# WMO code groups, from the codes Open-Meteo documents.
GROUPS = {
    "clear": {0, 1}, "cloudy": {2, 3}, "fog": {45, 48}, "drizzle": {51, 53, 55, 56, 57},
    "rain": {61, 63, 65, 66, 67, 80, 81, 82}, "snow": {71, 73, 75, 77, 85, 86}, "thunderstorms": {95, 96, 97, 99},
}
GROUP_PHRASES = {
    "clear": "mostly clear skies", "cloudy": "mostly cloudy skies", "fog": "fog at times",
    "drizzle": "drizzle at times", "rain": "rain at times", "snow": "snow at times",
    "thunderstorms": "thunderstorms possible",
}


@dataclass
class Alert:
    kind: str
    title: str
    reason: str
    value: str
    period: str
    uncertainty: str
    label: str = OFFICIAL_NOTE


@dataclass
class Recommendation:
    kind: str
    text: str
    why: str


@dataclass
class Briefing:
    date: str
    temperature_range: str
    conditions: str
    precipitation: str
    changes: list[str] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)


# --- Helpers -----------------------------------------------------------------------

def _t(celsius, units: U.Units) -> str:
    v = U.temperature(celsius, units.temperature)
    return "—" if v is None else f"{v:.0f}°"


def _dt(delta_c, units: U.Units) -> str:
    v = U.temperature_delta(None if delta_c is None else abs(delta_c), units.temperature)
    return "—" if v is None else f"{v:.0f}°"


def comfort_rule(units: U.Units) -> str:
    return (f"feels-like {_t(15, units)}–{_t(28, units)}, rain chance under 30%, "
            f"gusts under {_wind(40, units)}")


def _wind(kmh, units: U.Units) -> str:
    v = U.wind(kmh, units.wind)
    return "—" if v is None else f"{v:.0f} {units.wind}"


def _span(times: pd.DatetimeIndex) -> str:
    """'15:00–19:00 Mon' for a continuous run of flagged hours, otherwise
    'between 08:00 and 20:00 Mon (5 hours)' so gaps aren't hidden."""
    times = pd.DatetimeIndex(sorted(times))
    start, end = times.min(), times.max() + pd.Timedelta(hours=1)
    same_day = start.date() == (end - pd.Timedelta(minutes=1)).date()
    end_txt = "midnight" if end.hour == 0 and end.minute == 0 else f"{end:%H:%M}"  # not an ambiguous "00:00"
    a, b = (f"{start:%H:%M}", f"{end_txt} {start:%a}") if same_day else (f"{start:%a %H:%M}", f"{end:%a %H:%M}")
    continuous = len(times) == int((end - start) / pd.Timedelta(hours=1))
    return f"{a}–{b}" if continuous else f"between {a} and {b} ({len(times)} hours)"


def group_of(code) -> str | None:
    if code is None or pd.isna(code):
        return None
    for name, codes in GROUPS.items():
        if int(code) in codes:
            return name
    return None


def period_of(ts: pd.Timestamp) -> str:
    for name, lo, hi in PERIODS:
        if lo <= ts.hour < hi:
            return name
    return "night"


def window(forecast: ProviderForecast, now: pd.Timestamp, hours: int = 24) -> pd.DataFrame:
    return upcoming(forecast, now, hours)


# --- Summary -------------------------------------------------------------------------

def summary(forecast: ProviderForecast, now: pd.Timestamp, units: U.Units = U.METRIC) -> list[str]:
    """3-5 data-grounded sentences about the next 24 hours and tomorrow."""
    w = window(forecast, now, 24)
    if w.empty or w["temperature_2m"].notna().sum() < 6:
        return ["There isn't enough forecast data for this place to write a summary."]
    out = []
    cur = forecast.current

    # 1. Now (model estimate) and the temperature trend over the next 12 hours.
    w12 = w.iloc[:12].dropna(subset=["temperature_2m"])
    now_t = cur.get("temperature_2m")
    if now_t is not None and len(w12):
        hi_t, lo_t = w12["temperature_2m"].idxmax(), w12["temperature_2m"].idxmin()
        hi_v, lo_v = w12.loc[hi_t, "temperature_2m"], w12.loc[lo_t, "temperature_2m"]
        if hi_v - now_t >= 2:
            trend = f"warming to {_t(hi_v, units)} around {hi_t:%H:%M}"
        elif now_t - lo_v >= 2:
            trend = f"cooling to {_t(lo_v, units)} by {lo_t:%H:%M}"
        else:
            trend = f"staying near {_t(now_t, units)} over the next 12 hours"
        desc = (cur.get("description") or "").lower()
        cond = f" ({desc})" if desc and not desc.startswith("unknown") else ""
        out.append(f"It's {_t(now_t, units)} now{cond}, {trend}.")

    # 2. Dominant sky condition and cloud trend.
    groups = [g for g in (group_of(c) for c in w["weather_code"]) if g]
    if groups:
        dominant, n = Counter(groups).most_common(1)[0]
        phrase = GROUP_PHRASES[dominant]
        cc = w["cloud_cover"].dropna()
        if len(cc) >= 12:
            first, later = cc.iloc[:6].mean(), cc.iloc[6:12].mean()
            if later - first >= 25:
                phrase += ", with cloud cover increasing"
            elif first - later >= 25:
                phrase += ", with clouds clearing"
        out.append(f"Expect {phrase} over the next 24 hours ({n} of {len(groups)} hours).")

    # 3. Rain timing by part of the day.
    pp = w["precipitation_probability"].dropna()
    if len(pp):
        by_period = pp.groupby([period_of(t) for t in pp.index]).max()
        top_period, top = by_period.idxmax(), by_period.max()
        if top < 20:
            out.append(f"Rain is unlikely in the next 24 hours (no hour above {top:.0f}%).")
        else:
            low_period, low = by_period.idxmin(), by_period.min()
            if len(by_period) > 1 and top - low >= 20:
                out.append(f"Rain is more likely in the {top_period} (up to {top:.0f}%) "
                           f"than in the {low_period} (up to {low:.0f}%).")
            else:
                out.append(f"Rain is possible through the period, up to {top:.0f}% in the {top_period}.")

    # 4. Wind.
    gusts = w["wind_gusts_10m"].dropna() if "wind_gusts_10m" in w else pd.Series(dtype=float)
    if len(gusts) and gusts.max() >= 40:
        out.append(f"It will be gusty at times, up to {_wind(gusts.max(), units)} around {gusts.idxmax():%H:%M}.")

    # 5. Tomorrow versus today (daily maxima).
    today_date = now.tz_convert(forecast.hourly.index.tz).date()
    d = forecast.daily
    if today_date in d.index:
        i = list(d.index).index(today_date)
        if i + 1 < len(d):
            t0, t1 = d["temperature_2m_max"].iloc[i], d["temperature_2m_max"].iloc[i + 1]
            if pd.notna(t0) and pd.notna(t1):
                diff = t1 - t0
                if diff >= 3:
                    out.append(f"Tomorrow is forecast to be warmer than today (high {_t(t1, units)} vs {_t(t0, units)}).")
                elif diff <= -3:
                    out.append(f"Tomorrow is forecast to be cooler than today (high {_t(t1, units)} vs {_t(t0, units)}).")
                else:
                    out.append(f"Tomorrow looks similar to today (high {_t(t1, units)} vs {_t(t0, units)}).")
    return out


# --- Alerts --------------------------------------------------------------------------

def alerts(forecast: ProviderForecast, now: pd.Timestamp, thresholds: AlertThresholds = AlertThresholds(),
           units: U.Units = U.METRIC) -> list[Alert]:
    w = window(forecast, now, 24)
    out: list[Alert] = []
    if w.empty:
        return out

    def flagged(col, mask_fn):
        s = w[col].dropna() if col in w else pd.Series(dtype=float)
        return s[mask_fn(s)]

    rain = flagged("precipitation_probability", lambda s: s >= thresholds.rain_probability_pct)
    if len(rain):
        out.append(Alert("rain", "Rain likely",
                         f"Chance of rain reaches {thresholds.rain_probability_pct:.0f}% or more in {len(rain)} hour(s).",
                         f"up to {rain.max():.0f}%", _span(rain.index),
                         "A probability, not a certainty: it may stay dry at your exact spot, or rain at other times."))
    heat = flagged("apparent_temperature", lambda s: s >= thresholds.heat_feels_like_c)
    if len(heat):
        out.append(Alert("heat", "Very hot",
                         f"Feels-like temperature reaches {_t(thresholds.heat_feels_like_c, units)} or more.",
                         f"up to {_t(heat.max(), units)}", _span(heat.index),
                         "Feels-like values are model estimates. Shade, sun and wind change what you experience."))
    cold = flagged("apparent_temperature", lambda s: s <= thresholds.cold_feels_like_c)
    if len(cold):
        out.append(Alert("cold", "Cold",
                         f"Feels-like temperature drops to {_t(thresholds.cold_feels_like_c, units)} or below.",
                         f"down to {_t(cold.min(), units)}", _span(cold.index),
                         "Feels-like values are model estimates and vary with exposure to wind."))
    uv = flagged("uv_index", lambda s: s >= thresholds.uv_index)
    if len(uv):
        out.append(Alert("uv", "High UV",
                         f"UV index reaches {thresholds.uv_index:.0f} or more.",
                         f"peak {uv.max():.0f}", _span(uv.index),
                         "UV forecasts assume the forecast cloud cover; clear spells can be stronger."))
    wind = flagged("wind_gusts_10m", lambda s: s >= thresholds.wind_gust_kmh)
    if len(wind):
        out.append(Alert("wind", "Strong gusts",
                         f"Wind gusts reach {_wind(thresholds.wind_gust_kmh, units)} or more.",
                         f"up to {_wind(wind.max(), units)}", _span(wind.index),
                         "Gusts are local and short-lived; exposed places can see stronger ones."))
    today_date = now.tz_convert(forecast.hourly.index.tz).date()
    d = forecast.daily
    if today_date in d.index:
        i = list(d.index).index(today_date)
        if i + 1 < len(d):
            t0, t1 = d["temperature_2m_max"].iloc[i], d["temperature_2m_max"].iloc[i + 1]
            if pd.notna(t0) and pd.notna(t1) and abs(t1 - t0) >= thresholds.day_to_day_change_c:
                word = "warmer" if t1 > t0 else "cooler"
                out.append(Alert("change", f"Much {word} tomorrow",
                                 f"Tomorrow's high differs from today's by {_dt(thresholds.day_to_day_change_c, units)} or more.",
                                 f"{_t(t0, units)} → {_t(t1, units)}", f"{d.index[i]:%a} → {d.index[i + 1]:%a}",
                                 "Daily highs a day ahead typically carry an uncertainty of a degree or two."))
    return out


# --- Recommendations --------------------------------------------------------------------

def recommendations(forecast: ProviderForecast, now: pd.Timestamp, units: U.Units = U.METRIC) -> list[Recommendation]:
    w = window(forecast, now, 12)
    out: list[Recommendation] = []
    if w.empty:
        return out
    pp = w["precipitation_probability"].dropna()
    amount = w["precipitation"].dropna().sum()
    if len(pp):
        top = pp.max()
        p_amt = U.precipitation(amount, units.precipitation)
        amt_txt = f"{p_amt:.{U.precipitation_digits(units.precipitation)}f} {units.precipitation}"
        if top >= 50 or amount >= 1:
            wet = pp[pp >= 50]
            when = _span(wet.index) if len(wet) else f"{pp.idxmax():%H:%M}"
            out.append(Recommendation("umbrella", "An umbrella may be useful.",
                                      f"Up to {top:.0f}% chance of rain ({when}), about {amt_txt} expected in the next 12 hours."))
        elif top >= 30:
            out.append(Recommendation("umbrella", "Maybe pack a compact umbrella.",
                                      f"Rain chance peaks at {top:.0f}% around {pp.idxmax():%H:%M}."))
        else:
            out.append(Recommendation("umbrella", "An umbrella probably isn't needed.",
                                      f"Rain chance stays at or below {top:.0f}% for the next 12 hours."))

    feels = w["apparent_temperature"].dropna()
    if len(feels):
        lo, hi = feels.min(), feels.max()
        mid = feels.median()
        if mid >= 30:
            text = "Light, breathable clothing; stay hydrated."
        elif mid >= 22:
            text = "T-shirt weather."
        elif mid >= 15:
            text = "A light layer, such as a long sleeve or thin jacket."
        elif mid >= 8:
            text = "A sweater or light jacket."
        elif mid >= 0:
            text = "A warm coat."
        else:
            text = "Heavy winter layers, hat and gloves."
        if hi - lo >= 8:
            text += " Layers help: it changes a lot during the period."
        if len(pp) and pp.max() >= 50:
            text += " Add a waterproof layer."
        out.append(Recommendation("clothing", text,
                                  f"Feels-like temperature ranges from {_t(lo, units)} to {_t(hi, units)} over the next 12 hours."))

    day = w[w["is_day"] == 1] if "is_day" in w else w
    if len(day):
        ok = day[(day["apparent_temperature"].between(15, 28)) & (day["precipitation_probability"].fillna(100) < 30)
                 & (day["wind_gusts_10m"].fillna(0) < 40)]
        if len(ok):
            out.append(Recommendation("outdoors", f"Comfortable outdoor conditions for {len(ok)} daylight hour(s).",
                                      f"{_span(ok.index)} meets Cloudzy's comfort rule ({comfort_rule(units)})."))
        else:
            reasons = []
            if (day["apparent_temperature"] > 28).any():
                reasons.append("hot")
            if (day["apparent_temperature"] < 15).any():
                reasons.append("cool")
            if (day["precipitation_probability"] >= 30).any():
                reasons.append("rain chances")
            if (day["wind_gusts_10m"] >= 40).any():
                reasons.append("gusty")
            out.append(Recommendation("outdoors", "Few comfortable outdoor hours in daylight.",
                                      "Daylight hours are " + (", ".join(reasons) or "outside the comfort rule") +
                                      f" (rule: {comfort_rule(units)})."))
        uv = day["uv_index"].dropna() if "uv_index" in day else pd.Series(dtype=float)
        if len(uv) and uv.max() >= 3:
            out.append(Recommendation("uv", "Check UV before spending long outdoors.",
                                      f"UV peaks at {uv.max():.0f} around {uv.idxmax():%H:%M}. WHO advises sun protection "
                                      "from UV 3 upward."))
    return out


# --- Daily briefing -------------------------------------------------------------------

def briefing(forecast: ProviderForecast, now: pd.Timestamp, thresholds: AlertThresholds = AlertThresholds(),
             units: U.Units = U.METRIC) -> Briefing | None:
    tz = forecast.hourly.index.tz
    today_date = now.tz_convert(tz).date()
    d = forecast.daily
    if today_date not in d.index:
        return None
    row = d.loc[today_date]
    hours = forecast.hourly[forecast.hourly.index.date == today_date]
    groups = [g for g in (group_of(c) for c in hours["weather_code"]) if g]
    # Open-Meteo's daily weather code is the day's *most severe* condition, not the
    # typical one, so report both instead of letting them read as a contradiction.
    severe = row.get("description") or "Unknown"
    if groups:
        dominant = GROUP_PHRASES[Counter(groups).most_common(1)[0][0]]
        conditions = dominant[0].upper() + dominant[1:]
        if group_of(row.get("weather_code")) != Counter(groups).most_common(1)[0][0]:
            conditions += f"; most significant: {severe.lower()}"
    else:
        conditions = severe
    prob, total = row.get("precipitation_probability_max"), row.get("precipitation_sum")
    if pd.isna(prob) and pd.isna(total):
        precip = "No precipitation information available."
    else:
        tot = U.precipitation(total, units.precipitation)
        tot_txt = "—" if tot is None else f"{tot:.{U.precipitation_digits(units.precipitation)}f} {units.precipitation}"
        precip = f"Up to {prob:.0f}% chance; {tot_txt} expected in total." if pd.notna(prob) else f"{tot_txt} expected."
    # Only real changes, not the static "Expect …" or "Rain is unlikely" lines shown in the summary.
    change_words = ("warming", "cooling", "increasing", "clearing", "more likely", "gusty", "Tomorrow is forecast")
    changes = [s for s in summary(forecast, now, units) if any(k in s for k in change_words)]
    return Briefing(
        date=f"{pd.Timestamp(today_date):%A %d %B}",
        temperature_range=f"{_t(row['temperature_2m_min'], units)} to {_t(row['temperature_2m_max'], units)}",
        conditions=conditions, precipitation=precip, changes=changes,
        alerts=alerts(forecast, now, thresholds, units), recommendations=recommendations(forecast, now, units),
    )
