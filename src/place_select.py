"""The place for the Air Quality and Outdoor pages, with a compact way to change it.

The place is shared with the Weather page through the session (src.app_data): these pages start
from the place the Weather page last showed, else the home city, else New Delhi. Choosing a new
place here is remembered for the session and handed to the Weather page as a manual choice, the
same way the Places page opens a place. Only geocoded city coordinates are used, never the
device's position.
"""

from __future__ import annotations

import streamlit as st

from src import app_data
from src.data_collection import DataCollectionError
from src.provider import Place, ProviderError

DEFAULT_QUERY = "New Delhi"


def _choose(place: Place) -> None:
    app_data.remember_place(place)
    st.session_state["_open_place"] = place      # the Weather page opens it next (src: app_pages/weather.py)


def current(prefs) -> Place | None:
    """The place to show, or None (after explaining why) if none can be found."""
    place = app_data.current_place() or prefs.home_place()
    if place is not None:
        return place
    try:
        found = app_data.search(DEFAULT_QUERY)
    except (ProviderError, DataCollectionError) as exc:
        st.error(f"Couldn't look up a place to show: {exc}", icon=":material/error:")
        return None
    if not found:
        st.warning("No place to show. Search for one on the Weather page.", icon=":material/travel_explore:")
        return None
    app_data.remember_place(found[0])
    return found[0]


def picker(prefs, key: str) -> None:
    """A 'Change place' popover: search, or pick a saved place."""
    with st.popover("Change place", icon=":material/edit_location_alt:", width="stretch"):
        favs = prefs.favourite_places()
        if favs:
            st.caption("Saved places")
            for i, fav in enumerate(favs):
                if st.button(fav.label, key=f"{key}_fav_{i}", icon=":material/bookmark:", width="stretch"):
                    _choose(fav)
                    st.rerun()
        query = st.text_input("Search for a city or town", key=f"{key}_q", max_chars=80,
                              placeholder="e.g. Mumbai, Paris, Denver").strip()
        if len(query) < 2:
            st.caption("Type at least two letters and press Enter.")
            return
        try:
            results = app_data.search(query)
        except (ProviderError, DataCollectionError) as exc:
            st.error(f"Location search is unavailable right now. {exc}", icon=":material/error:")
            return
        if not results:
            st.caption(f"No places match “{query}”.")
            return
        pick = st.selectbox("Choose the right match", results, format_func=lambda p: p.label, key=f"{key}_pick")
        if st.button("Show this place", key=f"{key}_go", type="primary", icon=":material/check:"):
            _choose(pick)
            st.rerun()
