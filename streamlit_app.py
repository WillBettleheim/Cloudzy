"""Cloudzy — Weather with a little intelligence. Streamlit entry point.

Run:  streamlit run streamlit_app.py

Navigation (sidebar on wide screens; Streamlit collapses it behind a menu
button on narrow ones):
- Today: Overview (the landing page), Weather, Air Quality, Outdoor Intelligence
- Explore: Earth Intelligence (disasters and hazards on a 3D globe), Weather Explorer (global model map),
  History & Trends
- Cloudzy Core: Prediction History (live tracking), Model Lab (evaluation, registry, uncertainty)
- You: Alerts, Places (saved cities and home), Settings
Weather moved from "/" to "/weather" when Overview became the landing page; old "/?q=…" links are forwarded.
- Terms of Use (/terms): not in the menu; linked from the sidebar footer and Settings
"""

from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Cloudzy", page_icon="☁️", layout="wide", initial_sidebar_state="auto")

from src import browser_prefs  # noqa: E402  (after set_page_config)
from src.theme import GLOBAL_CSS  # noqa: E402

ASSETS = Path(__file__).parent / "assets"

pages = {
    "Today": [
        st.Page("app_pages/overview.py", title="Overview", icon=":material/dashboard:", default=True),
        st.Page("app_pages/weather.py", title="Weather", icon=":material/partly_cloudy_day:", url_path="weather"),
        st.Page("app_pages/air_quality.py", title="Air Quality", icon=":material/air:", url_path="air-quality"),
        st.Page("app_pages/outdoor.py", title="Outdoor Intelligence", icon=":material/directions_run:",
                url_path="outdoor"),
    ],
    "Explore": [
        st.Page("app_pages/earth.py", title="Earth Intelligence", icon=":material/travel_explore:", url_path="earth"),
        st.Page("app_pages/explorer.py", title="Weather Explorer", icon=":material/public:", url_path="explorer"),
        st.Page("app_pages/history.py", title="History & Trends", icon=":material/history:", url_path="history"),
    ],
    "Cloudzy Core": [
        st.Page("app_pages/predictions.py", title="Prediction History", icon=":material/query_stats:",
                url_path="predictions"),
        st.Page("app_pages/lab.py", title="Model Lab", icon=":material/science:", url_path="lab"),
    ],
    "You": [
        st.Page("app_pages/alerts.py", title="Alerts", icon=":material/notifications:", url_path="alerts"),
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
st.sidebar.caption("Weather with a little intelligence. Forecasts from Open-Meteo, air quality from CAMS; "
                   "Cloudzy Core is an experimental temperature model for New Delhi.")
st.sidebar.page_link("app_pages/terms.py", label="Terms of Use", icon=":material/gavel:")

# Evaluate matured Cloudzy predictions in the background (throttled; see src.monitoring.maybe_evaluate).
try:
    from src import monitoring
    monitoring.maybe_evaluate()
except Exception:   # tracking must never stop the app from drawing
    pass

# Each visitor's settings live in their own browser; wait for them before drawing any page.
if not browser_prefs.begin():
    st.stop()
page.run()
