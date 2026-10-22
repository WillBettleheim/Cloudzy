"""Weather Explorer: a 3D globe and a global map of NOAA GFS forecast fields with a forecast timeline.

The globe (src.globe_view) and the map (src.explorer_view) draw the same real 1° GFS grids (plus GFS-Wave
wave height) prepared by src.explorer_data, with the same layers. If the globe can't run (no WebGL, a lost
graphics context or a failed texture) the page switches to the map by itself.

A place can be chosen with the search box (Open-Meteo geocoding, the same as the Weather page) or a saved
place; the globe or map then flies there. Clicking either view also selects a point. This page then shows a
separate *point forecast* for it (Open-Meteo), and a Cloudzy Core point prediction only where that model is
valid (its Delhi grid cell).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import app_data, explorer_data, geo, nav, region_weather
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
FOCUS_KEY = "_explorer_focus"              # where the view should fly: {"kind", "a3", "id", "lat", "lon", "name", "token"}
FOCUS_PLACE_KEY = "_explorer_focus_place"  # the named Place behind the selected point, if any
PLACE_SOURCE_KEY = "_explorer_place_source"  # where that name came from (for attribution)
REGION_KEY = "_explorer_region"            # the selected region: {"kind", "a3", "id", "name", "fhr"}
CHOSEN_KEY = "_explorer_chosen"            # the last choice flown to, so a rerun doesn't fly again
KIND_LABEL = {"country": "Country", "admin1": "State/province", "city": "City", "point": "Coordinates",
              "continent": "Continent"}


def fly_to(target: dict, choice: tuple) -> None:
    """Point the globe/map at a search result, saved place or browsed region (once per distinct choice)."""
    if st.session_state.get(CHOSEN_KEY) == choice:
        return
    st.session_state[CHOSEN_KEY] = choice
    token = (st.session_state.get(FOCUS_KEY) or {}).get("token", 0) + 1
    kind = target["kind"]
    st.session_state[FOCUS_KEY] = {k: target.get(k) for k in ("kind", "a3", "id", "lat", "lon", "name")} | \
        {"label": target["label"], "token": token}
    if kind in ("country", "admin1", "continent"):
        st.session_state[REGION_KEY] = geo.clean_region(target)
        st.session_state[PICK_KEY] = st.session_state[FOCUS_PLACE_KEY] = None    # a region, not an old point
        return
    lat, lon = float(target["lat"]), float(target["lon"])
    st.session_state[PICK_KEY] = (round(lat, 3), round(lon, 3))
    st.session_state[FOCUS_PLACE_KEY] = target.get("place")
    st.session_state[PLACE_SOURCE_KEY] = target.get("source")
    st.session_state[REGION_KEY] = geo.region_at(lat, lon)


def search_targets(query: str) -> tuple[list[dict], str | None]:
    """Coordinates, countries, states/provinces (Natural Earth) and cities (Open-Meteo geocoding) for a query."""
    coords = geo.parse_coordinates(query)
    if coords:
        lat, lon = coords
        label = f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"
        return [{"kind": "point", "lat": lat, "lon": lon, "name": label, "label": f"Coordinates · {label}"}], None
    out = []
    for e in geo.search(query):
        where = e["cont"] if e["kind"] == "country" else f"{e.get('type') or 'division'}, {e['country']}"
        out.append({**e, "label": f"{KIND_LABEL[e['kind']]} · {e['name']} ({where})"})
    error = None
    try:
        for p in app_data.search(query):
            out.append({"kind": "city", "lat": p.latitude, "lon": p.longitude, "name": p.name, "place": p,
                        "source": "Open-Meteo geocoding (GeoNames data, CC BY 4.0)",
                        "label": f"City · {p.label}" + (f" · pop. {p.population:,}" if p.population else "")})
    except (ProviderError, DataCollectionError) as exc:
        error = str(exc)
    return out, error


st.html(T.page_header("Weather Explorer", "Forecast · global model view", "public"))
st.caption("NOAA GFS model forecast on a 1° grid (about 110 km), 3-hourly to +72 hours: not radar or observations. "
           "Search for a place or region, browse by region, or click the globe.")

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

# --- Search, region browser, saved places and the view switch: one compact row, so the globe keeps the space ---
fallback = st.session_state.get(FALLBACK_KEY)
geo_ok = geo.data_files_present()
with T.two_up("explorer_controls", first_full=True):
    q_col, b_col, v_col = st.columns([3, 1, 1.9], vertical_alignment="bottom")
query = q_col.text_input("Fly to a place", key="explorer_q", placeholder="Country, state, city or lat, lon",
                         max_chars=80, icon=":material/travel_explore:", label_visibility="collapsed",
                         help="Press Enter to search. Countries and states/provinces come from Natural Earth; cities "
                              "from Open-Meteo's geocoding, as on the Weather page; or type coordinates such as "
                              "28.61, 77.21. Nothing you search for is saved.").strip()
m_col = st.container()                     # matches or saved places, on their own line when there are any
with m_col:
    if len(query) >= 2:
        targets, search_error = search_targets(query) if geo_ok else ([], "geographic data is missing")
        if search_error:
            st.caption(f":material/error: City search is unavailable right now ({search_error}). "
                       "Countries and states can still be found.")
        if not targets and not search_error:
            st.caption(f"Nothing matches “{query}”. Check the spelling, or try a nearby larger town.")
        elif len(targets) == 1:                      # exactly one match: go there
            st.caption(f"{targets[0]['label']}")
            fly_to(targets[0], ("search", query, 0))
        elif targets:                                # several: the person chooses; nothing is picked for them
            i = st.selectbox("Matching places", range(len(targets)), index=None, key=f"explorer_match_{query}",
                             format_func=lambda k: targets[k]["label"], label_visibility="collapsed",
                             placeholder=f"{len(targets)} matches: choose one",
                             help="Several places and regions share this name. Pick the one you mean.")
            if i is not None:
                fly_to(targets[i], ("search", query, i))
    elif favs := prefs.favourite_places():
        fav_labels = [f.label for f in favs]
        fav = st.pills("Saved places", fav_labels, key="explorer_fav", label_visibility="collapsed",
                       help="Fly to one of your saved places (manage them on the Places page).")
        if fav in fav_labels:
            chosen = favs[fav_labels.index(fav)]
            fly_to({"kind": "city", "lat": chosen.latitude, "lon": chosen.longitude, "name": chosen.name,
                    "label": chosen.label, "place": chosen, "source": "your saved places"},
                   ("saved", chosen.latitude, chosen.longitude))
with b_col.popover("Browse", icon=":material/account_tree:", width="stretch",
                   help="Choose a continent, country and state or province from lists."):
    if not geo_ok:
        st.caption("Geographic data is missing (static/vendor/geo).")
    else:
        conts = [c["name"] for c in geo.countries()["continents"]]
        cont = st.selectbox("Continent", conts, index=None, key="explorer_b_cont", placeholder="Choose a continent")
        if cont:
            cs = sorted((c for c in geo.countries()["countries"] if c["cont"] == cont), key=lambda c: c["name"])
            ci = st.selectbox("Country or territory", range(len(cs)), index=None, key=f"explorer_b_ctry_{cont}",
                              format_func=lambda k: cs[k]["name"], placeholder="Choose a country")
            target = {"kind": "continent", "name": cont, "label": cont}
            if ci is not None:
                c = cs[ci]
                target = {"kind": "country", "a3": c["a3"], "id": c["a3"], "name": c["name"], "label": c["name"],
                          "lat": c["lat"], "lon": c["lon"]}
                us = sorted((geo.admin1(c["a3"]) or {}).get("units", []), key=lambda u: u["name"])
                if us:
                    ui = st.selectbox("State, province or territory", range(len(us)), index=None,
                                      key=f"explorer_b_adm_{c['a3']}", format_func=lambda k: us[k]["name"],
                                      placeholder=f"{len(us)} first-level divisions")
                    if ui is not None:
                        u = us[ui]
                        target = {"kind": "admin1", "a3": c["a3"], "id": u["id"], "name": u["name"],
                                  "label": u["name"], "lat": u["lat"], "lon": u["lon"]}
                else:
                    st.caption("Natural Earth has no first-level divisions for this country.")
            fly_to(target, ("browse", target["kind"], target.get("id") or target["name"]))
view = v_col.segmented_control("View", list(VIEWS), format_func=lambda v: VIEWS[v], key=VIEW_KEY, default="globe",
                               required=True, label_visibility="collapsed",
                               help="Globe: 3D Earth with borders, place names and real day and night. Map: flat map. "
                                    "Both have the same layers, timeline and point selection.")
if view == "globe" and fallback:
    st.info(f"The 3D globe isn't available here: {fallback.rstrip('.')}. The 2D map is shown instead.",
            icon=":material/map:")
    view = "map"

data = {
    "manifest": f"{explorer_data.STATIC_URL}/manifest.json",
    "units": {"temperature": units.temperature, "wind": units.wind, "precipitation": units.precipitation},
    "city": {"lat": city.latitude, "lon": city.longitude, "label": city.label} if city else None,
    "focus": st.session_state.get(FOCUS_KEY),
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
        st.session_state[PICK_KEY] = (round(lat, 3), round(lon, 3))
        name = picked.get("place")
        st.session_state[FOCUS_PLACE_KEY] = Place(str(name)[:80], round(lat, 3), round(lon, 3), "UTC") \
            if isinstance(name, str) and name else None
        st.session_state[PLACE_SOURCE_KEY] = "Natural Earth populated places (public domain)" if name else None
        # The globe says which region was under the click (at its zoom level); the 2D map doesn't, so look it up.
        st.session_state[REGION_KEY] = geo.clean_region(picked.get("region")) if "region" in picked \
            else (geo.region_at(lat, lon) if geo_ok else None)
    except (TypeError, ValueError):
        pass
region_event = getattr(result, "region", None)       # breadcrumb, "up" and reset on the globe
if isinstance(region_event, dict):
    st.session_state[REGION_KEY] = geo.clean_region(region_event.get("region"))
pick = st.session_state.get(PICK_KEY)
region = st.session_state.get(REGION_KEY)


# --- Selected region: Natural Earth context and a genuine regional aggregate of the GFS grid ------------------
def show_region(r: dict) -> None:
    trail = " › ".join(geo.breadcrumb(r["kind"], r.get("a3"), r.get("id")) if r["kind"] != "continent"
                       else ["Earth", r["name"]])
    st.html(T.section("Selected region", "travel_explore", trail))
    if r["kind"] == "continent":
        members = [c for c in geo.countries()["countries"] if c["cont"] == r["name"]]
        st.markdown(f"**{r['name']}** · continent · {len(members)} countries and territories in Natural Earth")
        st.caption("Regional weather values are given for countries and states/provinces. Choose one on the globe, "
                   "with Browse, or by search.")
        return
    c = geo.country(r["a3"]) or {}
    if r["kind"] == "country":
        st.markdown(f"**{r['name']}** · country or territory · {c.get('sub', '')}, {c.get('cont', '')}")
    else:
        u = geo.division(r["a3"], r["id"]) or {}
        st.markdown(f"**{r['name']}** · {u.get('type') or 'first-level division'} of {c.get('name', '')}")
    s = region_weather.summary(r["kind"], r["a3"], r.get("id") if r["kind"] == "admin1" else None, r.get("fhr"))
    if "error" in s:
        st.caption(s["error"])
    elif s["cells"] < s["min_cells"]:
        feature = c if r["kind"] == "country" else (geo.division(r["a3"], r["id"]) or c)
        cell = region_weather.cell_area_km2(feature.get("lat", 0))
        st.info(f"{r['name']} is smaller than the 1° forecast grid: {s['cells']} grid cell"
                f"{'' if s['cells'] == 1 else 's'} inside (each about {cell:,.0f} km² here), so there's no regional "
                "value. A single grid value wouldn't describe the whole region.", icon=":material/grid_off:")
        if feature.get("lat") is not None and st.button(f"Point forecast for the centre of {r['name']}",
                                                         icon=":material/location_searching:"):
            st.session_state[PICK_KEY] = (round(feature["lat"], 3), round(feature["lon"], 3))
            st.session_state[FOCUS_PLACE_KEY] = None
            st.rerun()
    else:
        valid = pd.Timestamp(s["valid_utc"])
        t, w, pr, cl = s.get("temperature"), s.get("wind"), s.get("precipitation"), s.get("clouds")
        with T.two_up("explorer_point_1"):
            m1, m2, m3, m4 = st.columns(4)
        if t:
            lo, hi = U.temperature(t["min"], units.temperature), U.temperature(t["max"], units.temperature)
            m1.metric("Average temperature", f"{U.temperature(t['mean'], units.temperature):.0f}{units.temperature}",
                      help=f"Grid cells range from {lo:.0f} to {hi:.0f}{units.temperature}")
        else:
            m1.metric("Average temperature", "Not available")
        if pr and pr["cover"] is not None:
            m2.metric("Area with precipitation", f"{pr['cover'] * 100:.0f}%",
                      help="Share of the region's area with at least 0.1 mm/h at this step"
                           + (f"; heaviest grid value {U.precipitation(pr['max'], units.precipitation)}"
                              f"{'in' if units.precipitation == 'in' else 'mm'}/h" if pr["max"] is not None else ""))
        else:
            m2.metric("Area with precipitation", "Not available")
        if w:
            m3.metric("Average wind", f"{U.wind(w['mean'] * 3.6, units.wind):.0f} {units.wind}",
                      help=f"Strongest grid value {U.wind(w['max'] * 3.6, units.wind):.0f} {units.wind}")
        else:
            m3.metric("Average wind", "Not available")
        m4.metric("Average cloud cover", f"{cl['mean']:.0f}%" if cl else "Not available")
        st.caption(f"NOAA GFS model forecast valid {valid:%a %d %b %H:%M} UTC (+{s['fhr']} h from the "
                   f"{s['run_utc'].replace('T', ' ')} run): area-weighted average over the {s['cells']} grid cells "
                   f"(1°, about 110 km) whose centres lie inside {r['name']}. A regional summary, not a city "
                   "forecast; conditions vary within the region. The globe's readout shows the same kind of "
                   "average for the layer and step on screen.")
    big = region_weather.places_in(r["kind"], r["a3"], r.get("id") if r["kind"] == "admin1" else None)
    if big:
        labels = [p["name"] for p in big]
        chosen = st.pills(f"Largest places in {r['name']} (Natural Earth): fly there for a point forecast", labels,
                          key=f"explorer_places_{r['kind']}_{r.get('id')}")
        if chosen in labels:
            p = big[labels.index(chosen)]
            fly_to({"kind": "city", "lat": p["lat"], "lon": p["lon"], "name": p["name"], "label": p["name"],
                    "place": Place(p["name"], p["lat"], p["lon"], "UTC"),
                    "source": "Natural Earth populated places (public domain)"},
                   ("region-place", r.get("id"), p["name"]))
            st.rerun()


if region and geo_ok:
    show_region(region)

# --- Selected point: a separate point forecast, clearly labelled ----------------------------------------
st.html(T.section("Selected point", "location_searching",
                  "Point forecast from Open-Meteo, a different source from the map layers"))
if not pick:
    st.caption("Click anywhere on the globe or map to see a point forecast for that spot."
               + (f" The yellow marker is {city.label}, the place on your Weather page." if city else ""))
else:
    lat, lon = pick
    point = Place("Selected point", lat, lon, "UTC")
    known = st.session_state.get(FOCUS_PLACE_KEY)   # chosen by search or saved place: name already known
    if known is not None and (round(known.latitude, 3), round(known.longitude, 3)) == (lat, lon):
        rev, name, label = None, known.name, known.label
    else:
        known = None
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
        if name and (rev or known):
            if st.button(f"Open {name} on the Weather page", icon=":material/arrow_forward:",
                         help="Shows this place's full forecast. Your home city and saved places aren't changed."):
                st.session_state["_open_place"] = known if known is not None else Place(
                    name, lat, lon, "UTC", (rev or {}).get("country"), (rev or {}).get("admin1"), None,
                    (rev or {}).get("country_code"))
                nav.switch_page("app_pages/weather.py")
        if rev:
            st.caption(G.OSM_ATTRIBUTION)
        elif known:
            st.caption(f"Place name from {st.session_state.get(PLACE_SOURCE_KEY) or 'your search'}.")
    with c2:
        if fc is not None:
            cur = fc.current
            t = U.temperature(cur.get("temperature_2m"), units.temperature)
            feels = U.temperature(cur.get("apparent_temperature"), units.temperature)
            wind = U.wind(cur.get("wind_speed_10m"), units.wind)
            with T.two_up("explorer_point_2"):
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
        f"- **Source:** {explorer_data.SOURCE}, plus {explorer_data.WAVE_SOURCE} for waves; run {run}. "
        "Public-domain NOAA data, refreshed when a newer "
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
        ("Snow depth", "Snow depth on the ground (SNOD)", "cm; below 0.5 cm left clear"),
        ("Wave height", "GFS-Wave significant wave height (0.25°, exact 1° points kept)",
         "m; wind waves and swell combined; no value over land or sea ice"),
    ], columns=["Layer", "GFS field", "Notes"]), hide_index=True, width="stretch")
    st.caption("Globe: three.js r186 (MIT). Earth imagery via NASA GIBS (public domain): Blue Marble shaded relief "
               "and bathymetry by day and VIIRS Black Marble 2016 city lights by night. Both are composites, not "
               "current conditions. Day and night follow the real sun position at the selected time. The globe has "
               "the temperature, wind, precipitation and cloud layers; the map has every layer. "
               "Map: Leaflet 1.9.4 (BSD-2-Clause). Basemap: Natural Earth coastlines, borders and cities (public "
               "domain). Data decoding: ECMWF ecCodes (Apache-2.0). Point forecasts: Open-Meteo (CC BY 4.0). "
               "Place names: © OpenStreetMap contributors (ODbL).")
