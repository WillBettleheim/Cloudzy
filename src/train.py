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
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import joblib
import pandas as pd
from sklearn.base import clone

from src import config
from src.data_validation import load_processed, processed_path
from src.evaluate import block_bootstrap_mae_diff, breakdown, mae, metrics, save_figures
from src.features import TARGET, TEMP, FeatureConfig, build_features, feature_columns
from src.models import SEED, candidates
from src.splits import DEFAULT_SPLITS, chronological_split, describe_splits, proportional_splits

logger = logging.getLogger(__name__)

MODEL_PATH = config.MODELS_DIR / f"cloudzy_core_{config.RUN_TAG}_h1.joblib"  # default Delhi model
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


class InsufficientDataError(ValueError):
    """The dataset is too small or too gappy for a meaningful chronological evaluation."""


MIN_SPLIT_ROWS = {"train": 24 * 90, "validation": 24 * 30, "test": 24 * 30}


def tag_for(location: config.Location, source: config.DataSource, start, end) -> str:
    """File-name tag. The default Delhi run keeps its historical names (= config.RUN_TAG)."""
    base = f"{location.slug}_{source.model}"
    return base if (start, end) == (config.START_DATE, config.END_DATE) else f"{base}_{start}_{end}"


def model_path(location: config.Location = config.DELHI, source: config.DataSource = config.REANALYSIS,
               start=config.START_DATE, end=config.END_DATE) -> Path:
    return config.MODELS_DIR / f"cloudzy_core_{tag_for(location, source, start, end)}_h1.joblib"


def splits_for(start, end) -> tuple:
    """One full calendar year each for the default period; otherwise 70/15/15 by time."""
    return DEFAULT_SPLITS if (start, end) == (config.START_DATE, config.END_DATE) else proportional_splits(start, end)


def check_size(splits: dict) -> None:
    small = {n: len(df) for n, df in splits.items() if len(df) < MIN_SPLIT_ROWS[n]}
    if small:
        raise InsufficientDataError(
            "Too little data for a meaningful evaluation: " + ", ".join(f"{n} has {r} rows (needs {MIN_SPLIT_ROWS[n]})"
                                                                       for n, r in small.items())
            + ". Use a longer date range.")


def alignment_check(table: pd.DataFrame, processed: pd.DataFrame, n: int = 5) -> list[dict]:
    """Show, for sample rows, that the target is the raw temperature exactly one hour after t."""
    rows = []
    for t in table.index[:: max(1, len(table) // n)][:n]:
        raw_next = processed.loc[t + pd.Timedelta(hours=1), TEMP]
        rows.append({"issue_time_utc": t.isoformat(), "temperature_at_t_c": float(table.loc[t, TEMP]),
                     "target_c": float(table.loc[t, TARGET]), "raw_temperature_at_t_plus_1h_c": float(raw_next)})
        if abs(rows[-1]["target_c"] - rows[-1]["raw_temperature_at_t_plus_1h_c"]) > 1e-9:
            raise AssertionError(f"Target misaligned at {t}: {rows[-1]}")
    return rows


def select_and_evaluate(table: pd.DataFrame, specs, cols: list[str], horizon_hours: int = 1) -> dict:
    """Tune on validation, refit on train+validation, score every family once on test."""
    splits = chronological_split(table, specs, horizon_hours)
    check_size(splits)
    for name, info in describe_splits(splits).items():
        logger.info("%-10s %6d rows  %s → %s", name, info["rows"], info["first_issue_time_utc"], info["last_issue_time_utc"])
    results = tune(splits, cols)
    best = {}
    for r in results:
        if r["family"] not in best or r["validation_mae_c"] < best[r["family"]]["validation_mae_c"]:
            best[r["family"]] = r
    selected = min(best.values(), key=lambda r: r["validation_mae_c"])  # validation only: test is never consulted
    logger.info("Selected on validation: %s %s", selected["family"], selected["params"])
    trval = pd.concat([splits["train"], splits["validation"]])
    X_trval, y_trval = xy(trval, cols)
    X_te, y_te = xy(splits["test"], cols)
    test_preds, fitted, test_metrics = {}, {}, {}
    for family, r in best.items():
        model = clone(r["estimator"]).fit(X_trval, y_trval)
        fitted[family] = model
        test_preds[family] = model.predict(X_te)
        test_metrics[family] = metrics(y_te, test_preds[family])
    return {"splits": splits, "results": results, "best": best, "selected": selected, "trval": trval,
            "X_te": X_te, "y_te": y_te, "fitted": fitted, "test_preds": test_preds, "test_metrics": test_metrics}


def run(location: config.Location = config.DELHI, start=config.START_DATE, end=config.END_DATE,
        source: config.DataSource = config.REANALYSIS) -> dict:
    cfg = FeatureConfig(local_timezone=location.local_timezone)
    cols = feature_columns(cfg)
    tag = tag_for(location, source, start, end)
    processed = load_processed(processed_path(location, source, start, end))
    table, feat_report = build_features(processed, cfg)
    specs = splits_for(start, end)
    ev = select_and_evaluate(table, specs, cols, cfg.horizon_hours)
    splits, best, selected = ev["splits"], ev["best"], ev["selected"]
    trval, X_te, y_te = ev["trval"], ev["X_te"], ev["y_te"]
    test_preds, fitted_models, test_metrics = ev["test_preds"], ev["fitted"], ev["test_metrics"]
    alignment = alignment_check(splits["test"], processed)

    sel = selected["family"]
    base_mae = test_metrics["persistence"]["mae_c"]
    sel_mae = test_metrics[sel]["mae_c"]
    period = "test year" if specs is DEFAULT_SPLITS else "test period"
    boot = block_bootstrap_mae_diff(X_te.index, y_te.to_numpy(), test_preds[sel], test_preds["persistence"])
    boot_diurnal = block_bootstrap_mae_diff(X_te.index, y_te.to_numpy(), test_preds[sel],
                                            test_preds["diurnal_baseline"])
    beats = boot["ci95_high_c"] < 0
    verdict = (
        f"{sel} beats persistence on the {period}: MAE {sel_mae:.3f} °C vs {base_mae:.3f} °C "
        f"({100 * (1 - sel_mae / base_mae):.1f}% lower). The 95% week-block bootstrap interval for "
        f"the MAE difference is [{boot['ci95_low_c']:.3f}, {boot['ci95_high_c']:.3f}] °C and excludes zero."
        if beats else
        f"{sel} does NOT reliably beat persistence on the {period}: MAE {sel_mae:.3f} °C vs "
        f"{base_mae:.3f} °C. The 95% interval for the difference is "
        f"[{boot['ci95_low_c']:.3f}, {boot['ci95_high_c']:.3f}] °C."
    )
    logger.info(verdict)

    figures = save_figures(X_te.index, y_te.to_numpy(), test_preds[sel], test_preds["persistence"],
                           sel.replace("_", " "), cfg.local_timezone, config.REPORTS_DIR / "figures" / tag,
                           reference_name=source.model.replace("_", "-").upper())

    provenance = {
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {p: version(p) for p in PACKAGES},
        "random_seed": SEED,
        "data": {
            "source": "Open-Meteo Historical Weather API (reanalysis: model-based, not station observations)",
            "model": source.model,
            "location": location.name,
            "requested_lat_lon": [location.latitude, location.longitude],
            "local_timezone": location.local_timezone,
            "period": [str(start), str(end)],
            "processed_file": processed_path(location, source, start, end).name,
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
    path = model_path(location, source, start, end)
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)
    from src.artifacts import write_metadata
    write_metadata(path, bundle, {"splits": describe_splits(splits), "split_rule": "calendar years"
                                 if specs is DEFAULT_SPLITS else "70/15/15 by time"})
    logger.info("Saved model bundle: %s (+ metadata sidecar)", path.name)

    report = {
        "task": f"Predict temperature_2m at t+1h from data available at t ({location.name}, {source.model})",
        "location": {"slug": location.slug, "name": location.name, "latitude": location.latitude,
                     "longitude": location.longitude, "timezone": location.local_timezone},
        "period": [str(start), str(end)],
        "split_rule": "calendar years" if specs is DEFAULT_SPLITS else "70/15/15 by time",
        "splits": describe_splits(splits),
        "split_note": "Split by issue time. Rows whose target falls in the next split were purged.",
        "feature_rows": feat_report.rows_output,
        "rows_dropped_incomplete": feat_report.rows_dropped_incomplete,
        "target_alignment_check": alignment,
        "tuning": [{k: v for k, v in r.items() if k != "estimator"} for r in ev["results"]],
        "best_per_family": {f: {"params": r["params"], "validation_mae_c": r["validation_mae_c"]}
                            for f, r in best.items()},
        "selected": {"family": sel, "params": selected["params"],
                     "reason": "lowest validation MAE among all candidates (test set not consulted)"},
        "test_metrics": test_metrics,
        "test_vs_persistence_bootstrap": boot,
        "test_vs_diurnal_baseline_bootstrap": boot_diurnal,
        "beats_baseline": beats,
        "verdict": verdict,
        "test_breakdown": breakdown(X_te.index, y_te.to_numpy(), test_preds, cfg.local_timezone),
        "figures": [str(p.relative_to(config.PROJECT_ROOT)) for p in figures],
        "model_file": path.name,
        "provenance": provenance,
    }
    out = config.REPORTS_DIR / f"evaluation_{tag}_h1.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    logger.info("Evaluation report: %s", out.name)
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    report = run()
    print(json.dumps({"test_metrics": report["test_metrics"], "verdict": report["verdict"]}, indent=2))


if __name__ == "__main__":
    main()
