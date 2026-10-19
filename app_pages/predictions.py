"""Prediction tracking page: every live Cloudzy Core prediction, and how it turned out."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from src import config, monitoring
from src import preferences as P
from src import units as U
from src.data_collection import DataCollectionError
from src.ui import CHART_CONFIG, palette

TZ = config.DELHI.local_timezone

st.markdown("## 📈 Prediction tracking")
st.caption("Every Cloudzy Core prediction the app actually makes is recorded on this computer, then checked once "
           "the reference data for its target hour exists.")

with st.expander("How tracking works"):
    st.markdown(
        f"- **What's recorded:** only valid predictions from the live model, one record per issue time and "
        f"horizon (re-renders and refreshes don't create duplicates). It includes the model version, the "
        f"prediction point (the Delhi training grid cell, never your own location), and the ECMWF IFS value "
        f"for the same hour.\n"
        f"- **How it's checked:** against **{monitoring.OBSERVATION_SOURCE}**, matched by {monitoring.MATCH_TOLERANCE}. "
        f"That reference is published about {config.REANALYSIS.lag_days} days late, so new predictions stay *pending* "
        f"until then. If it never appears within {monitoring.GIVE_UP_DAYS} days, the record is marked *unavailable*. "
        "Provider forecasts are never used as observations.\n"
        "- **Where it's stored:** a local SQLite file on this computer. Nothing is uploaded.")

units = P.load().unit_override() or U.METRIC
tu = units.temperature

c1, c2 = st.columns([1, 3], vertical_alignment="center")
if c1.button("Check matured predictions", icon="🔎", help="Fetch the reference data for predictions whose "
             "target hour is old enough, and evaluate them."):
    try:
        with st.spinner("Fetching reference data…"):
            st.session_state["track_eval"] = monitoring.evaluate()
    except (DataCollectionError, ValueError) as exc:
        st.error(f"Couldn't fetch reference data right now: {exc}. Nothing was changed; try again later.", icon="⚠️")
res = st.session_state.get("track_eval")
if res:
    c2.caption(f"Last check {res['evaluated_at_utc'][11:16]} UTC: {res['newly_evaluated']} evaluated, "
               f"{res['marked_unavailable']} marked unavailable, {res['still_pending']} still pending.")

all_records = monitoring.query()
if all_records.empty:
    st.info("No predictions recorded yet. Open the **Weather** page for New Delhi: Cloudzy Core records its "
            "predictions there automatically. Other places don't get Cloudzy predictions.", icon="📭")
    st.stop()

# --- Filters -----------------------------------------------------------------------------------
f1, f2, f3, f4 = st.columns([1.2, 1.6, 1.2, 1.6])
loc = f1.selectbox("Location", ["All"] + monitoring.distinct("location_id"),
                   format_func=lambda x: "Delhi grid cell" if x == monitoring.LOCATION_ID else x)
ver = f2.selectbox("Model version", ["All"] + monitoring.distinct("model_version"))
status = f3.selectbox("Status", ["All", "pending", "evaluated", "unavailable"])
first = all_records["target_time_utc"].min().tz_convert(TZ).date()
last = all_records["target_time_utc"].max().tz_convert(TZ).date()
picked = f4.date_input("Target dates (IST)", value=(first, last), min_value=first, max_value=last, format="YYYY-MM-DD")
if not isinstance(picked, tuple) or len(picked) != 2:
    st.info("Pick an end date.", icon="📅")
    st.stop()
start = pd.Timestamp(picked[0], tz=TZ)
end = pd.Timestamp(picked[1], tz=TZ) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
df = monitoring.query(location_id=None if loc == "All" else loc, model_version=None if ver == "All" else ver,
                      status=None if status == "All" else status, start=start, end=end)

counts = df["status"].value_counts()
m1, m2, m3, m4 = st.columns(4)
m1.metric("Records", len(df))
m2.metric("Evaluated", int(counts.get("evaluated", 0)))
m3.metric("Pending", int(counts.get("pending", 0)))
m4.metric("Refusals (incomplete inputs)", len(monitoring.refusals()))

# --- Aggregates (evaluated records only) ---------------------------------------------------------
agg = monitoring.aggregate(df)
st.markdown("#### Tracked accuracy")
if agg["n"] == 0:
    st.info("No evaluated predictions in this selection yet, so there's no tracked accuracy to show. Until there "
            "is, see the historical test results in **Cloudzy Lab**.", icon="⏳")
else:
    st.markdown(f"**{agg['n']} evaluated predictions** from **{agg['issue_times']} issue times**, target hours "
                f"{pd.Timestamp(agg['target_from_utc']).tz_convert(TZ):%d %b %Y %H:%M} → "
                f"{pd.Timestamp(agg['target_to_utc']).tz_convert(TZ):%d %b %Y %H:%M} IST.")
    if not agg["conclusive"]:
        st.warning(f"Only {agg['issue_times']} issue times so far. Errors from consecutive hours are strongly "
                   f"related, so this is far too little to draw conclusions; at least "
                   f"{monitoring.CONCLUSIVE_MIN_ISSUES} issue times are needed before this says much.", icon="🔬")
    rows = pd.DataFrame(agg["by_horizon"])
    shown = pd.DataFrame({
        "Ahead": [f"+{h} h" for h in rows["horizon_h"]], "Evaluated": rows["n"],
        f"MAE {tu}": [U.temperature_delta(v, tu) for v in rows["mae_c"]],
        f"RMSE {tu}": [U.temperature_delta(v, tu) for v in rows["rmse_c"]],
        f"Bias {tu}": [None if v is None else (U.temperature_delta(abs(v), tu) * (1 if v >= 0 else -1)) for v in rows["bias_c"]],
        f"IFS value MAE {tu}": [U.temperature_delta(v, tu) for v in rows["ifs_mae_c"]],
        f"2025 test MAE {tu} (historical)": [U.temperature_delta(v, tu) for v in rows["expected_mae_c"]],
    })
    st.dataframe(shown, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.2f") for c in shown.columns if tu in c})
    st.caption(f"Reference: {agg['observation_source']}. Matching: {agg['match_tolerance']}. The 2025 test MAE is "
               "the historical evaluation shown in Cloudzy Lab; the other columns come from live predictions.")
    ev = df[df["status"] == "evaluated"]
    import plotly.graph_objects as go
    from src.ui import _layout
    colors = palette()
    fig = go.Figure()
    fig.add_scatter(x=ev["observed"].map(lambda v: U.temperature(v, tu)), y=ev["predicted"].map(lambda v: U.temperature(v, tu)),
                    mode="markers", marker={"color": colors["cloudzy"], "size": 8},
                    text=[f"+{h} h" for h in ev["horizon_h"]], name="Cloudzy Core",
                    hovertemplate="%{text}: predicted %{y:.1f}, reference %{x:.1f}<extra></extra>")
    lo = float(min(ev["observed"].min(), ev["predicted"].min())) - 1
    hi = float(max(ev["observed"].max(), ev["predicted"].max())) + 1
    fig.add_scatter(x=[U.temperature(lo, tu), U.temperature(hi, tu)], y=[U.temperature(lo, tu), U.temperature(hi, tu)],
                    mode="lines", line={"color": colors["muted"], "dash": "dash"}, name="Perfect", hoverinfo="skip")
    fig = _layout(fig, colors, "Tracked predictions vs reference", tu, height=320)
    fig.update_xaxes(title_text=f"Reference (reanalysis) {tu}")
    fig.update_layout(hovermode="closest")
    st.plotly_chart(fig, width="stretch", theme="streamlit", config=CHART_CONFIG)
    st.caption("Chart: each dot is one evaluated prediction; dots on the dashed line were exact. The same values "
               "are in the table below.")

# --- Records ---------------------------------------------------------------------------------
st.markdown("#### Records")
if df.empty:
    st.info("No records match these filters.", icon="🔎")
else:
    table = pd.DataFrame({
        "Target (IST)": df["target_time_utc"].dt.tz_convert(TZ).dt.strftime("%d %b %H:%M"),
        "Issued (IST)": df["issue_time_utc"].dt.tz_convert(TZ).dt.strftime("%d %b %H:%M"),
        "Ahead": [f"+{h} h" for h in df["horizon_h"]],
        f"Cloudzy {tu}": [U.temperature(v, tu) for v in df["predicted"]],
        f"IFS forecast {tu}": [U.temperature(v, tu) for v in df["provider_value"]],
        "Status": df["status"],
        f"Reference {tu}": [U.temperature(v, tu) for v in df["observed"]],
        f"Error {tu}": [U.temperature_delta(v, tu) for v in df["abs_error"]],
        "Model version": df["model_version"],
    })
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.1f") for c in table.columns if c.endswith(tu)})
    st.download_button("Download these records (CSV)", table.to_csv(index=False).encode(), "cloudzy_predictions.csv",
                       "text/csv", icon="⬇️")

ref = monitoring.refusals()
if len(ref):
    with st.expander(f"Refusals ({len(ref)}): times Cloudzy declined to predict"):
        st.caption("Cloudzy refuses when its inputs are incomplete or can't be fetched, instead of guessing.")
        st.dataframe(ref.head(50), hide_index=True, width="stretch")

st.divider()
st.caption("Tracking data stays on this computer. Reference data: Open-Meteo (CC BY 4.0); ERA5-Land: Copernicus "
           "Climate Change Service.")
