"""Air Quality: the current index, pollutants, the hourly trend and the multi-day outlook.

- Data: CAMS model forecasts through the Open-Meteo Air Quality API (src.air_quality), cached for
  15 minutes per place. Every value is a model estimate, never a station reading, and the page says so.
- Index: the place's regional standard by default (US AQI, European AQI or India's NAQI), or the
  viewer's saved choice. Each number is printed with the name of its standard (src.aqi).
- Cloudzy Core doesn't predict air quality; nothing here comes from it.
- On failure the page explains why and offers a retry; it never shows invented values.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import air_quality as AQ
from src import air_view as AV
from src import app_data, aqi, browser_prefs, nav
from src import place_select as PS
from src import preferences as P
from src import theme as T
from src import ui
from src.data_collection import DataCollectionError

st.html(AV.CSS)
st.html(T.page_header("Air Quality", "Forecast · air-quality intelligence", "air",
                      "What's in the air, how it's expected to change, and what the index standard says about it."))

prefs = P.load()
now = pd.Timestamp.now(tz="UTC")
place = PS.current(prefs)
if place is None:
    st.stop()

where_col, pick_col = st.columns([4, 1.3], vertical_alignment="bottom")
where_col.markdown(f"**:material/location_on: {place.label}**")
with pick_col:
    PS.picker(prefs, "aq")

# --- Index standard ------------------------------------------------------------------------------
regional, regional_note = aqi.default_standard(place.country_code)
labels = {"auto": f"Regional ({regional.short})", "us_epa": "US AQI", "eu_eaqi": "European AQI",
          "in_naqi": "India NAQI"}
choice = st.segmented_control("Index standard", P.AQI_CHOICES, default=prefs.aqi_standard,
                              format_func=lambda k: labels.get(k, k), key="aq_standard",
                              help="Indices use different formulas and categories, so their numbers can't be "
                                   "compared with each other. Your choice is saved in this browser.")
if choice and choice != prefs.aqi_standard:
    prefs.aqi_standard = choice
    P.save(prefs)
    browser_prefs.flush()
standard, standard_note = aqi.resolve_standard(prefs.aqi_standard, place.country_code)

# --- Data ----------------------------------------------------------------------------------------
loading = st.empty()
loading.html(T.skeleton(300) + T.skeleton(120))
try:
    data = app_data.air(place)
except (AQ.AirQualityError, DataCollectionError) as exc:
    loading.empty()
    st.error(f"We couldn't get air-quality data for {place.label}. {exc} Cloudzy doesn't show an index it can't "
             "back with data.", icon=":material/cloud_off:")
    if st.button("Try again", icon=":material/refresh:", key="retry-aq"):
        app_data.air.clear()
        st.rerun()
    st.stop()
loading.empty()

reading = AQ.reading(data, standard, now)
short_note = "regional default" if prefs.aqi_standard == "auto" else "your choice"
st.html(AV.hero_html(place.label, reading, data, now, short_note))
st.caption(f"{standard_note} {standard.method} {AQ.KIND_NOTE} {AQ.DOMAIN_NOTE}")
if reading.value is None:
    st.warning(reading.note or "No current index value.", icon=":material/help:")

# --- Explanation and pollutants ------------------------------------------------------------------
facts_col, pol_col = st.columns([1, 1.35], gap="large")
with facts_col:
    st.html(T.eyebrow("What the data show", "insights"))
    st.html(AV.facts_html(AQ.current_factors(data, standard, now)))
    st.caption("Facts read from the model values; Cloudzy doesn't infer causes such as wind or rain clearing the air.")
with pol_col:
    st.html(T.eyebrow("Pollutants · this hour", "science"))
    st.html(AV.pollutants_html(data, now, AQ.rolling_mean))
    st.caption(f"Bars compare each pollutant's recent mean with the {aqi.WHO_SOURCE} level for the same averaging "
               "period. Guideline levels are long-term health targets, not safety limits.")

# --- Trend ---------------------------------------------------------------------------------------
hours_ahead = int((data.coverage_end - now.tz_convert(data.hourly.index.tz).floor("h")) / pd.Timedelta(hours=1))
st.html(T.section("Hourly trend", "monitoring", f"Forecast to {data.coverage_end:%a %d %b %H:%M} · about "
                  f"{max(hours_ahead, 0)} h ahead"))
colors = ui.palette()
idx_tab, pm_tab, gas_tab, tbl_tab = st.tabs(["Index", "Particles", "Gases", "Table"])
with idx_tab:
    if data.hourly[standard.column].notna().any():
        st.plotly_chart(AV.aqi_trend_chart(data, standard, now, colors), width="stretch", theme="streamlit",
                        config=ui.CHART_CONFIG)
    else:
        st.info(f"{standard.short} has no values for this place.", icon=":material/help:")
with pm_tab:
    st.plotly_chart(AV.pollutant_chart(data, ("pm2_5", "pm10"), now, colors, "Particles (hourly)"), width="stretch",
                    theme="streamlit", config=ui.CHART_CONFIG)
with gas_tab:
    st.plotly_chart(AV.pollutant_chart(data, ("nitrogen_dioxide", "ozone"), now, colors, "NO₂ and ozone (hourly)"),
                    width="stretch", theme="streamlit", config=ui.CHART_CONFIG)
with tbl_tab:
    st.dataframe(AV.aq_table(data, list(aqi.STANDARDS.values())), hide_index=True, width="stretch")
st.caption("Grey dotted lines: the model's values for earlier hours (not measurements). Solid lines: the forecast. "
           "Gaps mean the model has no value for those hours; nothing is filled in. Shaded bands are the "
           f"{standard.short} categories.")

# --- Daily outlook -------------------------------------------------------------------------------
st.html(T.section("Daily outlook", "calendar_month", standard.name))
days = AQ.daily_summary(data, standard, now)
if days.empty:
    st.info("The forecast has no upcoming hours with index values.", icon=":material/help:")
else:
    st.html(AV.days_html(days, standard))
    st.caption("Lowest and highest hourly index per local day, from the current hour on. A short last day means the "
               "model's forecast ends that day. Forecasts several days ahead are less certain than the next hours, "
               "and CAMS doesn't publish an uncertainty range for this data, so none is shown.")
nav.page_link("app_pages/outdoor.py", "Plan time outside with Outdoor Intelligence", ":material/directions_run:")

# --- About the data ------------------------------------------------------------------------------
with st.expander("About this data, the index standards and their limits"):
    st.markdown(
        f"- **Source:** {AQ.SOURCE_LABEL}. {AQ.DOMAIN_NOTE} CAMS European runs update every 24 hours "
        "(4-day forecast) and CAMS Global every 12 hours (5-day forecast).\n"
        "- **Model estimates, not observations.** Values describe a grid cell of about 11-45 km, not your street. "
        "Busy roads, fires or local industry can make real levels much higher or lower. Cloudzy has no "
        "monitoring-station source configured, so it shows no observations.\n"
        f"- **Index used here:** {standard.name}. {standard.method} Source: [{standard.source}]({standard.source_url}).\n"
        "- **Indices aren't interchangeable.** A US AQI of 150 and an India NAQI of 150 describe different "
        "pollution levels and categories. Cloudzy never relabels one as another.\n"
        "- **Cloudzy Core doesn't predict air quality.** It is a temperature model for New Delhi. Every forecast on "
        "this page comes from CAMS. Cloudzy checked whether its own model could add something; the available "
        "history is itself model output with no independent measurements to evaluate against, so no AQI model "
        "was built (see docs/AIR_QUALITY.md).")
    st.markdown(f"**{standard.name} categories and Cloudzy's activity guidance** (everyone / sensitive groups):")
    st.dataframe(aqi.categories_table(standard), hide_index=True, width="stretch")
    st.caption("ok = no change needed · caution = shorter, easier, or more breaks · avoid = postpone or move indoors.")

if data.warnings:
    with st.expander(f"Data notes ({len(data.warnings)})"):
        st.caption("Values that were unavailable or implausible in the provider response. They're shown as gaps or "
                   "“—”, never filled in.")
        for w in data.warnings:
            st.markdown(f"- {w}")

st.divider()
st.caption(f"{AQ.ATTRIBUTION} Index categories and colours follow each standard's own publication. Cloudzy is an "
           "experimental project, not an official air-quality service: follow your local authority's advisories.")
