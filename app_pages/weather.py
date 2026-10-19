"""Cloudzy weather page (Phase D). Opened through streamlit_app.py.

Layout: search → current conditions → rain indicator → tabs (next 48 hours,
7-day outlook, Cloudzy Core). Provider data and Cloudzy predictions are kept
in separate, labelled places, as in src/briefing.py. Every network call is
cached for 10 minutes per place to respect Open-Meteo's limits.
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from src import ui
from src.briefing import CLOUDZY_RADIUS_KM, cloudzy_block, distance_km
from src.config import DELHI
from src.data_collection import DataCollectionError
from src.provider import ATTRIBUTION, ProviderError, fetch_forecast, rain_outlook, search_locations, upcoming

CACHE_SECONDS = 600
DEFAULT_QUERY = "New Delhi"
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


# --- Header and search ----------------------------------------------------------

st.markdown("## ☁️ Cloudzy")
st.caption("Weather with a little intelligence.")

query = st.text_input("Search for a place", value=DEFAULT_QUERY, placeholder="City or town, e.g. Mumbai",
                      max_chars=80).strip()
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
    st.warning(f"No places match “{query}”. Check the spelling or try a nearby larger town.", icon="🗺️")
    st.stop()


def place_label(p) -> str:
    pop = f" · pop. {p.population:,}" if p.population else ""
    return f"{p.label}{pop}"


place = st.selectbox("Choose the right match", places, format_func=place_label,
                     label_visibility="visible" if len(places) > 1 else "collapsed")

# --- Provider forecast ------------------------------------------------------------

try:
    with st.spinner("Checking the sky…"):
        forecast = cached_forecast(place)
except (ProviderError, DataCollectionError) as exc:
    st.error(f"We couldn't get the forecast for {place.label}. {exc}", icon="⚠️")
    retry_button("retry-forecast")
    st.stop()

colors = ui.palette()
now = pd.Timestamp.now(tz="UTC")
cur = forecast.current
local_now = pd.Timestamp(cur["time"])

st.markdown(f"### {place.label}")
st.caption(f"{forecast.source_label} · current values as of {local_now:%H:%M} local time "
           f"({local_now.tzname()}), 15-minute model data, not a station reading")

with st.container(border=True):
    left, right = st.columns([1, 2], vertical_alignment="center")
    with left:
        st.markdown(f"<div style='font-size:3.2rem;line-height:1.1'>"
                    f"{ui.weather_icon(cur.get('weather_code'), cur.get('is_day'))} "
                    f"{ui.fmt(cur.get('temperature_2m'), '°', 0)}</div>", unsafe_allow_html=True)
        st.markdown(f"**{cur.get('description', 'Unknown')}**")
        today = forecast.daily.iloc[0]
        st.caption(f"Today {ui.fmt(today['temperature_2m_min'], '°')} – {ui.fmt(today['temperature_2m_max'], '°')}"
                   f" · sunrise {today['sunrise']:%H:%M} · sunset {today['sunset']:%H:%M}")
    with right:
        # A wrapping row: three per line on wide screens, two or one on phones.
        with st.container(horizontal=True, wrap=True, gap="small"):
            for label, value, tip in [
                ("Feels like", ui.fmt(cur.get("apparent_temperature"), " °C", 1), None),
                ("Humidity", ui.fmt(cur.get("relative_humidity_2m"), "%"), None),
                ("Cloud cover", ui.fmt(cur.get("cloud_cover"), "%"), None),
                (f"Wind from {ui.compass(cur.get('wind_direction_10m'))}", ui.fmt(cur.get("wind_speed_10m"), " km/h"),
                 "Compass direction the wind blows from"),
                ("Gusts", ui.fmt(cur.get("wind_gusts_10m"), " km/h"), None),
                ("Precipitation", ui.fmt(cur.get("precipitation"), " mm", 1), None),
            ]:
                st.metric(label, value, help=tip, width=160)

# --- Rain indicator --------------------------------------------------------------

rain = rain_outlook(forecast, now)
if rain["hours_checked"] == 0:
    st.info("No upcoming hourly data to check for rain.", icon="❔")
elif rain["rain_expected"]:
    first = pd.Timestamp(rain["first_rain_hour_local"])
    st.warning(f"**Rain likely from {first:%a %H:%M}** — {rain['rainy_hours']} of the next "
               f"{rain['hours_checked']} hours, up to {rain['max_probability_pct']:.0f}% chance, "
               f"about {rain['total_precipitation_mm']} mm in total.", icon="🌧️")
else:
    st.success(f"No rain expected in the next {rain['hours_checked']} hours "
               f"(highest chance {ui.fmt(rain['max_probability_pct'], '%')}).", icon="🌂")
st.caption(f"Rain indicator: {rain['rule']}. {rain['label']}")

# --- Tabs -------------------------------------------------------------------------

near_delhi = distance_km(place.latitude, place.longitude, DELHI.latitude, DELHI.longitude) <= CLOUDZY_RADIUS_KM
tab_hours, tab_days, tab_cloudzy = st.tabs(
    ["Next 48 hours", "7-day outlook", "Cloudzy Core 🧪" if near_delhi else "Cloudzy Core"])

with tab_hours:
    hours = upcoming(forecast, now, 48)
    if hours.empty:
        st.info("The provider returned no upcoming hours.", icon="❔")
    else:
        st.plotly_chart(ui.temperature_chart(hours, colors), width="stretch", theme="streamlit", config=ui.CHART_CONFIG)
        if hours["precipitation_probability"].fillna(0).max() == 0 and hours["precipitation"].fillna(0).max() == 0:
            st.caption("🌂 Chance of rain: 0% for every one of the next 48 hours.")
        else:
            st.plotly_chart(ui.rain_chart(hours, colors), width="stretch", theme="streamlit", config=ui.CHART_CONFIG)
        with st.expander("Hour-by-hour table (temperature, rain, humidity, wind)"):
            st.dataframe(ui.hourly_table(hours), hide_index=True, width="stretch", column_config={
                "Temp °C": st.column_config.NumberColumn(format="%.1f"),
                "Feels °C": st.column_config.NumberColumn(format="%.1f"),
                "Rain mm": st.column_config.NumberColumn(format="%.1f"),
            })

with tab_days:
    daily = forecast.daily
    with st.container(horizontal=True, wrap=True, gap="small"):
        for day, row in daily.iterrows():
            with st.container(border=True, width=150):
                st.markdown(f"**{pd.Timestamp(day):%a}**  \n{pd.Timestamp(day):%d %b}")
                st.markdown(f"<div style='font-size:1.8rem'>{ui.weather_icon(row['weather_code'])}</div>",
                            unsafe_allow_html=True)
                st.markdown(f"**{ui.fmt(row['temperature_2m_max'], '°')}** / "
                            f"{ui.fmt(row['temperature_2m_min'], '°')}")
                st.caption(f"{row['description']}  \nRain {ui.fmt(row['precipitation_probability_max'], '%')}"
                           f" · {ui.fmt(row['precipitation_sum'], ' mm', 1)}  \n"
                           f"Wind {ui.fmt(row['wind_speed_10m_max'], ' km/h')}  \n"
                           f"UV {ui.fmt(row['uv_index_max'], '', 1)}")
    st.plotly_chart(ui.daily_range_chart(daily, colors), width="stretch", theme="streamlit", config=ui.CHART_CONFIG)

with tab_cloudzy:
    st.markdown("**Cloudzy Core** is this project's own machine-learning model. It is experimental and "
                "separate from the provider forecast above.")
    if not near_delhi:
        st.info(f"Cloudzy Core is trained only for {DELHI.name}. It is shown for places within "
                f"{CLOUDZY_RADIUS_KM:.0f} km of its training point, so it isn't available for {place.label}.",
                icon="📍")
    else:
        with st.spinner("Asking Cloudzy Core…"):
            block = cached_cloudzy(place)
        if not block["available"]:
            st.warning(f"Cloudzy Core is unavailable right now: {block['reason']}", icon="🧪")
            retry_button("retry-cloudzy")
        else:
            preds = block["cloudzy"]["predictions"]
            prov = block["provider"]["forecasts"]
            st.caption(f"Prediction point: {block['prediction_point']}, {block['distance_km_from_place']} km "
                       f"from {place.name}. Issued {pd.Timestamp(block['issue_time_local']):%H:%M} local "
                       f"from ECMWF IFS inputs.")
            st.plotly_chart(ui.cloudzy_chart(preds, prov, colors), width="stretch", theme="streamlit", config=ui.CHART_CONFIG)
            st.dataframe(ui.cloudzy_table(preds, prov), hide_index=True, width="stretch", column_config={
                "Cloudzy °C": st.column_config.NumberColumn(format="%.1f"),
                "Provider °C": st.column_config.NumberColumn(format="%.1f"),
                "Cloudzy avg error °C (2025)": st.column_config.NumberColumn(format="%.2f"),
            })
            with st.expander("How to read this"):
                st.markdown(
                    "- Cloudzy predicts the **ERA5/ERA5-Land reanalysis** temperature for one grid cell, "
                    "correcting live ECMWF IFS inputs toward it. It isn't trying to beat the provider "
                    "forecast, and it isn't a thermometer reading.\n"
                    "- *Avg error* is the mean absolute error on a 2025 replay of archived inputs. That is "
                    "a best case for live use. It is **not** a confidence interval.\n"
                    "- On that replay, Cloudzy was closer to the reanalysis than the provider's own values "
                    "for horizons up to 12 hours. At 24 hours the difference was not significant.\n"
                    "- Use the provider forecast for decisions. Cloudzy is a learning experiment.")

# --- Data notes and footer ------------------------------------------------------------

if forecast.warnings:
    with st.expander(f"Data notes ({len(forecast.warnings)})"):
        st.caption("Values below were missing or implausible in the provider response. They are "
                   "shown as “—”, never filled in.")
        for w in forecast.warnings:
            st.markdown(f"- {w}")

st.divider()
st.caption(f"{ATTRIBUTION}. Reanalysis: Copernicus Climate Change Service (ERA5, ERA5-Land). "
           "Cloudzy is an experimental student project, not an operational weather service. "
           "Weather icons are approximate.")
