"""Cloudzy Outdoor Intelligence: when to run, walk, cycle or spend time outside.

Combines the weather forecast (src.provider) with the air-quality forecast (src.air_quality) hour by
hour, and applies the transparent rules in src.outdoor. The viewer's choices (activity, duration, time
range, conservative guidance, indoor suggestions) are optional and saved in their browser; nothing
medical is asked or stored. Saved windows are re-checked whenever new forecasts load, and the Alerts
page can notify when one becomes less suitable.

Both forecasts are essential: if either can't be loaded, the page explains which and plans nothing.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import air_alerts, app_data, aqi, browser_prefs, nav
from src import air_quality as AQ
from src import air_view as AV
from src import outdoor as O
from src import place_select as PS
from src import preferences as P
from src import theme as T
from src import units as U
from src.data_collection import DataCollectionError
from src.provider import ATTRIBUTION, ProviderError

st.html(AV.CSS)
st.html(T.page_header("Outdoor Intelligence", "Forecast · plan your time outside", "directions_run",
                      "Better times for a run, a walk, a ride or time outside, from the air-quality and weather "
                      "forecasts, with the reasons shown."))

prefs = P.load()
now = pd.Timestamp.now(tz="UTC")
place = PS.current(prefs)
if place is None:
    st.stop()
units = prefs.unit_override() or U.default_for_country(place.country_code)

where_col, pick_col = st.columns([4, 1.3], vertical_alignment="bottom")
where_col.markdown(f"**:material/location_on: {place.label}**")
with pick_col:
    PS.picker(prefs, "od")

# --- Preferences ---------------------------------------------------------------------------------
op = prefs.outdoor


def _duration_label(m: int) -> str:
    return f"{m} min" if m < 60 else f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


act_col, dur_col = st.columns([3, 1], vertical_alignment="bottom")
with act_col:
    picked = st.segmented_control("Activity", list(O.ACTIVITIES), default=op.activity, key="od_activity",
                                  format_func=lambda k: O.ACTIVITIES[k].name)
with dur_col:
    duration = st.selectbox("Duration", O.DURATIONS_MIN, index=O.DURATIONS_MIN.index(op.duration_min),
                            format_func=_duration_label, key="od_duration")
with T.two_up("outdoor_prefs", first_full=True):
    range_col, sens_col, indoor_col = st.columns([2.2, 1.2, 1.2], vertical_alignment="bottom")
with range_col:
    start_hour, end_hour = st.slider("Preferred hours (local time at this place)", 0, 24,
                                     (op.start_hour, op.end_hour), key="od_range", format="%d:00")
with sens_col:
    conservative = st.toggle("More conservative guidance", value=op.sensitivity == "conservative",
                             help=O.SENSITIVITY_HELP)
with indoor_col:
    indoor = st.toggle("Suggest indoor options", value=op.indoor,
                       help="When outdoor conditions are unfavourable, suggest an indoor alternative.")
new = P.OutdoorPrefs(activity=picked or op.activity, duration_min=int(duration),
                     start_hour=int(start_hour), end_hour=int(end_hour),
                     sensitivity="conservative" if conservative else "standard", indoor=bool(indoor))
if new.start_hour >= new.end_hour:
    st.warning("Choose a time range of at least one hour.", icon=":material/schedule:")
    st.stop()
if new != op:
    prefs.outdoor = new
    P.save(prefs)
    browser_prefs.flush()
activity = O.ACTIVITIES[new.activity]
if new.sensitivity == "conservative":
    st.caption(f":material/shield: {O.SENSITIVITY_HELP}")

# --- Data: both forecasts are essential ----------------------------------------------------------
loading = st.empty()
loading.html(T.skeleton(140) + T.skeleton(220))
try:
    forecast = app_data.forecast(place)
except (ProviderError, DataCollectionError) as exc:
    loading.empty()
    st.error(f"We couldn't get the weather forecast for {place.label}, so Cloudzy can't plan outdoor time. {exc}",
             icon=":material/cloud_off:")
    if st.button("Try again", icon=":material/refresh:", key="retry-od-wx"):
        app_data.forecast.clear()
        st.rerun()
    st.stop()
try:
    aq = app_data.air(place)
except (AQ.AirQualityError, DataCollectionError) as exc:
    loading.empty()
    st.error(f"We couldn't get air-quality data for {place.label}, so Cloudzy can't plan outdoor time: air quality "
             f"is an essential check. {exc}", icon=":material/cloud_off:")
    if st.button("Try again", icon=":material/refresh:", key="retry-od-aq"):
        app_data.air.clear()
        st.rerun()
    st.stop()
loading.empty()

standard, standard_note = aqi.resolve_standard(prefs.aqi_standard, place.country_code)
sensitivity = new.sensitivity
table = O.combined_table(aq, forecast, standard)
cur = now.tz_convert(table.index.tz).floor("h")
assessed = [h for h in O.assess(table, activity, standard, sensitivity, units) if h.time >= cur]
all_windows = O.windows(assessed, new.duration_min, new.start_hour, new.end_hour, standard, now, units)
best = O.best_windows(all_windows, activity, standard)
coverage = O.coverage_note(table, new.duration_min, now, new.start_hour, new.end_hour)

# --- Right now -----------------------------------------------------------------------------------
now_h = assessed[0] if assessed and assessed[0].time == cur else None
lines: list[str] = []
if now_h is None:
    level = O.UNAVAILABLE
    lines.append("The forecasts don't cover the current hour.")
else:
    level = now_h.level
    limits = now_h.limiting()
    lines += [f.text for f in limits[:3]] or ["No limiting factors in this hour's forecast."]
    soon = best["next_24h"]
    if soon is not None and soon.start > cur and O.RANK[soon.level] < O.RANK[level]:
        lines.append(f"A better window: {soon.start:%a %H:%M}–{soon.end:%H:%M} "
                     f"({O.LEVEL_LABELS[soon.level].lower()}).")
    if level == O.AVOID and new.indoor:
        lines.append(f"Indoor alternative: {activity.indoor_alternative}.")
st.html(AV.verdict_html(level, f"{activity.name} now", lines))
st.caption(f"Based on the forecasts for {cur:%H:%M} local time (model estimates, not measurements) · "
           f"{standard.name} ({'regional default' if prefs.aqi_standard == 'auto' else 'your choice'}) · "
           f"{'more conservative guidance' if sensitivity == 'conservative' else 'standard guidance'}.")

# --- Suggested windows ---------------------------------------------------------------------------
st.html(T.section("Suggested windows", "event_available",
                  f"{_duration_label(new.duration_min)} · {new.start_hour:02d}:00–{new.end_hour:02d}:00"))
if coverage:
    st.info(coverage, icon=":material/schedule:")

cards: list[tuple[str, O.Window]] = []
seen: set = set()


def _add(heading: str, w: O.Window | None) -> None:
    if w is not None and w.start not in seen:
        seen.add(w.start)
        cards.append((heading, w))


if best["next_24h"] is not None:
    w24 = best["next_24h"]
    _add("Best in the next 24 hours" if w24.level != O.AVOID else "Least unfavourable in the next 24 hours", w24)
if best["next_ok"] is not None and (best["next_24h"] is None or best["next_ok"].start != best["next_24h"].start):
    _add("Next suitable window", best["next_ok"])
for w in best["by_day"]:
    _add((f"Best on {w.start:%A}" if w.level != O.AVOID else f"Least unfavourable on {w.start:%A}"), w)


def _save_plan(w: O.Window) -> None:
    record = O.plan_record(place, activity, w, standard, sensitivity, now.isoformat(timespec="seconds"))
    prefs.plans = [p for p in prefs.plans if not (p["start"] == record["start"] and p["activity"] == record["activity"]
                                                  and p["name"] == record["name"])]
    prefs.plans = (prefs.plans + [record])[-P.MAX_PLANS:]
    P.save(prefs)


if not cards and not coverage:
    st.info("No window fits your activity length inside your preferred hours in the forecast that's left. Try a "
            "wider time range or a shorter activity.", icon=":material/event_busy:")
for row_start in range(0, len(cards), 3):
    cols = st.columns(3, gap="medium")
    for col, (heading, w) in zip(cols, cards[row_start:row_start + 3]):
        with col:
            st.html(AV.window_html(w, heading, standard, units))
            if w.level != O.UNAVAILABLE and st.button("Save this window", key=f"od_save_{w.start.isoformat()}",
                                                      icon=":material/bookmark_add:", width="stretch",
                                                      help="Cloudzy re-checks saved windows when new forecasts load, "
                                                           "and can alert you if one becomes less suitable."):
                _save_plan(w)
                st.toast("Window saved. Cloudzy will re-check it.", icon=":material/check:")
                st.rerun()
if cards and all(w.level == O.AVOID for _, w in cards) and new.indoor:
    st.warning(f"Every suggested window has a limiting factor that makes postponing or moving indoors preferable. "
               f"An indoor option: {activity.indoor_alternative}.", icon=":material/home:")
st.caption("Windows are ranked by their worst hour first, so a failed check (storms, very poor air, dangerous heat) "
           "is never outweighed by other good conditions. Ties are broken by fewer caution factors, better air, "
           "lower PM2.5, lower heat, lower UV and lower rain chance. " + O.DISCLAIMER)

# --- Environmental intelligence ------------------------------------------------------------------
st.html(T.section("What's behind it", "insights"))
insights = O.explain(table, assessed, best["next_24h"], activity, standard, sensitivity, now, units)
st.html(AV.facts_html([("auto_awesome", i.text, i.basis.capitalize()) for i in insights]))
st.caption("Written by fixed rules from the forecast values, not by a language model. Each sentence restates values "
           "it rests on and claims no causes.")

# --- Hour by hour --------------------------------------------------------------------------------
st.html(T.section("Hour by hour", "view_timeline", f"{activity.name} · until {table.dropna(subset=['aqi']).index.max():%a %H:%M}"
                  if table["aqi"].notna().any() else activity.name))
st.html(AV.timeline_html(assessed, standard, new.start_hour, new.end_hour, now, units))
with st.expander("Table: every hour's result and reasons"):
    st.dataframe(AV.assessment_table(assessed, standard, units), hide_index=True, width="stretch")
st.caption("Hours past the end of the air-quality forecast show “?”: Cloudzy doesn't judge an hour without air "
           "quality, temperature and the weather condition.")

# --- Saved windows -------------------------------------------------------------------------------
st.html(T.section("Saved windows", "bookmark"))
live = [p for p in prefs.plans if pd.Timestamp(p["end"]) > now]
if len(live) != len(prefs.plans):           # past windows are dropped (bounded storage)
    prefs.plans = live
    P.save(prefs)
_, results = air_alerts.plan_alerts(prefs.plans, aq, forecast, now, units, place)
others = [p for p in prefs.plans if not any(r["plan"] is p for r in results)]
if not prefs.plans:
    st.caption("No saved windows. Save a suggested window above and Cloudzy re-checks it each time new forecasts load; "
               "turn on “Saved outdoor plans” on the Alerts page to be alerted if one gets worse.")
for i, r in enumerate(results):
    p = r["plan"]
    start, end = pd.Timestamp(p["start"]).tz_convert(table.index.tz), pd.Timestamp(p["end"]).tz_convert(table.index.tz)
    act = O.ACTIVITIES[p["activity"]]
    now_level = r["level"] or O.UNAVAILABLE
    kind, ico = O.LEVEL_CHIPS[now_level]
    with st.container(border=True):
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        status = {"worse": "Less suitable than when you saved it", "uncovered": "Not covered by the latest forecast",
                  "unchanged_or_better": "Same or better than when you saved it"}.get(r["status"], "")
        c1.html(f"<p style='margin:0'><b>{act.name}</b> · {start:%a %d %b %H:%M}–{end:%H:%M} · "
                f"{aqi.STANDARDS[p['standard']].short}</p>"
                + T.chip(O.LEVEL_LABELS[now_level], kind, ico) + f" <span style='opacity:.75'>{status}; saved as "
                f"“{O.LEVEL_LABELS[p['level']].lower()}”.</span>")
        if r["reasons"]:
            c1.caption(" · ".join(r["reasons"][:3]))
        if c2.button("Remove", key=f"od_rm_{i}", icon=":material/delete:", width="stretch"):
            prefs.plans = [q for q in prefs.plans if q is not p]
            P.save(prefs)
            st.rerun()
if others:
    st.caption(f"{len(others)} saved window(s) for other places: open that place here to re-check them.")
nav.page_link("app_pages/alerts.py", "Alerts for saved windows, air quality and storms", ":material/notifications:")

# --- Method --------------------------------------------------------------------------------------
with st.expander("How Cloudzy decides (rules and sources)"):
    st.dataframe(pd.DataFrame([
        {"Check": "Air quality", "Rule": f"{standard.name} category → its guidance for "
                                         f"{'heavy' if activity.heavy else 'light'} exertion",
         "Postpone / indoors when": "the guidance says avoid", "Source": standard.source},
        {"Check": "Thunderstorms", "Rule": "WMO codes 95, 96, 99 in the hour", "Postpone / indoors when": "any",
         "Source": "NWS lightning safety"},
        {"Check": "Freezing rain", "Rule": "WMO codes 56, 57, 66, 67", "Postpone / indoors when": "any",
         "Source": "WMO weather codes"},
        {"Check": "Heat", "Rule": "NWS heat index from temperature and humidity",
         "Postpone / indoors when": "Danger (39.4 °C / 103 °F) or above; caution from 26.7 °C (heavy exertion) "
                                    "or 32.2 °C", "Source": "US National Weather Service"},
        {"Check": "Cold", "Rule": "Wind chill (or temperature in calm air)",
         "Postpone / indoors when": "-28 °C or colder; caution from -10 °C", "Source": "Environment and Climate "
                                                                                       "Change Canada"},
        {"Check": "UV", "Rule": "UV index", "Postpone / indoors when": "never; caution from 8, protection from 3",
         "Source": "WHO"},
        {"Check": "Rain", "Rule": "≥ 50% chance or ≥ 0.5 mm/h; heavy ≥ 7.6 mm/h",
         "Postpone / indoors when": "never on its own (caution)", "Source": "Cloudzy rain indicator; AMS"},
        {"Check": "Wind", "Rule": "Sustained wind and gusts (Beaufort)",
         "Postpone / indoors when": "gusts ≥ 62 km/h (cycling) or ≥ 75 km/h (others)", "Source": "WMO Beaufort scale"},
    ]), hide_index=True, width="stretch")
    st.markdown(
        "- **Heat index isn't WBGT.** Wet-bulb globe temperature, which sports bodies use for exercise in heat, also "
        "accounts for sun and wind. Cloudzy has no validated WBGT source, so it doesn't estimate one. The heat index "
        "assumes shade; full sun can add up to about 8 °C.\n"
        "- **No score.** Results are categories from the rules above. Nothing here is a validated medical or "
        "scientific measure.\n"
        "- **Conservative guidance** switches to the air-quality standard's advice for sensitive groups and starts "
        "heat cautions one category earlier. Cloudzy doesn't ask about, infer or store health conditions.\n"
        f"- {O.DISCLAIMER}")

st.divider()
st.caption(f"{ATTRIBUTION}. {AQ.ATTRIBUTION} Cloudzy is an experimental project, not an official warning or health "
           "service.")
