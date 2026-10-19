"""Cloudzy Lab (Phase E): how good is Cloudzy Core, really?

Reads only committed artifacts: reports/lab/ (built by `python -m src.lab_data`)
and the experiment reports. It never fits a model, so it runs anywhere the
repository is checked out.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import streamlit as st

from src import config, history, lab_ui, monitoring
from src import preferences as P
from src import units as U
from src.data_collection import DataCollectionError
from src.lab_data import LAB_DIR, MULTISTEP_REPORT, load_predictions
from src.provider import ProviderError, search_locations
from src.ui import CHART_CONFIG

TZ = config.DELHI.local_timezone
COMPACT_REPORT = config.REPORTS_DIR / f"compact_{config.DELHI.slug}.json"
SHIFT_REPORT = config.REPORTS_DIR / f"shift_{config.DELHI.slug}_{config.REANALYSIS.model}_vs_{config.LIVE_ARCHIVE.model}.json"
MODELS = ["persistence", "diurnal_baseline", "linear_regression", "gradient_boosting", "random_forest"]


@st.cache_data(show_spinner=False)
def load_lab():
    return {
        "phase_a": load_predictions(LAB_DIR / "phase_a_test_2025.csv.gz"),
        "serving": load_predictions(LAB_DIR / "serving_test_2025.csv.gz"),
        "bias": pd.read_csv(LAB_DIR / "shift_bias_by_hour.csv"),
        "manifest": json.loads((LAB_DIR / "manifest.json").read_text()),
        "multistep": json.loads(MULTISTEP_REPORT.read_text()),
        "shift": json.loads(SHIFT_REPORT.read_text()),
        "compact": json.loads(COMPACT_REPORT.read_text()) if COMPACT_REPORT.exists() else None,
        "importance": pd.read_csv(LAB_DIR / "feature_importance.csv") if (LAB_DIR / "feature_importance.csv").exists() else None,
    }


@st.cache_data(ttl=600, show_spinner=False)
def cached_places(query: str):
    return search_locations(query, count=8)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_history(place, start, end):
    return history.fetch_history(place, start, end)


def chart(fig) -> None:
    st.plotly_chart(fig, width="stretch", theme="streamlit", config=CHART_CONFIG)


st.markdown("## 🧪 Cloudzy Lab")
st.caption("How good is Cloudzy Core, really? Every number here comes from the untouched 2025 test year.")

try:
    with st.spinner("Loading the lab notebooks…"):
        lab = load_lab()
except FileNotFoundError as exc:
    st.error(f"Lab data is missing ({exc.filename}). Build it with `python -m src.lab_data` "
             "after running the Phase A and B experiments.", icon="🧪")
    st.stop()

colors = lab_ui.lab_palette()
a, serving, manifest = lab["phase_a"], lab["serving"], lab["manifest"]
checks = manifest["reproduction_checks"]

with st.container(border=True):
    st.markdown(
        "**What Cloudzy Core predicts:** Delhi's 2 m temperature, as given by ERA5/ERA5-Land "
        "**reanalysis** for one grid cell. A reanalysis blends observations with a weather model. It is "
        "not a thermometer reading.  \n"
        "**How it was tested:** models were trained on 2023, tuned on 2024, refitted on 2023–2024 and "
        "scored once on 2025. Nothing on this page influenced any modelling choice.")
    st.caption(f"Data rebuilt {manifest['generated_at_utc'][:10]} · {len(checks)} of {len(checks)} test "
               "MAEs reproduce the experiment reports exactly · predictions rounded to 0.01 °C")

compare = st.selectbox(
    "Compare Cloudzy (orange) with", [m for m in MODELS if m != lab_ui.CLOUDZY],
    index=1, format_func=lambda m: lab_ui.MODEL_NAMES[m],
    help="The comparison model is drawn in blue on every chart.")

tabs = st.tabs(["Scoreboard", "Predictions vs observed", "Error breakdown", "Live setting", "Multi-step",
                "Explainability", "Live monitoring", "History explorer", "Learn", "Limitations"])

# --- Scoreboard --------------------------------------------------------------------
with tabs[0]:
    table = lab_ui.summary(a, MODELS)
    chart(lab_ui.mae_bar_chart(table, colors))
    shown = table.assign(model=table["model"].map(lab_ui.MODEL_NAMES))
    st.dataframe(shown, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.3f") for c in shown.columns if "°C" in c})
    base = table.set_index("model")["MAE °C"]
    st.markdown(
        f"**Verdict.** Cloudzy's random forest beats every baseline on 2025. Its MAE is "
        f"{base['random_forest']:.3f} °C, against {base['persistence']:.3f} °C for persistence "
        f"(\"the next hour equals this hour\") and {base['diurnal_baseline']:.3f} °C for the diurnal baseline "
        "(\"this hour plus the usual change at this time of day and year\"). Most of the gain over "
        "persistence comes from simply knowing the daily cycle. The model's own contribution is the "
        "smaller gap to the diurnal baseline. That gap is real: a 95% week-block bootstrap interval "
        "excludes zero.")

# --- Predictions vs observed ---------------------------------------------------------
with tabs[1]:
    first, last = a.index.min().tz_convert(TZ).date(), a.index.max().tz_convert(TZ).date()
    picked = st.date_input("Period (local dates, 2025)", value=(dt.date(2025, 5, 18), dt.date(2025, 5, 24)),
                           min_value=first, max_value=last, format="YYYY-MM-DD")
    if not isinstance(picked, tuple) or len(picked) != 2:
        st.info("Pick an end date to show the period.", icon="📅")
    else:
        start, end = picked
        local = a.index.tz_convert(TZ)
        window = a[(local.date >= start) & (local.date <= end)]
        if window.empty:
            st.info("No test hours in that period.", icon="📅")
        else:
            chart(lab_ui.timeseries_chart(window, compare, colors, TZ))
            s = lab_ui.summary(window, [compare, lab_ui.CLOUDZY])
            st.caption("In this period: " + " · ".join(
                f"{lab_ui.MODEL_NAMES[r.model]} MAE {r._2:.2f} °C" for r in s.itertuples()))
    left, right = st.columns(2)
    with left:
        chart(lab_ui.scatter_chart(a, lab_ui.CLOUDZY, colors))
    with right:
        chart(lab_ui.error_histogram(a, compare, colors))
    with st.expander("Cloudzy's 10 largest errors in 2025"):
        worst = lab_ui.worst_hours(a, lab_ui.CLOUDZY)
        worst.index = worst.index.tz_convert(TZ).strftime("%Y-%m-%d %H:%M IST")
        local_idx = lab_ui.worst_hours(a, lab_ui.CLOUDZY).index.tz_convert(TZ)
        n_pre = int(sum(m in (4, 5, 6) for m in local_idx.month))
        n_1130 = int(sum((h, mi) == (11, 30) for h, mi in zip(local_idx.hour, local_idx.minute)))
        worst.index = local_idx.strftime("%Y-%m-%d %H:%M IST")
        st.dataframe(worst.round(2), width="stretch")
        st.caption(
            f"{n_pre} of these 10 fall in the pre-monsoon months (April–June), when storms bring abrupt changes. "
            f"{n_1130} are issued at 11:30 IST. On some days the reanalysis jumps 3–4 °C between 06 and 07 UTC "
            "and then goes flat (for example 19.8 → 24.0 → 24.5 °C on 2025-02-20). That looks like an artifact "
            "of how the reanalysis is produced rather than weather, and no model can anticipate it.")

# --- Error trends ------------------------------------------------------------------
with tabs[2]:
    days = st.select_slider("Rolling window", options=[7, 14, 30], value=14, format_func=lambda d: f"{d} days")
    chart(lab_ui.trend_chart(lab_ui.rolling_mae(a, [compare, lab_ui.CLOUDZY], TZ, days), compare, colors, days))
    left, right = st.columns(2)
    with left:
        chart(lab_ui.grouped_chart(lab_ui.mae_by(a, [compare, lab_ui.CLOUDZY], TZ, "local_hour"), compare, colors,
                                   "MAE by local hour of issue", "Hour (IST)"))
    with right:
        chart(lab_ui.grouped_chart(lab_ui.mae_by(a, [compare, lab_ui.CLOUDZY], TZ, "month"), compare, colors,
                                   "MAE by month", "Month"))
    by_month = lab_ui.mae_by(a, [lab_ui.CLOUDZY], TZ, "month")[lab_ui.CLOUDZY].sort_values(ascending=False)
    top = [dt.date(2025, int(m), 1).strftime("%B") for m in by_month.index[:2]]
    st.caption(f"Cloudzy's error is highest in {top[0]} ({by_month.iloc[0]:.3f} °C) and {top[1]} "
               f"({by_month.iloc[1]:.3f} °C), and lowest in {dt.date(2025, int(by_month.index[-1]), 1):%B} "
               f"({by_month.iloc[-1]:.3f} °C).")
    if "precip_at_issue_mm" in a:
        left, right = st.columns(2)
        by_temp = lab_ui.mae_by_group(a, [compare, lab_ui.CLOUDZY], "temperature")
        by_rain = lab_ui.mae_by_group(a, [compare, lab_ui.CLOUDZY], "rain")
        with left:
            chart(lab_ui.group_bar_chart(by_temp, compare, colors, "MAE by temperature range (observed)"))
        with right:
            chart(lab_ui.group_bar_chart(by_rain, compare, colors, "MAE when raining vs dry (at issue time)"))
        r = by_rain[lab_ui.CLOUDZY]
        st.caption(f"Cloudzy's error is {r['raining when issued'] / r['dry when issued']:.1f}× higher in hours that "
                   f"start with rain ({r['raining when issued']:.3f} °C over {int(by_rain.loc['raining when issued', 'hours'])} "
                   f"hours) than in dry hours ({r['dry when issued']:.3f} °C over {int(by_rain.loc['dry when issued', 'hours'])} "
                   "hours): showers cool the air quickly and unpredictably. Weather condition is taken from rain at the "
                   "time of the prediction, which the model knows; the temperature range uses the observed value.")

# --- Live setting ---------------------------------------------------------------------
with tabs[3]:
    st.markdown("In the live app, Cloudzy has no reanalysis to work from: it gets **ECMWF IFS** model "
                "output instead. These charts replay archived IFS inputs for 2025.")
    compact = lab["compact"]
    deployed = {int(h): v["compact_test"]["mae_c"] for h, v in compact["horizons"].items()} if compact else None
    chart(lab_ui.horizon_chart(serving, colors, deployed))
    if compact:
        st.caption(f"The app serves the **compact** models: gradient boosting, {compact['bundle_size_mb']} MB, "
                   "chosen on 2024 under a size limit. The research random forests are 1.8 GB. On 2025 the two "
                   "differ by at most 0.03 °C at any horizon, and no difference is statistically significant.")
    pers = lab_ui.horizon_mae(serving)["persistence"]
    st.caption("Persistence is left off the chart because it would squash the scale: its MAE is "
               + ", ".join(f"{v:.2f} °C at {h} h" for h, v in pers.items()) + ".")
    rows = []
    for h, v in lab["multistep"]["part2_serving_live_inputs_to_reanalysis"].items():
        d = v["direct_vs_provider"]  # research forest vs IFS
        rows.append({"Hours ahead": int(h), "Research forest MAE °C": v["direct"]["mae_c"],
                     **({"Deployed compact MAE °C": compact["horizons"][h]["compact_test"]["mae_c"]} if compact else {}),
                     "IFS value (optimistic) MAE °C": v["provider_optimistic"]["mae_c"],
                     "Difference °C": d["mae_diff_c"], "95% CI low": d["ci95_low_c"], "95% CI high": d["ci95_high_c"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.3f") for c in rows[0] if c != "Hours ahead"})
    st.markdown(
        "- Cloudzy is closer to the reanalysis than IFS's own values up to 12 hours ahead (the interval "
        "excludes zero). At 24 hours the difference is not significant.\n"
        "- This is a **bias correction toward the reanalysis**. It doesn't show that Cloudzy forecasts the "
        "weather better than ECMWF.\n"
        "- The IFS baseline is optimistic: in the archive, its value for t+h can come from a run started after t.")
    chart(lab_ui.bias_chart(lab["bias"], colors))
    t = lab["shift"]["source_comparison"]["temperature_2m"]
    st.caption(f"IFS − reanalysis temperature bias: {t['2023']['bias']:+.2f} °C in 2023, "
               f"{t['2024']['bias']:+.2f} °C in 2024, {t['2025']['bias']:+.2f} °C in 2025. The live source "
               "drifts, which is why the serving models are refitted monthly.")

# --- Multi-step -----------------------------------------------------------------------
with tabs[4]:
    part1 = lab["multistep"]["part1_reanalysis_direct_vs_recursive"]
    chart(lab_ui.multistep_chart(part1, colors))
    st.markdown(
        "**Direct** trains one model per horizon. **Recursive** feeds the 1-hour model its own "
        "predictions, holding humidity, pressure, wind and rain at their last values. Direct wins at "
        "every horizon from 2 hours on, and the gap widens. At 24 hours the recursive strategy is worse "
        "than persistence, because its errors compound and its frozen inputs go stale.")

# --- Limitations ------------------------------------------------------------------------
# --- Explainability -------------------------------------------------------------------
with tabs[5]:
    imp = lab["importance"]
    if imp is None:
        st.info("Feature importance hasn't been computed yet. Run `python -m src.lab_data`.", icon="🧪")
    else:
        st.markdown(
            "**Which inputs does the 1-hour model lean on?** *Permutation importance* shuffles one input's values "
            "across the 2025 test year, so the link to the right answer is broken. It then measures how much worse "
            "the predictions get. A bigger increase means the model relies on that input more.")
        chart(lab_ui.importance_chart(imp, colors, "learned change"))
        full = imp[(imp["view"] == "full prediction") & (imp["feature"] == "temperature_2m")]
        if len(full):
            st.caption(f"Not shown: shuffling the *current temperature* raises the MAE by "
                       f"{full['mae_increase_c'].iloc[0]:.1f} °C. The model predicts \"current temperature + a learned "
                       "change\", so the current temperature is its starting point, not something it learned. The "
                       "chart shows what the learned-change part relies on.")
        with st.expander("How to read this, and what it does *not* show"):
            st.markdown(
                "- **Association, not causation.** A high score means the model *uses* that input to predict well. "
                "It doesn't mean that input *causes* the temperature change.\n"
                "- **Related inputs share credit.** Temperatures 1, 2 and 3 hours ago carry similar information. "
                "Shuffling one lets the others fill in, so each can look less important than the group really is.\n"
                "- **Time of day dominates the learned change.** Temperature in Delhi follows a strong daily cycle, "
                "and the sine and cosine of the hour tell the model where in that cycle it is.\n"
                f"- **Method:** {lab['manifest'].get('feature_importance', {}).get('method', 'permutation importance')}, "
                "measured on data the model never trained on. The whiskers show the spread across repeats.")
        st.dataframe(imp[imp["view"] == "learned change"].assign(
            description=lambda d: d["feature"].map(lab_ui.FEATURE_DESCRIPTIONS)
        )[["feature", "description", "mae_increase_c", "std_c"]], hide_index=True, width="stretch",
            column_config={"mae_increase_c": st.column_config.NumberColumn("MAE increase °C", format="%.4f"),
                           "std_c": st.column_config.NumberColumn("± std °C", format="%.4f")})

# --- Live monitoring ---------------------------------------------------------------------
with tabs[6]:
    st.markdown("Every live Cloudzy prediction the app makes is logged **on this computer**. About "
                f"{config.REANALYSIS.lag_days} days later, when the reanalysis for those hours has been published, "
                "`python -m src.monitoring` scores the predictions against it. This tab only reads that saved result.")
    summ = monitoring.load_summary()
    logged = monitoring.load_predictions()
    if summ is None and logged.empty:
        st.info("No live predictions logged yet. Open the weather page for New Delhi to make one.", icon="📭")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Predictions logged", len(logged))
        c2.metric("Issue times", logged["issue_time_utc"].nunique() if len(logged) else 0)
        c3.metric("Refusals (incomplete inputs)", summ["refusals"] if summ else "—")
        if not summ:
            st.info("Run `python -m src.monitoring` to score the predictions whose reanalysis is available.", icon="⏳")
        elif summ["scores"]["n"] == 0:
            st.info(f"Nothing to score yet: {summ['matured_predictions']} predictions are old enough. The reanalysis "
                    f"is published about {summ['reanalysis_lag_days']} days late. Last checked "
                    f"{summ['evaluated_at_utc'][:16].replace('T', ' ')} UTC.", icon="⏳")
        else:
            sc = summ["scores"]
            st.markdown(f"**{sc['n']} matured predictions**, valid {sc['valid_from_utc'][:10]} → {sc['valid_to_utc'][:10]} "
                        "(UTC). Compare the live MAE with the 2025 test MAE the app shows as \"average error\":")
            st.dataframe(pd.DataFrame(sc["by_horizon"]), hide_index=True, width="stretch")
            st.dataframe(pd.DataFrame(sc["by_temperature_range"]), hide_index=True, width="stretch")
            st.caption("A few days of predictions is a small sample. Expect live errors to be larger than the 2025 "
                       "replay, because real-time inputs come from older forecast runs.")

# --- History explorer -------------------------------------------------------------------
with tabs[7]:
    st.markdown(f"Explore past weather anywhere since 1940. **Data type:** {history.LABEL}.")
    h_query = st.text_input("Place", value="New Delhi", key="hist_query").strip()
    today = dt.date.today()
    if len(h_query) < 2:
        st.info("Type at least two letters to search for a place.", icon="🔎")
    else:
        try:
            h_places = cached_places(h_query)
        except (ProviderError, DataCollectionError) as exc:
            h_places = None
            st.error(f"Location search is unavailable right now. {exc}", icon="⚠️")
        if h_places is not None and not h_places:
            st.warning(f"No places match “{h_query}”.", icon="🗺️")
        elif h_places:
            h_place = st.selectbox("Choose the right match", h_places, format_func=lambda p: p.label, key="hist_place")
            picked = st.date_input("Dates (local)", value=(today - dt.timedelta(days=37), today - dt.timedelta(days=7)),
                                   min_value=history.MIN_DATE, max_value=today, key="hist_dates", format="YYYY-MM-DD")
            if not isinstance(picked, tuple) or len(picked) != 2:
                st.info("Pick an end date.", icon="📅")
            else:
                start, end = picked
                try:
                    history.validate_range(start, end, today)
                    with st.spinner(f"Loading {(end - start).days + 1} days for {h_place.name}…"):
                        hist = cached_history(h_place, start, end)
                except ValueError as exc:
                    st.warning(str(exc), icon="📅")
                    hist = None
                except ProviderError as exc:
                    st.error(str(exc), icon="⚠️")
                    hist = None
                if hist is not None:
                    hunits = P.load().unit_override() or U.default_for_country(h_place.country_code)
                    tconv = lambda c: U.temperature(c, hunits.temperature)  # noqa: E731
                    daily = history.daily_summary(hist)
                    ov = history.overview(daily)
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Mean temperature", f"{tconv(ov['mean_c']):.1f} {hunits.temperature}")
                    c2.metric("Hottest day", f"{tconv(ov['hottest'][1]):.0f}{hunits.temperature[0]}",
                              help=f"{ov['hottest'][0]:%d %b %Y}")
                    c3.metric("Coldest night", f"{tconv(ov['coldest'][1]):.0f}{hunits.temperature[0]}",
                              help=f"{ov['coldest'][0]:%d %b %Y}")
                    tp = U.precipitation(ov["total_precip_mm"], hunits.precipitation)
                    c4.metric("Total rain", "—" if tp is None else
                              f"{tp:.{U.precipitation_digits(hunits.precipitation)}f} {hunits.precipitation}",
                              help=f"{ov['wet_days']} days with at least 1 mm")
                    chart(lab_ui.history_temperature_chart(daily, colors, hunits.temperature, tconv))
                    var = st.segmented_control("Also show", ["Rain", "Wind", "Humidity"], default="Rain", key="hist_var")
                    if var == "Rain":
                        chart(lab_ui.history_bar_chart(daily, "precipitation_mm", colors, "Daily rain", hunits.precipitation,
                                                       lambda v: U.precipitation(v, hunits.precipitation)))
                    elif var == "Wind":
                        chart(lab_ui.history_line_chart(daily, "gust_max_kmh", colors, "Strongest gust each day", hunits.wind,
                                                        lambda v: U.wind(v, hunits.wind)))
                    elif var == "Humidity":
                        chart(lab_ui.history_line_chart(daily, "humidity_mean_pct", colors, "Average humidity each day", "%"))
                    if (today - end).days < history.RECENT_DAYS_NOTE:
                        st.caption("The most recent days may come from operational weather-model analyses, before the "
                                   "ERA5 reanalysis is published, and can change slightly later.")
                    partial = daily[daily["hours"] < 23]
                    if len(partial):
                        st.caption(f"{len(partial)} day(s) have incomplete hours and are summarised from what's available.")

# --- Learn ------------------------------------------------------------------------------
with tabs[8]:
    st.markdown("""
#### How does Cloudzy Core make a prediction?
It takes the last 25 hours of weather at its Delhi grid cell (temperature, humidity, pressure, wind and rain), plus
the time of day and year, and turns them into 27 numbers called *features*. A gradient-boosting model, one per
forecast horizon, estimates how much the temperature will **change** from now. That change is added to the current
temperature. The model learned these patterns from 2023–2024 and was tested on 2025, data it never saw while
learning.

#### What do MAE and RMSE mean?
- **MAE (mean absolute error):** the average size of the miss, ignoring direction. "MAE 0.24 °C" means
  predictions were off by about a quarter of a degree on average.
- **RMSE (root mean squared error):** squares each miss before averaging, so a few big misses count more.
  RMSE is always at least as large as MAE. A big gap between them means occasional large errors.

#### Why does a baseline matter?
A model is only useful if it beats something simple. **Persistence** ("the next hour will be like this hour") and
the **diurnal baseline** ("this hour plus the usual change at this time of day") cost nothing. In Delhi, the daily
cycle alone explains most of the change. Cloudzy's real contribution is the smaller gap between it and the
diurnal baseline, and the Lab reports that gap with a bootstrap interval.

#### Why is weather prediction hard?
The atmosphere is chaotic: tiny differences grow into large ones. A single grid cell's recent history can't see a
storm forming 50 km away. That's why errors jump in the pre-monsoon storm season and in rainy hours, and why
forecasts days ahead need physical models of the whole atmosphere, like the provider's.

#### Why do errors vary by place and lead time?
Every place has its own climate, terrain and weather regimes, and Cloudzy was only trained and tested on Delhi.
Errors also grow with lead time: the further ahead, the less recent local data tells you.

#### Why doesn't past performance guarantee future accuracy?
The test year is a sample. Next year's weather can be different, and the live inputs Cloudzy gets come from a
weather model that is upgraded over time (the Lab's live setting shows its bias drifting). That's why Cloudzy is
refitted monthly and monitored live, and why the app labels its errors as **historical test performance**.
""")

# --- Limitations ------------------------------------------------------------------------
with tabs[9]:
    st.markdown(
        "- **Reanalysis targets, not thermometers.** Errors against a real station would be larger, and "
        "one ~9 km grid cell smooths out city effects such as the urban heat island.\n"
        "- **The target has its own artifacts.** On some days the reanalysis jumps 3–4 °C between 06 and "
        "07 UTC and then goes flat, which looks like a production artifact rather than weather. These "
        "jumps are among Cloudzy's largest misses.\n"
        "- **One place, one test year.** Results describe Delhi in 2025. They don't carry over to other "
        "cities or years without new tests.\n"
        "- **Live accuracy is a best case.** The live-setting replay uses archived IFS inputs. A real-time "
        "system sees older forecast runs for the latest hours.\n"
        "- **Point predictions only.** Average errors and bootstrap intervals describe the *comparison* "
        "between models, not the uncertainty of any single prediction.\n"
        "- **No official warnings.** Neither Cloudzy nor the app's rain indicator is a weather warning. "
        "Use national weather services for safety decisions.")

st.divider()
st.caption("Weather data by Open-Meteo.com (CC BY 4.0). Reanalysis: Copernicus Climate Change Service "
           "(ERA5, ERA5-Land). Cloudzy is an experimental student project, not an operational weather service.")
