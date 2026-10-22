"""Cloudzy Earth Intelligence: real disasters and hazards on an interactive 3D globe.

- Data: src.hazards (USGS, GDACS, NHC, NWS, NASA FIRMS, U.S. Drought Monitor, USGS volcano alerts, Smithsonian GVP,
  NOAA tsunami centres), fetched server-side, cached per source and refreshed in the background. Each feed's real
  freshness is shown; a failed feed never stops the others.
- Globe: src.earth_view (MapLibre GL JS, self-hosted). Selecting an event on the globe or in its list reports it
  here; this page shows the full record, the source link and nearby mapped settlements.
- Search (src.earth_search) flies the globe to a country, state, city, coordinates or an OpenStreetMap place and
  lists the loaded events there.
- Situations (src.hazards.situations): the globe's selector chooses which disaster situations, record layers and
  per-situation filters are shown. The globe reports its state here, so the summaries, the situation table and the
  event list below follow it without the globe being rebuilt; the page's own situation picker drives the globe too.
- Nothing is invented: counts come from the loaded records with stated filters, and an empty area isn't "safe".
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import app_data, monitoring, registry
from src import earth_search as ES
from src import theme as T
from src.earth_view import earth_globe
from src.hazards import analysis as A
from src.hazards import gdacs, http, seismicity
from src.hazards import situations as SIT
from src.hazards.schema import KINDS, RANK_LABELS, age_text
from src.hazards.sources import NOT_CONNECTED, SOURCES
from src.hazards.store import Query, store

SEL_KEY, FOCUS_KEY, REGION_KEY, TOKEN_KEY, FIRE_KEY = "_earth_sel", "_earth_focus", "_earth_region", "_earth_tok", "_earth_fire"
VIEW_KEY, SYNC_KEY, SITS_KEY, CLEAR_KEY = "_earth_view", "_earth_sitsync", "earth_sits", "_earth_pending_clear"
PERIODS = {"day": "Past 24 hours", "week": "Past 7 days", "month": "Past 30 days", "custom": "Chosen dates"}
EMERGENCY_NOTE = ("Cloudzy Earth Intelligence is for situational awareness only. It isn't an official warning "
                  "service and can be incomplete or delayed. In an emergency, follow your local emergency services "
                  "and national meteorological or disaster agency. No event shown nearby doesn't mean an area is safe.")


@st.cache_data(ttl=3600, show_spinner=False)
def gdacs_geometry(url: str):
    return gdacs.event_geometry(url)


@st.cache_data(ttl=86400, show_spinner=False, max_entries=64)
def historical_seismicity(lat: float, lon: float) -> dict:
    """USGS catalogue summary around a point, rounded to 0.25° so nearby selections share one request a day."""
    return seismicity.summarise(seismicity.fetch(lat, lon), pd.Timestamp.now(tz="UTC"))


def focus(target: dict) -> None:
    st.session_state[TOKEN_KEY] = st.session_state.get(TOKEN_KEY, 0) + 1
    st.session_state[FOCUS_KEY] = {k: target.get(k) for k in ("lat", "lon", "bbox", "zoom", "maxzoom", "label")} | \
        {"token": st.session_state[TOKEN_KEY]}
    st.session_state[REGION_KEY] = target if target.get("kind") in ("country", "admin1") else \
        {"kind": "near", "lat": target["lat"], "lon": target["lon"], "name": target.get("name"),
         "bbox": target.get("bbox")}


def select_event(event_id: str) -> None:
    st.session_state[SEL_KEY] = event_id
    st.session_state[FIRE_KEY] = None


def push_situations(on: set[str]) -> None:
    """Send a situation set to the globe (it applies each token once) and mirror it in the page's picker."""
    tok = (st.session_state.get(SYNC_KEY) or {}).get("token", 0) + 1
    st.session_state[SYNC_KEY] = {"on": sorted(on), "token": tok}
    view = dict(st.session_state.get(VIEW_KEY) or {})
    st.session_state[VIEW_KEY] = view | {"on": sorted(on)}


def picker_changed() -> None:
    push_situations(set(st.session_state.get(SITS_KEY) or []))


def ml_point() -> dict | None:
    """The newest recorded Cloudzy Core prediction, for the globe's ML layer. No network: monitoring records only."""
    try:
        df = monitoring.query()
    except Exception:            # an unreadable database must never break the globe
        return None
    if df.empty:
        return None
    issue = df["issue_time_utc"].max()
    rows = monitoring.classify(df[df["issue_time_utc"] == issue]).sort_values("horizon_h")
    dep = registry.deployed_entry()
    version = str(rows["model_version"].iloc[0])
    return {"lat": float(rows["latitude"].iloc[0]), "lon": float(rows["longitude"].iloc[0]),
            "where": "Delhi training grid cell", "issue": pd.Timestamp(issue).strftime("%Y-%m-%d %H:%M"),
            "version": version, "current": dep is not None and dep.version == version,
            "rows": [{"h": int(r.horizon_h), "valid": pd.Timestamp(r.target_time_utc).strftime("%d %b %H:%M UTC"),
                      "value": float(r.predicted),
                      "lo": None if pd.isna(r.interval_lower) else float(r.interval_lower),
                      "hi": None if pd.isna(r.interval_upper) else float(r.interval_upper),
                      "status": str(r.detail)} for r in rows.itertuples()],
            "note": "The newest prediction recorded by the app (made when the Weather or Overview page shows Delhi). "
                    "A reanalysis-temperature model for one grid cell: it predicts no hazard."}


st.html(T.page_header("Earth Intelligence", "Planet · disasters and hazards", "travel_explore",
                      "Real earthquakes, storms, floods, fires, volcanoes, drought and official warnings on a 3D globe, "
                      "each with its source, time and what kind of record it is."))
st.warning("**Situational awareness only, not an official warning service.** In an emergency, follow your local "
           "emergency services and national weather or disaster agency.", icon=":material/emergency:")

# --- Controls ------------------------------------------------------------------------------------
pending = st.session_state.pop(CLEAR_KEY, None)       # a filter chip removed on the globe (applied before the widgets)
if pending in ("period", "all"):
    st.session_state["earth_period"] = "week"
if pending in ("region", "all"):
    for k in (REGION_KEY, FOCUS_KEY):
        st.session_state.pop(k, None)
    st.session_state["earth_q"] = ""
search_col, period_col, mag_col, refresh_col = st.columns([3, 1.4, 1.2, 0.7], vertical_alignment="bottom")
with search_col:
    query = st.text_input("Search the globe", key="earth_q", max_chars=120, icon=":material/search:",
                          placeholder="Country, state, city, neighbourhood, address or 28.61, 77.21",
                          help="Countries and states: Natural Earth. Cities: Open-Meteo geocoding. Neighbourhoods, "
                               "streets and addresses: OpenStreetMap Nominatim (searched only when you press Enter). "
                               "Searches aren't saved.").strip()
with period_col:
    period = st.selectbox("Period", list(PERIODS), index=1, format_func=lambda k: PERIODS[k], key="earth_period",
                          help="Applies to earthquakes and GDACS events, and sets the timeline. Other feeds report "
                               "what is current.")
with mag_col:
    min_mag = st.select_slider("Earthquakes from", [0.0, 2.5, 4.0, 4.5, 5.0, 6.0, 7.0], value=2.5,
                               format_func=lambda m: "any magnitude" if m == 0 else f"M{m:g}", key="earth_mag")
with refresh_col:
    refresh = st.button("Refresh", icon=":material/refresh:", width="stretch",
                        help="Ask every source again now (each is otherwise re-checked on its own schedule).")

# Everything that appears only sometimes (dates, search matches, messages) lives in this one container, so the globe
# below keeps its place in the page and isn't rebuilt (losing the camera) when one of them shows up.
extras = st.container()
now = pd.Timestamp.now(tz="UTC")
start = end = None
if period == "custom":
    d1, d2 = extras.columns(2)
    picked_start = d1.date_input("From (UTC)", value=(now - pd.Timedelta(days=14)).date(),
                                 max_value=now.date(), key="earth_from")
    picked_end = d2.date_input("To (UTC)", value=now.date(), max_value=now.date(), key="earth_to")
    start = pd.Timestamp(picked_start, tz="UTC")
    end = min(pd.Timestamp(picked_end, tz="UTC") + pd.Timedelta(days=1), now)
    if end <= start:
        extras.error("The end date must be after the start date.", icon=":material/event_busy:")
        st.stop()
    if end - start > pd.Timedelta(days=62):
        extras.error("Choose at most 62 days, to keep the request within USGS and GDACS limits.", icon=":material/event_busy:")
        st.stop()
    extras.caption("Historical earthquakes come from the USGS FDSN event service (M2.5 and above) and GDACS events from its "
               "archive. Feeds that only report what is current (NWS, NHC, fire detections, drought, volcano alerts, "
               "tsunami bulletins) show today's records, not the chosen dates.")
period = period or "week"
q = Query(period, float(min_mag), start, end)
hz = store()
if refresh:
    for key in SOURCES:
        hz.refresh(key, q)

with extras, st.spinner("Loading disaster and hazard feeds… the first load takes up to 25 seconds; later views are "
                        "instant."):
    snap = hz.snapshot(q)

events = snap.events
by_id = {e.id: e for e in events}

# --- Search ---------------------------------------------------------------------------------------
if len(query) >= 2:
    targets, search_errors = ES.search(query, cities=app_data.search)
    for err in search_errors:
        extras.caption(f":material/error: Part of the search is unavailable ({err}); other results still work.")
    if not targets:
        extras.info(f"No place matches “{query}”.", icon=":material/travel_explore:")
    else:
        choice = extras.selectbox("Matching places", range(len(targets)), format_func=lambda i: targets[i]["label"],
                              key=f"earth_pick_{query}")
        if st.session_state.get("_earth_last_choice") != (query, choice):
            st.session_state["_earth_last_choice"] = (query, choice)
            focus(targets[choice])
        if targets[choice].get("source"):
            extras.caption(f"Place data: {targets[choice]['source']}.")

region = st.session_state.get(REGION_KEY)
region_geom = ES.region_geometry(region["kind"], region["a3"], region.get("id")) \
    if region and region.get("kind") in ("country", "admin1") else None

# --- Globe ----------------------------------------------------------------------------------------
selected_id = st.session_state.get(SEL_KEY)
selected = by_id.get(selected_id) if selected_id else None
sel_geometry = None
if selected is not None and selected.source == "gdacs" and selected.category != "cyclone" and selected.extra.get("geometry_url"):
    try:
        sel_geometry = gdacs_geometry(selected.extra["geometry_url"])
    except http.FeedError:
        sel_geometry = None
period_start = start if start is not None else now - pd.Timedelta(days=q.days())
period_end = end if end is not None else now
attribution = ("Data: USGS, GDACS, NOAA (NHC, NWS, tsunami centres), NASA FIRMS, U.S. Drought Monitor, "
               "Smithsonian GVP (full list below the globe)")
place_name = (region or {}).get("name")
result = earth_globe({
    "layers": snap.layers,
    "statuses": [{"source": s, "name": SOURCES[s].name, "label": st_.label} for s, st_ in snap.statuses.items()],
    "situations": SIT.selector_data(events),
    "presets": {k: {"label": label, "keys": list(keys)} for k, (label, keys) in SIT.PRESETS.items()},
    "record_layers": SIT.LAYERS, "basins": list(SIT.BASINS),
    "sit_sync": st.session_state.get(SYNC_KEY), "ml": ml_point(),
    "chips": {"period": PERIODS[period] if period != "week" else None, "region": place_name},
    "kinds": KINDS, "source_names": {k: s.name for k, s in SOURCES.items()},
    "period": {"start": int(period_start.timestamp() * 1000), "end": int(period_end.timestamp() * 1000)},
    "imagery_day": (now - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
    "attribution": attribution,
    "focus": st.session_state.get(FOCUS_KEY), "region": region_geom,
    "selected": selected_id, "selected_geometry": sel_geometry,
})
view_msg = getattr(result, "view", None)
if view_msg and view_msg.get("t") != st.session_state.get("_earth_view_t"):
    st.session_state["_earth_view_t"] = view_msg.get("t")
    st.session_state[VIEW_KEY] = view_msg
clear_msg = getattr(result, "chip", None)
if clear_msg and clear_msg.get("t") != st.session_state.get("_earth_clear_t"):
    st.session_state["_earth_clear_t"] = clear_msg.get("t")
    st.session_state[CLEAR_KEY] = clear_msg.get("what")
    st.rerun()
view = SIT.View.from_json(st.session_state.get(VIEW_KEY))
st.session_state[SITS_KEY] = sorted(view.on)          # the picker below always shows what the globe shows
picked = getattr(result, "select", None)
if picked and picked.get("t") != st.session_state.get("_earth_pick_t"):
    st.session_state["_earth_pick_t"] = picked.get("t")
    if picked.get("id"):
        select_event(picked["id"])
        selected = by_id.get(picked["id"])
    elif picked.get("fire"):
        st.session_state[FIRE_KEY] = picked["fire"]
        st.session_state[SEL_KEY] = None
        selected = None

st.caption("Drag to rotate, scroll or pinch to zoom, double-click to zoom in. Zooming reveals continents, countries, "
           "states, cities, streets and, from about street level, OpenStreetMap building footprints (outlines, not "
           "imagery). Relief imagery is a 2004 NASA composite; the satellite option shows a recent day at 250 m. "
           "Nothing on the map shows the condition of a particular building.")

# --- Feed status ----------------------------------------------------------------------------------
st.html(T.section("Data feeds", "sensors", f"Checked {now:%H:%M} UTC"))
rows = []
for key, s in snap.statuses.items():
    src = SOURCES[key]
    rows.append({"Source": src.name, "Status": s.label,
                 "Data time": "—" if s.data_time is None else f"{s.data_time:%d %b %H:%M} UTC ({age_text(s.data_time, now)})",
                 "Last checked": "never" if s.fetched_at is None else age_text(s.fetched_at, now),
                 "Records": s.count, "Coverage": src.coverage, "Updates": src.cadence_text,
                 "Notes": "; ".join(x for x in (s.error and f"Error: {s.error}", s.note) if x) or ""})
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
    "Coverage": st.column_config.TextColumn(width="large"), "Notes": st.column_config.TextColumn(width="large")})
st.caption("Live feed: the source updates every few minutes and its data are fresh. Recently updated: within the "
           "source's normal schedule (some update every few hours or weekly). Delayed data: older than expected, or "
           "the last refresh failed and earlier data are shown. Temporarily unavailable: no data could be loaded.")

# --- Summary ----------------------------------------------------------------------------------------
st.html(T.section("Summary of loaded records", "monitoring", PERIODS[period]))
fire_count = None
if snap.statuses.get("firms") and snap.statuses["firms"].note:
    digits = "".join(ch for ch in snap.statuses["firms"].note.split(" ")[0] if ch.isdigit())
    fire_count = int(digits) if digits else None
cols = st.columns(6)
for col, row in zip(cols, A.summary(events, snap.statuses, fire_count)):
    col.metric(row["label"], "—" if row["value"] is None else f"{row['value']:,}", help=row["definition"])
st.caption("Counts are of the records Cloudzy loaded, with the definitions shown on hover. They aren't worldwide "
           "totals: official warnings are connected for the United States only, and GDACS lists only events large "
           "enough to model.")

# --- Situations: follows the globe's selector, filters and the searched place ----------------------------
area_region = region if region and region.get("kind") in ("country", "admin1") else None
in_view = [e for e in events if SIT.matches(e, view, now)]
regional = A.filter_events(in_view, region=area_region, include_marine=True) if area_region else in_view
if region and region.get("kind") == "near":
    regional = A.events_near(region["lat"], region["lon"], regional, 150)
st.html(T.section("Situations", "tune", f"Near {place_name}" if place_name else "Follows the globe's selector"))
loaded_n, shown_n, here_n = SIT.counts(events), SIT.counts(in_view), SIT.counts(regional)
st.dataframe(pd.DataFrame([{
    "Situation": sit.label, "On the globe": "not connected" if not sit.sources else ("on" if k in view.on else "off"),
    "Loaded": loaded_n[k] if sit.sources else None, "Shown with filters": shown_n[k] if k in view.on else None,
    **({f"Near {place_name}": here_n[k] if k in view.on else None} if place_name else {}),
    "What it covers": sit.covers or sit.missing} for k, sit in SIT.SITUATIONS.items()]),
    hide_index=True, width="stretch", column_config={"What it covers": st.column_config.TextColumn(width="large")})
st.caption("Loaded: records from the connected feeds for the period. Shown: after the globe's record layers and "
           "filters, at Now (the timeline can narrow the globe further). Fire detections and the drought map are "
           "drawn as overlays and counted under the data feeds. Not connected: Cloudzy has no source, so nothing can "
           "be said about it here.")

# --- Highest-priority official alerts ----------------------------------------------------------------
st.html(T.section("Official warnings in force", "campaign", "Severe and extreme, newest first"))
official = sorted([e for e in events if e.official and e.kind == "warning" and e.current and e.severity_rank >= 3
                   and not e.extra.get("marine")], key=lambda e: (-e.severity_rank, -(e.time or now).timestamp()))
if not official:
    st.caption("No severe or extreme official warnings from the connected agencies right now. Agencies in most "
               "countries aren't connected, so check your national service.")
for e in official[:8]:
    with st.container(border=True):
        a, b = st.columns([4, 1], vertical_alignment="center")
        a.markdown(f"**{e.title}** · {e.severity_label} · {e.area or e.country or ''}"[:400])
        a.caption(f"{SOURCES[e.source].publisher} · issued {age_text(e.updated, now)}"
                  + (f" · expires {e.ends:%d %b %H:%M} UTC" if e.ends is not None else ""))
        if e.url:
            b.link_button("Official source", e.url, icon=":material/open_in_new:", width="stretch")
        if b.button("Show", key=f"earth_show_{e.id}", icon=":material/my_location:", width="stretch"):
            select_event(e.id)
            if e.lat is not None:
                focus({"lat": e.lat, "lon": e.lon, "zoom": 6, "label": e.title})
            st.rerun()

# --- Selected event ---------------------------------------------------------------------------------
st.html(T.section("Selected", "location_searching", "Select a marker or a list entry"))
fire = st.session_state.get(FIRE_KEY)
if selected is not None:
    sit = SIT.SITUATIONS[SIT.situation_for(selected)]
    st.markdown(f"#### :material/{sit.icon}: {selected.title}")
    chips = [sit.label, KINDS[selected.kind], SIT.LAYERS[SIT.layer_for(selected)],
             "Official source" if selected.official else "Not an official warning",
             "Current" if selected.current else "Not current"]
    st.caption(" · ".join(chips))
    if not SIT.matches(selected, view, now):
        why = (f"{sit.label} are turned off in the globe's situation selector" if sit.key not in view.on else
               f"the “{SIT.LAYERS[SIT.layer_for(selected)]}” layer is hidden" if SIT.layer_for(selected) not in view.layers
               else "a filter on the globe excludes it (see the chips at the top of the globe)")
        hc1, hc2 = st.columns([4, 1], vertical_alignment="center")
        hc1.info(f"This record is selected but hidden on the globe and in the event list: {why}.",
                 icon=":material/visibility_off:")
        if sit.key not in view.on and hc2.button(f"Show {sit.label.lower()}", key="earth_unhide",
                                                 icon=":material/visibility:", width="stretch"):
            push_situations(view.on | {sit.key})
            st.rerun()
    act1, act2, act3 = st.columns(3)
    if selected.lat is not None and act1.button("Focus on the globe", key="earth_focus_sel", icon=":material/my_location:",
                                                width="stretch"):
        focus({"lat": selected.lat, "lon": selected.lon, "zoom": 3.5 if selected.category == "cyclone" else 6,
               "label": selected.title})
        st.rerun()
    if sit.key in view.on and act2.button(f"Hide {sit.label.lower()}", key="earth_hide_sit",
                                          icon=":material/visibility_off:", width="stretch"):
        push_situations(view.on - {sit.key})
        st.rerun()
    if selected.url:
        act3.link_button("Open source", selected.url, icon=":material/open_in_new:", width="stretch")
    left, right = st.columns([1.3, 1], gap="large")
    with left:
        facts = {"Situation": sit.label, "Severity": f"{selected.severity_label} (Cloudzy rank: "
                 f"{RANK_LABELS[selected.severity_rank]})", "Location": selected.area or selected.country or "Not available",
                 "Coordinates": "Not available" if selected.lat is None else f"{selected.lat:.4f}, {selected.lon:.4f}",
                 "Time": "Not available" if selected.time is None else f"{selected.time:%Y-%m-%d %H:%M} UTC",
                 "Last update": "Not available" if selected.updated is None else
                 f"{selected.updated:%Y-%m-%d %H:%M} UTC ({age_text(selected.updated, now)})",
                 "Source": SOURCES[selected.source].publisher}
        if selected.ends is not None:
            facts["Ends or expires"] = f"{selected.ends:%Y-%m-%d %H:%M} UTC"
        facts.update(selected.measurements)
        st.dataframe(pd.DataFrame({"Field": list(facts), "Value": [str(v) for v in facts.values()]}),
                     hide_index=True, width="stretch")
        st.caption(f"Severity as the source defines it: {selected.severity_definition}")
        if selected.summary:
            st.markdown(f"**From the source:** {selected.summary[:1200]}")
        if selected.extra.get("description"):
            with st.expander("Full official text"):
                st.write(selected.extra["description"])
        if selected.extra.get("cone_note"):
            st.caption(selected.extra["cone_note"])
        if selected.lat is not None:
            st.caption("Coordinates (copy with the button on the right):")
            st.code(f"{selected.lat:.5f}, {selected.lon:.5f}", language=None)
        links = st.columns(2)
        if selected.url:
            links[0].link_button("Open original source", selected.url, icon=":material/open_in_new:", width="stretch")
        for link in selected.extra.get("related_links", [])[:1]:
            links[1].link_button(link["label"], link["url"], icon=":material/link:", width="stretch")
        if selected.related:
            st.markdown("**Same event in other sources**")
            st.caption("; ".join(f"{SOURCES[r.split(':')[0]].name if r.split(':')[0] in SOURCES else r}: "
                                 f"{by_id[r].title if r in by_id else 'folded into this record'}" for r in selected.related))
    with right:
        ctx = A.context(selected)
        st.markdown("**Nearby mapped settlements**")
        if ctx["places"]:
            st.dataframe(pd.DataFrame([{"Place": p.name, "Admin. area": p.admin1, "Country": p.country_a3,
                                        "Distance km": p.distance_km, "Population (NE)": p.population}
                                       for p in ctx["places"]]), hide_index=True, width="stretch")
        else:
            st.caption("No mapped settlement in the search area, or the event has no location.")
        st.caption(ctx["method"] + " " + " ".join(ctx["notes"]))
        near = A.nearby_events(selected, events, 300)
        if near:
            st.markdown("**Related: other loaded events within 300 km**")
            st.dataframe(pd.DataFrame([{"Event": e.title, "Distance km": d,
                                        "Situation": SIT.SITUATIONS[SIT.situation_for(e)].label, "Kind": KINDS[e.kind],
                                        "Time (UTC)": "" if e.time is None else f"{e.time:%d %b %H:%M}"}
                                       for e, d in near]), hide_index=True, width="stretch")
    if selected.category == "earthquake" and selected.lat is not None and selected.lon is not None:
        st.markdown(f"**Historical seismicity within {seismicity.RADIUS_KM} km · historical analysis**")
        hist: dict | None
        try:
            with st.spinner("Reading the USGS catalogue since 1976…"):
                hist = historical_seismicity(round(selected.lat * 4) / 4, round(selected.lon * 4) / 4)
        except http.FeedError as exc:
            st.caption(f":material/cloud_off: The USGS catalogue isn't available right now ({exc}).")
            hist = None
        if hist and hist["n"]:
            h1, h2, h3, h4 = st.columns(4)
            for col, t in zip((h1, h2, h3), hist["thresholds"]):
                col.metric(f"M{t['min_mag']:g}+ recorded", f"{t['count']:,}",
                           help=f"About {t['per_decade']:g} per decade since {hist['start'][:4]}: a historical "
                                "count, not a forecast probability.")
            big = hist["largest"]
            h4.metric("Largest recorded", f"M{big['mag']:.1f}", help=f"{big['mag_type']}, {big['time'][:10]}")
            bv = hist.get("b_value")
            st.caption(
                f"{hist['n']:,} earthquakes of M{seismicity.MIN_MAG:g}+ in {hist['span_years']:g} years, "
                f"{hist['last_5_years']:,} in the last 5. "
                + (f"Gutenberg-Richter b-value {bv['b']:.2f} (90% bootstrap interval {bv['ci90'][0]:.2f}-{bv['ci90'][1]:.2f}, "
                   f"{bv['n']:,} events above an estimated completeness of M{bv['mc']:.1f}). "
                   if bv else "Too few events above the catalogue's completeness for a b-value. ")
                + "Magnitude types are mixed ("
                + ", ".join(f"{k} {v:.0%}" for k, v in hist["mag_types"].items())
                + "), so the b-value is indicative. " + hist["note"])
            for label, url in hist["hazard_links"]:
                st.link_button(label, url, icon=":material/open_in_new:")
        elif hist is not None:
            st.caption("No M4+ earthquakes recorded within this radius since 1976.")
elif fire:
    st.markdown("#### :material/local_fire_department: Satellite fire detection")
    st.caption("Satellite detection · not a confirmed fire · NASA FIRMS, VIIRS NOAA-20 375 m")
    t = pd.Timestamp(fire["tms"], unit="ms", tz="UTC")
    st.markdown(f"Detected **{t:%Y-%m-%d %H:%M} UTC** ({age_text(t, now)}) at {fire['lat']:.3f}, {fire['lon']:.3f}, "
                f"{'night' if fire['night'] else 'day'} overpass. Fire radiative power: "
                f"{'not given' if fire['frp'] is None else str(fire['frp']) + ' MW'}; confidence: {fire['conf']}.")
    near = A.places_near(fire["lat"], fire["lon"], 25)
    if near:
        st.dataframe(pd.DataFrame([{"Place": p.name, "Country": p.country_a3, "Distance km": p.distance_km}
                                   for p in near]), hide_index=True, width="stretch")
    st.caption("Hotspots also come from industry, gas flares and volcanoes. For active wildfires and evacuations, "
               "follow your national fire service. " + A.PROXIMITY_NOTE)
    st.link_button("Open NASA FIRMS at this point",
                   f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{fire['lon']:.3f},{fire['lat']:.3f},9.0z",
                   icon=":material/open_in_new:")
else:
    st.caption("Nothing selected yet.")

# --- Events table (synchronised with the globe) -------------------------------------------------------
st.html(T.section("Events", "list", f"Near {place_name}" if place_name else "Follows the globe's selector"))
connected = [k for k, x in SIT.SITUATIONS.items() if x.sources]
st.multiselect("Situations", connected, key=SITS_KEY, on_change=picker_changed,
               format_func=lambda k: SIT.SITUATIONS[k].label,
               help="The same selection as the globe's situation selector: changing either changes both. The globe's "
                    "record layers and filters apply here too.")
area_only = bool(place_name) and st.toggle(f"Only events in or near {place_name}", value=True, key="earth_area")
table_events = A.filter_events(in_view, region=area_region if area_only else None, include_marine=True)
if area_only and region and region.get("kind") == "near":
    table_events = A.events_near(region["lat"], region["lon"], table_events, 150)
    st.caption("Events within 150 km of the place.")
table_events.sort(key=lambda e: (-e.severity_rank, -(e.time or now).timestamp()))
if not table_events:
    st.info(("No situation is selected on the globe or above. " if not view.on else "No loaded records match the "
             "globe's situations and filters. ") + "That doesn't mean nothing is happening there: many hazards and "
            "countries aren't covered by the connected feeds.", icon=":material/info:")
else:
    df = pd.DataFrame([{"Event": e.title, "Situation": SIT.SITUATIONS[SIT.situation_for(e)].label, "Kind": KINDS[e.kind],
                        "Severity": e.severity_label, "Where": (e.area or e.country or "")[:80],
                        "Time (UTC)": None if e.time is None else e.time.tz_localize(None),
                        "Source": SOURCES[e.source].name, "Official": e.official, "id": e.id}
                       for e in table_events[:2000]])
    pick = st.dataframe(df.drop(columns="id"), hide_index=True, width="stretch", on_select="rerun",
                        selection_mode="single-row", key="earth_table",
                        column_config={"Time (UTC)": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")})
    st.caption(f"{len(table_events):,} records" + (" (first 2,000 shown)" if len(table_events) > 2000 else "") +
               ". Select a row to show it on the globe.")
    rows_sel = getattr(getattr(pick, "selection", None), "rows", []) or []
    if rows_sel:
        chosen = df.iloc[rows_sel[0]]["id"]
        if chosen != st.session_state.get(SEL_KEY):
            select_event(chosen)
            st.rerun()

# --- Sources, gaps and terms ------------------------------------------------------------------------------
with st.expander("What isn't connected, and why"):
    st.dataframe(pd.DataFrame(NOT_CONNECTED, columns=["Not connected", "Why"]), hide_index=True, width="stretch")
with st.expander("Sources, licences and attribution"):
    st.dataframe(pd.DataFrame([{"Source": s.name, "Publisher": s.publisher, "Licence or terms": s.licence,
                                "Attribution": s.attribution, "Home": s.home, "Note": s.note}
                               for s in SOURCES.values()]), hide_index=True, width="stretch")
    st.markdown("- **Base map:** OpenFreeMap vector tiles (© OpenMapTiles, © OpenStreetMap contributors, ODbL). "
                "**Imagery:** NASA GIBS (public domain). **Terrain:** AWS Terrain Tiles (Mapzen; SRTM, GMTED, "
                "ETOPO1 and others). **Places and borders:** Natural Earth (public domain), shown from India's "
                "official point of view. **Search:** Open-Meteo geocoding (GeoNames, CC BY 4.0) and OpenStreetMap "
                "Nominatim. **Map engine:** MapLibre GL JS (BSD-3-Clause).")
if snap.warnings:
    with st.expander(f"Data notes ({len(snap.warnings)})"):
        for w in snap.warnings:
            st.markdown(f"- {w}")
st.caption(EMERGENCY_NOTE)
