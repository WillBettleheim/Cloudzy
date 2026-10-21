"""Cached data access shared by the app's pages (Weather, Air Quality, Outdoor, Alerts, Places).

Calls go through the provider and briefing modules at call time (not imported
names), so tests can replace them. Every result is cached for 10 minutes per
place, which keeps Open-Meteo use far inside its free limits. Air-quality data are cached for
15 minutes: CAMS runs update every 12-24 hours, and the current hour changes at most hourly.

The place the visitor is looking at is kept in their session, so the Alerts
page can show alerts for the same place as the Weather page.
"""

from __future__ import annotations

import os

import streamlit as st

from src import air_quality, briefing, provider
from src.provider import Place

CACHE_SECONDS = 600
AIR_CACHE_SECONDS = 900
PLACE_KEY = "_cz_place"


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def search(query: str):
    return provider.search_locations(query, count=8)


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def forecast(place):
    return provider.fetch_forecast(place)


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def cloudzy(place):
    return briefing.cloudzy_block(place, snapshot=os.environ.get("CLOUDZY_SNAPSHOTS", "1") == "1")


@st.cache_data(ttl=AIR_CACHE_SECONDS, show_spinner=False)
def air(place):
    return air_quality.fetch_air_quality(place)


def environment_alerts(place: Place, forecast, prefs, units, now) -> tuple[list, str | None]:
    """Air-quality, particle and saved-plan alerts for `place`. Never raises: if the air-quality data can't be
    loaded, returns no alerts and the reason, so the weather alerts still show."""
    from src import air_alerts, aqi
    from src.data_collection import DataCollectionError
    enabled = prefs.enabled_alert_kinds()
    if not enabled & {"air", "particles", "plan"}:
        return [], None
    try:
        aq = air(place)
    except (air_quality.AirQualityError, DataCollectionError) as exc:
        return [], f"Air-quality alerts are unavailable right now: {exc}"
    standard, _ = aqi.resolve_standard(prefs.aqi_standard, place.country_code)
    found = air_alerts.air_alerts(aq, now, standard, prefs.alerts, enabled)
    if "plan" in enabled and prefs.plans and forecast is not None:
        found += air_alerts.plan_alerts(prefs.plans, aq, forecast, now, units, place)[0]
    return found, None


def remember_place(place: Place) -> None:
    st.session_state[PLACE_KEY] = place


def current_place() -> Place | None:
    place = st.session_state.get(PLACE_KEY)
    return place if isinstance(place, Place) else None
