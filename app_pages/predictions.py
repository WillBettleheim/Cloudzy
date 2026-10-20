"""Prediction tracking page: every live Cloudzy Core prediction, and how it turned out."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import config, monitoring
from src import theme as T
from src import preferences as P
from src import units as U
from src.ui import CHART_CONFIG, palette

TZ = config.DELHI.local_timezone
M = monitoring

st.markdown("## :material/query_stats: Prediction tracking")
st.caption("Every Cloudzy Core prediction the app actually makes is recorded, then checked once the reference data "
           "for its target hour exists.")

with st.expander("How tracking works"):
    st.markdown(
        f"- **What's recorded:** only valid predictions from the live model, one record per model version, issue "
        f"time and horizon (re-renders and refreshes don't create duplicates). Times are stored in UTC. A record "
        f"includes the model version, the prediction point (the Delhi training grid cell, never your own location), "
        f"and the ECMWF IFS value for the same hour.\n"
        f"- **How it's checked:** against **{M.OBSERVATION_SOURCE}**, matched by {M.MATCH_TOLERANCE}, same variable "
        f"(2 m temperature) and unit (°C). That reference is published about {config.REANALYSIS.lag_days} days late, "
        f"so a record is only checked from {config.REANALYSIS.lag_days + 1} days after its target date; before that "
        f"no request is made. If a checked hour still has no value, the record stays pending and is retried on later "
        f"checks; after {M.GIVE_UP_DAYS} days it's marked *unavailable*, with the reason. A failed request changes no "
        "record. Provider forecasts are never used as observations, and missing values are never filled in.\n"
        "- **Where it's stored:** a SQLite file on the computer running Cloudzy. It holds the model's predictions "
        "only, no visitor data, and is shared by everyone using this copy of the app.")

units = P.load().unit_override() or U.METRIC
tu = units.temperature


def ist(ts) -> str:
    return f"{pd.Timestamp(ts).tz_convert(TZ):%d %b %Y %H:%M} IST"


# --- Check now, and the last check (stored, so it survives reloads) ------------------------------------
c1, c2 = st.columns([1, 3], vertical_alignment="center")
if c1.button("Check matured predictions", icon=":material/search:", help="Fetch the reference data for predictions whose "
             "target hour is old enough, and evaluate them. Safe to press again: nothing is evaluated twice."):
    try:
        with st.spinner("Fetching reference data…"):
            M.evaluate()
    except Exception as exc:  # stored as the last check; no record was changed
        st.error(f"Couldn't fetch reference data right now ({type(exc).__name__}). Nothing was changed; "
                 "try again later.", icon=":material/warning:")
last = M.last_check()
if last:
    when = ist(last["evaluated_at_utc"])
    if last.get("outcome") == "failed":
        c2.warning(f"Last check {when} failed: {last.get('error')}. No record was changed.", icon=":material/warning:")
    elif last.get("outcome") == "nothing due":
        c2.caption(f"Last check {when}: no prediction was old enough to check, so no request was made.")
    else:
        ref_until = last.get("reference_until_utc")
        c2.caption(f"Last check {when}: {last['newly_evaluated']} evaluated, {last['marked_unavailable']} marked "
                   f"unavailable, {last['still_pending']} still pending."
                   + (f" Reference data then reached {ist(ref_until)}." if ref_until else ""))
else:
    c2.caption("No check has run yet. Press the button, or run `python -m src.monitoring` (it can be scheduled).")

all_records = M.query()
if all_records.empty:
    st.info("No predictions recorded yet. Open the **Weather** page for New Delhi: Cloudzy Core records its "
            "predictions there automatically. Other places don't get Cloudzy predictions.", icon=":material/inbox:")
    st.stop()

# --- Filters -----------------------------------------------------------------------------------
f1, f2, f3, f4 = st.columns([1.2, 1.6, 1.2, 1.6])
loc = f1.selectbox("Location", ["All"] + M.distinct("location_id"),
                   format_func=lambda x: "Delhi grid cell" if x == M.LOCATION_ID else x)
ver = f2.selectbox("Model version", ["All"] + M.distinct("model_version"))
status = f3.selectbox("Status", ["All", "pending", "evaluated", "unavailable"])
first = all_records["target_time_utc"].min().tz_convert(TZ).date()
last_day = all_records["target_time_utc"].max().tz_convert(TZ).date()
picked = f4.date_input("Target dates (IST)", value=(first, last_day), min_value=first, max_value=last_day,
                       format="YYYY-MM-DD")
if not isinstance(picked, tuple) or len(picked) != 2:
    st.info("Pick an end date.", icon=":material/calendar_month:")
    st.stop()
start = pd.Timestamp(picked[0], tz=TZ)
end = pd.Timestamp(picked[1], tz=TZ) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
df = M.classify(M.query(location_id=None if loc == "All" else loc, model_version=None if ver == "All" else ver,
                        status=None if status == "All" else status, start=start, end=end))

# --- Status breakdown ---------------------------------------------------------------------------
counts = M.status_counts(df)
waiting = counts[M.WAIT_TARGET] + counts[M.WAIT_REFERENCE]
attention = counts[M.DUE] + counts[M.CHECKED_MISSING] + counts[M.UNAVAILABLE]
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Records", len(df))
m2.metric("Evaluated", counts[M.EVALUATED])
m3.metric("Waiting (normal)", waiting, help="Target hour not reached yet, or the reference data isn't "
          "published yet. These need nothing from you.")
m4.metric("Due or needs attention", attention, help="Old enough to check: due on the next check, checked "
          "with the reference value still missing, or given up on.")
m5.metric("Refusals (incomplete inputs)", len(M.refusals()))

# Every status has an icon and a text label (never colour alone).
STATUS_STYLE = {
    M.EVALUATED: ("ok", "check_circle", "✔"), M.WAIT_TARGET: ("wait", "schedule", "◷"),
    M.WAIT_REFERENCE: ("wait", "hourglass_top", "◷"), M.DUE: ("warn", "update", "↻"),
    M.CHECKED_MISSING: ("warn", "report", "⚠"), M.UNAVAILABLE: ("risk", "block", "✕"),
}
explain = {
    M.EVALUATED: "Compared with the reference value.",
    M.WAIT_TARGET: "The predicted hour hasn't happened yet.",
    M.WAIT_REFERENCE: f"The hour has passed; the reanalysis is published about {config.REANALYSIS.lag_days} days "
                      "later. No request is made until then.",
    M.DUE: "Old enough to check; the next check (button above, or the scheduled command) evaluates it.",
    M.CHECKED_MISSING: "Checked, but the reference had no value for that hour yet. Retried on every check.",
    M.UNAVAILABLE: f"No reference value appeared within {M.GIVE_UP_DAYS} days. Excluded from accuracy.",
}
breakdown = pd.DataFrame({"Status": list(counts), "Records": list(counts.values()),
                          "What it means": [explain[k] for k in counts]})
st.html('<div style="display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 8px">' + "".join(
    T.chip(f"{k} · {v}", STATUS_STYLE[k][0], STATUS_STYLE[k][1], explain[k]) for k, v in counts.items() if v)
    + "</div>")
with st.expander("Why records are pending (status breakdown)", expanded=attention > 0):
    st.dataframe(breakdown, hide_index=True, width="stretch")
    pending = df[df["status"] == "pending"]
    if len(pending):
        st.caption(f"Pending records become checkable from {pending['checkable_from_utc'].min():%d %b %Y} "
                   f"(UTC dates) onward; the newest from {pending['checkable_from_utc'].max():%d %b %Y}.")

# --- Tracked accuracy: evaluated records only, one model version at a time ------------------------------
st.html(T.section("Tracked accuracy (live predictions)", "monitoring"))
st.caption("Kept separate from the offline test results in **Cloudzy Lab** (the 2025 test year). Model versions "
           "are never pooled: each has its own numbers.")
evaluated = df[df["status"] == "evaluated"]
if evaluated.empty:
    st.info("No evaluated predictions in this selection yet, so there's no tracked accuracy to show. Until there "
            "is, see the historical test results in **Cloudzy Lab**.", icon=":material/hourglass_top:")
else:
    import plotly.graph_objects as go

    from src.ui import _layout
    colors = palette()
    versions = sorted(evaluated["model_version"].unique(), key=lambda v: (v.startswith("unrecorded"), v))
    for version in versions:
        vdf = df[df["model_version"] == version]
        agg = M.aggregate(vdf)
        st.markdown(f"##### Model `{version}`")
        st.markdown(f"**{agg['n']} evaluated predictions** from **{agg['issue_times']} issue times**, target hours "
                    f"{ist(agg['target_from_utc'])} → {ist(agg['target_to_utc'])}.")
        if not agg["conclusive"]:
            st.warning(f"Only {agg['issue_times']} issue times so far. Errors from consecutive hours are strongly "
                       f"related, so this is far too little to judge the model; at least "
                       f"{M.CONCLUSIVE_MIN_ISSUES} issue times are needed before this says much.", icon=":material/biotech:")
        rows = pd.DataFrame(agg["by_horizon"])
        shown = pd.DataFrame({
            "Ahead": [f"+{h} h" for h in rows["horizon_h"]], "Evaluated": rows["n"],
            f"MAE {tu}": [U.temperature_delta(v, tu) for v in rows["mae_c"]],
            f"RMSE {tu}": [U.temperature_delta(v, tu) for v in rows["rmse_c"]],
            f"Bias {tu}": [None if v is None else (U.temperature_delta(abs(v), tu) * (1 if v >= 0 else -1))
                           for v in rows["bias_c"]],
            f"IFS value MAE {tu}": [U.temperature_delta(v, tu) for v in rows["ifs_mae_c"]],
            f"2025 test MAE {tu} (offline, for reference)": [U.temperature_delta(v, tu) for v in rows["expected_mae_c"]],
        })
        st.dataframe(shown, hide_index=True, width="stretch", column_config={
            c: st.column_config.NumberColumn(format="%.2f") for c in shown.columns if tu in c})
        ev = vdf[vdf["status"] == "evaluated"]
        fig = go.Figure()
        fig.add_scatter(x=ev["observed"].map(lambda v: U.temperature(v, tu)),
                        y=ev["predicted"].map(lambda v: U.temperature(v, tu)),
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
        st.plotly_chart(fig, width="stretch", theme="streamlit", config=CHART_CONFIG, key=f"scatter_{version}")
    st.caption(f"Reference: {M.OBSERVATION_SOURCE}. Matching: {M.MATCH_TOLERANCE}. Charts: each dot is one evaluated "
               "prediction; dots on the dashed line were exact. The same values are in the records table below.")

# --- Records ---------------------------------------------------------------------------------
st.html(T.section("Records", "table_rows"))
if df.empty:
    st.info("No records match these filters.", icon=":material/search:")
else:
    table = pd.DataFrame({
        "Target (IST)": df["target_time_utc"].dt.tz_convert(TZ).dt.strftime("%d %b %H:%M"),
        "Issued (IST)": df["issue_time_utc"].dt.tz_convert(TZ).dt.strftime("%d %b %H:%M"),
        "Ahead": [f"+{h} h" for h in df["horizon_h"]],
        f"Cloudzy {tu}": [U.temperature(v, tu) for v in df["predicted"]],
        f"IFS forecast {tu}": [U.temperature(v, tu) for v in df["provider_value"]],
        "Status": [f"{STATUS_STYLE[d][2]} {d}" for d in df["detail"]],
        "Checkable from (UTC)": [f"{d:%d %b}" for d in df["checkable_from_utc"]],
        f"Reference {tu}": [U.temperature(v, tu) for v in df["observed"]],
        f"Error {tu}": [U.temperature_delta(v, tu) for v in df["abs_error"]],
        "Note": df["status_note"].fillna(""),
        "Model version": df["model_version"],
    })
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        **{c: st.column_config.NumberColumn(format="%.1f") for c in table.columns if c.endswith(tu)},
        "Status": st.column_config.TextColumn(help="✔ evaluated · ◷ waiting (normal) · ↻ due on the next check · "
                                              "⚠ checked, reference still missing · ✕ unavailable"),
        f"Reference {tu}": st.column_config.NumberColumn(format="%.1f", help="Empty until the record is evaluated: "
                                                         "missing values are never filled in."),
    })
    st.download_button("Download these records (CSV)", table.to_csv(index=False).encode(), "cloudzy_predictions.csv",
                       "text/csv", icon=":material/download:")

ref = M.refusals()
if len(ref):
    with st.expander(f"Refusals ({len(ref)}): times Cloudzy declined to predict"):
        st.caption("Cloudzy refuses when its inputs are incomplete or can't be fetched, instead of guessing.")
        st.dataframe(ref.head(50), hide_index=True, width="stretch")

st.divider()
st.caption("Reference data: Open-Meteo (CC BY 4.0); ERA5-Land: Copernicus Climate Change Service.")
