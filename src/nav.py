"""Links between pages that also work when a page runs on its own (tests, `streamlit run app_pages/x.py`)."""

from __future__ import annotations

import streamlit as st
from streamlit.errors import StreamlitAPIException


def page_link(path: str, label: str, icon: str | None = None) -> None:
    """st.page_link, or a plain pointer to the sidebar when the page isn't part of the navigation."""
    try:
        st.page_link(path, label=label, icon=icon)
    except StreamlitAPIException:  # includes StreamlitPageNotFoundError
        st.caption(f"{label}: open it from the sidebar.")


def switch_page(path: str) -> None:
    """Go to another page; if the page isn't part of the navigation, stay and say so."""
    try:
        st.switch_page(path)
    except StreamlitAPIException:
        st.info("Open the Weather page from the sidebar to see this place.", icon=":material/info:")
