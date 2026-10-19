"""Cloudzy — Weather with a little intelligence. Streamlit entry point.

Run:  streamlit run streamlit_app.py

Pages: Weather (forecast, alerts, Cloudzy Core), History (reanalysis),
Predictions (tracking of live Cloudzy Core predictions), Cloudzy Lab (model
evaluation) and Settings.
"""

import streamlit as st

st.set_page_config(page_title="Cloudzy", page_icon="☁️", layout="wide")

page = st.navigation([
    st.Page("app_pages/weather.py", title="Weather", icon="☁️", default=True),
    st.Page("app_pages/history.py", title="History", icon="🕰️", url_path="history"),
    st.Page("app_pages/predictions.py", title="Predictions", icon="📈", url_path="predictions"),
    st.Page("app_pages/lab.py", title="Cloudzy Lab", icon="🧪", url_path="lab"),
    st.Page("app_pages/settings.py", title="Settings", icon="⚙️", url_path="settings"),
], position="top")
page.run()
