"""Alerts: Cloudzy's own threshold alerts for the place on the Weather page, and their settings.

Everything here is the visitor's own (saved in their browser): which kinds of alert they want,
the thresholds, dismissals and whether to offer browser notifications. The alerts are simple
checks on the provider forecast, never official warnings.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import app_data, aqi, browser_prefs
from src import insights as I
from src import nav
from src import preferences as P
from src import theme as T
from src import units as U
from src import weather_view as V
from src.data_collection import DataCollectionError
from src.provider import ProviderError

st.html(V.CSS)
st.html(T.page_header("Alerts", "Forecast · your thresholds", "notifications"))
st.info("Cloudzy alerts are automatic checks of the forecast against **your thresholds**. They are **not "
        "official warnings** and have no severity levels: for warnings, follow your national weather service.",
        icon=":material/info:")

prefs = P.load()
now = pd.Timestamp.now(tz="UTC")
place = app_data.current_place() or prefs.home_place()
if place is None:
    try:
        found_places = app_data.search("New Delhi")
    except (ProviderError, DataCollectionError) as exc:
        st.error(f"Couldn't look up a place to check: {exc}", icon=":material/error:")
        st.stop()
    place = found_places[0] if found_places else None
if place is None:
    st.warning("No place to check. Search for one on the Weather page.", icon=":material/travel_explore:")
    st.stop()
units = prefs.unit_override() or U.default_for_country(place.country_code)

# --- Active and dismissed alerts for this place ---------------------------------------------------------
st.html(T.section(f"Next 24 hours · {place.label}", "location_on", "Change the place on the Weather page"))
loading = st.empty()
loading.html(T.skeleton(120))
try:
    forecast = app_data.forecast(place)
except (ProviderError, DataCollectionError) as exc:
    loading.empty()
    st.error(f"We couldn't get the forecast for {place.label}, so alerts can't be checked. {exc}",
             icon=":material/cloud_off:")
    forecast = None
loading.empty()

# Air-quality, particle and saved-plan alerts: loaded separately, so an air-quality outage never hides the
# weather alerts (and vice versa).
env_alerts, env_error = app_data.environment_alerts(place, forecast, prefs, units, now)
if env_error:
    st.caption(f":material/cloud_off: {env_error}")
active: list = []
if forecast is not None or env_alerts:
    found = (I.alerts(forecast, now, prefs.alerts, units, enabled=prefs.enabled_alert_kinds())
             if forecast is not None else []) + env_alerts
    dismissed = prefs.active_dismissals(now)
    active = [a for a in found if a.key not in dismissed]
    hidden = [a for a in found if a.key in dismissed]
    c1, c2, c3 = st.columns(3)
    c1.metric("Active", len(active))
    c2.metric("Dismissed", len(hidden))
    c3.metric("Categories on", f"{len(prefs.enabled_alert_kinds())} of {len(I.ALERT_KINDS)}")
    if not found:
        st.success("No alerts for the next 24 hours with your thresholds and categories.",
                   icon=":material/check_circle:")
    for i, a in enumerate(active):
        st.html(T.chip("Active", "warn", "notifications_active") + V.alerts_html([a]))
        if st.button("Dismiss for 24 hours", key=f"al_dismiss_{i}_{a.kind}", icon=":material/close:"):
            prefs.dismiss(a.key, (now + pd.Timedelta(hours=24)).isoformat())
            P.save(prefs)
            st.rerun()
    for i, a in enumerate(hidden):
        until = pd.Timestamp(prefs.dismissed_alerts[a.key]).tz_convert(place.timezone)
        with st.expander(f"Dismissed: {a.title} · {a.value} (until {until:%a %H:%M} local time)"):
            st.html(T.chip(f"Dismissed until {until:%a %H:%M}", "muted", "notifications_off") + V.alerts_html([a]))
            if st.button("Show again", key=f"al_restore_{i}_{a.kind}", icon=":material/undo:"):
                prefs.dismissed_alerts.pop(a.key, None)
                P.save(prefs)
                st.rerun()

if prefs.dismissed_alerts:
    with st.expander(f"All dismissals ({len(prefs.dismissed_alerts)})"):
        st.caption("A dismissal hides one kind of alert for one day, for 24 hours. A new or different alert still "
                   "shows.")
        st.dataframe(pd.DataFrame({
            "Alert (kind | day)": list(prefs.dismissed_alerts),
            "Hidden until (UTC)": [str(v)[:16].replace("T", " ") for v in prefs.dismissed_alerts.values()]}),
            hide_index=True, width="stretch")
        if st.button("Show all dismissed alerts again", icon=":material/undo:"):
            prefs.dismissed_alerts = {}
            P.save(prefs)
            st.rerun()

# --- Categories --------------------------------------------------------------------------------
st.html(T.section("Which alerts", "checklist"))
cols = st.columns(3)
cats = {}
for i, (kind, name) in enumerate(I.ALERT_KINDS.items()):
    cats[kind] = cols[i % 3].checkbox(name, value=prefs.alert_categories.get(kind, True), key=f"cat_{kind}")
if cats != {k: prefs.alert_categories.get(k, True) for k in I.ALERT_KINDS}:
    prefs.alert_categories = cats
    P.save(prefs)
    browser_prefs.flush()   # no rerun here, so write to the browser now
    st.toast("Alert categories saved", icon=":material/check:")

# --- Thresholds ----------------------------------------------------------------------------------
st.html(T.section("Thresholds", "tune", f"In {units.temperature}, {units.wind}"))
with st.form("thresholds"):
    th = prefs.alerts
    c1, c2 = st.columns(2)
    rain_pct = c1.number_input("Chance of rain at least (%)", 10, 100, int(th.rain_probability_pct), step=5)
    uv_val = c2.number_input("UV index at least", 3, 15, int(th.uv_index))
    heat = c1.number_input(f"Feels-like at least ({units.temperature})",
                           value=round(U.temperature(th.heat_feels_like_c, units.temperature)))
    cold = c2.number_input(f"Feels-like at most ({units.temperature})",
                           value=round(U.temperature(th.cold_feels_like_c, units.temperature)))
    gust = c1.number_input(f"Gusts at least ({units.wind})", value=round(U.wind(th.wind_gust_kmh, units.wind)))
    change = c2.number_input(f"Day-to-day high change ({units.temperature})",
                             value=round(U.temperature_delta(th.day_to_day_change_c, units.temperature)))
    aq_standard, _ = aqi.resolve_standard(prefs.aqi_standard, place.country_code)
    aq_cat = c1.selectbox(f"Air quality at least ({aq_standard.short} category)", range(1, 6),
                          index=int(th.aqi_category) - 1, format_func=lambda i: aq_standard.categories[i].label,
                          help="The same level on every index: the third category is 'Unhealthy for Sensitive Groups' "
                               "(US AQI), 'Moderately polluted' (India NAQI) or 'Moderate' (European AQI).")
    pm25 = c2.number_input("PM2.5 24-hour mean at least (μg/m³)", 10, 250, int(th.pm25_24h_ugm3), step=5,
                           help="35 μg/m³ is the level of the US EPA 24-hour PM2.5 standard; WHO's 2021 guideline "
                                "is 15 μg/m³.")
    if st.form_submit_button("Save thresholds", icon=":material/save:"):
        new = P.AlertThresholds(
            rain_probability_pct=float(rain_pct), uv_index=float(uv_val),
            heat_feels_like_c=U.to_celsius(heat, units.temperature),
            cold_feels_like_c=U.to_celsius(cold, units.temperature),
            wind_gust_kmh=U.to_kmh(gust, units.wind),
            day_to_day_change_c=change / 1.8 if units.temperature == "°F" else float(change),
            aqi_category=float(aq_cat), pm25_24h_ugm3=float(pm25))
        bad = [k for k, (lo, hi) in P.AlertThresholds.LIMITS.items() if not lo <= getattr(new, k) <= hi]
        if bad:
            st.error("Some values are outside the allowed range: " + ", ".join(bad), icon=":material/error:")
        else:
            prefs.alerts = new
            P.save(prefs)
            st.toast("Thresholds saved", icon=":material/check:")
            st.rerun()

# --- Browser notifications -----------------------------------------------------------------------
st.html(T.section("Browser notifications", "notifications_active"))
notify = st.toggle("Offer browser notifications for alerts", value=prefs.notifications,
                   help="Your browser asks permission only when you click its button below.")
if notify != prefs.notifications:
    prefs.notifications = notify
    P.save(prefs)
    st.rerun()
if prefs.notifications:
    from src.notify import browser_notifications
    browser_notifications(active)
st.caption("Notifications only appear while Cloudzy is open in a browser tab; there are no background or push "
           "notifications. Each alert notifies once. If your browser blocks them, alerts still show on the page.")
nav.page_link("app_pages/weather.py", "Back to the weather", ":material/arrow_back:")
