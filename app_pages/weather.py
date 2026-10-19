"""Cloudzy weather page. Opened through streamlit_app.py.

A phone-style weather app built on real Open-Meteo data:
search → current conditions (hero) → rain banner → next 24 hours (cards and
graph) → 7-day forecast → weather details → Cloudzy Core (experimental, Delhi
only) → attribution.

- Values are fetched in metric units and converted for display (src.units).
- Every network call is cached for 10 minutes per place, to stay well inside
  Open-Meteo's free limits.
- The location is kept in the URL (?q=…&i=…), so a refresh or a shared link
  shows the same place.
- On any failure the page shows the reason and a retry button, never stale or
  invented numbers.
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from src import ui
from src import units as U
from src import weather_view as V
from src.briefing import CLOUDZY_RADIUS_KM, cloudzy_block, distance_km
from src.config import DELHI
from src.data_collection import DataCollectionError
from src.provider import ATTRIBUTION, ProviderError, fetch_forecast, rain_outlook, search_locations, upcoming

CACHE_SECONDS = 600
DEFAULT_QUERY = "New Delhi"
HOURS_AHEAD = 24
SAVE_SNAPSHOTS = os.environ.get("CLOUDZY_SNAPSHOTS", "1") == "1"


# --- Cached data access -----------------------------------------------------------

@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def cached_search(query: str):
    return search_locations(query, count=8)


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def cached_forecast(place):
    return fetch_forecast(place)


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def cached_cloudzy(place):
    return cloudzy_block(place, snapshot=SAVE_SNAPSHOTS)


def retry_button(key: str) -> None:
    if st.button("Try again", key=key, icon="🔄"):
        st.cache_data.clear()
        st.rerun()


def place_label(p) -> str:
    pop = f" · pop. {p.population:,}" if p.population else ""
    return f"{p.label}{pop}"


# --- Header, units and search ---------------------------------------------------------

st.html(V.CSS)
head, unit_box = st.columns([3, 1], vertical_alignment="bottom")
with head:
    st.markdown("## ☁️ Cloudzy")
    st.caption("Weather with a little intelligence.")
with unit_box:
    with st.popover("Units", icon="⚙️", width="stretch"):
        temp_unit = st.segmented_control("Temperature", U.TEMPERATURE, default="°C", key="unit_temp")
        wind_unit = st.segmented_control("Wind", U.WIND, default="km/h", key="unit_wind")
        rain_unit = st.segmented_control("Rain", U.PRECIPITATION, default="mm", key="unit_rain")
units = U.Units(temp_unit or "°C", wind_unit or "km/h", rain_unit or "mm")

params = st.query_params
query = st.text_input("Search for a city or town", value=params.get("q", DEFAULT_QUERY),
                      placeholder="e.g. Mumbai, Reykjavik, Lagos", max_chars=80).strip()
if len(query) < 2:
    st.info("Type at least two letters to search for a place.", icon="🔎")
    st.stop()

try:
    with st.spinner("Looking for that place…"):
        places = cached_search(query)
except (ProviderError, DataCollectionError) as exc:
    st.error(f"Location search is unavailable right now. {exc}", icon="⚠️")
    retry_button("retry-search")
    st.stop()

if not places:
    st.warning(f"No places match “{query}”. Check the spelling, or try a nearby larger town.", icon="🗺️")
    st.stop()

try:
    start_index = int(params.get("i", 0)) if params.get("q") == query else 0
except ValueError:
    start_index = 0
place = st.selectbox("Choose the right match", places, index=min(max(start_index, 0), len(places) - 1),
                     format_func=place_label, help="Several places can share a name. Pick the one you mean.")
params["q"], params["i"] = query, str(places.index(place))

# --- Provider forecast ------------------------------------------------------------

try:
    with st.spinner(f"Checking the sky over {place.name}…"):
        forecast = cached_forecast(place)
except (ProviderError, DataCollectionError) as exc:
    st.error(f"We couldn't get the forecast for {place.label}. {exc}", icon="⚠️")
    retry_button("retry-forecast")
    st.stop()

colors = ui.palette()
now = pd.Timestamp.now(tz="UTC")
cur = forecast.current
local_now = pd.Timestamp(cur["time"]).tz_convert(forecast.hourly.index.tz)  # keeps the zone name, e.g. IST
daily = forecast.daily
today_key = now.tz_convert(forecast.hourly.index.tz).date()
today = daily.loc[today_key] if today_key in daily.index else (daily.iloc[0] if len(daily) else None)

st.html(V.hero_html(place.label, cur, today, units, local_now.tzname() or str(forecast.hourly.index.tz)))
st.caption(f"{forecast.source_label} · {V.updated_label(forecast.retrieved_at_utc, forecast.hourly.index.tz)}, "
           "refreshed at most every 10 minutes · "
           "current values are model estimates for this location, not a station reading")

# --- Rain banner -------------------------------------------------------------------

rain = rain_outlook(forecast, now)
if rain["hours_checked"] == 0:
    st.info("No upcoming hourly data to check for rain.", icon="❔")
elif rain["rain_expected"]:
    first = pd.Timestamp(rain["first_rain_hour_local"])
    total = U.precipitation(rain["total_precipitation_mm"], units.precipitation)
    st.warning(f"**Rain likely from {first:%a %H:%M}**: {rain['rainy_hours']} of the next {rain['hours_checked']} "
               f"hours, up to {rain['max_probability_pct']:.0f}% chance, about "
               f"{total:.{U.precipitation_digits(units.precipitation)}f} {units.precipitation} in total.", icon="🌧️")
else:
    st.success(f"No rain expected in the next {rain['hours_checked']} hours "
               f"(highest chance {ui.fmt(rain['max_probability_pct'], '%')}).", icon="🌂")
st.caption(f"Rain indicator: {rain['rule']}. {rain['label']}")

# --- Next 24 hours -------------------------------------------------------------------

st.markdown("#### Next 24 hours")
hours = upcoming(forecast, now, HOURS_AHEAD)
if hours.empty:
    st.info("The provider returned no upcoming hours for this place.", icon="❔")
else:
    st.html(V.hourly_strip_html(hours, units))
    shown = U.convert_frame(hours, units)
    st.plotly_chart(ui.temperature_chart(shown, colors, units.temperature, "Temperature and feels-like"),
                    width="stretch", theme="streamlit", config=ui.CHART_CONFIG)
    if shown["precipitation_probability"].fillna(0).max() > 0 or shown["precipitation"].fillna(0).max() > 0:
        st.plotly_chart(ui.rain_chart(shown, colors, units.precipitation), width="stretch",
                        theme="streamlit", config=ui.CHART_CONFIG)
    with st.expander("Hour-by-hour table"):
        hourly_tbl = ui.hourly_table(shown, units.temperature, units.wind, units.precipitation)
        st.dataframe(hourly_tbl, hide_index=True, width="stretch", column_config={
            c: st.column_config.NumberColumn(format="%.2f" if c == "Rain in" else "%.1f")
            for c in hourly_tbl.columns if c.startswith(("Temp", "Feels", "Rain mm", "Rain in", "UV"))})

# --- 7-day forecast ----------------------------------------------------------------

st.markdown("#### 7-day forecast")
if daily.empty:
    st.info("The provider returned no daily forecast for this place.", icon="❔")
else:
    st.html(V.daily_list_html(daily, units, today_key))

# --- Details -------------------------------------------------------------------------

st.markdown("#### Right now")
st.html(V.detail_cards_html(cur, today, units))

# --- Cloudzy Core (experimental) ------------------------------------------------------

st.markdown("#### 🧪 Cloudzy Core · experimental")
d_km = distance_km(place.latitude, place.longitude, DELHI.latitude, DELHI.longitude)
with st.container(border=True):
    st.markdown("Cloudzy Core is this project's own machine-learning model. It is **separate from the forecast "
                "above**, which comes from Open-Meteo.")
    if d_km > CLOUDZY_RADIUS_KM:
        st.info(f"Cloudzy Core was trained and tested only for **{DELHI.name}**. It isn't shown for "
                f"{place.label} ({d_km:,.0f} km away), because it has never been validated there. "
                "Search for New Delhi to see it.", icon="📍")
    else:
        with st.spinner("Asking Cloudzy Core…"):
            block = cached_cloudzy(place)
        if not block["available"]:
            st.warning(f"No Cloudzy prediction right now. {block['reason']} Cloudzy only predicts when all "
                       "of its inputs (the last 25 hours of live data) are complete.", icon="🧪")
            retry_button("retry-cloudzy")
        else:
            tu = units.temperature
            preds = [{**p, "temperature_2m_c": U.temperature(p["temperature_2m_c"], tu),
                      "typical_error_c": U.temperature_delta(p["typical_error_c"], tu)}
                     for p in block["cloudzy"]["predictions"]]
            prov = [{**p, "temperature_2m_c": U.temperature(p["temperature_2m_c"], tu)}
                    for p in block["provider"]["forecasts"]]
            st.caption(f"Prediction point: {block['prediction_point']}, {block['distance_km_from_place']} km from "
                       f"{place.name}. Issued {pd.Timestamp(block['issue_time_local']):%H:%M} local time from "
                       "live ECMWF IFS inputs.")
            st.plotly_chart(ui.cloudzy_chart(preds, prov, colors, tu), width="stretch", theme="streamlit",
                            config=ui.CHART_CONFIG)
            table = ui.cloudzy_table(preds, prov, tu)
            st.dataframe(table, hide_index=True, width="stretch", column_config={
                c: st.column_config.NumberColumn(format="%.2f" if "error" in c else "%.1f")
                for c in table.columns if c.endswith(tu)})
            with st.expander("How to read this"):
                st.markdown(
                    "- **What it predicts:** the ERA5-Land *reanalysis* temperature for one ~9 km grid cell, 1 to "
                    "24 hours ahead, starting from live ECMWF IFS model inputs. It isn't a thermometer reading.\n"
                    "- **Average error** is what Cloudzy achieved on a **replay of 2025** (historical test "
                    "data). It is not a promise about today's accuracy, and not a confidence interval. Live "
                    "inputs arrive later than the replay assumes, so real errors are likely larger.\n"
                    "- **The ECMWF IFS column** is the model Cloudzy takes its inputs from. On the 2025 replay, "
                    "Cloudzy was closer to the reanalysis than those IFS values up to 12 hours ahead, which "
                    "shows a bias correction toward the reanalysis. That **doesn't** make Cloudzy a better "
                    "weather forecast than the provider.\n"
                    "- **For decisions, use the forecast above.** Details are in Cloudzy Lab and the model card.")

# --- Data notes and footer ------------------------------------------------------------

if forecast.warnings:
    with st.expander(f"Data notes ({len(forecast.warnings)})"):
        st.caption("These values were missing, unavailable or implausible in the provider response. They are "
                   "shown as “—”, never filled in.")
        for w in forecast.warnings:
            st.markdown(f"- {w}")

st.divider()
st.caption(f"{ATTRIBUTION}. Reanalysis: Copernicus Climate Change Service (ERA5, ERA5-Land). Weather icons follow "
           "the WMO weather codes in the forecast. Cloudzy is an experimental student project, not an "
           "operational weather service.")
