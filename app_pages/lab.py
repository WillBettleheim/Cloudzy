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

from src import config, lab_ui
from src.lab_data import LAB_DIR, MULTISTEP_REPORT, load_predictions
from src.ui import CHART_CONFIG

TZ = config.DELHI.local_timezone
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
    }


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

tabs = st.tabs(["Scoreboard", "Predictions vs observed", "Error trends", "Live setting", "Multi-step",
                "Limitations"])

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

# --- Live setting ---------------------------------------------------------------------
with tabs[3]:
    st.markdown("In the live app, Cloudzy has no reanalysis to work from: it gets **ECMWF IFS** model "
                "output instead. These charts replay archived IFS inputs for 2025.")
    chart(lab_ui.horizon_chart(serving, colors))
    pers = lab_ui.horizon_mae(serving)["persistence"]
    st.caption("Persistence is left off the chart because it would squash the scale: its MAE is "
               + ", ".join(f"{v:.2f} °C at {h} h" for h, v in pers.items()) + ".")
    rows = []
    for h, v in lab["multistep"]["part2_serving_live_inputs_to_reanalysis"].items():
        d = v["direct_vs_provider"]
        rows.append({"Hours ahead": int(h), "Cloudzy MAE °C": v["direct"]["mae_c"],
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
with tabs[5]:
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
