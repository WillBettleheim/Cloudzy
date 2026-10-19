"""Refit the serving models on all data up to the latest complete month.

Steps
1. End date: the last day of the latest calendar month that both sources cover
   completely. ERA5 reanalysis lags real time by about `REANALYSIS.lag_days`,
   so on 2026-10-19 the end date is 2026-09-30.
2. Download live IFS inputs and the reanalysis target for START_DATE → end date
   with `collect()`. Raw files are cached per calendar year, and the cache name
   contains the chunk's end date, so the current-year chunk is downloaded again
   whenever the end date moves. Complete past years are reused.
3. Validate both with `validate_and_clean`, as in Phase A.
4. Build the direct per-horizon tables exactly as `multistep.run_serving` does:
   IFS features at t → reanalysis temperature at t+h.
5. Hold out the most recent month (the end date's month). Refit each horizon on
   everything before it, with the hyperparameters of the current serving
   bundle. Nothing is re-tuned.
6. Score the refitted models and the current bundle on the held-out month and
   log both MAEs. The refitted models replace the bundle only if their mean MAE
   across horizons is not worse (override with --allow-worse).
7. The new bundle carries provenance (data end date, row counts, raw files,
   package versions). The previous bundle is kept as a dated backup.

The shipped models are exactly the ones scored on the held-out month, so they
never see that month. Dates are passed explicitly; config.START_DATE/END_DATE
and processed_path() are not touched, so the Phase A/B experiments stay
reproducible.

Run:  python -m src.refit [--allow-worse]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone

from src import config
from src.data_collection import collect, load_raw_frames
from src.data_validation import validate_and_clean, validate_metadata
from src.evaluate import mae
from src.features import TARGET, TEMP, FeatureConfig, build_features, feature_columns
from src.multistep import HORIZONS, target_for
from src.service import SERVING_BUNDLE
from src.splits import SplitSpec, chronological_split

logger = logging.getLogger(__name__)

PACKAGES = ["numpy", "pandas", "scikit-learn", "joblib", "requests"]
REPORT_PATH = config.REPORTS_DIR / f"refit_{config.DELHI.slug}.json"


class RefitError(RuntimeError):
    """The refit cannot be carried out safely."""


def refit_end_date(today: date, sources: tuple[config.DataSource, ...] = (config.REANALYSIS,
                                                                           config.LIVE_ARCHIVE)) -> date:
    """Last day of the latest month that every source covers completely."""
    latest = today - timedelta(days=max(s.lag_days for s in sources))
    if (latest + timedelta(days=1)).month != latest.month:
        return latest  # `latest` is itself a month end
    return latest.replace(day=1) - timedelta(days=1)


def holdout_start(end: date) -> date:
    """First day of the held-out month (the month that ends on `end`)."""
    return end.replace(day=1)


def load_source(source: config.DataSource, start: date, end: date,
                raw_dir: Path = config.RAW_DIR) -> tuple[pd.DataFrame, dict]:
    """Download (or reuse cached) raw data, then validate and clean it."""
    paths = collect(start=start, end=end, source=source, raw_dir=raw_dir)
    raw, metadata = load_raw_frames(paths)
    validate_metadata(metadata, config.HOURLY_VARIABLES)
    clean, report = validate_and_clean(raw, list(config.HOURLY_VARIABLES), config.PLAUSIBLE_RANGES, start, end)
    for w in report.warnings:
        logger.warning("%s: %s", source.model, w)
    summary = {
        "rows": report.rows_clean, "start_utc": report.start_utc, "end_utc": report.end_utc,
        "missing_timestamps_inserted": report.missing_timestamps_inserted,
        "missing_values": report.missing_values, "warnings": report.warnings,
        "raw_files": [{"file": m["file"], "retrieved_at_utc": m["retrieved_at_utc"]} for m in metadata],
    }
    return clean, summary


def serving_table(ref: pd.DataFrame, live: pd.DataFrame, h: int) -> pd.DataFrame:
    """IFS features at t → reanalysis temperature at t+h, as in multistep.run_serving."""
    table, _ = build_features(live, FeatureConfig(horizon_hours=h))
    table[TARGET] = target_for(table.index, ref[TEMP], h)
    return table.dropna(subset=[TARGET])


def split_refit_holdout(table: pd.DataFrame, start: date, hold_start: date,
                        h: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Refit rows: issue time and target time both before the held-out month.
    Holdout rows: issue time inside the held-out month (targets inside the data)."""
    cut = pd.Timestamp(hold_start, tz="UTC")
    refit_rows = chronological_split(
        table, (SplitSpec("refit", str(start), str(hold_start)),), horizon_hours=h)["refit"]
    holdout_rows = table[table.index >= cut]
    if holdout_rows.empty:
        raise RefitError(f"No held-out rows for h={h} from {hold_start}")
    return refit_rows, holdout_rows


def trained_until(entry: dict) -> pd.Timestamp | None:
    """Last issue time the current bundle's model for one horizon was trained on."""
    rng = entry.get("deployed_model_trained_on")
    return pd.Timestamp(rng[1]) if rng else None


def refit_horizons(ref: pd.DataFrame, live: pd.DataFrame, current: dict, start: date,
                   hold_start: date) -> tuple[dict, dict]:
    """Returns (new horizon entries, held-out scores per horizon)."""
    cols = current["feature_columns"]
    if cols != feature_columns():
        raise RefitError("Current bundle's feature columns differ from features.feature_columns()")
    new, scores = {}, {}
    for h in HORIZONS:
        old = current["horizons"][h]
        table = serving_table(ref, live, h)
        refit_rows, holdout_rows = split_refit_holdout(table, start, hold_start, h)
        model = clone(old["model"]).fit(refit_rows[cols], refit_rows[TARGET])  # same hyperparameters
        y = holdout_rows[TARGET].to_numpy()
        new_mae = round(mae(y, model.predict(holdout_rows[cols])), 4)
        cur_mae = round(mae(y, old["model"].predict(holdout_rows[cols])), 4)
        seen = trained_until(old)
        leaked = seen is not None and seen + pd.Timedelta(hours=h) >= holdout_rows.index.min()
        if leaked:
            logger.warning("h=%d: the current bundle was trained on part of the held-out month; "
                           "its held-out MAE is optimistic", h)
        scores[h] = {"new_mae_c": new_mae, "current_mae_c": cur_mae, "holdout_rows": len(holdout_rows),
                     "current_saw_holdout": leaked}
        new[h] = {
            "model": model, "params": old["params"],
            "validation_mae_c": old.get("validation_mae_c"),
            "deployed_model_trained_on": [refit_rows.index.min().isoformat(),
                                          refit_rows.index.max().isoformat(), len(refit_rows)],
            "holdout_mae_c": new_mae,
        }
        logger.info("h=%2d  held-out %s: new %.3f  current %.3f  (%d refit rows, %d held-out rows)",
                    h, hold_start.strftime("%Y-%m"), new_mae, cur_mae, len(refit_rows), len(holdout_rows))
    return new, scores


def build_bundle(current: dict, horizons: dict, scores: dict, provenance: dict) -> dict:
    return {
        "kind": current["kind"],
        "inputs_source": current["inputs_source"],
        "target_source": current["target_source"],
        "feature_columns": current["feature_columns"],
        "feature_config": current["feature_config"],
        "horizons": horizons,
        # The service's typical_error_c: the 2025-replay error of this configuration.
        "test_2025_mae_c": current["test_2025_mae_c"],
        "test_2025_provider_optimistic_mae_c": current.get("test_2025_provider_optimistic_mae_c"),
        "holdout_mae_c": {h: s["new_mae_c"] for h, s in scores.items()},
        "provenance": provenance,
    }


def backup_path(path: Path, stamp: date) -> Path:
    """`<name>.backup-YYYY-MM-DD.joblib`, with -2, -3, ... if that date is taken."""
    base = f"{path.stem}.backup-{stamp.isoformat()}"
    candidate, n = path.with_name(base + path.suffix), 2
    while candidate.exists():
        candidate = path.with_name(f"{base}-{n}{path.suffix}")
        n += 1
    return candidate


def install_bundle(bundle: dict, path: Path, stamp: date) -> Path | None:
    """Write `bundle` to `path`, moving any existing file to a dated backup first.

    The new bundle is written to a temporary file before anything is moved, so
    a failed write leaves the current bundle in place."""
    tmp = path.with_suffix(".joblib.tmp")
    joblib.dump(bundle, tmp, compress=3)  # the deployed bundle is committed to git; keep it small
    backup = None
    if path.exists():
        backup = backup_path(path, stamp)
        os.replace(path, backup)
    os.replace(tmp, path)
    from src.artifacts import write_metadata
    write_metadata(path, bundle)  # the sidecar must always describe the exact file now in place
    return backup


def run(today: date | None = None, start: date = config.START_DATE, raw_dir: Path = config.RAW_DIR,
        bundle_path: Path = SERVING_BUNDLE, report_path: Path | None = REPORT_PATH,
        allow_worse: bool = False) -> dict:
    today = today or datetime.now(timezone.utc).date()
    end = refit_end_date(today)
    hold = holdout_start(end)
    logger.info("Refit: data %s → %s, held-out month %s", start, end, hold.strftime("%Y-%m"))
    if not bundle_path.exists():
        raise RefitError(f"{bundle_path} not found; run `python -m src.multistep` first")
    current = joblib.load(bundle_path)

    ref, ref_info = load_source(config.REANALYSIS, start, end, raw_dir)
    live, live_info = load_source(config.LIVE_ARCHIVE, start, end, raw_dir)
    horizons, scores = refit_horizons(ref, live, current, start, hold)

    new_mean = float(np.mean([s["new_mae_c"] for s in scores.values()]))
    cur_mean = float(np.mean([s["current_mae_c"] for s in scores.values()]))
    replace = new_mean <= cur_mean or allow_worse
    logger.info("Held-out mean MAE: new %.4f, current %.4f → %s", new_mean, cur_mean,
                "replacing bundle" if replace else "keeping current bundle")

    provenance = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": "python -m src.refit",
        "data_start_date": start.isoformat(),
        "data_end_date": end.isoformat(),
        "holdout_month": [hold.isoformat(), end.isoformat()],
        "refit_data_end_exclusive": hold.isoformat(),
        "sources": {config.REANALYSIS.model: ref_info, config.LIVE_ARCHIVE.model: live_info},
        "rows_per_horizon": {h: {"refit": e["deployed_model_trained_on"][2],
                                 "holdout": scores[h]["holdout_rows"]} for h, e in horizons.items()},
        "hyperparameters_from": bundle_path.name,
        "previous_bundle_trained_on": {h: current["horizons"][h].get("deployed_model_trained_on")
                                       for h in HORIZONS},
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {p: version(p) for p in PACKAGES},
    }
    backup = None
    if replace:
        bundle = build_bundle(current, horizons, scores, provenance)
        backup = install_bundle(bundle, bundle_path, today)
        logger.info("Saved %s (previous bundle kept as %s)", bundle_path.name, backup.name if backup else None)

    report = {
        "data_end_date": end.isoformat(),
        "holdout_month": hold.strftime("%Y-%m"),
        "holdout_mae_c": {str(h): s for h, s in scores.items()},
        "mean_holdout_mae_c": {"new": round(new_mean, 4), "current": round(cur_mean, 4)},
        "replaced": replace,
        "backup": backup.name if backup else None,
        "provenance": {k: v for k, v in provenance.items() if k != "sources"}
        | {"sources": {k: {kk: vv for kk, vv in v.items() if kk != "missing_values"}
                       for k, v in provenance["sources"].items()}},
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, default=str))
        logger.info("Refit report: %s", report_path.name)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-worse", action="store_true",
                        help="replace the bundle even if the refitted models score worse on the held-out month")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    r = run(allow_worse=args.allow_worse)
    print(f"Data to {r['data_end_date']}, held-out month {r['holdout_month']} (MAE °C)")
    for h, s in r["holdout_mae_c"].items():
        print(f"  h={h:>2}  new {s['new_mae_c']:.3f}  current {s['current_mae_c']:.3f}  "
              f"({s['holdout_rows']} rows)")
    m = r["mean_holdout_mae_c"]
    print(f"  mean  new {m['new']:.3f}  current {m['current']:.3f}  → "
          f"{'replaced, backup ' + str(r['backup']) if r['replaced'] else 'kept current bundle'}")


if __name__ == "__main__":
    main()
