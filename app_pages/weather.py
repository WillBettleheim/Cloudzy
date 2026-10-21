"""Cloudzy weather page. Opened through streamlit_app.py.

Layout, most important first:
1. Search row: city search, "Use my location" and units, with saved places as quick switches.
2. Above the fold: current conditions (hero) beside "Cloudzy says" and active alerts.
3. Next 24 hours (cards, then temperature and rain charts), the 7-day forecast beside the
   current measurements, today's briefing and suggestions.
4. Cloudzy Core preview (experimental, Delhi only), clearly separate from the provider forecast.

- Values are fetched in metric units and converted for display (src.units). Units default to the
  place's region; the viewer can override them.
- Every network call is cached for 10 minutes per place (src.app_data).
- The location is kept in the URL (?q=…&i=…), never device coordinates.
- On any failure the page shows the reason and a retry button, never stale or invented numbers.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import air_quality as AQ
from src import air_view as AV
from src import app_data, aqi
from src import geolocation as G
from src import insights as I
from src import nav
from src import preferences as P
from src import theme as T
from src import ui
from src import units as U
from src import weather_view as V
from src.briefing import CLOUDZY_RADIUS_KM, distance_km
from src.config import DELHI
from src.data_collection import DataCollectionError
from src.provider import ATTRIBUTION, ProviderError, rain_outlook, upcoming

DEFAULT_QUERY = "New Delhi"
HOURS_AHEAD = 24
UNIT_KEYS = {"temperature": "unit_temp", "wind": "unit_wind", "precipitation": "unit_rain"}


def retry_button(key: str) -> None:
    if st.button("Try again", key=key, icon=":material/refresh:"):
        st.cache_data.clear()
        st.rerun()


def place_label(p) -> str:
    pop = f" · pop. {p.population:,}" if p.population else ""
    return f"{p.label}{pop}"


# --- Preference callbacks ------------------------------------------------------------

def save_unit_override() -> None:
    prefs = P.load()
    prefs.units = {name: st.session_state[key] for name, key in UNIT_KEYS.items() if st.session_state.get(key)}
    if prefs.unit_override() is None:
        prefs.units = None
    P.save(prefs)
    st.session_state["_units_for"] = None  # re-derive on the next run (now from the override)


def use_regional_units() -> None:
    prefs = P.load()
    prefs.units = None
    P.save(prefs)
    st.session_state["_units_for"] = None


def pick_favourite() -> None:
    G.use_manual(G.state(st.session_state))  # a manual choice always wins over device location
    label = st.session_state.get("fav_pick")
    for fav in P.load().favourite_places():
        if fav.label == label:
            st.session_state["search"] = fav.name
            st.session_state["_fav_target"] = (fav.latitude, fav.longitude)
    st.session_state["fav_pick"] = None


def request_location() -> None:
    G.start(G.state(st.session_state))


def cancel_location() -> None:
    G.cancel(G.state(st.session_state))


def search_instead() -> None:
    G.use_manual(G.state(st.session_state))


# --- Location: device or manual ------------------------------------------------------------

st.html(V.CSS)
prefs = P.load()
params = st.query_params
geo = G.state(st.session_state)

# A place opened from the Places page shows here (a manual choice, so it wins over device location).
opened = st.session_state.pop("_open_place", None)
if opened is not None:
    G.use_manual(geo)
    st.session_state["search"] = opened.name
    st.session_state["_fav_target"] = (opened.latitude, opened.longitude)
    for k in ("q", "i"):
        if k in params:
            del params[k]

# Opt-in: look up the location once when a session opens, unless the link names a city.
if prefs.auto_locate and not geo["auto_tried"] and "q" not in params:
    geo["auto_tried"] = True
    G.start(geo)

# The invisible browser script runs one lookup per request number. Its answer is applied only if it
# matches the request still pending, so late answers can't override a newer choice.
G.handle(geo, G.browser_location(geo["pending"] or 0))

if geo["pending"] is not None:
    msg_col, cancel_col = st.columns([4, 1], vertical_alignment="center")
    msg_col.info("**Finding your location…** If your browser asks, choose *Allow*. Cloudzy looks your "
                 "position up once and doesn't track you.", icon=":material/my_location:")
    cancel_col.button("Cancel", on_click=cancel_location, width="stretch")
err = geo["error"]
if err is not None and geo["pending"] is None:
    st.warning(f"**{err.title}** {err.detail}", icon=":material/location_off:")
    if err.retry:
        st.button("Try again", icon=":material/refresh:", key="geo-retry", on_click=request_location)

detected_slot = None
if geo["mode"] == "device" and geo["fix"] is not None:
    fix = geo["fix"]
    with st.spinner("Looking up the place name…"):
        rev = G.reverse_geocode(fix.latitude, fix.longitude)
    place = G.device_place(fix, rev)
    for k in ("q", "i"):  # the URL must not name a different place, and never carries coordinates
        if k in params:
            del params[k]
    loc_col, unit_col = st.columns([5, 1.4], vertical_alignment="bottom")
    with loc_col:
        st.markdown(f"**:material/my_location: {place.label}** · your device location")
        detected_slot = st.empty()
        a, b = st.columns(2)
        a.button("Refresh location", icon=":material/refresh:", on_click=request_location, width="stretch",
                 disabled=geo["pending"] is not None)
        b.button("Search for a city instead", icon=":material/search:", on_click=search_instead, width="stretch")
else:
    if "search" not in st.session_state:
        # Streamlit drops a widget's value while it's hidden (device mode), so restore the last manual city.
        home = prefs.home_place()
        if params.get("q") or st.session_state.get("_last_query") or home is None:
            st.session_state["search"] = params.get("q") or st.session_state.get("_last_query", DEFAULT_QUERY)
        else:  # first visit with a saved home city (set on the Places page)
            st.session_state["search"] = home.name
            st.session_state["_fav_target"] = (home.latitude, home.longitude)
    search_col, loc_col, unit_col = st.columns([3.2, 2, 1.3], vertical_alignment="bottom")
    query = search_col.text_input(
        "Search for a city or town", key="search", placeholder="e.g. Mumbai, Reykjavik, Lagos", max_chars=80,
        icon=":material/search:", help="Press Enter to search. Results use Open-Meteo's geocoding service.").strip()
    loc_col.button("Use my location", icon=":material/my_location:", type="primary", on_click=request_location,
                   width="stretch", disabled=geo["pending"] is not None,
                   help="Your browser asks for permission. The position is used once for the weather and a place "
                        "name, and isn't saved or tracked.")
    favs = prefs.favourite_places()
    if favs:
        st.pills("Saved places", [f.label for f in favs], key="fav_pick", on_change=pick_favourite,
                 label_visibility="collapsed", help="Your saved places (manage them on the Places page).")
    if len(query) < 2:
        st.info("Type at least two letters to search for a place.", icon=":material/search:")
        st.stop()

    try:
        with st.spinner("Looking for that place…"):
            places = app_data.search(query)
    except (ProviderError, DataCollectionError) as exc:
        st.error(f"Location search is unavailable right now. {exc}", icon=":material/error:")
        retry_button("retry-search")
        st.stop()

    if not places:
        st.warning(f"No places match “{query}”. Check the spelling, or try a nearby larger town.",
                   icon=":material/travel_explore:")
        st.stop()

    target = st.session_state.pop("_fav_target", None)
    if target:  # a saved place was picked: choose the result with its exact coordinates
        start_index = next((i for i, p in enumerate(places)
                            if abs(p.latitude - target[0]) < 0.01 and abs(p.longitude - target[1]) < 0.01), 0)
    else:
        try:
            start_index = int(params.get("i", st.session_state.get("_last_index", 0))) \
                if params.get("q", st.session_state.get("_last_query")) == query else 0
        except ValueError:
            start_index = 0
    pick_col, fav_col = st.columns([5, 1.2], vertical_alignment="bottom")
    with pick_col:
        place = st.selectbox("Choose the right match", places, index=min(max(start_index, 0), len(places) - 1),
                             format_func=place_label, help="Several places can share a name. Pick the one you mean.")
    params["q"], params["i"] = query, str(places.index(place))  # never device coordinates in the URL
    st.session_state["_last_query"], st.session_state["_last_index"] = query, places.index(place)

    with fav_col:
        saved = prefs.is_favourite(place)
        if st.button("Saved" if saved else "Save",
                     icon=":material/bookmark_added:" if saved else ":material/bookmark_add:",
                     width="stretch", help="Remove from saved places" if saved else "Save this place"):
            try:
                prefs.toggle_favourite(place)
                P.save(prefs)
            except ValueError as exc:
                st.toast(str(exc))
            st.rerun()

app_data.remember_place(place)

# Units: the saved override if any, else the place's regional default.
regional = U.default_for_country(place.country_code)
override = prefs.unit_override()
effective = override or regional
units_for = (place.country_code, override)
if st.session_state.get("_units_for") != units_for:
    for name, key in UNIT_KEYS.items():
        st.session_state[key] = getattr(effective, name)
    st.session_state["_units_for"] = units_for
with unit_col, st.popover("Units", icon=":material/straighten:", width="stretch"):
    st.segmented_control("Temperature", U.TEMPERATURE, key="unit_temp", on_change=save_unit_override)
    st.segmented_control("Wind", U.WIND, key="unit_wind", on_change=save_unit_override)
    st.segmented_control("Rain", U.PRECIPITATION, key="unit_rain", on_change=save_unit_override)
    st.caption(f"Default for {place.country or 'this place'}: {regional.temperature}, {regional.wind}, "
               f"{regional.precipitation}." + (" You've saved your own choice." if override else ""))
    if override:
        st.button("Use regional defaults", on_click=use_regional_units)
units = U.Units(st.session_state.get("unit_temp") or effective.temperature,
                st.session_state.get("unit_wind") or effective.wind,
                st.session_state.get("unit_rain") or effective.precipitation)

# --- Provider forecast (a skeleton shows while it loads) ------------------------------------

loading = st.empty()
loading.html(T.skeleton(260) + T.skeleton(90))
try:
    forecast = app_data.forecast(place)
except (ProviderError, DataCollectionError) as exc:
    loading.empty()
    st.error(f"We couldn't get the forecast for {place.label}. {exc}", icon=":material/cloud_off:")
    retry_button("retry-forecast")
    st.stop()
loading.empty()

colors = ui.palette()
now = pd.Timestamp.now(tz="UTC")
cur = forecast.current
tz = forecast.hourly.index.tz
local_now = pd.Timestamp(cur["time"]).tz_convert(tz)  # keeps the zone name, e.g. IST
daily = forecast.daily
today_key = now.tz_convert(tz).date()
today = daily.loc[today_key] if today_key in daily.index else (daily.iloc[0] if len(daily) else None)

if detected_slot is not None:
    fix = geo["fix"]
    acc = f" · accuracy about ±{fix.accuracy_m:,.0f} m" if fix.accuracy_m is not None else ""
    name_note = "" if place.name != G.NEUTRAL_LABEL else " · place name unavailable, weather uses your exact coordinates"
    detected_slot.caption(f"Location detected {pd.Timestamp(fix.detected_at_utc).tz_convert(tz):%H:%M} local time"
                          f"{acc} · one-time lookup, not tracking{name_note}"
                          + ("" if place.name == G.NEUTRAL_LABEL else f" · {G.OSM_ATTRIBUTION}"))

# --- Above the fold: conditions, Cloudzy says, alerts --------------------------------------------

st.html(V.hero_html(place.label, cur, today, units, local_now.tzname() or str(tz)))
st.caption(f"{forecast.source_label} · {V.updated_label(forecast.retrieved_at_utc, tz)}, refreshed at most every "
           "10 minutes · model estimates for this location, not a station reading · the sky behind the reading "
           "reflects the forecast condition and time of day; it is decoration, not imagery")
side_col, alert_col = st.columns([1.5, 1], gap="large")

found = I.alerts(forecast, now, prefs.alerts, units, enabled=prefs.enabled_alert_kinds())
env_alerts, env_error = app_data.environment_alerts(place, forecast, prefs, units, now)  # never raises
found += env_alerts
dismissed = prefs.active_dismissals(now)
shown = [a for a in found if a.key not in dismissed]
with side_col:
    st.html(V.summary_html(I.summary(forecast, now, units, style=prefs.summary_style)))
    # Air quality in one line. It loads separately, so a failure here never affects the weather forecast.
    try:
        aq_data = app_data.air(place)
        aq_std, _ = aqi.resolve_standard(prefs.aqi_standard, place.country_code)
        st.html(AV.CSS + AV.strip_html(AQ.reading(aq_data, aq_std, now)))
    except (AQ.AirQualityError, DataCollectionError):
        st.caption(":material/cloud_off: Air quality isn't available right now.")
    nav.page_link("app_pages/air_quality.py", "Air quality details and outdoor planner", ":material/air:")
with alert_col:
    st.html(T.eyebrow("Alerts · next 24 hours", "notifications"))
    for i, alert in enumerate(shown):
        st.html(V.alerts_html([alert]))
        if st.button("Dismiss for 24 hours", key=f"dismiss_{i}_{alert.kind}", icon=":material/close:",
                     help="Hide this alert for this time window. A new or changed alert still shows."):
            prefs.dismiss(alert.key, (now + pd.Timedelta(hours=24)).isoformat())
            P.save(prefs)
            st.rerun()
    if not found:
        st.caption("No Cloudzy alerts for the next 24 hours with your thresholds and alert categories.")
    elif not shown:
        st.caption(f"{len(found)} alert(s) dismissed for now.")
    if env_error:
        st.caption(env_error)
    nav.page_link("app_pages/alerts.py", "Alert settings and dismissed alerts", ":material/tune:")
if shown and prefs.notifications:
    from src.notify import browser_notifications
    browser_notifications(shown)

# --- Rain outlook -------------------------------------------------------------------

rain = rain_outlook(forecast, now)
if rain["hours_checked"] == 0:
    st.info("No upcoming hourly data to check for rain.", icon=":material/help:")
elif rain["rain_expected"]:
    first = pd.Timestamp(rain["first_rain_hour_local"])
    total = U.precipitation(rain["total_precipitation_mm"], units.precipitation)
    st.warning(f"**Rain likely from {first:%a %H:%M}**: {rain['rainy_hours']} of the next {rain['hours_checked']} "
               f"hours, up to {rain['max_probability_pct']:.0f}% chance, about "
               f"{total:.{U.precipitation_digits(units.precipitation)}f} {units.precipitation} in total.",
               icon=":material/rainy:")
else:
    st.success(f"No rain expected in the next {rain['hours_checked']} hours "
               f"(highest chance {ui.fmt(rain['max_probability_pct'], '%')}).", icon=":material/umbrella:")
st.caption(f"Rain indicator: {rain['rule']}. {rain['label']}")

# --- Next 24 hours -------------------------------------------------------------------

st.html(T.section("Next 24 hours", "schedule", "Scroll the cards sideways"))
hours = upcoming(forecast, now, HOURS_AHEAD)
if hours.empty:
    st.info("The provider returned no upcoming hours for this place.", icon=":material/help:")
else:
    st.html(V.hourly_strip_html(hours, units))
    shown_hours = U.convert_frame(hours, units)
    has_rain = (shown_hours["precipitation_probability"].fillna(0).max() > 0
                or shown_hours["precipitation"].fillna(0).max() > 0)
    t_tab, r_tab, tbl_tab = st.tabs(["Temperature", "Rain", "Table"])
    with t_tab:
        st.plotly_chart(ui.temperature_chart(shown_hours, colors, units.temperature, "Temperature and feels-like"),
                        width="stretch", theme="streamlit", config=ui.CHART_CONFIG)
    with r_tab:
        if has_rain:
            st.plotly_chart(ui.rain_chart(shown_hours, colors, units.precipitation), width="stretch",
                            theme="streamlit", config=ui.CHART_CONFIG)
        else:
            st.caption("No rain chance or amount in the next 24 hours, so there's nothing to chart.")
    with tbl_tab:
        hourly_tbl = ui.hourly_table(shown_hours, units.temperature, units.wind, units.precipitation)
        st.dataframe(hourly_tbl, hide_index=True, width="stretch", column_config={
            c: st.column_config.NumberColumn(format="%.2f" if c == "Rain in" else "%.1f")
            for c in hourly_tbl.columns if c.startswith(("Temp", "Feels", "Rain mm", "Rain in", "UV"))})

# --- Daily forecast and current details ----------------------------------------------

days_col, now_col = st.columns([1.15, 1], gap="medium")
with days_col:
    st.html(T.section(f"{len(daily)}-day forecast" if len(daily) else "Daily forecast", "calendar_month"))
    if daily.empty:
        st.info("The provider returned no daily forecast for this place.", icon=":material/help:")
    else:
        st.html(V.daily_list_html(daily, units, today_key))
        with st.expander("Daily details"):
            st.dataframe(V.daily_table(daily, units), hide_index=True, width="stretch")
with now_col:
    st.html(T.section("Right now", "sensors"))
    st.html(V.detail_cards_html(cur, today, units))

# --- Daily briefing and suggestions ----------------------------------------------------

st.html(T.section("Today's briefing", "event_note"))
brief = I.briefing(forecast, now, prefs.alerts, units)
if brief is None:
    st.info("Today's data isn't in this forecast, so there's no briefing.", icon=":material/help:")
else:
    b_col, s_col = st.columns([1, 1], gap="medium")
    with b_col:
        st.html(V.briefing_html(brief, units))
    with s_col:
        if brief.recommendations:
            st.markdown("##### Suggestions")
            st.html(V.recommendations_html(brief.recommendations))
            st.caption("Suggestions come from simple, transparent rules applied to the forecast above. Each one "
                       "says why it was made. They are not guarantees.")
        else:
            st.caption("No suggestions for today.")

# --- Cloudzy Core preview (experimental) ------------------------------------------------

st.html(T.section("Cloudzy Core · experimental", "science", "Separate from the forecast above"))
d_km = distance_km(place.latitude, place.longitude, DELHI.latitude, DELHI.longitude)
with st.container(border=True):
    if d_km > CLOUDZY_RADIUS_KM:
        st.info(f"Cloudzy Core was trained and tested only for **{DELHI.name}**. It isn't shown for "
                f"{place.label} ({d_km:,.0f} km away), because it has never been validated there. "
                "Search for New Delhi to see it.", icon=":material/location_on:")
    else:
        with st.spinner("Asking Cloudzy Core…"):
            block = app_data.cloudzy(place)
        if not block["available"]:
            st.warning(f"No Cloudzy prediction right now. {block['reason']} Cloudzy only predicts when all "
                       "of its inputs (the last 25 hours of live data) are complete.", icon=":material/science:")
            retry_button("retry-cloudzy")
        else:
            tu = units.temperature
            preds = [{**p, "temperature_2m_c": U.temperature(p["temperature_2m_c"], tu),
                      "typical_error_c": U.temperature_delta(p["typical_error_c"], tu)}
                     for p in block["cloudzy"]["predictions"]]
            prov = [{**p, "temperature_2m_c": U.temperature(p["temperature_2m_c"], tu)}
                    for p in block["provider"]["forecasts"]]
            ifs_by_h = {p["horizon_h"]: p["temperature_2m_c"] for p in prov}
            first_p = preds[0]
            m1, m2, m3 = st.columns(3)
            m1.metric(f"Cloudzy Core, +{first_p['horizon_h']} h", f"{first_p['temperature_2m_c']:.1f} {tu}",
                      help="Cloudzy's own model, predicting the reanalysis temperature for its Delhi grid cell.")
            ifs = ifs_by_h.get(first_p["horizon_h"])
            m2.metric(f"ECMWF IFS, +{first_p['horizon_h']} h", "Not available" if ifs is None else f"{ifs:.1f} {tu}",
                      help="The forecast model Cloudzy takes its inputs from, for the same hour.")
            m3.metric("Average error (2025 test)", f"±{first_p['typical_error_c']:.2f} {tu}",
                      help="Mean absolute error on a 2025 replay. Not a promise about today, not a confidence "
                           "interval.")
            st.caption(f"Prediction point: {block['prediction_point']}, {block['distance_km_from_place']} km from "
                       f"{place.name}. Issued {pd.Timestamp(block['issue_time_local']):%H:%M} local time from "
                       "live ECMWF IFS inputs. For decisions, use the forecast above.")
            with st.expander("All horizons, chart and how to read this"):
                st.plotly_chart(ui.cloudzy_chart(preds, prov, colors, tu), width="stretch", theme="streamlit",
                                config=ui.CHART_CONFIG)
                table = ui.cloudzy_table(preds, prov, tu)
                st.dataframe(table, hide_index=True, width="stretch", column_config={
                    c: st.column_config.NumberColumn(format="%.2f" if "error" in c else "%.1f")
                    for c in table.columns if c.endswith(tu)})
                st.markdown(
                    "- **What it predicts:** the ERA5-Land *reanalysis* temperature for one ~9 km grid cell, 1 to "
                    "24 hours ahead, starting from live ECMWF IFS model inputs. It isn't a thermometer reading.\n"
                    "- **Average error** is what Cloudzy achieved on a **replay of 2025** (historical test "
                    "data). It is not a promise about today's accuracy, and not a confidence interval. Live "
                    "inputs arrive later than the replay assumes, so real errors are likely larger.\n"
                    "- **The ECMWF IFS column** is the model Cloudzy takes its inputs from. On the 2025 replay, "
                    "Cloudzy was closer to the reanalysis than those IFS values up to 12 hours ahead, which "
                    "shows a bias correction toward the reanalysis. That **doesn't** make Cloudzy a better "
                    "weather forecast than the provider.\n"
                    "- **For decisions, use the forecast above.** Details are in Cloudzy Lab and the model card.")
            l1, l2 = st.columns(2)
            with l1:
                nav.page_link("app_pages/predictions.py", "Live tracking of these predictions",
                              ":material/query_stats:")
            with l2:
                nav.page_link("app_pages/lab.py", "How Cloudzy Core was tested", ":material/science:")

# --- Data notes and footer ------------------------------------------------------------

if forecast.warnings:
    with st.expander(f"Data notes ({len(forecast.warnings)})"):
        st.caption("These values were missing, unavailable or implausible in the provider response. They are "
                   "shown as “—”, never filled in.")
        for w in forecast.warnings:
            st.markdown(f"- {w}")

st.divider()
st.caption(f"{ATTRIBUTION}. {AQ.ATTRIBUTION} Reanalysis: Copernicus Climate Change Service (ERA5, ERA5-Land). Weather icons follow "
           "the WMO weather codes in the forecast. Cloudzy is an experimental student project, not an operational "
           "weather service, and its alerts are not official warnings.")
