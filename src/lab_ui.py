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


def horizon_chart(serving: pd.DataFrame, colors: dict) -> go.Figure:
    """Live setting: MAE by horizon for Cloudzy and the provider's own values.

    Persistence is left out on purpose: its 4-6 °C errors at mid horizons would
    flatten the comparison that matters. The page reports its values in text.
    """
    g = horizon_mae(serving)
    fig = go.Figure()
    for col, name, color, dash in (("provider", "IFS value at t+h (optimistic)", colors["provider"], None),
                                   ("cloudzy", "Cloudzy (direct)", colors["cloudzy"], None)):
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
