"""Overview: Cloudzy's command centre for one place. A short summary of each feature, linking to its page.

- Every tile states what kind of data it shows (provider forecast, model estimate, official warning, Cloudzy
  ML prediction, rule-based guidance, tracked evaluation) and where it comes from.
- Each tile is independent: if one source fails, that tile says so and the rest still show.
- Hazard feeds are read without waiting (src.hazards.store): on a cold start the tile says they're loading.
- Old links to the Weather page ("/?q=…") are forwarded there.
"""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from src import air_quality as AQ
from src import app_data, aqi, nav
from src import insights as I
from src import outdoor as O
from src import overview_view as OV
from src import place_select as PS
from src import preferences as P
from src import theme as T
from src import ui
from src import units as U
from src import weather_view as V
from src.briefing import CLOUDZY_RADIUS_KM, distance_km
from src.config import DELHI
from src.data_collection import DataCollectionError
from src.provider import ProviderError, rain_outlook, upcoming

if "q" in st.query_params:                     # links made before Overview existed pointed at the Weather page
    nav.switch_page("app_pages/weather.py", query_params=dict(st.query_params))

st.html(V.CSS + OV.CSS)
st.html(T.page_header("Overview", "Today · command centre", "dashboard",
                      "The essentials for one place, each linked to its full page."))
prefs = P.load()
now = pd.Timestamp.now(tz="UTC")
place = PS.current(prefs)
if place is None:
    st.stop()
units = prefs.unit_override() or U.default_for_country(place.country_code)
where_col, pick_col = st.columns([4, 1.3], vertical_alignment="bottom")
where_col.markdown(f"**:material/location_on: {place.label}**")
with pick_col:
    PS.picker(prefs, "ov")
st.html(OV.kinds_legend())

# --- Weather (provider) --------------------------------------------------------------------------
forecast = None
try:
    with st.spinner("Loading the forecast…"):
        forecast = app_data.forecast(place)
except (ProviderError, DataCollectionError) as exc:
    st.error(f"The weather forecast isn't available right now: {exc}", icon=":material/cloud_off:")

tiles: list[str] = []
if forecast is not None:
    cur, daily = forecast.current, forecast.daily
    tz = forecast.hourly.index.tz
    local_now = pd.Timestamp(cur["time"]).tz_convert(tz)
    today_key = now.tz_convert(tz).date()
    today = daily.loc[today_key] if today_key in daily.index else None
    st.html(V.hero_html(place.label, cur, today, units, local_now.tzname() or str(tz)))
    st.caption(f"{forecast.source_label} · {V.updated_label(forecast.retrieved_at_utc, tz)} · model estimates for "
               "this location, not a station reading")
    st.html(V.summary_html(I.summary(forecast, now, units, style="brief")))

    hrs = upcoming(forecast, now, 24)
    rain = rain_outlook(forecast, now)
    if not hrs.empty:
        t_hi, t_lo = hrs["temperature_2m"].max(), hrs["temperature_2m"].min()
        gust = hrs["wind_gusts_10m"].max()
        body = (f"<p>{escape(V.temp_text(t_lo, units))} to {escape(V.temp_text(t_hi, units))}; gusts up to "
                f"{escape(ui.fmt(U.wind(gust, units.wind), ' ' + units.wind))}</p>"
                + (f"<p>Rain likely from {pd.Timestamp(rain['first_rain_hour_local']):%a %H:%M} "
                   f"(up to {rain['max_probability_pct']:.0f}%)</p>" if rain["rain_expected"]
                   else f"<p>No rain expected (highest chance {ui.fmt(rain['max_probability_pct'], '%')})</p>"))
        tiles.append(OV.tile("Next 24 hours", "schedule", "provider", body=body,
                             foot="Open-Meteo forecast · rain rule: ≥ 50% or ≥ 0.5 mm in an hour"))
    found = I.alerts(forecast, now, prefs.alerts, units, enabled=prefs.enabled_alert_kinds())
    env, env_error = app_data.environment_alerts(place, forecast, prefs, units, now)
    found += env
    active = [a for a in found if a.key not in prefs.active_dismissals(now)]
    body = ("<ul>" + "".join(f"<li>{escape(a.title)} · {escape(a.value)}</li>" for a in active[:4]) + "</ul>"
            if active else "<p>None for the next 24 hours with your thresholds.</p>")
    tiles.append(OV.tile("Cloudzy alerts", "notifications", "rules", big=str(len(active)), big_note="active",
                         body=body, foot="Automatic threshold checks, not official warnings"
                         + (f" · {env_error}" if env_error else "")))

# --- Air quality (CAMS model estimate) -------------------------------------------------------------
aq = None
try:
    aq = app_data.air(place)
    standard, _ = aqi.resolve_standard(prefs.aqi_standard, place.country_code)
    r = AQ.reading(aq, standard, now)
    if r.category:
        body = (f'<p><span class="sw" style="background:{r.category.color}" aria-hidden="true"></span>'
                f"<b>{escape(r.category.label)}</b> · main pollutant {escape(aqi.POLLUTANTS[r.dominant].symbol) if r.dominant else '—'}</p>"
                f"<p>{escape(r.category.general)}</p>")
        tiles.append(OV.tile("Air quality now", "air", "model_estimate", big=r.text, big_note=standard.short,
                             body=body, foot=f"CAMS via Open-Meteo, for {r.time:%H:%M} · not a station reading"))
    else:
        tiles.append(OV.tile("Air quality now", "air", "model_estimate", body=f"<p>{escape(r.note)}</p>"))
except (AQ.AirQualityError, DataCollectionError) as exc:
    tiles.append(OV.tile("Air quality now", "air", "model_estimate",
                         body=f"<p>Not available right now: {escape(str(exc))}</p>"))

# --- Outdoor planner (rules) ---------------------------------------------------------------------
if forecast is not None and aq is not None:
    op = prefs.outdoor
    act = O.ACTIVITIES[op.activity]
    standard, _ = aqi.resolve_standard(prefs.aqi_standard, place.country_code)
    table = O.combined_table(aq, forecast, standard)
    cur_h = now.tz_convert(table.index.tz).floor("h")
    assessed = [h for h in O.assess(table, act, standard, op.sensitivity, units) if h.time >= cur_h]
    best = O.best_windows(O.windows(assessed, op.duration_min, op.start_hour, op.end_hour, standard, now, units),
                          act, standard)
    w = best["next_ok"]
    if w is not None:
        body = (f"<p>{escape(act.name)}, {op.duration_min} min: <b>{w.start:%a %H:%M}–{w.end:%H:%M}</b></p>"
                f"<p>{escape(w.supporting[0]) if w.supporting else ''}</p>")
        tiles.append(OV.tile("Next suitable window", "directions_run", "rules", body=body,
                             foot="From forecasts and published guidance; not a safety promise"))
    else:
        w = best["next_24h"]
        body = (f"<p>No fully suitable {escape(act.name.lower())} window in the forecast you set.</p>"
                + (f"<p>Least unfavourable: {w.start:%a %H:%M} ({escape(O.LEVEL_LABELS[w.level].lower())})</p>"
                   if w is not None else ""))
        tiles.append(OV.tile("Outdoor planner", "directions_run", "rules", body=body,
                             foot="Limiting factors are on the Outdoor page"))

# --- Hazards nearby (official and other feeds; never waits) --------------------------------------------
try:
    from src.hazards import analysis as HA
    from src.hazards.store import store
    snap = store().snapshot(wait_s=0)
    loaded = [s for s in snap.statuses.values() if s.count or s.ok]
    near = HA.events_near(place.latitude, place.longitude, snap.events, 500)
    near = [e for e in near if not e.extra.get("marine") and (e.current or e.category == "earthquake")]
    official = [e for e in near if e.official and e.kind == "warning"]
    if not loaded:
        body = "<p>Disaster feeds are still loading. Open Earth Intelligence, or check back in a minute.</p>"
        tiles.append(OV.tile("Hazards within 500 km", "travel_explore", "mixed", body=body))
    else:
        top = sorted(near, key=lambda e: (-e.severity_rank, -(e.time or now).timestamp()))[:3]
        body = ("<ul>" + "".join(f"<li>{escape(e.title[:70])} · {escape(e.severity_label)}</li>" for e in top) + "</ul>"
                if top else "<p>No loaded records within 500 km. That doesn't mean the area is safe: many "
                            "hazards and countries aren't covered.</p>")
        tiles.append(OV.tile("Hazards within 500 km", "travel_explore", "official" if official else "mixed",
                             big=str(len(near)), big_note=f"records · {len(official)} official warnings",
                             body=body, foot=f"{len(loaded)} of {len(snap.statuses)} feeds loaded · see Earth "
                                             "Intelligence for sources"))
except Exception as exc:
    tiles.append(OV.tile("Hazards within 500 km", "travel_explore", "mixed",
                         body=f"<p>Unavailable: {escape(type(exc).__name__)}</p>"))

# --- Cloudzy Core (ML) and tracking -----------------------------------------------------------------
d_km = distance_km(place.latitude, place.longitude, DELHI.latitude, DELHI.longitude)
if d_km <= CLOUDZY_RADIUS_KM:
    block = app_data.cloudzy(place)
    if block.get("available"):
        p0 = block["cloudzy"]["predictions"][0]
        iv = p0.get("interval")
        body = (f"<p>80% range {escape(V.temp_text(iv['lower_c'], units, 1))} to "
                f"{escape(V.temp_text(iv['upper_c'], units, 1))} (validated coverage {iv['validated_coverage']:.0%})</p>"
                if iv else "<p>No validated range for this model version.</p>")
        tiles.append(OV.tile(f"Cloudzy Core +{p0['horizon_h']} h", "science", "ml",
                             big=V.temp_text(p0["temperature_2m_c"], units, 1), body=body,
                             foot="Predicts the reanalysis temperature of the Delhi grid cell; experimental"))
    else:
        tiles.append(OV.tile("Cloudzy Core", "science", "ml", body=f"<p>No prediction right now: "
                                                                 f"{escape(str(block.get('reason')))}</p>"))
else:
    tiles.append(OV.tile("Cloudzy Core", "science", "ml",
                         body=f"<p>Cloudzy's own model only predicts for New Delhi ({d_km:,.0f} km away), where it "
                              "was trained and tested.</p>"))
try:
    from src import monitoring as M
    recs = M.query()
    counts = M.status_counts(recs)
    agg = M.aggregate(recs)
    body = (f"<p>{counts[M.EVALUATED]} evaluated · {counts[M.WAIT_TARGET]} awaiting target time · "
            f"{counts[M.WAIT_REFERENCE]} waiting for the reference data</p>")
    if agg["n"]:
        h0 = agg["by_horizon"][0]
        body += (f"<p>Tracked MAE at +{h0['horizon_h']} h: {h0['mae_c']:.2f} °C over {h0['n']} predictions"
                 + ("" if agg["conclusive"] else " (too few to be conclusive)") + "</p>")
    tiles.append(OV.tile("Prediction tracking", "query_stats", "tracking", big=str(len(recs)), big_note="records",
                         body=body, foot="Reference: ERA5-Land reanalysis, published about 6 days late"))
except Exception as exc:
    tiles.append(OV.tile("Prediction tracking", "query_stats", "tracking",
                         body=f"<p>Unavailable: {escape(type(exc).__name__)}</p>"))

st.html(T.section("At a glance", "dashboard"))
st.html('<div class="cz-tiles">' + "".join(tiles) + "</div>")

st.html(T.section("Go deeper", "arrow_forward"))
links = [("app_pages/weather.py", "Full forecast", ":material/partly_cloudy_day:"),
         ("app_pages/air_quality.py", "Air quality", ":material/air:"),
         ("app_pages/outdoor.py", "Outdoor planner", ":material/directions_run:"),
         ("app_pages/earth.py", "Earth Intelligence", ":material/travel_explore:"),
         ("app_pages/alerts.py", "Alerts", ":material/notifications:"),
         ("app_pages/predictions.py", "Prediction history", ":material/query_stats:"),
         ("app_pages/lab.py", "Model Lab", ":material/science:")]
cols = st.columns(3)
for i, (path, label, ico) in enumerate(links):
    with cols[i % 3]:
        nav.page_link(path, label, ico)

if forecast is not None and not upcoming(forecast, now, 24).empty:
    st.html(T.section("Next 24 hours", "show_chart", "Provider forecast"))
    shown = U.convert_frame(upcoming(forecast, now, 24), units)
    st.plotly_chart(ui.temperature_chart(shown, ui.palette(), units.temperature,
                                         f"Temperature and feels-like ({units.temperature}, local time)"),
                    width="stretch", theme="streamlit", config=ui.CHART_CONFIG)
st.divider()
st.caption("Weather: Open-Meteo (CC BY 4.0). Air quality: CAMS via Open-Meteo. Hazards: see Earth Intelligence. "
           "Cloudzy is an experimental project, not an official warning service.")
