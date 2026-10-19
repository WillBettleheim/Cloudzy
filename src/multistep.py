"""Multi-step forecasting: direct vs recursive strategies, horizons 1-24 h.

Strategies
- Direct: one model per horizon h. Each is trained on features at t with target
  temperature(t+h). The models never consume their own predictions.
- Recursive: the 1-hour model is applied repeatedly, and each prediction is
  appended to the temperature history. Future humidity, pressure, wind and
  precipitation are unknown, so they are held at their last known values
  ("frozen"). This is the main weakness of the recursive strategy.

Both strategies are scored on the same 2025 issue times, against persistence and
the climatological diurnal baseline fitted for that horizon. Each direct model
is tuned on 2024 (fitted on 2023) among the residual random-forest
configurations, refitted on 2023-2024, then tested once on 2025.

Part 1 runs in the clean reanalysis → reanalysis setting, so the strategies are
compared without the source shift. Part 2 trains direct models for the
serving setup (live IFS inputs → reanalysis target, setup E in src.shift). It
compares them with the provider's own value for t+h. That baseline is
optimistic: in the archive, the IFS value for t+h may come from a run started
up to h hours after t.

Run:  python -m src.multistep
"""

from __future__ import annotations

import json
import logging

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.base import clone

from src import config
from src.data_validation import load_processed, processed_path
from src.evaluate import (
    BASELINE_COLOR, INK, INK_MUTED, MODEL_COLOR, _save, _style, block_bootstrap_mae_diff, mae, metrics,
)
from src.features import TARGET, TEMP, FeatureConfig, build_features, feature_columns, features_from_windows
from src.models import DiurnalChangeBaseline, candidates
from src.predict import load_bundle
from src.splits import SplitSpec, chronological_split

logger = logging.getLogger(__name__)

HORIZONS = (1, 2, 3, 6, 12, 24)
MAX_H = max(HORIZONS)
HISTORY = 25  # hours of temperature needed per issue time (t-24 .. t)
TEST_YEAR = 2025
RECURSIVE_COLOR = "#1baf7a"  # validated categorical slot 3
PHASE_A_MODEL = config.MODELS_DIR / f"cloudzy_core_{config.DELHI.slug}_{config.REANALYSIS.model}_h1.joblib"


def eval_origins(df: pd.DataFrame, year: int = TEST_YEAR) -> pd.DatetimeIndex:
    """Issue times in `year` whose full 24 h future is inside `year` and inside the data."""
    start = pd.Timestamp(year, 1, 1, tz="UTC")
    last = min(pd.Timestamp(year + 1, 1, 1, tz="UTC") - pd.Timedelta(hours=1), df.index.max())
    last -= pd.Timedelta(hours=MAX_H)
    idx = df.index[(df.index >= start) & (df.index <= last)]
    if (df.index.get_indexer(idx) < HISTORY - 1).any():
        raise ValueError("Not enough history before the first issue time")
    return idx


def recursive_forecast(model, df: pd.DataFrame, origins: pd.DatetimeIndex, steps: int,
                       cfg: FeatureConfig = FeatureConfig()) -> np.ndarray:
    """Returns predictions of shape (len(origins), steps); column j is t + (j+1) h."""
    pos = df.index.get_indexer(origins)
    if (pos < 0).any():
        raise ValueError("Some origins are not in the data")
    temp = df[TEMP].to_numpy()
    offsets = np.arange(-(HISTORY - 1), 1)
    window = temp[pos[:, None] + offsets]
    pres = df["surface_pressure"].to_numpy()[pos[:, None] + np.arange(-3, 1)]
    precip = df["precipitation"].to_numpy()[pos[:, None] + np.arange(-2, 1)]
    frozen = {v: df[v].to_numpy()[pos] for v in cfg.current_vars}
    if np.isnan(window).any() or np.isnan(pres).any() or np.isnan(precip).any():
        raise ValueError("History windows contain NaN")
    out = np.empty((len(origins), steps))
    for j in range(steps):
        issue = origins + pd.Timedelta(hours=j)
        X = features_from_windows(window, {**frozen, "surface_pressure_window": pres,
                                           "precipitation_window": precip}, issue, cfg)
        pred = model.predict(X)
        out[:, j] = pred
        window = np.column_stack([window[:, 1:], pred])
        pres = np.column_stack([pres[:, 1:], pres[:, -1]])  # frozen: last known value
        precip = np.column_stack([precip[:, 1:], precip[:, -1]])
    return out


def residual_rf_grid():
    return [(p, est) for p, est in candidates()["random_forest"] if p["residual"]]


def tune_and_fit_direct(table: pd.DataFrame, cols: list[str], h: int):
    """Tune on 2024 (fit on 2023), refit best on 2023-2024. Returns (model, params, val_mae)."""
    sel = chronological_split(table, (SplitSpec("fit", "2023-01-01", "2024-01-01"),
                                      SplitSpec("val", "2024-01-01", "2025-01-01")), horizon_hours=h)
    scores = []
    for params, est in residual_rf_grid():
        m = clone(est).fit(sel["fit"][cols], sel["fit"][TARGET])
        scores.append((mae(sel["val"][TARGET], m.predict(sel["val"][cols])), params, est))
    val_mae, params, est = min(scores, key=lambda s: s[0])
    full = chronological_split(table, (SplitSpec("fit", "2023-01-01", "2025-01-01"),), horizon_hours=h)["fit"]
    return clone(est).fit(full[cols], full[TARGET]), params, round(val_mae, 4), full


def target_for(table_index, truth: pd.Series, h: int) -> np.ndarray:
    return truth.shift(-h).reindex(table_index).to_numpy()


def run_reanalysis(ref: pd.DataFrame) -> tuple[dict, dict]:
    """Part 1: direct vs recursive in the reanalysis → reanalysis setting."""
    origins = eval_origins(ref)
    one_hour = load_bundle(PHASE_A_MODEL)["model"]  # trained on 2023-2024, Phase A
    rec = recursive_forecast(one_hour, ref, origins, MAX_H)
    results, direct_models = {}, {}
    for h in HORIZONS:
        cfg = FeatureConfig(horizon_hours=h)
        cols = feature_columns(cfg)
        table, _ = build_features(ref, cfg)
        model, params, val_mae, fit_rows = tune_and_fit_direct(table, cols, h)
        direct_models[h] = {"model": model, "params": params}
        X = table.loc[origins, cols]
        y = target_for(origins, ref[TEMP], h)
        diurnal = DiurnalChangeBaseline().fit(fit_rows[cols], fit_rows[TARGET])
        preds = {
            "direct": model.predict(X),
            "recursive": rec[:, h - 1],
            "persistence": X[TEMP].to_numpy(),
            "diurnal_baseline": diurnal.predict(X),
        }
        results[str(h)] = {
            "direct_params": params, "direct_validation_mae_c": val_mae,
            **{k: metrics(y, v) for k, v in preds.items()},
            "direct_vs_recursive": block_bootstrap_mae_diff(origins, y, preds["direct"], preds["recursive"]),
            "direct_vs_diurnal": block_bootstrap_mae_diff(origins, y, preds["direct"], preds["diurnal_baseline"]),
        }
        r = results[str(h)]
        logger.info("h=%2d  direct %.3f  recursive %.3f  persistence %.3f  diurnal %.3f", h,
                    r["direct"]["mae_c"], r["recursive"]["mae_c"], r["persistence"]["mae_c"],
                    r["diurnal_baseline"]["mae_c"])
    return results, {"origins": [origins.min().isoformat(), origins.max().isoformat(), len(origins)],
                     "direct_models": direct_models}


def run_serving(ref: pd.DataFrame, live: pd.DataFrame) -> tuple[dict, dict]:
    """Part 2: direct models for live IFS inputs → reanalysis target, per horizon."""
    origins = eval_origins(live)
    results, models = {}, {}
    for h in HORIZONS:
        cfg = FeatureConfig(horizon_hours=h)
        cols = feature_columns(cfg)
        table, _ = build_features(live, cfg)
        table[TARGET] = target_for(table.index, ref[TEMP], h)
        table = table.dropna(subset=[TARGET])
        model, params, val_mae, fit_rows = tune_and_fit_direct(table, cols, h)
        # Deployment fit: same configuration, refitted on every available row
        # (2023-2025). The 2025 test score below stays the error estimate.
        deploy = clone(model).fit(table[cols], table[TARGET])
        models[h] = {"model": deploy, "params": params, "validation_mae_c": val_mae,
                     "evaluated_model_trained_on": [fit_rows.index.min().isoformat(),
                                                    fit_rows.index.max().isoformat(), len(fit_rows)],
                     "deployed_model_trained_on": [table.index.min().isoformat(),
                                                   table.index.max().isoformat(), len(table)]}
        X = table.loc[origins, cols]
        y = table.loc[origins, TARGET].to_numpy()
        diurnal = DiurnalChangeBaseline().fit(fit_rows[cols], fit_rows[TARGET])
        preds = {
            "direct": model.predict(X),
            "persistence": X[TEMP].to_numpy(),
            "diurnal_baseline": diurnal.predict(X),
            "provider_optimistic": target_for(origins, live[TEMP], h),
        }
        results[str(h)] = {
            "direct_params": params, "direct_validation_mae_c": val_mae,
            **{k: metrics(y, v) for k, v in preds.items()},
            "direct_vs_provider": block_bootstrap_mae_diff(origins, y, preds["direct"], preds["provider_optimistic"]),
        }
        r = results[str(h)]
        logger.info("serving h=%2d  direct %.3f  provider(optimistic) %.3f  persistence %.3f  diurnal %.3f", h,
                    r["direct"]["mae_c"], r["provider_optimistic"]["mae_c"], r["persistence"]["mae_c"],
                    r["diurnal_baseline"]["mae_c"])
    return results, models


def figure(part1: dict, path) -> None:
    hs = [int(h) for h in part1]
    fig, ax = plt.subplots(figsize=(7, 4))
    for key, label, color in [("persistence", "Persistence", BASELINE_COLOR),
                              ("recursive", "Recursive (1 h model)", RECURSIVE_COLOR),
                              ("direct", "Direct (model per horizon)", MODEL_COLOR)]:
        ax.plot(hs, [part1[str(h)][key]["mae_c"] for h in hs], color=color, linewidth=2,
                marker="o", markersize=5, label=label)
    ax.plot(hs, [part1[str(h)]["diurnal_baseline"]["mae_c"] for h in hs], color=INK_MUTED, linewidth=1.5,
            linestyle="--", marker="o", markersize=4, label="Diurnal baseline")
    ax.set_xticks(hs)
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, labelcolor=INK)
    _style(ax, "Test 2025 (reanalysis): MAE by forecast horizon", "Horizon (hours ahead)", "MAE (°C)")
    _save(fig, path)


def run() -> dict:
    ref = load_processed(processed_path(source=config.REANALYSIS))
    live = load_processed(processed_path(source=config.LIVE_ARCHIVE))
    part1, info = run_reanalysis(ref)
    part2, serving_models = run_serving(ref, live)

    fig_dir = config.REPORTS_DIR / "figures" / "multistep"
    fig_dir.mkdir(parents=True, exist_ok=True)
    figure(part1, fig_dir / "mae_by_horizon_reanalysis.png")

    bundle_path = config.MODELS_DIR / f"cloudzy_core_{config.DELHI.slug}_serving_direct.joblib"
    joblib.dump({
        "kind": "direct_multi_horizon",
        "inputs_source": config.LIVE_ARCHIVE.model,
        "target_source": config.REANALYSIS.model,
        "feature_columns": feature_columns(),
        "feature_config": FeatureConfig().to_dict(),
        "horizons": {h: m for h, m in serving_models.items()},
        "test_2025_mae_c": {h: part2[str(h)]["direct"]["mae_c"] for h in HORIZONS},
        "test_2025_provider_optimistic_mae_c": {h: part2[str(h)]["provider_optimistic"]["mae_c"] for h in HORIZONS},
    }, bundle_path)
    logger.info("Saved serving bundle: %s", bundle_path.name)

    report = {
        "horizons": HORIZONS,
        "test_origins": info["origins"],
        "part1_reanalysis_direct_vs_recursive": part1,
        "part2_serving_live_inputs_to_reanalysis": part2,
        "serving_bundle": bundle_path.name,
        "notes": [
            "Recursive strategy freezes humidity, pressure, wind and precipitation at their last values.",
            "provider_optimistic uses the archived IFS value for t+h, which may come from a run after t.",
            "Direct models tuned on 2024 among residual random-forest configs, tested once on 2025.",
        ],
    }
    out = config.REPORTS_DIR / f"multistep_{config.DELHI.slug}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    logger.info("Multi-step report: %s", out.name)
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    r = run()
    print("Part 1 — reanalysis → reanalysis, test 2025 MAE °C")
    for h, v in r["part1_reanalysis_direct_vs_recursive"].items():
        d, dd = v["direct_vs_recursive"], v["direct_vs_diurnal"]
        print(f"  h={h:>2}  direct {v['direct']['mae_c']:.3f}  recursive {v['recursive']['mae_c']:.3f}  "
              f"persistence {v['persistence']['mae_c']:.3f}  diurnal {v['diurnal_baseline']['mae_c']:.3f}  "
              f"direct−recursive {d['mae_diff_c']:+.3f} [{d['ci95_low_c']:+.3f},{d['ci95_high_c']:+.3f}]  "
              f"direct−diurnal {dd['mae_diff_c']:+.3f} [{dd['ci95_low_c']:+.3f},{dd['ci95_high_c']:+.3f}]  "
              f"{v['direct_params']}")
    print("Part 2 — live IFS inputs → reanalysis, test 2025 MAE °C")
    for h, v in r["part2_serving_live_inputs_to_reanalysis"].items():
        d = v["direct_vs_provider"]
        print(f"  h={h:>2}  direct {v['direct']['mae_c']:.3f}  provider(opt) {v['provider_optimistic']['mae_c']:.3f}  "
              f"persistence {v['persistence']['mae_c']:.3f}  diurnal {v['diurnal_baseline']['mae_c']:.3f}  "
              f"direct−provider {d['mae_diff_c']:+.3f} [{d['ci95_low_c']:+.3f},{d['ci95_high_c']:+.3f}]")


if __name__ == "__main__":
    main()
