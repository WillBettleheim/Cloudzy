"""Cloudzy alerts that need air-quality data: air quality, fine particles and saved outdoor plans.

They are the same kind of object as the weather alerts in src.insights (so dismissal, duplicate
suppression and browser notifications work unchanged), and follow the same principles: automatic
threshold checks on forecasts, never official warnings, and each one names its source, the time of
the data and the rule that fired.

- **air:** the selected AQI standard reaches the viewer's category threshold (on the 0-5 scale
  every standard shares) within the next 24 hours.
- **particles:** the 24-hour mean of the modelled PM2.5 reaches the viewer's threshold
  (default 35 µg/m³, the level of the US EPA 24-hour PM2.5 standard; WHO's 2021 guideline is 15).
- **plan:** a window the viewer saved from the outdoor planner is now judged less suitable by
  the latest forecast than when it was saved.

Each alert's key is its kind plus a stable event identity, so the same event alerts once.
"""

from __future__ import annotations

import pandas as pd

from src import aqi
from src import outdoor as O
from src import units as U
from src.air_quality import AirQuality
from src.insights import Alert, _ends_on, _span
from src.preferences import AlertThresholds
from src.provider import ProviderForecast

SOURCE_NOTE = "CAMS model forecast via Open-Meteo"


def _retrieved(aq: AirQuality) -> str:
    try:
        t = pd.Timestamp(aq.retrieved_at_utc)
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        return f"{SOURCE_NOTE}, retrieved {t.tz_convert(aq.hourly.index.tz):%H:%M} local time"
    except (ValueError, TypeError):
        return SOURCE_NOTE


def air_alerts(aq: AirQuality, now: pd.Timestamp, standard: aqi.Standard,
               thresholds: AlertThresholds = AlertThresholds(), enabled: set[str] | None = None) -> list[Alert]:
    """Air-quality and PM2.5 alerts for the next 24 hours."""
    out: list[Alert] = []
    window = aq.forecast_hours(now).iloc[:24]
    if window.empty:
        return out
    threshold_cat = standard.categories[int(thresholds.aqi_category)]
    if enabled is None or "air" in enabled:
        values = window[standard.column].dropna()
        flagged = values[[(c := standard.category(v)) is not None and c.index >= threshold_cat.index for v in values]]
        peak_cat = standard.category(flagged.max()) if len(flagged) else None
        if peak_cat is not None:
            peak_t = flagged.idxmax()
            open_ended = bool(window.loc[peak_t].get("naqi_open")) if standard is aqi.IN_NAQI else False
            out.append(Alert(
                "air", f"Air quality: {peak_cat.label}",
                f"{standard.name} reaches '{threshold_cat.label}' or worse in {len(flagged)} hour(s). "
                f"{peak_cat.general} Source: {_retrieved(aq)}.",
                f"up to {aqi.index_text(standard, flagged.max(), open_ended)} {standard.short}", _span(flagged.index),
                "Air-quality forecasts are model estimates for an area of about 11-45 km, not station readings; "
                "local levels can differ.", ends_on=_ends_on(flagged.index)))
    if enabled is None or "particles" in enabled:
        mean24 = aq.hourly["pm2_5"].rolling("24h", min_periods=18).mean().loc[window.index]
        high = mean24[mean24 >= thresholds.pm25_24h_ugm3].dropna()
        if len(high):
            out.append(Alert(
                "particles", "High fine-particle levels",
                f"The 24-hour mean of PM2.5 reaches {thresholds.pm25_24h_ugm3:.0f} μg/m³ or more "
                f"(WHO's 2021 24-hour guideline is 15 μg/m³). Source: {_retrieved(aq)}.",
                f"up to {high.max():.0f} μg/m³ (24-hour mean)", _span(high.index),
                "Modelled concentrations; a 24-hour mean changes slowly and lags short spikes.",
                ends_on=_ends_on(high.index)))
    return out


def plan_alerts(plans: list[dict], aq: AirQuality, forecast: ProviderForecast, now: pd.Timestamp,
                units: U.Units = U.METRIC, place=None) -> tuple[list[Alert], list[dict]]:
    """Re-check saved plans for this place. Returns alerts for plans that got worse, and every result."""
    alerts: list[Alert] = []
    results: list[dict] = []
    for plan in plans:
        if place is not None and (abs(plan["latitude"] - place.latitude) > 0.01
                                  or abs(plan["longitude"] - place.longitude) > 0.01):
            continue
        standard = aqi.STANDARDS[plan["standard"]]
        activity = O.ACTIVITIES[plan["activity"]]
        table = O.combined_table(aq, forecast, standard)
        assessed = O.assess(table, activity, standard, plan["sensitivity"], units)
        result = O.recheck_plan(plan, assessed, standard, now, units) | {"plan": plan}
        results.append(result)
        if result["status"] == "worse":
            start = pd.Timestamp(plan["start"]).tz_convert(table.index.tz)
            end = pd.Timestamp(plan["end"]).tz_convert(table.index.tz)
            reasons = "; ".join(result["reasons"][:2]) or "conditions changed"
            alerts.append(Alert(
                "plan", f"Your {activity.name.lower()} plan is now less suitable",
                f"The latest forecast rates {start:%a %H:%M}–{end:%H:%M} as "
                f"'{O.LEVEL_LABELS[result['level']].lower()}' (it was '{O.LEVEL_LABELS[plan['level']].lower()}' "
                f"when you saved it): {reasons}. Source: {_retrieved(aq)}.",
                O.LEVEL_LABELS[result["level"]], f"{start:%a %H:%M}–{end:%H:%M}",
                "Forecasts change; Cloudzy re-checks saved plans each time new data load while the app is open.",
                # One alert per plan and new result, so a further change alerts again but a re-render doesn't.
                ends_on=f"{plan['start']}|{result['level']}"))
    return alerts, results
