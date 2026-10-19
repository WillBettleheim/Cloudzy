"""Metrics and charts for Cloudzy Lab (Phase E). Pure functions, no Streamlit calls.

Colour rules (each checked with a palette validator against both themes):
- Cloudzy is always orange. The model or baseline it is compared with is blue.
  A third series, where needed, is aqua. Only these three stay distinguishable
  in every pairing, including for colour-blind viewers, so further series are
  grey and dashed with labels.
- Observed values are a thicker, solid neutral grey, which reads on both themes.
- Years in the bias chart share one blue and differ by line style (dotted →
  dashed → solid = older → newer). No three-step colour ramp stays readable
  on both backgrounds, and line style doesn't depend on colour vision at all.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from src.ui import _layout, palette

MODEL_NAMES = {
    "random_forest": "Random forest (Cloudzy)",
    "gradient_boosting": "Gradient boosting",
    "linear_regression": "Linear regression",
    "diurnal_baseline": "Diurnal baseline",
    "persistence": "Persistence",
}
CLOUDZY = "random_forest"
YEAR_COLOR = "#4f8fe0"  # >= 3:1 on both backgrounds
YEAR_DASHES = ["dot", "dash", "solid"]


def lab_palette(theme_type: str | None = None) -> dict:
    return {**palette(), "year": YEAR_COLOR, "year_dashes": YEAR_DASHES}


# --- Metrics ---------------------------------------------------------------------

def summary(df: pd.DataFrame, models: list[str], observed: str = "observed") -> pd.DataFrame:
    rows = []
    for m in models:
        err = df[m] - df[observed]
        rows.append({"model": m, "MAE °C": err.abs().mean(), "RMSE °C": np.sqrt((err ** 2).mean()),
                     "Bias °C": round(float(err.mean()), 6) + 0.0,  # + 0.0 turns -0.0 into 0.0 "90th pct abs error °C": err.abs().quantile(0.9),
                     "Max abs error °C": err.abs().max(), "n": int(err.notna().sum())})
    return pd.DataFrame(rows).sort_values("MAE °C").reset_index(drop=True)


def daily_abs_error(df: pd.DataFrame, models: list[str], tz: str, observed: str = "observed") -> pd.DataFrame:
    err = df[models].sub(df[observed], axis=0).abs()
    err.index = err.index.tz_convert(tz)
    return err.groupby(err.index.date).mean()


def rolling_mae(df: pd.DataFrame, models: list[str], tz: str, days: int) -> pd.DataFrame:
    """MAE over a trailing window of `days` days (needs a full window, so the first days are empty)."""
    daily = daily_abs_error(df, models, tz)
    daily.index = pd.to_datetime(daily.index)
    return daily.rolling(days, min_periods=days).mean()


def mae_by(df: pd.DataFrame, models: list[str], tz: str, by: str, observed: str = "observed") -> pd.DataFrame:
    local = df.index.tz_convert(tz)
    key = {"local_hour": local.hour, "month": local.month}[by]
    err = df[models].sub(df[observed], axis=0).abs()
    return err.groupby(np.asarray(key)).mean()


def worst_hours(df: pd.DataFrame, model: str, n: int = 10, observed: str = "observed") -> pd.DataFrame:
    err = (df[model] - df[observed])
    idx = err.abs().nlargest(n).index
    return pd.DataFrame({"observed": df.loc[idx, observed], "predicted": df.loc[idx, model],
                         "error": err.loc[idx]}, index=idx)


# --- Charts -----------------------------------------------------------------------

def mae_bar_chart(table: pd.DataFrame, colors: dict, highlight: str = CLOUDZY) -> go.Figure:
    t = table.sort_values("MAE °C", ascending=True)
    names = [MODEL_NAMES.get(m, m) for m in t["model"]]
    bar_colors = [colors["cloudzy"] if m == highlight else colors["muted"] for m in t["model"]]
    fig = go.Figure(go.Bar(
        x=t["MAE °C"], y=names, orientation="h", marker={"color": bar_colors, "cornerradius": 4},
        text=[f"{v:.3f} °C" for v in t["MAE °C"]], textposition="outside",
        hovertemplate="%{y}: %{x:.3f} °C<extra></extra>"))
    fig = _layout(fig, colors, "Mean absolute error, 2025 test year", "", height=60 + 44 * len(t))
    fig.update_yaxes(autorange="reversed", title_text="")
    fig.update_xaxes(title_text="MAE (°C)", range=[0, t["MAE °C"].max() * 1.25])
    fig.update_layout(hovermode="closest", showlegend=False)
    return fig


def _line(fig, x, y, name, color, dash=None, width=2):
    fig.add_scatter(x=x, y=y, name=name, mode="lines", line={"color": color, "width": width, "dash": dash},
                    hovertemplate=f"%{{y:.2f}}<extra>{name}</extra>")


def timeseries_chart(df: pd.DataFrame, compare: str, colors: dict, tz: str) -> go.Figure:
    x = df.index.tz_convert(tz)
    fig = go.Figure()
    _line(fig, x, df["observed"], "Observed (reanalysis)", colors["observed"], width=3.5)
    _line(fig, x, df[compare], MODEL_NAMES.get(compare, compare), colors["provider"])
    _line(fig, x, df[CLOUDZY], MODEL_NAMES[CLOUDZY], colors["cloudzy"])
    return _layout(fig, colors, "Predicted vs observed temperature (1 hour ahead)", "°C", height=340)


def scatter_chart(df: pd.DataFrame, model: str, colors: dict) -> go.Figure:
    color = colors["cloudzy"] if model == CLOUDZY else colors["provider"]
    lo, hi = float(df["observed"].min()) - 1, float(df["observed"].max()) + 1
    fig = go.Figure()
    fig.add_scatter(x=[lo, hi], y=[lo, hi], mode="lines", name="Perfect prediction",
                    line={"color": colors["muted"], "dash": "dash", "width": 1}, hoverinfo="skip")
    fig.add_scatter(x=df["observed"], y=df[model], mode="markers", name=MODEL_NAMES.get(model, model),
                    marker={"color": color, "size": 4, "opacity": 0.35},
                    hovertemplate="observed %{x:.1f} °C<br>predicted %{y:.1f} °C<extra></extra>")
    fig = _layout(fig, colors, f"{MODEL_NAMES.get(model, model)}: predicted vs observed", "Predicted (°C)",
                  height=380)
    fig.update_xaxes(title_text="Observed (°C)", range=[lo, hi])
    fig.update_yaxes(range=[lo, hi])
    fig.update_layout(hovermode="closest", showlegend=False)
    return fig


def error_histogram(df: pd.DataFrame, compare: str, colors: dict) -> go.Figure:
    fig = go.Figure()
    bins = {"start": -4.05, "end": 4.05, "size": 0.1}  # centred on the data's 0.1 °C steps
    for model, color in ((compare, colors["provider"]), (CLOUDZY, colors["cloudzy"])):
        fig.add_histogram(x=df[model] - df["observed"], xbins=bins, name=MODEL_NAMES.get(model, model),
                          marker={"color": color}, opacity=0.6,
                          hovertemplate="%{x:.1f} °C: %{y} hours<extra>" + MODEL_NAMES.get(model, model) + "</extra>")
    fig = _layout(fig, colors, "Error distribution (prediction − observed)", "Hours", height=300)
    fig.update_xaxes(title_text="Error (°C)")
    fig.update_layout(barmode="overlay", hovermode="closest")
    return fig


def trend_chart(rolling: pd.DataFrame, compare: str, colors: dict, days: int) -> go.Figure:
    fig = go.Figure()
    _line(fig, rolling.index, rolling[compare], MODEL_NAMES.get(compare, compare), colors["provider"])
    _line(fig, rolling.index, rolling[CLOUDZY], MODEL_NAMES[CLOUDZY], colors["cloudzy"])
    fig = _layout(fig, colors, f"Rolling {days}-day MAE through 2025", "MAE (°C)", height=300)
    fig.update_yaxes(rangemode="tozero")
    return fig


def grouped_chart(table: pd.DataFrame, compare: str, colors: dict, title: str, x_title: str) -> go.Figure:
    fig = go.Figure()
    for model, color in ((compare, colors["provider"]), (CLOUDZY, colors["cloudzy"])):
        fig.add_scatter(x=table.index, y=table[model], name=MODEL_NAMES.get(model, model), mode="lines+markers",
                        line={"color": color, "width": 2}, marker={"size": 6},
                        hovertemplate=f"%{{y:.3f}} °C<extra>{MODEL_NAMES.get(model, model)}</extra>")
    fig = _layout(fig, colors, title, "MAE (°C)", height=280)
    fig.update_xaxes(title_text=x_title, dtick=1 if len(table) <= 12 else 3)
    fig.update_yaxes(rangemode="tozero")
    return fig


def horizon_mae(serving: pd.DataFrame) -> pd.DataFrame:
    return serving.assign(
        cloudzy=(serving["cloudzy"] - serving["observed"]).abs(),
        provider=(serving["provider_optimistic"] - serving["observed"]).abs(),
        persistence=(serving["persistence"] - serving["observed"]).abs(),
    ).groupby("horizon_h")[["cloudzy", "provider", "persistence"]].mean()


def horizon_chart(serving: pd.DataFrame, colors: dict, deployed: dict | None = None) -> go.Figure:
    """Live setting: MAE by horizon for Cloudzy and the provider's own values.

    Persistence is left out on purpose: its 4-6 °C errors at mid horizons would
    flatten the comparison that matters. The page reports its values in text.
    """
    g = horizon_mae(serving)
    fig = go.Figure()
    if deployed:  # the compact models actually served by the app (src.compact)
        hs = sorted(int(h) for h in deployed)
        fig.add_scatter(x=hs, y=[deployed[h] if h in deployed else deployed[str(h)] for h in hs],
                        name="Cloudzy deployed (compact)", mode="lines+markers",
                        line={"color": colors["third"], "width": 2, "dash": "dash"}, marker={"size": 7},
                        hovertemplate="%{y:.2f} °C<extra>Deployed (compact)</extra>")
    for col, name, color, dash in (("provider", "IFS value at t+h (optimistic)", colors["provider"], None),
                                   ("cloudzy", "Cloudzy research forest", colors["cloudzy"], None)):
        fig.add_scatter(x=g.index, y=g[col], name=name, mode="lines+markers",
                        line={"color": color, "width": 2, "dash": dash}, marker={"size": 7},
                        hovertemplate=f"%{{y:.2f}} °C<extra>{name}</extra>")
    fig = _layout(fig, colors, "Live inputs → reanalysis: MAE by horizon (2025)", "MAE (°C)", height=320)
    fig.update_xaxes(title_text="Hours ahead", tickvals=list(g.index))
    fig.update_yaxes(rangemode="tozero")
    return fig


def multistep_chart(part1: dict, colors: dict) -> go.Figure:
    hs = sorted(int(h) for h in part1)
    fig = go.Figure()
    for key, name, color, dash in (("persistence", "Persistence", colors["muted"], "dash"),
                                   ("diurnal_baseline", "Diurnal baseline", colors["muted"], "dot"),
                                   ("recursive", "Recursive (1 h model)", colors["third"], None),
                                   ("direct", "Direct (Cloudzy)", colors["cloudzy"], None)):
        fig.add_scatter(x=hs, y=[part1[str(h)][key]["mae_c"] for h in hs], name=name, mode="lines+markers",
                        line={"color": color, "width": 2, "dash": dash}, marker={"size": 7},
                        hovertemplate=f"%{{y:.3f}} °C<extra>{name}</extra>")
    fig = _layout(fig, colors, "Reanalysis setting: MAE by horizon (2025)", "MAE (°C)", height=320)
    fig.update_xaxes(title_text="Hours ahead", tickvals=hs)
    fig.update_yaxes(rangemode="tozero")
    return fig


def bias_chart(bias: pd.DataFrame, colors: dict) -> go.Figure:
    fig = go.Figure()
    years = sorted(bias["year"].unique())
    for year, dash in zip(years, colors["year_dashes"][-len(years):]):
        b = bias[bias["year"] == year]
        fig.add_scatter(x=b["local_hour"], y=b["mean_ifs_minus_reanalysis_c"], name=str(year),
                        mode="lines+markers", line={"color": colors["year"], "width": 2, "dash": dash},
                        marker={"size": 5},
                        hovertemplate=f"%{{y:+.2f}} °C<extra>{year}</extra>")
    fig.add_hline(y=0, line={"color": colors["muted"], "width": 1})
    fig = _layout(fig, colors, "Live-input bias: ECMWF IFS − reanalysis temperature", "°C", height=320)
    fig.update_xaxes(title_text="Local hour (IST)", dtick=3)
    return fig


# --- Phase 7: breakdowns, explainability, history -------------------------------------------

FEATURE_DESCRIPTIONS = {
    "temperature_2m": "Temperature at the time the prediction is made",
    "temp_lag_1h": "Temperature 1 hour earlier", "temp_lag_2h": "Temperature 2 hours earlier",
    "temp_lag_3h": "Temperature 3 hours earlier", "temp_lag_6h": "Temperature 6 hours earlier",
    "temp_lag_12h": "Temperature 12 hours earlier", "temp_lag_24h": "Temperature at the same time yesterday",
    "temp_change_1h": "How much the temperature changed in the last hour",
    "temp_change_24h": "Temperature now minus 24 hours ago",
    "temp_roll_mean_3h": "Average temperature over the last 3 hours", "temp_roll_std_3h": "Variability over the last 3 hours",
    "temp_roll_mean_6h": "Average temperature over the last 6 hours", "temp_roll_std_6h": "Variability over the last 6 hours",
    "temp_roll_mean_24h": "Average temperature over the last 24 hours", "temp_roll_std_24h": "Variability over the last 24 hours (day–night swing)",
    "temp_roll_min_24h": "Lowest temperature in the last 24 hours", "temp_roll_max_24h": "Highest temperature in the last 24 hours",
    "relative_humidity_2m": "Relative humidity now", "surface_pressure": "Air pressure now",
    "wind_speed_10m": "Wind speed now", "precipitation": "Rain in the last hour",
    "pressure_change_3h": "Pressure change over the last 3 hours", "precip_sum_3h": "Rain over the last 3 hours",
    "hour_sin": "Time of day (sine part of a 24-hour clock)", "hour_cos": "Time of day (cosine part of a 24-hour clock)",
    "doy_sin": "Time of year (sine part)", "doy_cos": "Time of year (cosine part)",
}
TEMP_BINS = [-50, 10, 20, 30, 60]
TEMP_LABELS = ["below 10 °C", "10–20 °C", "20–30 °C", "30 °C and above"]


def mae_by_group(df: pd.DataFrame, models: list[str], by: str) -> pd.DataFrame:
    """MAE per group with sample counts. by='temperature' (observed °C) or 'rain' (rain at issue time)."""
    if by == "temperature":
        key = pd.cut(df["observed"], TEMP_BINS, labels=TEMP_LABELS)
    elif by == "rain":
        key = pd.Series(np.where(df["precip_at_issue_mm"] > 0, "raining when issued", "dry when issued"), index=df.index)
    else:
        raise ValueError(by)
    err = df[models].sub(df["observed"], axis=0).abs()
    g = err.groupby(key, observed=True)
    out = g.mean()
    out["hours"] = g.size()
    return out


def group_bar_chart(table: pd.DataFrame, compare: str, colors: dict, title: str) -> go.Figure:
    fig = go.Figure()
    for model, color in ((compare, colors["provider"]), (CLOUDZY, colors["cloudzy"])):
        fig.add_bar(x=[str(i) for i in table.index], y=table[model], name=MODEL_NAMES.get(model, model),
                    marker={"color": color, "cornerradius": 4},
                    customdata=table["hours"], hovertemplate="%{y:.3f} °C (%{customdata} hours)<extra>"
                    + MODEL_NAMES.get(model, model) + "</extra>")
    fig = _layout(fig, colors, title, "MAE (°C)", height=300)
    fig.update_layout(barmode="group", hovermode="closest", bargap=0.3)
    return fig


def importance_chart(imp: pd.DataFrame, colors: dict, view: str = "learned change", top: int = 12) -> go.Figure:
    t = imp[imp["view"] == view].sort_values("mae_increase_c", ascending=False).head(top)
    labels = [FEATURE_DESCRIPTIONS.get(f, f) for f in t["feature"]]
    fig = go.Figure(go.Bar(
        x=t["mae_increase_c"], y=labels, orientation="h", marker={"color": colors["cloudzy"], "cornerradius": 4},
        error_x={"type": "data", "array": t["std_c"], "visible": True, "color": colors["muted"]},
        customdata=t["feature"], text=[f"+{v:.3f} °C" for v in t["mae_increase_c"]], textposition="outside",
        hovertemplate="%{customdata}: +%{x:.3f} °C MAE when shuffled<extra></extra>"))
    fig = _layout(fig, colors, f"Permutation importance ({view}), 2025 test set", "", height=80 + 30 * len(t))
    fig.update_yaxes(autorange="reversed", title_text="")
    fig.update_xaxes(title_text="Increase in MAE when the feature is shuffled (°C)",
                     range=[0, t["mae_increase_c"].max() * 1.3 if len(t) else 1])
    fig.update_layout(hovermode="closest", showlegend=False)
    return fig


def history_temperature_chart(daily: pd.DataFrame, colors: dict, unit: str, convert) -> go.Figure:
    """Daily min–max band with the daily mean. `convert` maps °C to the display unit."""
    lo, hi, mean = (daily[c].map(convert) for c in ("temp_min_c", "temp_max_c", "temp_mean_c"))
    fig = go.Figure()
    fig.add_scatter(x=daily.index, y=hi, mode="lines", line={"width": 0}, showlegend=False, hoverinfo="skip")
    fig.add_scatter(x=daily.index, y=lo, mode="lines", line={"width": 0}, fill="tonexty",
                    fillcolor="rgba(57,135,229,0.22)", name="Daily low–high", hoverinfo="skip")
    fig.add_scatter(x=daily.index, y=mean, mode="lines", name="Daily mean", line={"color": colors["provider"], "width": 2},
                    customdata=list(zip(lo, hi)),
                    hovertemplate=f"mean %{{y:.1f}} {unit} · low %{{customdata[0]:.1f}} · high %{{customdata[1]:.1f}}<extra></extra>")
    return _layout(fig, colors, "Temperature (historical reanalysis)", unit, height=320)


def history_bar_chart(daily: pd.DataFrame, column: str, colors: dict, title: str, unit: str, convert=None) -> go.Figure:
    y = daily[column].map(convert) if convert else daily[column]
    fig = go.Figure(go.Bar(x=daily.index, y=y, marker={"color": colors["provider"], "cornerradius": 2},
                           hovertemplate=f"%{{y:.1f}} {unit}<extra></extra>"))
    fig = _layout(fig, colors, title, unit, height=260)
    fig.update_layout(hovermode="closest", showlegend=False, bargap=0.15)
    return fig


def history_line_chart(daily: pd.DataFrame, column: str, colors: dict, title: str, unit: str, convert=None) -> go.Figure:
    y = daily[column].map(convert) if convert else daily[column]
    fig = go.Figure(go.Scatter(x=daily.index, y=y, mode="lines", line={"color": colors["provider"], "width": 2},
                               hovertemplate=f"%{{y:.1f}} {unit}<extra></extra>"))
    return _layout(fig, colors, title, unit, height=260)



# --- Model card and other locations (from committed evaluation reports) ------------------------

def model_card(report: dict) -> dict:
    """The facts a model card shows, read from an evaluation report (never typed in by hand)."""
    sel = report["selected"]["family"]
    sp = report["splits"]
    tm = report["test_metrics"]
    data = report.get("provenance", {}).get("data", {})
    return {
        "Model": f"{MODEL_NAMES.get(sel, sel)} · {report['selected']['params'] or 'default settings'}",
        "Task": "Temperature 1 hour ahead (°C), from values at or before the prediction time",
        "Data": f"{data.get('source', 'Open-Meteo archive')} · {data.get('model', '')} · {data.get('location', '')}",
        "Period": f"{report.get('period', ['2023-01-01', '2025-12-31'])[0]} → {report.get('period', ['', '2025-12-31'])[1]}",
        "Training examples": f"{sp['train']['rows'] + sp['validation']['rows']:,} (train {sp['train']['rows']:,} + "
                             f"validation {sp['validation']['rows']:,}, refitted together after selection)",
        "Test period": f"{sp['test']['first_issue_time_utc'][:10]} → {sp['test']['last_issue_time_utc'][:10]} "
                       f"({sp['test']['rows']:,} hours, never used for choices)",
        "Test MAE / RMSE": f"{tm[sel]['mae_c']:.3f} / {tm[sel]['rmse_c']:.3f} °C",
        "Persistence MAE / RMSE": f"{tm['persistence']['mae_c']:.3f} / {tm['persistence']['rmse_c']:.3f} °C",
    }


def locations_table(reports: list[dict]) -> pd.DataFrame:
    rows = []
    for r in reports:
        sel = r["selected"]["family"]
        tm = r["test_metrics"]
        loc = r.get("location", {}).get("name") or r.get("provenance", {}).get("data", {}).get("location", "?")
        rows.append({
            "Location": loc,
            "Period": " → ".join(r.get("period", ["2023-01-01", "2025-12-31"])),
            "Split": r.get("split_rule", "calendar years"),
            "Test hours": r["splits"]["test"]["rows"],
            "Selected (on validation)": MODEL_NAMES.get(sel, sel),
            "Model MAE °C": tm[sel]["mae_c"], "Model RMSE °C": tm[sel]["rmse_c"],
            "Persistence MAE °C": tm["persistence"]["mae_c"],
            "Diurnal baseline MAE °C": tm["diurnal_baseline"]["mae_c"],
            "Beats persistence (95% CI)": "yes" if r.get("beats_baseline") else "no",
        })
    return pd.DataFrame(rows)
