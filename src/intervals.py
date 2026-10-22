"""Prediction intervals for Cloudzy Core: split-conformal ranges, calibrated and then validated on later data.

Why: the app used to show only a 2025 average error next to each prediction, which says nothing about how far
off a given prediction may be. A prediction interval does, if its coverage has been checked on data the
calibration never saw.

Method (per horizon, asymmetric split conformal on residuals r = observed - predicted)
- An interval at nominal level 1 - α is [ŷ + q_lo, ŷ + q_hi], where q_lo and q_hi are the empirical
  α/2 and 1 - α/2 quantiles of calibration residuals, each taken at the finite-sample-corrected rank
  ceil((n + 1)·p)/n. Calibration residuals always come from a model predicting data it was not fitted on.
- Two levels: 80% and 95%.

Two chronological stages, no period reused (all models use the deployed bundle's hyperparameters; no tuning)
- **Method check:** fit on 2023-2024; calibrate on residuals for 2025-01..2025-06; measure coverage on
  2025-07..2025-12. This tests whether residual quantiles from one half-year transfer to the next.
- **Deployed intervals:** fit the same configuration on 2023-2025; calibrate on its residuals for 2026-01..
  2026-08 (the most recent out-of-sample errors, after the IFS drift documented in the model card). Those
  quantiles are what the app shows. They are then checked on the **deployed** models' predictions for the
  held-out month 2026-09, which the deployed models never saw (src.refit). That check is one month, ~700
  hours per horizon, consecutive and therefore correlated: it's evidence, not proof.

What the interval does not cover: errors from live inputs arriving later than in the replay (the replay uses
the newest model run for each hour), reanalysis artefacts, and anything outside Delhi. Live coverage is tracked
on evaluated predictions (src.monitoring) once enough of them exist.

All inputs come from the cached raw files (no download when the cache is complete).

Run:  python -m src.intervals
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone

from src import config
from src.features import TARGET
from src.multistep import HORIZONS
from src.refit import load_source, serving_table
from src.service import SERVING_BUNDLE

logger = logging.getLogger(__name__)

REPORT_PATH = config.REPORTS_DIR / f"intervals_{config.DELHI.slug}.json"
LEVELS = (0.80, 0.95)
DATA_START, DATA_END = date(2023, 1, 1), date(2026, 9, 30)
STAGES = {
    "method_check": {"fit": ("2023-01-01", "2025-01-01"), "calibrate": ("2025-01-01", "2025-07-01"),
                     "validate": ("2025-07-01", "2026-01-01")},
    "deployed": {"fit": ("2023-01-01", "2026-01-01"), "calibrate": ("2026-01-01", "2026-09-01"),
                 "validate": ("2026-09-01", "2026-10-01")},
}


def conformal_quantiles(residuals: np.ndarray, level: float) -> tuple[float, float]:
    """(q_lo, q_hi) so that [ŷ + q_lo, ŷ + q_hi] covers `level` of exchangeable future residuals."""
    r = np.sort(np.asarray(residuals, float)[np.isfinite(residuals)])
    n = len(r)
    if n < 50:
        raise ValueError(f"too few calibration residuals ({n}) for a {level:.0%} interval")
    alpha = 1 - level
    k_hi = min(n, math.ceil((n + 1) * (1 - alpha / 2)))      # finite-sample correction (rank, 1-based)
    k_lo = max(1, math.floor((n + 1) * (alpha / 2)))
    return float(r[k_lo - 1]), float(r[k_hi - 1])


def coverage(y: np.ndarray, pred: np.ndarray, q: tuple[float, float]) -> dict:
    lo, hi = pred + q[0], pred + q[1]
    inside = (y >= lo) & (y <= hi)
    return {"coverage": round(float(inside.mean()), 4), "n": len(y), "mean_width_c": round(q[1] - q[0], 3),
            "below": round(float((y < lo).mean()), 4), "above": round(float((y > hi).mean()), 4)}


def _rows(table: pd.DataFrame, span: tuple[str, str], h: int, target_inside: bool) -> pd.DataFrame:
    """Issue times inside [start, end); with `target_inside`, targets too (no training label past the end)."""
    start, end = (pd.Timestamp(s, tz="UTC") for s in span)
    sel = table[(table.index >= start) & (table.index < end)]
    if target_inside:
        sel = sel[sel.index + pd.Timedelta(hours=h) < end]
    return sel


def study(ref: pd.DataFrame, live: pd.DataFrame, bundle: dict) -> dict:
    cols = bundle["feature_columns"]
    out: dict = {"levels": list(LEVELS), "stages": STAGES, "horizons": {}}
    for h in HORIZONS:
        entry = bundle["horizons"][h]
        table = serving_table(ref, live, h)
        res: dict = {}
        for stage, spans in STAGES.items():
            fit = _rows(table, spans["fit"], h, target_inside=True)
            cal = _rows(table, spans["calibrate"], h, target_inside=True)
            val = _rows(table, spans["validate"], h, target_inside=False)
            model = clone(entry["model"]).fit(fit[cols], fit[TARGET])
            cal_res = cal[TARGET].to_numpy() - model.predict(cal[cols])
            # Validation: the method check uses the stage's own model; the deployed stage checks the deployed
            # model on its held-out month.
            judge = model if stage == "method_check" else entry["model"]
            val_pred = judge.predict(val[cols])
            y = val[TARGET].to_numpy()
            levels = {}
            for lv in LEVELS:
                q = conformal_quantiles(cal_res, lv)
                levels[f"{lv:.2f}"] = {"q_lo_c": round(q[0], 3), "q_hi_c": round(q[1], 3),
                                       "validation": coverage(y, val_pred, q)}
                if stage == "method_check":            # coverage by calendar month: does it hold across seasons?
                    months = val.index.month
                    levels[f"{lv:.2f}"]["validation_by_month"] = {
                        int(m): coverage(y[months == m], val_pred[months == m], q)["coverage"]
                        for m in sorted(set(months))}
            res[stage] = {"fit_rows": len(fit), "calibration_rows": len(cal), "validation_rows": len(val),
                          "fit_issue_range": [fit.index.min().isoformat(), fit.index.max().isoformat()],
                          "calibration_bias_c": round(float(np.mean(cal_res)), 3), "levels": levels}
        out["horizons"][h] = res
        logger.info("h=%2d  deployed 80%%: %s  95%%: %s", h,
                    res["deployed"]["levels"]["0.80"]["validation"], res["deployed"]["levels"]["0.95"]["validation"])
    return out


def run() -> dict:
    ref, ref_summary = load_source(config.REANALYSIS, DATA_START, DATA_END)
    live, live_summary = load_source(config.LIVE_ARCHIVE, DATA_START, DATA_END)
    bundle = joblib.load(SERVING_BUNDLE)
    report = study(ref, live, bundle)
    report["provenance"] = {"created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                            "command": "python -m src.intervals", "bundle": SERVING_BUNDLE.name,
                            "bundle_sha256": bundle_sha256(),
                            "bundle_trained_through": bundle.get("provenance", {}).get("refit_data_end_exclusive"),
                            "reference": config.REANALYSIS.model, "inputs": config.LIVE_ARCHIVE.model,
                            "reference_rows": ref_summary["rows"], "input_rows": live_summary["rows"]}
    REPORT_PATH.write_text(json.dumps(report, indent=1))
    return report


# --- Serving --------------------------------------------------------------------------------------

@lru_cache(maxsize=4)
def bundle_sha256(path: str = str(SERVING_BUNDLE)) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def _report(path: str = str(REPORT_PATH)) -> dict | None:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else None


def interval(horizon_h: int, predicted: float, level: float = 0.80, path: Path = REPORT_PATH,
             bundle_path: Path = SERVING_BUNDLE) -> dict | None:
    """The deployed interval for one prediction, with its validated coverage, or None if not available.

    Only served for the exact bundle it was calibrated for (SHA-256 match): after a refit, no range is shown
    until `python -m src.intervals` has recalibrated and re-validated it."""
    rep = _report(str(path))
    if not rep or rep.get("provenance", {}).get("bundle_sha256") != bundle_sha256(str(bundle_path)):
        return None
    try:
        lv = rep["horizons"][str(horizon_h)]["deployed"]["levels"][f"{level:.2f}"]
    except KeyError:
        return None
    return {"level": level, "lower_c": round(predicted + lv["q_lo_c"], 2), "upper_c": round(predicted + lv["q_hi_c"], 2),
            "validated_coverage": lv["validation"]["coverage"], "validation_n": lv["validation"]["n"],
            "validation_period": rep["stages"]["deployed"]["validate"]}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    rep = run()
    for h, r in rep["horizons"].items():
        mc, dp = r["method_check"]["levels"], r["deployed"]["levels"]
        print(f"+{h:>2} h  method check 80% {mc['0.80']['validation']['coverage']:.1%} 95% "
              f"{mc['0.95']['validation']['coverage']:.1%} | deployed Sept-2026 80% "
              f"{dp['0.80']['validation']['coverage']:.1%} (width {dp['0.80']['validation']['mean_width_c']} °C) "
              f"95% {dp['0.95']['validation']['coverage']:.1%}")


if __name__ == "__main__":
    main()
