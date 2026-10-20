"""Weather Explorer: a 3D globe and a global map of NOAA GFS forecast fields with a forecast timeline.

The globe (src.globe_view, Milestone 1: temperature) and the map (src.explorer_view, all layers) draw
real 1° GFS grids prepared by src.explorer_data. If the globe can't run (no WebGL, a lost graphics
context or a failed texture) the page switches to the map by itself. Clicking either selects a point; this page then shows a separate *point forecast* for it (Open-Meteo), and a
Cloudzy Core point prediction only where that model is valid (its Delhi grid cell).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import app_data, explorer_data, nav
from src import geolocation as G
from src import preferences as P
from src import theme as T
from src import units as U
from src.briefing import CLOUDZY_RADIUS_KM, distance_km
from src.config import DELHI
from src.data_collection import DataCollectionError
from src.explorer_view import explorer_map
from src.globe_view import globe
from src.provider import Place, ProviderError

PICK_KEY = "_explorer_pick"
VIEW_KEY = "_explorer_view"
FALLBACK_KEY = "_explorer_globe_fallback"
VIEWS = {"globe": ":material/public: Globe", "map": ":material/map: Map"}

st.html(T.page_header("Weather Explorer", "Forecast · global model view", "public"))
st.caption("Global forecast from the NOAA GFS weather model on a 1° grid (about 110 km), 3-hourly to +72 hours. "
           "A model forecast, not radar or observations. Click the globe or map to select a point.")

prefs = P.load()
status = explorer_data.ensure_current()
manifest = status["manifest"]
if manifest is None and status["error"]:
    st.error(f"Couldn't prepare the global forecast data: {status['error']}. The map will retry in a few minutes; "
             "the rest of Cloudzy works normally.", icon=":material/cloud_off:")
elif manifest is None:
    st.info("Preparing global forecast data for the first time. This takes about a minute; the map fills in as "
            "steps become ready.", icon=":material/hourglass_top:")
elif manifest.get("status") != "complete":
    ready = sum(f["ready"] for f in manifest["frames"])
    st.caption(f"Preparing the {manifest['run_utc']} run: {ready} of {len(manifest['frames'])} forecast steps ready.")

city = app_data.current_place() or prefs.home_place()
units = prefs.unit_override() or (U.default_for_country(city.country_code) if city else U.METRIC)
pick = st.session_state.get(PICK_KEY)

fallback = st.session_state.get(FALLBACK_KEY)
view = st.segmented_control("View", list(VIEWS), format_func=lambda v: VIEWS[v], key=VIEW_KEY, default="globe",
                            required=True, label_visibility="collapsed",
                            help="Globe: 3D Earth with the temperature layer. Map: flat map with every layer.")
if view == "globe" and fallback:
    st.info(f"The 3D globe isn't available here: {fallback.rstrip('.')}. The 2D map is shown instead.",
            icon=":material/map:")
    view = "map"

data = {
    "manifest": f"{explorer_data.STATIC_URL}/manifest.json",
    "units": {"temperature": units.temperature, "wind": units.wind, "precipitation": units.precipitation},
    "city": {"lat": city.latitude, "lon": city.longitude, "label": city.label} if city else None,
}
if view == "globe":
    result = globe(data)
    fb = getattr(result, "fallback", None)
    if isinstance(fb, dict) and not fallback:
        st.session_state[FALLBACK_KEY] = str(fb.get("reason") or "it couldn't start")[:200]
        st.rerun()
else:
    result = explorer_map({**data, "layer": "wind"})
picked = getattr(result, "picked", None)
if isinstance(picked, dict) and {"lat", "lon"} <= set(picked):
    try:
        lat, lon = G.validate_coordinates(float(picked["lat"]), float(picked["lon"]))
        pick = st.session_state[PICK_KEY] = (round(lat, 3), round(lon, 3))
    except (TypeError, ValueError):
        pass

# --- Selected point: a separate point forecast, clearly labelled ----------------------------------------
st.html(T.section("Selected point", "location_searching",
                  "Point forecast from Open-Meteo, a different source from the map layers"))
if not pick:
    st.caption("Click anywhere on the globe or map to see a point forecast for that spot."
               + (f" The yellow marker is {city.label}, the place on your Weather page." if city else ""))
else:
    lat, lon = pick
    point = Place("Selected point", lat, lon, "UTC")
    rev = G.reverse_geocode(lat, lon)
    name = rev["name"] if rev else None
    label = ", ".join(x for x in (name, rev.get("country") if rev else None) if x) or "No named place nearby"
    try:
        fc = app_data.forecast(point)
    except (ProviderError, DataCollectionError) as exc:
        fc = None
        st.error(f"The point forecast isn't available right now: {exc}", icon=":material/cloud_off:")
    c1, c2 = st.columns([1.2, 2], gap="medium")
    with c1:
        st.markdown(f"**{label}**  \n{abs(lat):.2f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}")
        if fc is not None:
            tz = fc.hourly.index.tz
            st.caption(f"Local time {pd.Timestamp.now(tz='UTC').tz_convert(tz):%a %H:%M} ({tz})")
        if name and rev:
            if st.button(f"Open {name} on the Weather page", icon=":material/arrow_forward:",
                         help="Shows the nearest named place's full forecast. Your home city and saved places "
                              "aren't changed."):
                st.session_state["_open_place"] = Place(name, lat, lon, "UTC", rev.get("country"), rev.get("admin1"),
                                                        None, rev.get("country_code"))
                nav.switch_page("app_pages/weather.py")
        if rev:
            st.caption(G.OSM_ATTRIBUTION)
    with c2:
        if fc is not None:
            cur = fc.current
            t = U.temperature(cur.get("temperature_2m"), units.temperature)
            feels = U.temperature(cur.get("apparent_temperature"), units.temperature)
            wind = U.wind(cur.get("wind_speed_10m"), units.wind)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Temperature", "Not available" if t is None else f"{t:.0f}{units.temperature}",
                      help=None if feels is None else f"Feels like {feels:.0f}{units.temperature}")
            m2.metric("Wind", "Not available" if wind is None else f"{wind:.0f} {units.wind}")
            cc = cur.get("cloud_cover")
            m3.metric("Cloud cover", "Not available" if cc is None or pd.isna(cc) else f"{cc:.0f}%")
            m4.metric("Condition", cur.get("description") or "Not available")
            st.caption(f"{fc.source_label}, current conditions for this coordinate (model estimates). This is a "
                       "point forecast, not the GFS grid value drawn on the map, so the two can differ.")

    # Cloudzy Core only where it was trained and tested.
    d_km = distance_km(lat, lon, DELHI.latitude, DELHI.longitude)
    with st.container(border=True):
        if d_km > CLOUDZY_RADIUS_KM:
            st.caption(f":material/science: Cloudzy Core isn't shown here: it's a single-point model for the New Delhi "
                       f"grid cell ({d_km:,.0f} km away), not a global forecast.")
        else:
            block = app_data.cloudzy(point)
            if block.get("available"):
                p1 = block["cloudzy"]["predictions"][0]
                v = U.temperature(p1["temperature_2m_c"], units.temperature)
                st.markdown(f":material/science: **Cloudzy Core point prediction:** {v:.1f} {units.temperature} at "
                            f"{pd.Timestamp(p1['valid_time_local']):%a %H:%M} local time (+{p1['horizon_h']} h), for "
                            f"its Delhi grid cell. Experimental; separate from the GFS map and the point forecast.")
            else:
                st.caption(f":material/science: No Cloudzy Core prediction right now. {block.get('reason', '')}")

# --- Layer guide ---------------------------------------------------------------------------------
with st.expander("About the layers and data"):
    run = manifest["run_utc"] if manifest else "not loaded yet"
    st.markdown(
        f"- **Source:** {explorer_data.SOURCE}, run {run}. Public-domain NOAA data, refreshed when a newer "
        "run is published (4 runs a day, each available a few hours after its start time).\n"
        "- **Kind:** every layer is a **numerical model forecast**. The +0 h step is the model's analysis. "
        "Nothing here is radar or station observations.\n"
        "- **Resolution:** 1° (about 110 km). Small features such as single thunderstorms and sea breezes are "
        "not resolved; values between grid points are interpolated for display.\n"
        "- **Time:** 3-hourly steps to +72 h. Each step is that forecast hour's own instantaneous field, never a "
        "time average, and never a reused frame.")
    st.dataframe(pd.DataFrame([
        ("Wind", "10 m wind (u, v components)", "particles follow the actual vectors; colour = speed"),
        ("Temperature", "2 m air temperature", "°C or °F, as in your settings"),
        ("Precipitation", "Surface precipitation rate + GFS rain/snow/freezing flags", "rate in mm/h or in/h"),
        ("Cloud cover", "Total cloud cover (whole atmosphere)", "low, mid and high cloud are not split"),
        ("Pressure", "Mean sea-level pressure", "isobars every 4 hPa, traced on the model grid"),
        ("Wind gusts", "Surface wind gusts", "same units as wind"),
        ("Humidity", "2 m relative humidity", "%"),
        ("CAPE", "Surface-based convective available potential energy", "J/kg; instability, not a storm forecast"),
    ], columns=["Layer", "GFS field", "Notes"]), hide_index=True, width="stretch")
    st.caption("Globe: three.js r186 (MIT). Earth imagery: NASA Blue Marble shaded relief and bathymetry via "
               "NASA GIBS (public domain; a cloud-free composite, not current conditions). In this first version the "
               "globe shows the temperature layer; the map has every layer. "
               "Map: Leaflet 1.9.4 (BSD-2-Clause). Basemap: Natural Earth coastlines, borders and cities (public "
               "domain). Data decoding: ECMWF ecCodes (Apache-2.0). Point forecasts: Open-Meteo (CC BY 4.0). "
               "Place names: © OpenStreetMap contributors (ODbL).")
