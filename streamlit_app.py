"""Cloudzy — Weather with a little intelligence. Streamlit entry point.

Run:  streamlit run streamlit_app.py

Two pages: the weather app (Phase D) and Cloudzy Lab, the model evaluation
dashboard (Phase E).
"""

import streamlit as st

st.set_page_config(page_title="Cloudzy", page_icon="☁️", layout="wide")

page = st.navigation([
    st.Page("app_pages/weather.py", title="Weather", icon="☁️", default=True),
    st.Page("app_pages/lab.py", title="Cloudzy Lab", icon="🧪", url_path="lab"),
], position="top")
page.run()
