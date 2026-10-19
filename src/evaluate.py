"""Evaluation metrics, error breakdowns and figures.

All errors are prediction - observation, in °C.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # file output only; no display needed
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SEASONS_IN = {  # India Meteorological Department convention
    12: "winter", 1: "winter", 2: "winter",
    3: "pre-monsoon", 4: "pre-monsoon", 5: "pre-monsoon",
    6: "monsoon", 7: "monsoon", 8: "monsoon", 9: "monsoon",
    10: "post-monsoon", 11: "post-monsoon",
}


def mae(y_true, y_pred) -> float:
    return float(np.mean(np.abs(np.asarray(y_pred) - np.asarray(y_true))))


def rmse(y_true, y_pred) -> float:
    return float(np.sqrt(np.mean((np.asarray(y_pred) - np.asarray(y_true)) ** 2)))


def metrics(y_true, y_pred) -> dict[str, float]:
    err = np.asarray(y_pred) - np.asarray(y_true)
    if not np.isfinite(err).all():
        raise ValueError("Predictions or targets contain non-finite values")
    abs_err = np.abs(err)
    return {
        "n": int(err.size),
        "mae_c": round(mae(y_true, y_pred), 4),
        "rmse_c": round(rmse(y_true, y_pred), 4),
        "bias_c": round(float(err.mean()), 4),
        "abs_error_p50_c": round(float(np.percentile(abs_err, 50)), 3),
        "abs_error_p90_c": round(float(np.percentile(abs_err, 90)), 3),
        "abs_error_p99_c": round(float(np.percentile(abs_err, 99)), 3),
        "abs_error_max_c": round(float(abs_err.max()), 3),
    }


def breakdown(index: pd.DatetimeIndex, y_true, preds: dict[str, np.ndarray], local_tz: str) -> dict:
    """MAE per local hour, month and season, for each named prediction."""
    local = index.tz_convert(local_tz)
    frame = pd.DataFrame({name: np.abs(p - np.asarray(y_true)) for name, p in preds.items()}, index=index)
    groups = {
        "by_local_hour": local.hour,
        "by_month": local.month,
        "by_season": local.month.map(SEASONS_IN),
    }
    out = {}
    for label, key in groups.items():
        g = frame.groupby(np.asarray(key))
        table = g.mean().round(4)
        table["n"] = g.size()
        out[label] = {str(k): row.to_dict() for k, row in table.iterrows()}
    return out


def block_bootstrap_mae_diff(
    index: pd.DatetimeIndex, y_true, pred_a, pred_b,
    block: str = "7D", n_boot: int = 1000, seed: int = 42,
) -> dict[str, float]:
    """95% interval for MAE(a) - MAE(b), resampling whole weeks.

    Hourly errors are strongly autocorrelated, so resampling single hours would
    give falsely narrow intervals. Resampling contiguous weeks keeps most of
    that dependence. A negative difference means a beats b.
    """
    diff = np.abs(np.asarray(pred_a) - y_true) - np.abs(np.asarray(pred_b) - y_true)
    s = pd.Series(diff, index=index)
    groups = s.groupby(pd.Grouper(freq=block))
    sums = groups.sum().to_numpy()
    counts = groups.size().to_numpy()
    keep = counts > 0
    sums, counts = sums[keep], counts[keep]
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(sums), size=(n_boot, len(sums)))
    boot = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    return {
        "mae_diff_c": round(float(diff.mean()), 4),
        "ci95_low_c": round(float(np.percentile(boot, 2.5)), 4),
        "ci95_high_c": round(float(np.percentile(boot, 97.5)), 4),
        "block": block,
        "n_blocks": len(sums),
        "n_boot": n_boot,
    }


# --- Figures ---------------------------------------------------------------
# Static report figures. Colours follow a validated categorical order
# (slot 1 blue = selected model, slot 2 orange = persistence baseline).
# Text stays in neutral ink, with a recessive grid.
MODEL_COLOR, BASELINE_COLOR = "#2a78d6", "#eb6834"
INK, INK_MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _style(ax, title, xlabel, ylabel):
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    ax.set_xlabel(xlabel, color=INK_MUTED)
    ax.set_ylabel(ylabel, color=INK_MUTED)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED)


def save_figures(index, y_true, model_pred, base_pred, model_name, local_tz, out_dir: Path,
                 reference_name: str = "reanalysis") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    y_true = np.asarray(y_true)
    paths = []

    fig, ax = plt.subplots(figsize=(5.5, 5))
    ax.scatter(y_true, model_pred, s=3, alpha=0.25, color=MODEL_COLOR, linewidths=0)
    lims = [min(y_true.min(), model_pred.min()) - 1, max(y_true.max(), model_pred.max()) + 1]
    ax.plot(lims, lims, color=INK_MUTED, linewidth=1, linestyle="--")
    ax.set_xlim(lims), ax.set_ylim(lims)
    _style(ax, f"Test set: {model_name} vs. {reference_name}", "Observed temperature at t+1 (°C)", "Predicted (°C)")
    paths.append(_save(fig, out_dir / "test_predicted_vs_observed.png"))

    fig, ax = plt.subplots(figsize=(7, 4))
    # Data are rounded to 0.1 °C; centre bins on those steps to avoid a comb artifact.
    bins = np.arange(-4.05, 4.06, 0.1)
    ax.hist(base_pred - y_true, bins=bins, histtype="step", linewidth=2, color=BASELINE_COLOR, label="Persistence")
    ax.hist(model_pred - y_true, bins=bins, histtype="step", linewidth=2, color=MODEL_COLOR, label=model_name)
    ax.legend(frameon=False, labelcolor=INK)
    _style(ax, "Test set: error distribution", "Prediction − observation (°C)", "Hours")
    paths.append(_save(fig, out_dir / "test_error_distribution.png"))

    local = index.tz_convert(local_tz)
    errs = pd.DataFrame({"model": np.abs(model_pred - y_true), "base": np.abs(base_pred - y_true)}, index=index)
    for key, fname, xlabel in [
        (local.hour, "test_mae_by_hour.png", "Local hour (IST) when the prediction is issued"),
        (local.month, "test_mae_by_month.png", "Month"),
    ]:
        g = errs.groupby(np.asarray(key)).mean()
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot(g.index, g["base"], color=BASELINE_COLOR, linewidth=2, marker="o", markersize=4, label="Persistence")
        ax.plot(g.index, g["model"], color=MODEL_COLOR, linewidth=2, marker="o", markersize=4, label=model_name)
        ax.set_ylim(bottom=0)
        ax.legend(frameon=False, labelcolor=INK)
        _style(ax, f"Test set: MAE by {'hour' if 'hour' in fname else 'month'}", xlabel, "MAE (°C)")
        paths.append(_save(fig, out_dir / fname))
    return paths


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
