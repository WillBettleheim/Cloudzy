"""Places: the visitor's saved cities, their home city, and quick switching to any of them.

Saved in the visitor's own browser (src.preferences). Places are added from the Weather page's
Save button; only city coordinates from geocoding are kept, never device coordinates.
"""

from __future__ import annotations

from dataclasses import asdict
from html import escape

import pandas as pd
import streamlit as st

from src import app_data, nav
from src import units as U
from src import weather_view as V
from src.data_collection import DataCollectionError
from src.provider import ProviderError
from src import preferences as P
from src import theme as T

st.html(T.page_header("Places", "You · saved cities", "bookmark"))
prefs = P.load()
favs = prefs.favourite_places()
home = prefs.home_place()


def open_place(place) -> None:
    st.session_state["_open_place"] = place
    nav.switch_page("app_pages/weather.py")


st.caption(f"{len(favs)} of {P.MAX_FAVOURITES} saved. To add one, search for it on the Weather page and press "
           "**Save**. Your home city opens first when you start Cloudzy (unless a link names a city, or you ask "
           "for your device location).")

if not favs:
    st.info("No saved places yet. Search for a city on the Weather page and press **Save**.",
            icon=":material/bookmark_add:")
    nav.page_link("app_pages/weather.py", "Go to the Weather page", ":material/partly_cloudy_day:")
    st.stop()

st.html(V.CSS)
override = prefs.unit_override()
cols = st.columns(2, gap="large")
for i, f in enumerate(favs):
    is_home = home is not None and P._same_place(f, home)
    units = override or U.default_for_country(f.country_code)
    try:
        fc = app_data.forecast(f)            # cached 10 minutes, shared with the Weather page
        cur = fc.current
        key = pd.Timestamp.now(tz="UTC").tz_convert(fc.hourly.index.tz).date()
        today = fc.daily.loc[key] if key in fc.daily.index else None
    except (ProviderError, DataCollectionError):
        cur, today = None, None
    with cols[i % 2]:
        st.html(V.place_card_html(f, cur, today, units, is_home)
                + f'<p class="cz-place-card" style="margin:6px 2px 0;font-size:.78rem;opacity:.65">'
                  f'{f.latitude:.2f}°, {f.longitude:.2f}° · {escape(f.timezone)}</p>')
        with T.two_up(f"places_actions_{i}"):
            b1, b2, b3 = st.columns([1, 1.45, 1.15])
        if b1.button("Open", key=f"open_{i}", icon=":material/arrow_forward:", width="stretch",
                     help=f"Show the weather for {f.label}"):
            open_place(f)
        if is_home:
            if b2.button("Unset home", key=f"unhome_{i}", icon=":material/home:", width="stretch"):
                prefs.home = None
                P.save(prefs)
                st.toast("Home city cleared", icon=":material/check:")
                st.rerun()
        elif b2.button("Make home", key=f"home_{i}", icon=":material/home:", width="stretch"):
            prefs.home = asdict(f)
            P.save(prefs)
            st.toast(f"{f.name} is your home city", icon=":material/check:")
            st.rerun()
        if b3.button("Remove", key=f"rm_fav_{i}", icon=":material/delete:", width="stretch"):
            prefs.toggle_favourite(f)
            if is_home:
                prefs.home = None
            P.save(prefs)
            st.toast(f"Removed {f.name}", icon=":material/check:")
            st.rerun()
        st.write("")
