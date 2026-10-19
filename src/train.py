"""Train, tune, select and evaluate Cloudzy Core (1-hour-ahead temperature).

Protocol:
  1. Chronological split: train 2023, validation 2024, test 2025.
  2. Every candidate config is fitted on train and scored on validation (MAE).
  3. The best config per model family, and the overall winner, are chosen on
     validation MAE alone.
  4. Each family's best config is refitted on train + validation, then scored
     once on the untouched test year. The test year plays no part in any choice.
  5. The selected model is saved with everything needed to reproduce inference.

Run:  python -m src.train
"""

from __future__ import annotations

import json
import logging
import platform
import time
from datetime import datetime, timezone
from importlib.metadata import version

import joblib
import pandas as pd
from sklearn.base import clone

from src import config
from src.data_validation import load_processed, processed_path
from src.evaluate import block_bootstrap_mae_diff, breakdown, mae, metrics, save_figures
from src.features import TARGET, FeatureConfig, build_features, feature_columns
from src.models import SEED, candidates
from src.splits import DEFAULT_SPLITS, chronological_split, describe_splits

logger = logging.getLogger(__name__)

MODEL_PATH = config.MODELS_DIR / f"cloudzy_core_{config.RUN_TAG}_h1.joblib"
PACKAGES = ["numpy", "pandas", "scikit-learn", "joblib", "requests", "matplotlib"]


def xy(df: pd.DataFrame, cols: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    return df[cols], df[TARGET]


def tune(splits, cols) -> list[dict]:
    """Fit every candidate on train and score it on validation."""
    X_tr, y_tr = xy(splits["train"], cols)
    X_va, y_va = xy(splits["validation"], cols)
    results = []
    for family, configs in candidates().items():
        for params, est in configs:
            t0 = time.perf_counter()
            fitted = clone(est).fit(X_tr, y_tr)
            score = mae(y_va, fitted.predict(X_va))
            results.append({"family": family, "params": params, "estimator": est,
                            "validation_mae_c": round(score, 4),
                            "fit_seconds": round(time.perf_counter() - t0, 2)})
            logger.info("%-18s %-70s val MAE %.4f", family, params, score)
    return results


def run() -> dict:
    cfg = FeatureConfig()
    cols = feature_columns(cfg)
    table, feat_report = build_features(load_processed(processed_path()), cfg)
    splits = chronological_split(table, DEFAULT_SPLITS, cfg.horizon_hours)
    for name, info in describe_splits(splits).items():
        logger.info("%-10s %6d rows  %s → %s", name, info["rows"],
                    info["first_issue_time_utc"], info["last_issue_time_utc"])

    results = tune(splits, cols)
    best = {}
    for r in results:
        if r["family"] not in best or r["validation_mae_c"] < best[r["family"]]["validation_mae_c"]:
            best[r["family"]] = r
    selected = min(best.values(), key=lambda r: r["validation_mae_c"])
    logger.info("Selected on validation: %s %s", selected["family"], selected["params"])

    # Refit on train + validation; score once on test.
    trval = pd.concat([splits["train"], splits["validation"]])
    X_trval, y_trval = xy(trval, cols)
    X_te, y_te = xy(splits["test"], cols)
    test_preds, fitted_models, test_metrics = {}, {}, {}
    for family, r in best.items():
        model = clone(r["estimator"]).fit(X_trval, y_trval)
        fitted_models[family] = model
        test_preds[family] = model.predict(X_te)
        test_metrics[family] = metrics(y_te, test_preds[family])

    sel = selected["family"]
    base_mae = test_metrics["persistence"]["mae_c"]
    sel_mae = test_metrics[sel]["mae_c"]
    boot = block_bootstrap_mae_diff(X_te.index, y_te.to_numpy(), test_preds[sel], test_preds["persistence"])
    boot_diurnal = block_bootstrap_mae_diff(X_te.index, y_te.to_numpy(), test_preds[sel],
                                            test_preds["diurnal_baseline"])
    beats = boot["ci95_high_c"] < 0
    verdict = (
        f"{sel} beats persistence on the test year: MAE {sel_mae:.3f} °C vs {base_mae:.3f} °C "
        f"({100 * (1 - sel_mae / base_mae):.1f}% lower). The 95% week-block bootstrap interval for "
        f"the MAE difference is [{boot['ci95_low_c']:.3f}, {boot['ci95_high_c']:.3f}] °C and excludes zero."
        if beats else
        f"{sel} does NOT reliably beat persistence on the test year: MAE {sel_mae:.3f} °C vs "
        f"{base_mae:.3f} °C. The 95% interval for the difference is "
        f"[{boot['ci95_low_c']:.3f}, {boot['ci95_high_c']:.3f}] °C."
    )
    logger.info(verdict)

    figures = save_figures(X_te.index, y_te.to_numpy(), test_preds[sel], test_preds["persistence"],
                           sel.replace("_", " "), cfg.local_timezone, config.REPORTS_DIR / "figures" / config.RUN_TAG,
                           reference_name=config.WEATHER_MODEL.replace("_", "-").upper())

    provenance = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {p: version(p) for p in PACKAGES},
        "random_seed": SEED,
        "data": {
            "source": "Open-Meteo Historical Weather API",
            "model": config.WEATHER_MODEL,
            "location": config.DELHI.name,
            "requested_lat_lon": [config.DELHI.latitude, config.DELHI.longitude],
            "processed_file": processed_path().name,
        },
    }
    bundle = {
        "model": fitted_models[sel],
        "model_family": sel,
        "params": selected["params"],
        "feature_columns": cols,
        "feature_config": cfg.to_dict(),
        "target": TARGET,
        "trained_on": {
            "first_issue_time_utc": trval.index.min().isoformat(),
            "last_issue_time_utc": trval.index.max().isoformat(),
            "rows": len(trval),
        },
        "validation_mae_c": selected["validation_mae_c"],
        "test_metrics": test_metrics[sel],
        "provenance": provenance,
    }
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)
    logger.info("Saved model bundle: %s", MODEL_PATH.name)

    report = {
        "task": f"Predict temperature_2m at t+1h from data available at t (Delhi, {config.WEATHER_MODEL})",
        "splits": describe_splits(splits),
        "split_note": "Split by issue time. Rows whose target falls in the next split were purged.",
        "feature_rows": feat_report.rows_output,
        "tuning": [{k: v for k, v in r.items() if k != "estimator"} for r in results],
        "best_per_family": {f: {"params": r["params"], "validation_mae_c": r["validation_mae_c"]}
                            for f, r in best.items()},
        "selected": {"family": sel, "params": selected["params"]},
        "test_metrics": test_metrics,
        "test_vs_persistence_bootstrap": boot,
        "test_vs_diurnal_baseline_bootstrap": boot_diurnal,
        "beats_baseline": beats,
        "verdict": verdict,
        "test_breakdown": breakdown(X_te.index, y_te.to_numpy(), test_preds, cfg.local_timezone),
        "figures": [str(p.relative_to(config.PROJECT_ROOT)) for p in figures],
        "provenance": provenance,
    }
    out = config.REPORTS_DIR / f"evaluation_{config.RUN_TAG}_h1.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    logger.info("Evaluation report: %s", out.name)
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    report = run()
    print(json.dumps({"test_metrics": report["test_metrics"], "verdict": report["verdict"]}, indent=2))


if __name__ == "__main__":
    main()
