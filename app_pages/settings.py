"""Settings: units, home city, favourites, summary style, alerts and notifications.

Everything here is saved in one JSON file on this device (src.preferences).
Nothing is synced to a server, and precise device coordinates are never saved.
"""

from __future__ import annotations

from dataclasses import asdict

import streamlit as st

from src import browser_prefs
from src import insights as I
from src import preferences as P
from src import units as U

st.markdown("## ⚙️ Settings")
if P.store() == "browser":
    if browser_prefs.storage_available() is False:
        st.warning("This browser isn't letting Cloudzy store settings (private browsing, or site data blocked), so "
                   "changes here last only until you close this tab.", icon="🔒")
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
    st.toast(message, icon="✅")


# --- Units ---------------------------------------------------------------------------------
st.markdown("#### Units")
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

# --- Places --------------------------------------------------------------------------------
st.markdown("#### Places")
favs = prefs.favourite_places()
if not favs:
    st.info("No favourite places yet. On the **Weather** page, search for a city and press **☆ Save**.", icon="⭐")
else:
    labels = ["(none: start with New Delhi or the last city you searched)"] + [f.label for f in favs]
    home = prefs.home_place()
    idx = next((i + 1 for i, f in enumerate(favs) if home and P._same_place(f, home)), 0)
    pick = st.selectbox("Home city, shown when Cloudzy opens", labels, index=idx,
                        help="A link that names a city, a city you pick, or your device location (when you "
                             "ask for it) always takes priority.")
    new_home = None if pick == labels[0] else asdict(favs[labels.index(pick) - 1])
    if new_home != prefs.home:
        prefs.home = new_home
        save("Home city saved")
    st.caption("Favourite places (switch between them from the top of the Weather page):")
    for i, f in enumerate(favs):
        a, b = st.columns([5, 1], vertical_alignment="center")
        a.markdown(f"⭐ {f.label}")
        if b.button("Remove", key=f"rm_fav_{i}", width="stretch"):
            prefs.toggle_favourite(f)
            if prefs.home_place() and P._same_place(prefs.home_place(), f):
                prefs.home = None
            save(f"Removed {f.name}")
            st.rerun()

auto = st.toggle("Detect my location when Cloudzy opens", value=prefs.auto_locate,
                 help="Your browser asks permission first. Only this yes/no choice is saved, never your position.")
if auto != prefs.auto_locate:
    prefs.auto_locate = auto
    save()

# --- Summary -------------------------------------------------------------------------------
st.markdown("#### “Cloudzy says” summary")
style = st.radio("Summary style", P.SUMMARY_STYLES, index=P.SUMMARY_STYLES.index(prefs.summary_style),
                 format_func={"detailed": "Detailed (all relevant sentences)",
                              "brief": "Brief (current conditions and rain only)"}.get, horizontal=True)
if style != prefs.summary_style:
    prefs.summary_style = style
    save()

# --- Alerts --------------------------------------------------------------------------------
st.markdown("#### Alerts")
st.caption("Cloudzy alerts are simple threshold checks on the forecast for the next 24 hours, not official "
           "warnings. Thresholds can be changed under **Alert thresholds** on the Weather page.")
cols = st.columns(3)
cats = {}
for i, (kind, name) in enumerate(I.ALERT_KINDS.items()):
    cats[kind] = cols[i % 3].checkbox(name, value=prefs.alert_categories.get(kind, True), key=f"cat_{kind}")
if cats != {k: prefs.alert_categories.get(k, True) for k in I.ALERT_KINDS}:
    prefs.alert_categories = cats
    save("Alert categories saved")
if prefs.dismissed_alerts:
    if st.button(f"Show {len(prefs.dismissed_alerts)} dismissed alert(s) again"):
        prefs.dismissed_alerts = {}
        save("Dismissed alerts restored")
notify = st.toggle("Offer browser notifications for alerts", value=prefs.notifications,
                   help="Adds a button on the Weather page. Your browser asks permission only when you click it.")
if notify != prefs.notifications:
    prefs.notifications = notify
    save()
st.caption("Notifications only appear while Cloudzy is open in a browser tab; there are no background or push "
           "notifications. Each alert notifies once. If your browser blocks them, alerts still show on the page.")

# --- Theme ---------------------------------------------------------------------------------
st.markdown("#### Theme")
st.markdown("Light, dark or *use system setting*: open the **⋮** menu (top right) → **Settings** → "
            "**Theme**. Your browser remembers the choice. Cloudzy's charts and cards follow it.")

# --- Reset ---------------------------------------------------------------------------------
st.divider()
with st.expander("Reset"):
    st.caption("Deletes your saved units, favourites, home city, thresholds and alert choices. "
               "Prediction tracking records are kept.")
    if st.button("Reset all settings", type="secondary"):
        P.save(P.Preferences())
        st.toast("Settings reset", icon="♻️")
        st.rerun()
