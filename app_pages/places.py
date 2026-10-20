"""Places: the visitor's saved cities, their home city, and quick switching to any of them.

Saved in the visitor's own browser (src.preferences). Places are added from the Weather page's
Save button; only city coordinates from geocoding are kept, never device coordinates.
"""

from __future__ import annotations

from dataclasses import asdict
from html import escape

import streamlit as st

from src import nav
from src import preferences as P
from src import theme as T

st.markdown("## :material/bookmark: Places")
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

cols = st.columns(2, gap="medium")
for i, f in enumerate(favs):
    is_home = home is not None and P._same_place(f, home)
    with cols[i % 2], st.container(border=True):
        badge = T.chip("Home", "ok", "home") if is_home else ""
        st.html(f'<div class="cz-place-card"><h4 style="margin:0 0 4px;display:flex;gap:8px;align-items:center;'
                f'flex-wrap:wrap">{T.icon("location_on")}{escape(f.name)} {badge}</h4>'
                f'<p style="margin:0;opacity:.85">{escape(", ".join(x for x in (f.admin1, f.country) if x))}</p>'
                f'<p style="margin:2px 0 0;font-size:.82rem;opacity:.7">{f.latitude:.2f}°, {f.longitude:.2f}° · '
                f'{escape(f.timezone)}</p></div>')
        b1, b2, b3 = st.columns(3)
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
