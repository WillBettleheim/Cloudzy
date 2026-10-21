"""Cloudzy — Weather with a little intelligence. Streamlit entry point.

Run:  streamlit run streamlit_app.py

Navigation (sidebar on wide screens; Streamlit collapses it behind a menu
button on narrow ones):
- Forecast: Weather, Weather Explorer (global map), History & Trends, Alerts
- Cloudzy Core: Predictions (live tracking), Cloudzy Lab (offline evaluation)
- You: Places (saved cities and home), Settings
- Terms of Use (/terms): not in the menu; linked from the sidebar footer and Settings
"""

from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Cloudzy", page_icon="☁️", layout="wide", initial_sidebar_state="auto")

from src import browser_prefs  # noqa: E402  (after set_page_config)
from src.theme import GLOBAL_CSS  # noqa: E402

ASSETS = Path(__file__).parent / "assets"

pages = {
    "Forecast": [
        st.Page("app_pages/weather.py", title="Weather", icon=":material/partly_cloudy_day:", default=True),
        st.Page("app_pages/explorer.py", title="Weather Explorer", icon=":material/public:", url_path="explorer"),
        st.Page("app_pages/history.py", title="History & Trends", icon=":material/history:", url_path="history"),
        st.Page("app_pages/alerts.py", title="Alerts", icon=":material/notifications:", url_path="alerts"),
    ],
    "Cloudzy Core": [
        st.Page("app_pages/predictions.py", title="Predictions", icon=":material/query_stats:",
                url_path="predictions"),
        st.Page("app_pages/lab.py", title="Cloudzy Lab", icon=":material/science:", url_path="lab"),
    ],
    "You": [
        st.Page("app_pages/places.py", title="Places", icon=":material/bookmark:", url_path="places"),
        st.Page("app_pages/settings.py", title="Settings", icon=":material/settings:", url_path="settings"),
        # Not in the menu: linked from the sidebar footer and from Settings.
        st.Page("app_pages/terms.py", title="Terms of Use", icon=":material/gavel:", url_path="terms",
                visibility="hidden"),
    ],
}
page = st.navigation(pages, position="sidebar")
st.logo(str(ASSETS / "cloudzy_logo.svg"), icon_image=str(ASSETS / "cloudzy_icon.svg"), size="large")
st.html(GLOBAL_CSS)
st.sidebar.caption("Weather with a little intelligence. Forecasts from Open-Meteo; Cloudzy Core is an "
                   "experimental model for New Delhi.")
st.sidebar.page_link("app_pages/terms.py", label="Terms of Use", icon=":material/gavel:")

# Each visitor's settings live in their own browser; wait for them before drawing any page.
if not browser_prefs.begin():
    st.stop()
page.run()
