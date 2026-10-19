"""Cloudzy weather page. Opened through streamlit_app.py.

A phone-style weather app built on real Open-Meteo data:
favourites → search → current conditions (hero) → "Cloudzy says" summary →
unofficial alerts → rain banner → next 24 hours (cards and graph) → 7-day
forecast → weather details → daily briefing and suggestions → Cloudzy Core
(experimental, Delhi only) → attribution.

- Values are fetched in metric units and converted for display (src.units).
  Units default to the selected place's region, and the viewer can override
  them. Overrides, favourites and alert thresholds are saved locally
  (src.preferences).
- Every network call is cached for 10 minutes per place, to stay well inside
  Open-Meteo's free limits.
- The location is kept in the URL (?q=…&i=…).
- On any failure the page shows the reason and a retry button, never stale or
  invented numbers.
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from src import geolocation as G
from src import insights as I
from src import preferences as P
from src import ui
from src import units as U
from src import weather_view as V
from src.briefing import CLOUDZY_RADIUS_KM, cloudzy_block, distance_km
from src.config import DELHI
from src.data_collection import DataCollectionError
from src.provider import ATTRIBUTION, ProviderError, fetch_forecast, rain_outlook, search_locations, upcoming

CACHE_SECONDS = 600
DEFAULT_QUERY = "New Delhi"
HOURS_AHEAD = 24
SAVE_SNAPSHOTS = os.environ.get("CLOUDZY_SNAPSHOTS", "1") == "1"
UNIT_KEYS = {"temperature": "unit_temp", "wind": "unit_wind", "precipitation": "unit_rain"}


# --- Cached data access -----------------------------------------------------------

@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def cached_search(query: str):
    return search_locations(query, count=8)


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def cached_forecast(place):
    return fetch_forecast(place)


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def cached_cloudzy(place):
    return cloudzy_block(place, snapshot=SAVE_SNAPSHOTS)


def retry_button(key: str) -> None:
    if st.button("Try again", key=key, icon="🔄"):
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


# --- Header and location (device or manual) --------------------------------------------

def request_location() -> None:
    G.start(G.state(st.session_state))


def cancel_location() -> None:
    G.cancel(G.state(st.session_state))


def search_instead() -> None:
    G.use_manual(G.state(st.session_state))


def toggle_auto_locate() -> None:
    prefs_now = P.load()
    prefs_now.auto_locate = bool(st.session_state.get("auto_locate"))
    P.save(prefs_now)


st.html(V.CSS)
# st.html's browser-side sanitiser drops <svg> and data-URI images; markdown keeps the mascot.
st.markdown(V.brand_html(), unsafe_allow_html=True)
prefs = P.load()
params = st.query_params
geo = G.state(st.session_state)

# Opt-in: look up the location once when a session opens, unless the link names a city.
if prefs.auto_locate and not geo["auto_tried"] and "q" not in params:
    geo["auto_tried"] = True
    G.start(geo)

# The invisible browser script runs one lookup per request number. Its answer is applied only if it
# matches the request still pending, so late answers can't override a newer choice.
G.handle(geo, G.browser_location(geo["pending"] or 0))

show_panel = geo["pending"] is not None or geo["mode"] != "device" or geo["error"] is not None
with st.container(border=True) if show_panel else st.empty():
    if geo["pending"] is not None:
        msg_col, cancel_col = st.columns([4, 1], vertical_alignment="center")
        msg_col.info("**Finding your location…** If your browser asks, choose *Allow*. Cloudzy looks your "
                     "position up once and doesn't track you.", icon="📍")
        cancel_col.button("Cancel", on_click=cancel_location, width="stretch")
    elif geo["mode"] != "device":
        btn_col, text_col = st.columns([1.3, 3], vertical_alignment="center")
        btn_col.button("Use my location", icon="📍", type="primary", on_click=request_location, width="stretch",
                       help="Your browser will ask for permission. Cloudzy uses the position once to load the weather.")
        text_col.caption("Uses your browser's location, with your permission. The position is used once for "
                         "the weather and a place name, and isn't saved or tracked. Or search for a city below.")
        st.toggle("Detect my location when Cloudzy opens", key="auto_locate", value=prefs.auto_locate,
                  on_change=toggle_auto_locate, help="Saved on this computer as a yes/no choice. Your location "
                  "itself is never saved.")
    err = geo["error"]
    if err is not None and geo["pending"] is None:
        st.warning(f"**{err.title}** {err.detail}", icon="📍")
        if err.retry:
            st.button("Try again", icon="🔄", key="geo-retry", on_click=request_location)

detected_slot = None
if geo["mode"] == "device" and geo["fix"] is not None:
    fix = geo["fix"]
    with st.spinner("Looking up the place name…"):
        rev = G.reverse_geocode(fix.latitude, fix.longitude)
    place = G.device_place(fix, rev)
    for k in ("q", "i"):  # the URL must not name a different place, and never carries coordinates
        if k in params:
            del params[k]
    loc_col, unit_col = st.columns([5, 1.7], vertical_alignment="bottom")
    with loc_col:
        st.markdown(f"**📍 {place.label}** · your device location")
        detected_slot = st.empty()
        a, b = st.columns(2)
        a.button("Refresh location", icon="🔄", on_click=request_location, width="stretch",
                 disabled=geo["pending"] is not None)
        b.button("Search for a city instead", icon="🔎", on_click=search_instead, width="stretch")
else:
    favs = prefs.favourite_places()
    if favs:
        st.pills("Favourite places", [f.label for f in favs], key="fav_pick", on_change=pick_favourite,
                 help="Saved on this computer only.")
    if "search" not in st.session_state:
        # Streamlit drops a widget's value while it's hidden (device mode), so restore the last manual city.
        st.session_state["search"] = params.get("q") or st.session_state.get("_last_query", DEFAULT_QUERY)
    query = st.text_input("Search for a city or town", key="search", placeholder="e.g. Mumbai, Reykjavik, Lagos",
                          max_chars=80, help="Press Enter to search. Results use Open-Meteo's geocoding service.").strip()
    if len(query) < 2:
        st.info("Type at least two letters to search for a place.", icon="🔎")
        st.stop()

    try:
        with st.spinner("Looking for that place…"):
            places = cached_search(query)
    except (ProviderError, DataCollectionError) as exc:
        st.error(f"Location search is unavailable right now. {exc}", icon="⚠️")
        retry_button("retry-search")
        st.stop()

    if not places:
        st.warning(f"No places match “{query}”. Check the spelling, or try a nearby larger town.", icon="🗺️")
        st.stop()

    target = st.session_state.pop("_fav_target", None)
    if target:  # a favourite was picked: choose the result with its exact coordinates
        start_index = next((i for i, p in enumerate(places)
                            if abs(p.latitude - target[0]) < 0.01 and abs(p.longitude - target[1]) < 0.01), 0)
    else:
        try:
            start_index = int(params.get("i", st.session_state.get("_last_index", 0))) \
                if params.get("q", st.session_state.get("_last_query")) == query else 0
        except ValueError:
            start_index = 0
    pick_col, fav_col, unit_col = st.columns([5, 1.5, 1.7], vertical_alignment="bottom")
    with pick_col:
        place = st.selectbox("Choose the right match", places, index=min(max(start_index, 0), len(places) - 1),
                             format_func=place_label, help="Several places can share a name. Pick the one you mean.")
    params["q"], params["i"] = query, str(places.index(place))  # never device coordinates in the URL
    st.session_state["_last_query"], st.session_state["_last_index"] = query, places.index(place)

    with fav_col:
        saved = prefs.is_favourite(place)
        if st.button("★ Saved" if saved else "☆ Save", width="stretch",
                     help="Remove from favourites" if saved else "Save as a favourite place"):
            try:
                prefs.toggle_favourite(place)
                P.save(prefs)
            except ValueError as exc:
                st.toast(str(exc))
            st.rerun()

# Units: the saved override if any, else the place's regional default.
regional = U.default_for_country(place.country_code)
override = prefs.unit_override()
effective = override or regional
units_for = (place.country_code, override)
if st.session_state.get("_units_for") != units_for:
    for name, key in UNIT_KEYS.items():
        st.session_state[key] = getattr(effective, name)
    st.session_state["_units_for"] = units_for
with unit_col:
    with st.popover("Units", icon="⚙️", width="stretch"):
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

# --- Provider forecast ------------------------------------------------------------

try:
    with st.spinner(f"Checking the sky over {place.name}…"):
        forecast = cached_forecast(place)
except (ProviderError, DataCollectionError) as exc:
    st.error(f"We couldn't get the forecast for {place.label}. {exc}", icon="⚠️")
    retry_button("retry-forecast")
    st.stop()

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
st.html(V.hero_html(place.label, cur, today, units, local_now.tzname() or str(tz)))
st.caption(f"{forecast.source_label} · {V.updated_label(forecast.retrieved_at_utc, tz)}, refreshed at most every "
           "10 minutes · current values are model estimates for this location, not a station reading")

# --- Cloudzy says (summary) and alerts --------------------------------------------------

st.html(V.summary_html(I.summary(forecast, now, units)))

found = I.alerts(forecast, now, prefs.alerts, units)
if found:
    st.html(V.alerts_html(found))
else:
    st.caption("No Cloudzy alerts for the next 24 hours with your thresholds.")
with st.expander("Alert thresholds"):
    st.caption("Cloudzy's alerts are simple threshold checks on the forecast. They are not official warnings; "
               "follow your national weather service for those.")
    with st.form("thresholds"):
        th = prefs.alerts
        c1, c2 = st.columns(2)
        rain_pct = c1.number_input("Chance of rain at least (%)", 10, 100, int(th.rain_probability_pct), step=5)
        uv_val = c2.number_input("UV index at least", 3, 15, int(th.uv_index))
        heat = c1.number_input(f"Feels-like at least ({units.temperature})",
                               value=round(U.temperature(th.heat_feels_like_c, units.temperature)))
        cold = c2.number_input(f"Feels-like at most ({units.temperature})",
                               value=round(U.temperature(th.cold_feels_like_c, units.temperature)))
        gust = c1.number_input(f"Gusts at least ({units.wind})", value=round(U.wind(th.wind_gust_kmh, units.wind)))
        change = c2.number_input(f"Day-to-day high change ({units.temperature})",
                                 value=round(U.temperature_delta(th.day_to_day_change_c, units.temperature)))
        if st.form_submit_button("Save thresholds"):
            new = P.AlertThresholds(
                rain_probability_pct=float(rain_pct), uv_index=float(uv_val),
                heat_feels_like_c=U.to_celsius(heat, units.temperature),
                cold_feels_like_c=U.to_celsius(cold, units.temperature),
                wind_gust_kmh=U.to_kmh(gust, units.wind),
                day_to_day_change_c=change / 1.8 if units.temperature == "°F" else float(change))
            bad = [k for k, (lo, hi) in P.AlertThresholds.LIMITS.items() if not lo <= getattr(new, k) <= hi]
            if bad:
                st.error("Some values are outside the allowed range: " + ", ".join(bad))
            else:
                prefs.alerts = new
                P.save(prefs)
                st.rerun()

# --- Rain banner -------------------------------------------------------------------

rain = rain_outlook(forecast, now)
if rain["hours_checked"] == 0:
    st.info("No upcoming hourly data to check for rain.", icon="❔")
elif rain["rain_expected"]:
    first = pd.Timestamp(rain["first_rain_hour_local"])
    total = U.precipitation(rain["total_precipitation_mm"], units.precipitation)
    st.warning(f"**Rain likely from {first:%a %H:%M}**: {rain['rainy_hours']} of the next {rain['hours_checked']} "
               f"hours, up to {rain['max_probability_pct']:.0f}% chance, about "
               f"{total:.{U.precipitation_digits(units.precipitation)}f} {units.precipitation} in total.", icon="🌧️")
else:
    st.success(f"No rain expected in the next {rain['hours_checked']} hours "
               f"(highest chance {ui.fmt(rain['max_probability_pct'], '%')}).", icon="🌂")
st.caption(f"Rain indicator: {rain['rule']}. {rain['label']}")

# --- Next 24 hours -------------------------------------------------------------------

st.markdown("#### Next 24 hours")
hours = upcoming(forecast, now, HOURS_AHEAD)
if hours.empty:
    st.info("The provider returned no upcoming hours for this place.", icon="❔")
else:
    st.html(V.hourly_strip_html(hours, units))
    shown = U.convert_frame(hours, units)
    st.plotly_chart(ui.temperature_chart(shown, colors, units.temperature, "Temperature and feels-like"),
                    width="stretch", theme="streamlit", config=ui.CHART_CONFIG)
    if shown["precipitation_probability"].fillna(0).max() > 0 or shown["precipitation"].fillna(0).max() > 0:
        st.plotly_chart(ui.rain_chart(shown, colors, units.precipitation), width="stretch",
                        theme="streamlit", config=ui.CHART_CONFIG)
    with st.expander("Hour-by-hour table"):
        hourly_tbl = ui.hourly_table(shown, units.temperature, units.wind, units.precipitation)
        st.dataframe(hourly_tbl, hide_index=True, width="stretch", column_config={
            c: st.column_config.NumberColumn(format="%.2f" if c == "Rain in" else "%.1f")
            for c in hourly_tbl.columns if c.startswith(("Temp", "Feels", "Rain mm", "Rain in", "UV"))})

# --- 7-day forecast ----------------------------------------------------------------

st.markdown("#### 7-day forecast")
if daily.empty:
    st.info("The provider returned no daily forecast for this place.", icon="❔")
else:
    st.html(V.daily_list_html(daily, units, today_key))
    with st.expander("Daily details"):
        st.dataframe(V.daily_table(daily, units), hide_index=True, width="stretch")

# --- Details -------------------------------------------------------------------------

st.markdown("#### Right now")
st.html(V.detail_cards_html(cur, today, units))

# --- Daily briefing and suggestions ----------------------------------------------------

st.markdown("#### Today's briefing")
brief = I.briefing(forecast, now, prefs.alerts, units)
if brief is None:
    st.info("Today's data isn't in this forecast, so there's no briefing.", icon="❔")
else:
    st.html(V.briefing_html(brief, units))
    if brief.recommendations:
        st.markdown("##### Suggestions")
        st.html(V.recommendations_html(brief.recommendations))
        st.caption("Suggestions come from simple, transparent rules applied to the forecast above. Each one says "
                   "why it was made. They are not guarantees.")

# --- Cloudzy Core (experimental) ------------------------------------------------------

st.markdown("#### 🧪 Cloudzy Core · experimental")
d_km = distance_km(place.latitude, place.longitude, DELHI.latitude, DELHI.longitude)
with st.container(border=True):
    st.markdown("Cloudzy Core is this project's own machine-learning model. It is **separate from the forecast "
                "above**, which comes from Open-Meteo.")
    if d_km > CLOUDZY_RADIUS_KM:
        st.info(f"Cloudzy Core was trained and tested only for **{DELHI.name}**. It isn't shown for "
                f"{place.label} ({d_km:,.0f} km away), because it has never been validated there. "
                "Search for New Delhi to see it.", icon="📍")
    else:
        with st.spinner("Asking Cloudzy Core…"):
            block = cached_cloudzy(place)
        if not block["available"]:
            st.warning(f"No Cloudzy prediction right now. {block['reason']} Cloudzy only predicts when all "
                       "of its inputs (the last 25 hours of live data) are complete.", icon="🧪")
            retry_button("retry-cloudzy")
        else:
            tu = units.temperature
            preds = [{**p, "temperature_2m_c": U.temperature(p["temperature_2m_c"], tu),
                      "typical_error_c": U.temperature_delta(p["typical_error_c"], tu)}
                     for p in block["cloudzy"]["predictions"]]
            prov = [{**p, "temperature_2m_c": U.temperature(p["temperature_2m_c"], tu)}
                    for p in block["provider"]["forecasts"]]
            st.caption(f"Prediction point: {block['prediction_point']}, {block['distance_km_from_place']} km from "
                       f"{place.name}. Issued {pd.Timestamp(block['issue_time_local']):%H:%M} local time from "
                       "live ECMWF IFS inputs.")
            st.plotly_chart(ui.cloudzy_chart(preds, prov, colors, tu), width="stretch", theme="streamlit",
                            config=ui.CHART_CONFIG)
            table = ui.cloudzy_table(preds, prov, tu)
            st.dataframe(table, hide_index=True, width="stretch", column_config={
                c: st.column_config.NumberColumn(format="%.2f" if "error" in c else "%.1f")
                for c in table.columns if c.endswith(tu)})
            with st.expander("How to read this"):
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

# --- Data notes and footer ------------------------------------------------------------

if forecast.warnings:
    with st.expander(f"Data notes ({len(forecast.warnings)})"):
        st.caption("These values were missing, unavailable or implausible in the provider response. They are "
                   "shown as “—”, never filled in.")
        for w in forecast.warnings:
            st.markdown(f"- {w}")

st.divider()
st.caption(f"{ATTRIBUTION}. Reanalysis: Copernicus Climate Change Service (ERA5, ERA5-Land). Weather icons follow "
           "the WMO weather codes in the forecast. Favourites and settings are stored only on this computer. "
           "Cloudzy is an experimental student project, not an operational weather service.")
