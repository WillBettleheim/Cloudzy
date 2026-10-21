"""Weather history: past weather anywhere since 1940, by day and by month, and a
comparison with the same dates in earlier years.

Source: Open-Meteo Historical Weather API (reanalysis, src.history). These are
model-based reconstructions of past weather, not station observations, and are
kept separate from forecasts and from Cloudzy Core's predictions.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from src import app_data, history, lab_ui
from src import theme as T
from src import preferences as P
from src import units as U
from src.data_collection import DataCollectionError
from src.provider import ProviderError, search_locations
from src.ui import CHART_CONFIG, palette


@st.cache_data(ttl=600, show_spinner=False)
def cached_places(query: str):
    return search_locations(query, count=8)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def cached_history(place, start, end):
    return history.fetch_history(place, start, end)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def cached_reference(place, start, end, years):
    return history.fetch_reference(place, start, end, years)


def chart(fig) -> None:
    st.plotly_chart(fig, width="stretch", theme="streamlit", config=CHART_CONFIG)


st.html(T.page_header("History & Trends", "Forecast · the past, reconstructed", "history"))
st.caption(f"Past weather anywhere since 1940. **Data type:** {history.LABEL}. Grid cells are roughly 9–25 km "
           "across, so values can differ from a nearby weather station. This is not a forecast.")

colors = palette()
viewing = app_data.current_place()   # the place on the Weather page, if any
q_col, r_col = st.columns([2, 3], vertical_alignment="bottom")
h_query = q_col.text_input("Place", value=viewing.name if viewing else "New Delhi", key="hist_query",
                           icon=":material/search:").strip()
RANGES = {"Last 30 days": 30, "Last 90 days": 90, "Last 12 months": 365, "Custom": None}
range_pick = r_col.segmented_control("Time range", list(RANGES), default="Last 30 days", key="hist_range",
                                     help="Presets end a week ago, when the reanalysis is complete.")
today = dt.date.today()
if len(h_query) < 2:
    st.info("Type at least two letters to search for a place.", icon=":material/search:")
    st.stop()
try:
    with st.spinner("Looking for that place…"):
        h_places = cached_places(h_query)
except (ProviderError, DataCollectionError) as exc:
    st.error(f"Location search is unavailable right now. {exc}", icon=":material/warning:")
    st.stop()
if not h_places:
    st.warning(f"No places match “{h_query}”.", icon=":material/travel_explore:")
    st.stop()

h_place = st.selectbox("Choose the right match", h_places, format_func=lambda p: p.label, key="hist_place")
span_days = RANGES.get(range_pick or "Last 30 days")
if span_days is None:
    picked = st.date_input("Dates (local)", value=(today - dt.timedelta(days=37), today - dt.timedelta(days=7)),
                           min_value=history.MIN_DATE, max_value=today, key="hist_dates", format="YYYY-MM-DD",
                           help=f"Up to {history.MAX_SPAN_DAYS} days, from {history.MIN_DATE:%Y} onward.")
else:
    end_default = today - dt.timedelta(days=7)
    picked = (end_default - dt.timedelta(days=span_days - 1), end_default)
if not isinstance(picked, tuple) or len(picked) != 2:
    st.info("Pick an end date.", icon=":material/calendar_month:")
    st.stop()
start, end = picked
try:
    history.validate_range(start, end, today)
    with st.spinner(f"Loading {(end - start).days + 1} days for {h_place.name}…"):
        hist = cached_history(h_place, start, end)
except ValueError as exc:
    st.warning(str(exc), icon=":material/calendar_month:")
    st.stop()
except ProviderError as exc:
    st.error(str(exc), icon=":material/warning:")
    st.stop()

hunits = P.load().unit_override() or U.default_for_country(h_place.country_code)
tu = hunits.temperature
tconv = lambda c: U.temperature(c, tu)  # noqa: E731
rconv = lambda v: U.precipitation(v, hunits.precipitation)  # noqa: E731
rdig = U.precipitation_digits(hunits.precipitation)
daily = history.daily_summary(hist)
if daily.empty:
    st.info("The archive has no data for these dates.", icon=":material/inbox:")
    st.stop()
ov = history.overview(daily)

st.html(T.section(f"{h_place.label} · {start:%d %b %Y} – {end:%d %b %Y}", "location_on",
                  f"{(end - start).days + 1} days · reanalysis"))
c1, c2, c3, c4 = st.columns(4)
c1.metric("Mean temperature", f"{tconv(ov['mean_c']):.1f} {tu}")
c2.metric("Hottest day", f"{tconv(ov['hottest'][1]):.0f}{tu[0]}", help=f"{ov['hottest'][0]:%d %b %Y}")
c3.metric("Coldest night", f"{tconv(ov['coldest'][1]):.0f}{tu[0]}", help=f"{ov['coldest'][0]:%d %b %Y}")
tp = U.precipitation(ov["total_precip_mm"], hunits.precipitation)
c4.metric("Total rain", "—" if tp is None else f"{tp:.{rdig}f} {hunits.precipitation}",
          help=f"{ov['wet_days']} days with at least 1 mm")

# Keyed, so the open tab survives reruns (for example after loading the comparison).
daily_tab, monthly_tab, compare_tab = st.tabs(["Daily", "Monthly", "Compared with earlier years"],
                                              key="hist_tab", on_change="rerun")

with daily_tab:
    chart(lab_ui.history_temperature_chart(daily, colors, tu, tconv))
    var = st.segmented_control("Also show", ["Rain", "Wind", "Humidity"], default="Rain", key="hist_var")
    if var == "Rain":
        chart(lab_ui.history_bar_chart(daily, "precipitation_mm", colors, "Daily rain", hunits.precipitation, rconv))
    elif var == "Wind":
        chart(lab_ui.history_line_chart(daily, "gust_max_kmh", colors, "Strongest gust each day", hunits.wind,
                                        lambda v: U.wind(v, hunits.wind)))
    elif var == "Humidity":
        chart(lab_ui.history_line_chart(daily, "humidity_mean_pct", colors, "Average humidity each day", "%"))
    with st.expander("Daily values as a table"):
        st.dataframe(pd.DataFrame({
            "Date": daily.index.strftime("%Y-%m-%d"),
            f"Mean {tu}": daily["temp_mean_c"].map(tconv), f"Low {tu}": daily["temp_min_c"].map(tconv),
            f"High {tu}": daily["temp_max_c"].map(tconv),
            f"Rain {hunits.precipitation}": daily["precipitation_mm"].map(rconv),
            "Hours of data": daily["hours"]}), hide_index=True, width="stretch",
            column_config={c: st.column_config.NumberColumn(format="%.1f") for c in
                           (f"Mean {tu}", f"Low {tu}", f"High {tu}")})
    if (today - end).days < history.RECENT_DAYS_NOTE:
        st.caption("The most recent days may come from operational weather-model analyses, before the ERA5 "
                   "reanalysis is published, and can change slightly later.")
    partial = daily[daily["hours"] < 23]
    if len(partial):
        st.caption(f"{len(partial)} day(s) have incomplete hours and are summarised from what's available.")

with monthly_tab:
    monthly = history.monthly_summary(daily)
    if len(monthly) < 2:
        st.info("Choose dates that cover at least two calendar months to compare months.", icon=":material/date_range:")
    else:
        st.dataframe(pd.DataFrame({
            "Month": [p.strftime("%b %Y") for p in monthly.index],
            "Days selected": [f"{d} of {m}" for d, m in zip(monthly["days"], monthly["days_in_month"])],
            f"Mean {tu}": monthly["temp_mean_c"].map(tconv), f"Highest {tu}": monthly["temp_max_c"].map(tconv),
            f"Lowest {tu}": monthly["temp_min_c"].map(tconv),
            f"Rain {hunits.precipitation}": monthly["precipitation_mm"].map(rconv),
            "Wet days (≥ 1 mm)": monthly["wet_days"]}), hide_index=True, width="stretch",
            column_config={c: st.column_config.NumberColumn(format="%.1f") for c in
                           (f"Mean {tu}", f"Highest {tu}", f"Lowest {tu}")})
        if (monthly["days"] < monthly["days_in_month"]).any():
            st.caption("Months only partly inside your dates are summarised from the selected days only, so their "
                       "rain totals aren't whole-month totals.")

with compare_tab:
    st.caption("Compares the selected dates with the **same calendar dates in earlier years**, from the same "
               "reanalysis archive at the same grid point. It's a comparison with recent years, not an official "
               "climate normal, and one period says nothing on its own about long-term change.")
    years = st.segmented_control("Earlier years to compare with", history.REFERENCE_CHOICES, default=10,
                                 key="hist_ref_years", format_func=lambda n: f"{n} years")
    if years and st.toggle("Load the comparison", key="hist_ref_on",
                           help=f"One extra request for about {years} years of daily values. Cached for 6 hours."):
        try:
            with st.spinner(f"Loading the same dates in the previous {years} years…"):
                ref_daily = cached_reference(h_place, start, end, years)
            cmp = history.reference_comparison(ref_daily, start, end, years)
        except ProviderError as exc:
            st.error(str(exc), icon=":material/warning:")
            cmp = None
        if cmp is not None and not cmp["valid"]:
            st.info(cmp["reason"], icon=":material/info:")
        elif cmp is not None:
            span = f"{cmp['years'][0]}–{cmp['years'][-1]}"
            diff = U.temperature_delta(abs(cmp["difference_c"]), tu)
            word = "warmer" if cmp["difference_c"] > 0 else "cooler"
            a, b = st.columns(2)
            a.metric("Mean temperature, selected dates", f"{tconv(cmp['mean_c']):.1f} {tu}",
                     delta=f"{'+' if cmp['difference_c'] > 0 else '−'}{diff:.1f} {tu} vs {span} average",
                     delta_color="off")
            b.metric(f"Same dates, {span} ({cmp['n_years']} years)", f"{tconv(cmp['ref_mean_c']):.1f} {tu}",
                     help=f"Range across those years: {tconv(cmp['ref_low_c']):.1f} to {tconv(cmp['ref_high_c']):.1f} {tu}")
            st.markdown(
                f"These dates were **{diff:.1f} {tu} {word}** than the average of the same dates in {span}, and "
                f"warmer than **{cmp['warmer_than']} of those {cmp['n_years']} years**."
                if diff >= 0.05 else f"These dates were about as warm as the average of the same dates in {span}.")
            if cmp["rain_mm"] is not None and cmp["ref_rain_mean_mm"] is not None:
                st.markdown(f"Rain: **{rconv(cmp['rain_mm']):.{rdig}f} {hunits.precipitation}**, against an average "
                            f"of {rconv(cmp['ref_rain_mean_mm']):.{rdig}f} {hunits.precipitation} for the same dates "
                            f"(range {rconv(cmp['ref_rain_low_mm']):.{rdig}f}–{rconv(cmp['ref_rain_high_mm']):.{rdig}f}).")
            with st.expander("Each year as a table"):
                st.dataframe(pd.DataFrame({
                    "Year": list(cmp["per_year"]),
                    f"Mean {tu}": [tconv(v["mean_c"]) for v in cmp["per_year"].values()],
                    f"Rain {hunits.precipitation}": [rconv(v["rain_mm"]) for v in cmp["per_year"].values()]}),
                    hide_index=True, width="stretch",
                    column_config={f"Mean {tu}": st.column_config.NumberColumn(format="%.1f")})
            st.caption(f"Years with any missing day are left out. At least {history.MIN_REFERENCE_YEARS} complete "
                       "years are needed for a comparison.")

st.divider()
st.caption("Weather data by Open-Meteo.com (CC BY 4.0). ERA5 / ERA5-Land: Copernicus Climate Change Service.")
