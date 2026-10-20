"""Settings: units, location detection, summary style, theme and reset.

Saved places and the home city are on the Places page; alert categories, thresholds and
notifications are on the Alerts page. Settings are saved in the visitor's own browser
(src.preferences), never synced, and precise device coordinates are never saved.
"""

from __future__ import annotations

import streamlit as st

from src import browser_prefs, nav
from src import preferences as P
from src import theme as T
from src import units as U

st.markdown("## :material/settings: Settings")
if P.store() == "browser":
    if browser_prefs.storage_available() is False:
        st.warning("This browser isn't letting Cloudzy store settings (private browsing, or site data blocked), so "
                   "changes here last only until you close this tab.", icon=":material/lock:")
    else:
        st.caption("Saved in this browser only. Other people using Cloudzy don't see or change your settings, and "
                   "nothing is synced between your devices.")
else:
    st.caption("Saved in a settings file on the computer running Cloudzy (file mode, for one person). Nothing is "
               "uploaded or synced.")
prefs = P.load()


def save(message: str = "Saved") -> None:
    P.save(prefs)
    browser_prefs.flush()   # write to this browser now; this page doesn't rerun after saving
    st.toast(message, icon=":material/check:")


# --- Units ---------------------------------------------------------------------------------
st.html(T.section("Units", "straighten"))
override = prefs.unit_override()
regional = st.toggle("Use each place's regional units", value=override is None,
                     help="For example °F and mph in the United States, °C and km/h in most other places.")
if regional:
    if override is not None:
        prefs.units = None
        save("Using regional units")
else:
    current = override or U.METRIC
    c1, c2, c3 = st.columns(3)
    t = c1.segmented_control("Temperature", U.TEMPERATURE, default=current.temperature, key="set_t")
    w = c2.segmented_control("Wind", U.WIND, default=current.wind, key="set_w")
    r = c3.segmented_control("Rain", U.PRECIPITATION, default=current.precipitation, key="set_r")
    chosen = {"temperature": t or current.temperature, "wind": w or current.wind, "precipitation": r or current.precipitation}
    if prefs.units != chosen:
        prefs.units = chosen
        save("Units saved")

# --- Location ----------------------------------------------------------------------------------
st.html(T.section("Location", "my_location"))
auto = st.toggle("Detect my location when Cloudzy opens", value=prefs.auto_locate,
                 help="Your browser asks permission first. Only this yes/no choice is saved, never your position.")
if auto != prefs.auto_locate:
    prefs.auto_locate = auto
    save()
nav.page_link("app_pages/places.py", "Saved places and home city", ":material/bookmark:")

# --- Summary -------------------------------------------------------------------------------
st.html(T.section("“Cloudzy says” summary", "auto_awesome"))
style = st.radio("Summary style", P.SUMMARY_STYLES, index=P.SUMMARY_STYLES.index(prefs.summary_style),
                 format_func={"detailed": "Detailed (all relevant sentences)",
                              "brief": "Brief (current conditions and rain only)"}.get, horizontal=True)
if style != prefs.summary_style:
    prefs.summary_style = style
    save()

# --- Alerts (pointer) ------------------------------------------------------------------------
st.html(T.section("Alerts", "notifications"))
st.caption(f"{len(prefs.enabled_alert_kinds())} alert categories on · notifications "
           f"{'offered' if prefs.notifications else 'off'} · {len(prefs.dismissed_alerts)} dismissed.")
nav.page_link("app_pages/alerts.py", "Alert categories, thresholds and notifications", ":material/tune:")

# --- Theme ---------------------------------------------------------------------------------
st.html(T.section("Theme", "contrast"))
st.markdown("Light, dark or *use system setting*: open the **⋮** menu (top right) → **Settings** → "
            "**Theme**. Your browser remembers the choice. Cloudzy's charts and cards follow it.")

# --- Reset ---------------------------------------------------------------------------------
st.divider()
with st.expander("Reset"):
    st.caption("Deletes your saved units, places, home city, thresholds and alert choices. "
               "Prediction tracking records are kept.")
    if st.button("Reset all settings", type="secondary", icon=":material/restart_alt:"):
        P.save(P.Preferences())
        st.toast("Settings reset", icon=":material/check:")
        st.rerun()
