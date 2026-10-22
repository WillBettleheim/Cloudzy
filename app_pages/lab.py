"""Model Lab (Phase E): how good is Cloudzy Core, really?

Reads only committed artifacts: reports/lab/ (built by `python -m src.lab_data`)
and the experiment reports. It never fits a model, so it runs anywhere the
repository is checked out.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import streamlit as st

from src import artifacts, config, intervals, lab_ui, monitoring, registry
from src.lab_data import LAB_DIR, MULTISTEP_REPORT, load_predictions
from src.ui import CHART_CONFIG, figure_table
from src import theme as T

TZ = config.DELHI.local_timezone
COMPACT_REPORT = config.REPORTS_DIR / f"compact_{config.DELHI.slug}.json"
SHIFT_REPORT = config.REPORTS_DIR / f"shift_{config.DELHI.slug}_{config.REANALYSIS.model}_vs_{config.LIVE_ARCHIVE.model}.json"
MODELS = ["persistence", "diurnal_baseline", "linear_regression", "gradient_boosting", "random_forest"]


@st.cache_data(show_spinner=False)
def load_lab():
    reports = sorted(config.REPORTS_DIR.glob("evaluation_*_era5_seamless*_h1.json"))
    return {
        "evaluations": {p.name: json.loads(p.read_text()) for p in reports},
        "phase_a": load_predictions(LAB_DIR / "phase_a_test_2025.csv.gz"),
        "serving": load_predictions(LAB_DIR / "serving_test_2025.csv.gz"),
        "bias": pd.read_csv(LAB_DIR / "shift_bias_by_hour.csv"),
        "manifest": json.loads((LAB_DIR / "manifest.json").read_text()),
        "multistep": json.loads(MULTISTEP_REPORT.read_text()),
        "shift": json.loads(SHIFT_REPORT.read_text()),
        "compact": json.loads(COMPACT_REPORT.read_text()) if COMPACT_REPORT.exists() else None,
        "importance": pd.read_csv(LAB_DIR / "feature_importance.csv") if (LAB_DIR / "feature_importance.csv").exists() else None,
    }


def chart(fig) -> None:
    """Draw a chart and offer the same numbers as a table (screen readers, colour-vision differences, copying)."""
    st.plotly_chart(fig, width="stretch", theme="streamlit", config=CHART_CONFIG)
    out = figure_table(fig)
    if out is None:
        return
    df, cut = out
    title = fig.layout.title.text if fig.layout.title and fig.layout.title.text else ""
    with st.expander(f"Chart data as a table{': ' + title if title else ''}"):
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"value": st.column_config.NumberColumn(format="%.3f")})
        if cut:
            st.caption(f"Showing the first {len(df):,} rows only.")


st.html(T.page_header("Model Lab", "Cloudzy Core · offline evaluation", "science"))
st.caption("How good is Cloudzy Core, really? The research results come from the untouched 2025 test year; the "
           "deployed model's own tests and its previous version are under **Registry**.")

try:
    with st.spinner("Loading the lab notebooks…"):
        lab = load_lab()
except FileNotFoundError as exc:
    st.error(f"Lab data is missing ({exc.filename}). Build it with `python -m src.lab_data` "
             "after running the Phase A and B experiments.", icon=":material/science:")
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
                "Explainability", "Live tracking", "Learn", "Limitations", "Registry", "Uncertainty",
                "Station check"])

# --- Scoreboard --------------------------------------------------------------------
with tabs[0]:
    serving_meta = artifacts.read_metadata(config.MODELS_DIR / "cloudzy_core_delhi_serving_compact.joblib")
    if serving_meta:
        trained = serving_meta["models"]["1"]["trained_on"]
        st.info(f"**Model in the live app:** `{serving_meta['artifact_version']}` · horizons "
                f"{', '.join(f'+{h} h' for h in serving_meta['horizons_h'])} · trained on {trained[2]:,} hours "
                f"({trained[0][:10]} → {trained[1][:10]}) · target: {serving_meta['target']}. Its live record is on "
                "the **Predictions** page.", icon=":material/rocket_launch:")
    else:
        st.warning("The live model's metadata file (`models/cloudzy_core_delhi_serving_compact.json`) is missing, "
                   "so its version can't be shown. The app won't serve a model it can't verify.",
                   icon=":material/warning:")
    main_report = lab["evaluations"].get(f"evaluation_{config.RUN_TAG}_h1.json")
    if main_report:
        # Headline: the selected model against persistence, straight from the saved report (offline test only).
        sel = main_report["selected"]["family"]
        tm = main_report["test_metrics"]
        test_split = main_report["splits"]["test"]
        st.markdown(f"**Offline test · {test_split['first_issue_time_utc'][:10]} → "
                    f"{test_split['last_issue_time_utc'][:10]} · {tm[sel]['n']:,} hourly predictions, 1 hour ahead**")
        with T.two_up("lab_headline"):
            h1, h2, h3, h4 = st.columns(4)
        h1.metric("Selected model", lab_ui.MODEL_NAMES.get(sel, sel),
                  help=main_report["selected"]["reason"])
        h2.metric("Test MAE", f"{tm[sel]['mae_c']:.3f} °C",
                  delta=f"{tm[sel]['mae_c'] - tm['persistence']['mae_c']:+.3f} °C vs persistence",
                  delta_color="inverse", help="Mean absolute error: the typical size of a miss. Lower is better.")
        h3.metric("Test RMSE", f"{tm[sel]['rmse_c']:.3f} °C",
                  delta=f"{tm[sel]['rmse_c'] - tm['persistence']['rmse_c']:+.3f} °C vs persistence",
                  delta_color="inverse", help="Root mean squared error: like MAE, but large misses count more.")
        h4.metric("Persistence baseline MAE", f"{tm['persistence']['mae_c']:.3f} °C",
                  help="\"The next hour equals this hour.\" The bar every model must clear.")
        with st.container(border=True):
            st.markdown("**Model card · Cloudzy Core 1-hour model (Delhi)**")
            card = lab_ui.model_card(main_report)
            st.markdown("\n".join(f"- **{k}:** {v}" for k, v in card.items()))
            st.caption("Values come straight from the saved evaluation report (`python -m src.pipeline evaluate` "
                       "re-checks them). The live app serves a compact gradient-boosting version of this "
                       "task for live inputs: see *Live setting*.")
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
    others = [r for name, r in lab["evaluations"].items() if name != f"evaluation_{config.RUN_TAG}_h1.json"]
    if others:
        st.markdown("##### Other locations (research runs)")
        st.dataframe(lab_ui.locations_table([main_report] + others if main_report else others), hide_index=True,
                     width="stretch", column_config={c: st.column_config.NumberColumn(format="%.3f")
                                                     for c in ("Model MAE °C", "Model RMSE °C", "Persistence MAE °C",
                                                               "Diurnal baseline MAE °C")})
        st.caption("The same pipeline (`python -m src.pipeline all --city …`), trained and tested separately for "
                   "each place on its own reanalysis data. These show the method carries over, but each model is "
                   "valid only for its own grid cell, and none of them is used in the live app, which serves "
                   "Delhi only. Errors differ by climate: a strong daily cycle (Delhi, Mumbai) gives a model more "
                   "to learn than Reykjavik's muted one.")

# --- Predictions vs observed ---------------------------------------------------------
with tabs[1]:
    first, last = a.index.min().tz_convert(TZ).date(), a.index.max().tz_convert(TZ).date()
    picked = st.date_input("Period (local dates, 2025)", value=(dt.date(2025, 5, 18), dt.date(2025, 5, 24)),
                           min_value=first, max_value=last, format="YYYY-MM-DD")
    if not isinstance(picked, tuple) or len(picked) != 2:
        st.info("Pick an end date to show the period.", icon=":material/calendar_month:")
    else:
        start, end = picked
        local = a.index.tz_convert(TZ)
        window = a[(local.date >= start) & (local.date <= end)]
        if window.empty:
            st.info("No test hours in that period.", icon=":material/calendar_month:")
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
        st.info("Feature importance hasn't been computed yet. Run `python -m src.lab_data`.",
                icon=":material/science:")
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

# --- Live tracking ------------------------------------------------------------------------
with tabs[6]:
    st.markdown("Live predictions are recorded and later checked against the reanalysis on the **Predictions** "
                "page. This tab shows the result for the model the app serves now, kept separate from the "
                "historical test evaluation above; other model versions are on the Predictions page.")
    live_version = serving_meta["artifact_version"] if serving_meta else None
    live_records = monitoring.query(model_version=live_version) if live_version else monitoring.query().iloc[0:0]
    tracked = monitoring.aggregate(live_records)
    live_counts = monitoring.status_counts(live_records)
    waiting = live_counts[monitoring.WAIT_TARGET] + live_counts[monitoring.WAIT_REFERENCE]
    st.caption(f"Model `{live_version or 'unknown'}`: {len(live_records)} records, {tracked['n']} evaluated, "
               f"{waiting} waiting normally for their target hour or the reference data.")
    if tracked["n"] == 0:
        st.info("No live predictions from this model have been evaluated yet. The reference data arrives about "
                f"{config.REANALYSIS.lag_days} days after each prediction's target hour.", icon=":material/hourglass_top:")
    else:
        st.markdown(f"**{tracked['n']} evaluated predictions** from {tracked['issue_times']} issue times "
                    f"({tracked['target_from_utc'][:10]} → {tracked['target_to_utc'][:10]}, UTC)."
                    + ("" if tracked["conclusive"] else " Far too few to be conclusive."))
        st.dataframe(pd.DataFrame(tracked["by_horizon"]), hide_index=True, width="stretch")
        if tracked.get("skill") and tracked["skill"].get("mae_skill_vs_provider") is not None:
            st.caption(f"MAE skill vs the IFS value: {tracked['skill']['mae_skill_vs_provider']:+.2f} on "
                       f"{tracked['skill']['n']} predictions (1 − MAE Cloudzy / MAE IFS).")
        if tracked.get("interval_coverage"):
            ic = tracked["interval_coverage"]
            st.caption(f"{ic['level']:.0%} range coverage on tracked predictions: {ic['coverage']:.0%} of {ic['n']}.")

# --- Learn ------------------------------------------------------------------------------
with tabs[7]:
    st.markdown("""
#### How does Cloudzy Core make a prediction?
It takes the last 25 hours of weather at its Delhi grid cell (temperature, humidity, pressure, wind and rain), plus
the time of day and year, and turns them into 27 numbers called *features*. Since October 2026 it adds two more:
what ECMWF's forecast from about two days earlier said the target hour would be, and how far that is from now. A
gradient-boosting model, one per forecast horizon, estimates how much the temperature will **change** from now.
That change is added to the current temperature. The configuration was tuned on the second half of 2025 and
tested on January–August 2026, data it never saw while learning; the deployed models were then retrained on all
data through August 2026 and checked on September.

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
with tabs[8]:
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
        "- **Uncertainty is limited to the deployed temperature model.** It has validated 80% and 95% ranges "
        "(see *Uncertainty*); these cover model error on the replay, not input delays or reanalysis artefacts. "
        "The research models have average errors only.\n"
        "- **No official warnings.** Neither Cloudzy nor the app's rain indicator is a weather warning. "
        "Use national weather services for safety decisions.")

# --- Registry --------------------------------------------------------------------------------
with tabs[9]:
    st.markdown("Every forecasting system in Cloudzy. **ML models** are listed from their artifact metadata; the "
                "**other systems** are providers, baselines and rules, listed so none of them is mistaken for a "
                "Cloudzy prediction.")
    entries = registry.model_entries()
    st.dataframe(pd.DataFrame([{
        "Model": e.id.removeprefix("cloudzy_core_"), "Status": registry.STATUS_LABELS[e.status].split(" (")[0],
        "Artifact": e.artifact, "Reproduced": e.reproduced,
        "Algorithm": e.algorithm, "Target": f"{e.target} ({e.unit})", "Where": e.location,
        "Horizons": ", ".join(f"+{h} h" for h in e.horizons_h), "Inputs → target": f"{e.inputs_source} → {e.target_source}",
        "Features": e.feature_count, "Version": e.version} for e in entries]), hide_index=True, width="stretch")
    st.caption("Artifact: *verified* = the file is in this checkout and matches its recorded SHA-256, so predictions "
               "can be reproduced here; *metadata only* = documented but the file isn't here; *checksum mismatch* = "
               "changed after it was described (not trusted). Reproduced: the recorded test metric was recomputed from the "
               "artifact and cached data and matched (`python -m src.reproduce`, no retraining).")
    for e in entries:
        with st.expander(f"{e.id} · {registry.STATUS_LABELS[e.status]}"):
            st.markdown(f"**Validation:** {e.validation}")
            st.markdown("**Training data:** " + "; ".join(
                f"+{h} h: {v['first'][:10]} → {v['last'][:10]} ({v['rows']:,} rows)" for h, v in sorted(e.trained.items())))
            st.markdown(f"**Parameters:** `{json.dumps(e.parameters)}` · **Libraries:** "
                        + ", ".join(f"{k} {v}" for k, v in e.library_versions.items()))
            if e.metrics:
                st.dataframe(pd.DataFrame(e.metrics).rename(columns=str.capitalize), hide_index=True, width="stretch")
                st.caption("Metrics from different periods, places or horizons aren't comparable with each other.")
            st.markdown("**Limitations:** " + "; ".join(e.limitations) + ".")
            with st.popover(f"Feature schema ({e.feature_count})"):
                st.write(", ".join(e.features))
    dep = next((e for e in entries if e.status == "deployed"), None)
    if dep and dep.previous_version:
        pv = dep.previous_version
        st.markdown("##### Previous version → current version")
        st.markdown(f"`{pv['artifact_version']}` → `{dep.version}`, replaced on {pv['replaced_on']}. {pv['reason']}. "
                    f"The previous file is kept as `{pv['file']}`.")
        st.dataframe(pd.DataFrame([{"Ahead": f"+{r['horizon_h']} h", "Previous MAE °C": r["previous_mae_c"],
                                    "Current MAE °C": r["current_mae_c"],
                                    "Change °C": round(r["current_mae_c"] - r["previous_mae_c"], 4)}
                                   for r in pv["comparison"]]), hide_index=True, width="stretch")
        st.caption(f"Both scored on {pv['holdout_month']}, a month neither was trained on. One month is a small "
                   "sample; the decision itself rests on the 2026-01..08 test below.")
    mos_path = config.REPORTS_DIR / f"mos_{config.DELHI.slug}.json"
    if mos_path.exists():
        mos = json.loads(mos_path.read_text())
        with st.expander("Upgrade experiment: lagged ECMWF forecast as an input (src.mos)"):
            rows = []
            for h, r in mos["horizons"].items():
                t = r["test"]
                d = t["candidate_minus_control"]
                rows.append({"Ahead": f"+{h} h", "Previous config MAE °C": t["control"]["mae_c"],
                             "New config MAE °C": t["candidate"]["mae_c"],
                             "Difference °C [95% CI]": f"{d['mae_diff_c']:+.3f} [{d['ci95_low_c']:+.3f}, "
                                                       f"{d['ci95_high_c']:+.3f}]",
                             "Lagged ECMWF alone °C": t["lagged_ifs_forecast"]["mae_c"],
                             "Ridge, same inputs °C": t["ridge_candidate_features"]["mae_c"],
                             "Persistence °C": t["persistence"]["mae_c"], "n": t["candidate"]["n"]})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            dec = mos["decision"]
            st.caption(f"Test period {mos['spans']['test'][0]} to {mos['spans']['test'][1]} (exclusive). "
                       "Confidence intervals: week-block bootstrap. Decision rule fixed before results were seen: "
                       "lower MAE at every horizon, a significant gain at four or more, and no horizon worse by "
                       f"over 0.05 °C in September 2026. Result: {'adopted' if dec['adopt'] else 'not adopted'} "
                       f"({dec['horizons_with_significant_gain']} of 6 horizons significant).")
    others = registry.other_artifacts()
    if others:
        st.markdown("##### Other model files in `models/`")
        st.dataframe(pd.DataFrame([{"File": o.file, "Size MB": o.size_mb, "Role": o.role} for o in others]),
                     hide_index=True, width="stretch")
        st.caption("No metadata file describes these, so the Lab makes no claims about them. Backups are earlier "
                   "deployed versions kept for rollback; undocumented files are old research artifacts the app "
                   "never loads.")
    st.markdown("##### Other systems (not Cloudzy ML predictions)")
    st.dataframe(pd.DataFrame([{"System": x.name, "Kind": x.kind, "Predicts or provides": x.predicts,
                                "Source": x.source, "Evaluation": x.evaluated} for x in registry.SYSTEMS]),
                 hide_index=True, width="stretch")

# --- Uncertainty -------------------------------------------------------------------------------
with tabs[10]:
    rep = intervals._report()
    current_sha = intervals.bundle_sha256()
    if not rep:
        st.info("No interval study yet. Run `python -m src.intervals`.", icon=":material/science:")
    else:
        valid_for_bundle = rep.get("provenance", {}).get("bundle_sha256") == current_sha
        st.markdown(
            "The deployed model shows an **80% range** with each prediction: where the reference temperature "
            "fell 80% of the time in validation. It comes from **split conformal prediction**: the spread of "
            "the model's own errors on data it wasn't fitted on, per horizon, asymmetric (the model tends to miss "
            "more in one direction), with a finite-sample correction.")
        if not valid_for_bundle:
            st.warning("This study was made for a different model file than the one now deployed, so the app shows "
                       "no ranges until `python -m src.intervals` is run again.", icon=":material/warning:")
        rows = []
        for h, r in rep["horizons"].items():
            for lv in ("0.80", "0.95"):
                mc, dp = r["method_check"]["levels"][lv], r["deployed"]["levels"][lv]
                rows.append({"Ahead": f"+{h} h", "Level": f"{float(lv):.0%}",
                             "Range around prediction °C": f"{dp['q_lo_c']:+.2f} to {dp['q_hi_c']:+.2f}",
                             "Width °C": dp["validation"]["mean_width_c"],
                             "Coverage, method check (2025 H2)": f"{mc['validation']['coverage']:.1%}",
                             "n (2025 H2)": mc["validation"]["n"],
                             "Coverage, deployed model (2026-09)": f"{dp['validation']['coverage']:.1%}",
                             "n (2026-09)": dp["validation"]["n"]})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.markdown(
            "- **Method check:** model fitted on the data before 2025 (from Feb 2024 for a model using the lagged "
            "ECMWF forecast, when that archive starts), ranges calibrated on its errors in Jan-Jun 2025, coverage "
            "measured on Jul-Dec 2025.\n"
            "- **Deployed ranges:** calibrated on the errors of a fit on the data before 2026 during Jan-Aug 2026 "
            "(the most recent "
            "out-of-sample errors), then checked on the deployed model's predictions for September 2026, a month it "
            "never saw. One month of hourly, correlated values: evidence, not proof.\n"
            "- **Reading the results:** coverage above the level means the ranges are a little wide (conservative); "
            "below would mean too narrow. Check each row: a range a few points under its level in one month is "
            "within what one correlated month can show; a large shortfall would mean recalibrating.\n"
            "- **What a range doesn't include:** delays in live inputs (the replay is a best case), errors in the "
            "reanalysis itself, and any place other than the Delhi grid cell. Coverage on live predictions is tracked "
            "on the Predictions page once they are evaluated.\n"
            "- **Different kinds of uncertainty:** the range describes the model's error against the reference. "
            "Natural variability inside one grid cell, and the reference's own uncertainty, aren't quantified.")
        mc_rows = [{"Ahead": f"+{h} h", **{dt.date(2025, int(m), 1).strftime("%b"): f"{v:.0%}" for m, v in
                    r["method_check"]["levels"]["0.80"]["validation_by_month"].items()}}
                   for h, r in rep["horizons"].items()]
        st.markdown("**80% range coverage by month in the method check (2025 H2)**: does it hold across seasons?")
        st.dataframe(pd.DataFrame(mc_rows), hide_index=True, width="stretch")
        st.caption(f"Study run {rep['provenance']['created_at_utc'][:16]} UTC for bundle "
                   f"{rep['provenance']['bundle_sha256'][:12]} · `python -m src.intervals` reproduces it from cached data.")

# --- Station check -------------------------------------------------------------------------------------
with tabs[11]:
    st_path = config.REPORTS_DIR / f"station_eval_{config.DELHI.slug}.json"
    if not st_path.exists():
        st.info("No station evaluation yet. Run `python -m src.station_eval`.", icon=":material/science:")
    else:
        se = json.loads(st_path.read_text())
        prim = se["primary"]
        sta = se["stations"][prim]
        qc = se["quality_control"][prim]
        st.markdown(
            "Everything else in this Lab scores Cloudzy against the **reanalysis**, its own training target. Here it "
            f"is compared with **real thermometer readings**: routine METAR reports from {sta['name']} "
            f"({prim}), {qc['distance_km_from_grid_point']} km from Cloudzy's grid point. The test period is "
            f"{se['spans']['test'][0]} to {se['spans']['test'][1]} (exclusive), the same rows for every model. "
            "These questions were written down before the results were seen.")
        rows = []
        for h, r in se["horizons"].items():
            p = r["test"][prim]
            d = p["candidate_minus_ifs_at_target"]
            rows.append({"Ahead": f"+{h} h", "Cloudzy (current)": p["candidate"]["mae_c"],
                         "Cloudzy (previous)": p["control"]["mae_c"], "ECMWF IFS value": p["ifs_at_target"]["mae_c"],
                         "Reanalysis (Cloudzy's target)": p["reanalysis"]["mae_c"],
                         "Cloudzy − IFS [95% CI]": f"{d['mae_diff_c']:+.3f} [{d['ci95_low_c']:+.3f}, {d['ci95_high_c']:+.3f}]",
                         "n": p["candidate"]["n"]})
        st.markdown("**Average error against the station (MAE, °C)**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.warning("**Against the thermometer, the raw ECMWF value is closer than Cloudzy at every horizon.** "
                   "Cloudzy learns to reproduce the reanalysis, and the reanalysis is itself further from the "
                   "station than ECMWF is. The October 2026 upgrade still helps here (closer than the previous "
                   "version from +3 h on), but it improves agreement with the reanalysis, not with a thermometer.",
                   icon=":material/thermostat:")
        b = se["horizons"]["1"]["test"][prim]["bias_by_local_hour"]
        st.markdown("**Why: average offset from the station by local hour (model − station, °C, +1 h)**")
        st.dataframe(pd.DataFrame([{"Local hour": f"{int(k):02d}:00", "Cloudzy": v["candidate"],
                                    "ECMWF IFS": v["ifs_at_target"], "Reanalysis": v["reanalysis"]}
                                   for k, v in b.items()]), hide_index=True, width="stretch")
        st.caption("At night the reanalysis is 1.5–1.8 °C colder than the station, the urban heat island a ~9 km "
                   "grid cell doesn't capture, and Cloudzy inherits about 1 °C of it. ECMWF is nearly unbiased at "
                   "night but too warm around midday.")
        st.markdown(
            "- **Measurement noise:** METAR temperatures are whole degrees, and the same station's SYNOP reports "
            "differ from its METARs by 0.6 °C on average. No model can be expected to reach zero.\n"
            "- **Checks:** the station data match NOAA's independent copy of the same reports at 99.9% of hours. The "
            "rebuilt models reproduce the experiment's reanalysis scores exactly before any station comparison.\n"
            "- **A closer station agrees:** Safdarjung (3.4 km away) only reports from July 2026, but shows the same "
            "ordering.\n"
            "- **What would fix it:** training Cloudzy against station readings instead of the reanalysis. That "
            "would be a new experiment, with its own rules fixed before the results are seen.")
        st.caption(f"{se['provenance']['observations']}. Run {se['provenance']['created_at_utc'][:16]} UTC · "
                   "`python -m src.station_eval` reproduces it.")
    sm_path = config.REPORTS_DIR / f"station_model_{config.DELHI.slug}.json"
    if sm_path.exists():
        sm = json.loads(sm_path.read_text())
        st.markdown("##### Experiment: training Cloudzy on the station readings")
        st.markdown(
            "Two candidates were trained to predict Palam's own readings, with the rules written before any result "
            "was seen (`src/station_model.py`). Same splits and test rows as above.\n"
            "- **A:** the current inputs only.\n"
            "- **B:** also the station's latest readings at prediction time. Live, these are published within "
            "minutes of the hour.")
        rows = []
        for h, r in sm["horizons"].items():
            t = r["test"]
            d = t["candidate_B_minus_raw_ecmwf"]
            rows.append({"Ahead": f"+{h} h", "A: station target": t["candidate_A"]["mae_c"],
                         "B: + station inputs": t["candidate_B"]["mae_c"], "ECMWF value": t["raw_ecmwf"]["mae_c"],
                         "ECMWF + hourly correction": t["bias_corrected_ecmwf"]["mae_c"],
                         "Station persistence": t["station_persistence"]["mae_c"],
                         "B − ECMWF [95% CI]": f"{d['mae_diff_c']:+.3f} [{d['ci95_low_c']:+.3f}, {d['ci95_high_c']:+.3f}]"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        dec = sm["decision"]
        st.info(f"**Verdict under the pre-set rules: A {dec['A']['verdict']}; B {dec['B']['verdict']}.** "
                "B roughly halves ECMWF's error 1 hour ahead and is significantly better up to 3 hours, also at "
                "Safdarjung, but is no better at 6 hours and worse at 12-24 hours. The rules required a win at every "
                "horizon. The ECMWF baseline is optimistic: the archive holds the newest run for each hour, which "
                "a model at time t couldn't have seen, and that matters most at long horizons.",
                icon=":material/science:")
        st.caption("Nothing was deployed: a station-target model would change what Cloudzy predicts and how live "
                   "predictions are evaluated. `python -m src.station_model` reproduces this.")

st.divider()
st.caption("Weather data by Open-Meteo.com (CC BY 4.0). Reanalysis: Copernicus Climate Change Service "
           "(ERA5, ERA5-Land). Cloudzy is an experimental student project, not an operational weather service.")
