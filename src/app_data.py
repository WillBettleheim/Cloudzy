"""Cached data access shared by the app's pages (Weather, Alerts, Places).

Calls go through the provider and briefing modules at call time (not imported
names), so tests can replace them. Every result is cached for 10 minutes per
place, which keeps Open-Meteo use far inside its free limits.

The place the visitor is looking at is kept in their session, so the Alerts
page can show alerts for the same place as the Weather page.
"""

from __future__ import annotations

import os

import streamlit as st

from src import briefing, provider
from src.provider import Place

CACHE_SECONDS = 600
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


def remember_place(place: Place) -> None:
    st.session_state[PLACE_KEY] = place


def current_place() -> Place | None:
    place = st.session_state.get(PLACE_KEY)
    return place if isinstance(place, Place) else None
