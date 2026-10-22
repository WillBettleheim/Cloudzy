"""Candidate upgrade for Cloudzy Core: add a lagged ECMWF forecast for the target hour (a MOS-style feature).

Question: does the deployed model get better if, besides the last 25 hours of IFS inputs, it also sees what an
earlier ECMWF IFS run predicted for the target hour?

Leakage
- The feature must have been *published* before the prediction time t. Open-Meteo's Previous Runs API gives, for
  each valid hour T, `temperature_2m_previous_day2`: the value forecast 48 hours before T. For T = t + h with
  h ≤ 24, that run started at T - 48 h ≤ t - 24 h, and IFS runs are published within about 8 hours, so the value was
  public at least ~16 hours before t. (`previous_day1` would not be safe for h = 24: that run starts at t itself.)
- Live, the same value comes from the same API, so inference uses exactly what training used.

Protocol (identical for the control and the candidate; nothing here was tuned on the test period)
- Data: 2024-02-05 (first complete previous-runs day) to 2026-09-30, Delhi grid cell; inputs ecmwf_ifs (as
  deployed), lagged forecast ecmwf_ifs025, target era5_seamless reanalysis.
- Tune: fit on 2024-02-05 → 2025-07-01, choose among the deployed model's 8 residual-HGB configurations by MAE on
  2025-07 → 2026-01.
- Test: refit the chosen configuration on 2024-02-05 → 2026-01-01, score once on 2026-01 → 2026-09 (8 months),
  and report September 2026 separately (the month the deployed bundle was accepted on).
- Control = the deployed feature set (27 features). Candidate = the same + the lagged forecast and its difference
  from the current temperature. Baselines on the same rows: persistence, the lagged forecast itself, the
  (optimistic) archived IFS value at t + h, and ridge regression on the candidate features.
- Week-block bootstrap 95% interval for MAE(candidate) - MAE(control).

Decision rule, fixed before running: adopt the candidate only if, on the test period, (1) its MAE is lower at
every horizon, (2) the 95% interval of the MAE difference excludes zero at ≥ 4 of 6 horizons, and (3) no horizon
is worse on September 2026 by more than 0.05 °C. Otherwise the deployed model is retained.

Deploy (only after the rule says adopt): `python -m src.mos --deploy` trains the candidate on all data up to the
latest complete month, scores it and the current bundle on that held-out month, and installs it (keeping a dated
backup) only if its mean held-out MAE isn't worse. The bundle records the lagged-forecast spec, so the service, the
monthly refit and the interval study all add the same feature.

Run:  python -m src.mos     → reports/mos_delhi.json
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
from sklearn.base import clone
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src import config
from src.compact import compact_grid
from src.data_collection import INTERACTIVE, fetch_json
from src.evaluate import block_bootstrap_mae_diff, mae, rmse
from src.features import TARGET, TEMP, feature_columns
from src.multistep import HORIZONS
from src.refit import load_source, serving_table

logger = logging.getLogger(__name__)

PREV_FILE = config.RAW_DIR / "previous_runs" / "delhi_ecmwf_ifs025_t2m_previous_day2_2024-01-01_2026-09-30.json"
PREV_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
PREV_MODEL = "ecmwf_ifs025"
PREV_VAR = "temperature_2m_previous_day2"
REPORT_PATH = config.REPORTS_DIR / f"mos_{config.DELHI.slug}.json"
LAG_FEATURES = ["ifs_prev2_target", "ifs_prev2_minus_now"]
START = "2024-02-05"
SPANS = {"fit": (START, "2025-07-01"), "val": ("2025-07-01", "2026-01-01"), "refit": (START, "2026-01-01"),
         "test": ("2026-01-01", "2026-09-01"), "september": ("2026-09-01", "2026-10-01")}


LAGGED_SPEC = {"model": PREV_MODEL, "variable": PREV_VAR, "features": LAG_FEATURES, "data_start": START,
               "published_before_issue": "the run starts 48 h before the target hour, so at least 24 h before t"}


def parse_previous_runs(d: dict) -> pd.Series:
    """Hourly lagged forecast (°C), UTC index. Units and time zone are checked, missing values stay missing."""
    if not isinstance(d, dict) or d.get("error"):
        raise ValueError(f"previous-runs response unusable: {d.get('reason') if isinstance(d, dict) else d}")
    if d.get("utc_offset_seconds", 0) != 0:
        raise ValueError("expected UTC timestamps")
    if d.get("hourly_units", {}).get(PREV_VAR) != "°C":
        raise ValueError(f"expected {PREV_VAR} in °C")
    h = d["hourly"]
    if len(h.get("time", [])) != len(h.get(PREV_VAR, [])):
        raise ValueError("previous-runs times and values differ in length")
    return pd.Series(h[PREV_VAR], index=pd.to_datetime(h["time"], utc=True), dtype="float64")


def load_previous_runs(path: Path = PREV_FILE) -> pd.Series:
    return parse_previous_runs(json.loads(path.read_text()))


def _params(location: config.Location) -> dict:
    return {"latitude": location.latitude, "longitude": location.longitude, "hourly": PREV_VAR, "models": PREV_MODEL,
            "timezone": "GMT"}


def fetch_previous_runs(start: date, end: date, location: config.Location = config.DELHI,
                        cache_dir: Path = config.RAW_DIR / "previous_runs", session=None) -> pd.Series:
    """Archived lagged forecasts for a date range; downloaded once and cached under data/raw (git-ignored)."""
    path = cache_dir / f"{location.slug}_{PREV_MODEL}_t2m_previous_day2_{start}_{end}.json"
    if not path.exists():
        payload = fetch_json(PREV_URL, _params(location) | {"start_date": str(start), "end_date": str(end)},
                             session=session)
        parse_previous_runs(payload)                      # validate before caching
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    return load_previous_runs(path)


def fetch_live_lagged(location: config.Location = config.DELHI, session: requests.Session | None = None) -> pd.Series:
    """Lagged forecasts from 2 days ago to 2 days ahead, for live inference."""
    payload = fetch_json(PREV_URL, _params(location) | {"past_days": 2, "forecast_days": 2}, session=session,
                         **INTERACTIVE)
    return parse_previous_runs(payload)


def with_lag_features(table: pd.DataFrame, prev: pd.Series, h: int) -> pd.DataFrame:
    """Add the lagged forecast for t + h (published before t; see module docstring)."""
    out = table.copy()
    out["ifs_prev2_target"] = prev.reindex(out.index + pd.Timedelta(hours=h)).to_numpy()
    out["ifs_prev2_minus_now"] = out["ifs_prev2_target"] - out[TEMP]
    return out


def _span(table: pd.DataFrame, key: str, h: int, target_inside: bool = True) -> pd.DataFrame:
    start, end = (pd.Timestamp(x, tz="UTC") for x in SPANS[key])
    sel = table[(table.index >= start) & (table.index < end)]
    return sel[sel.index + pd.Timedelta(hours=h) < end] if target_inside else sel


def _tune(fit: pd.DataFrame, val: pd.DataFrame, cols: list[str]):
    scored = []
    for params, est in compact_grid():
        m = clone(est).fit(fit[cols], fit[TARGET])
        scored.append((mae(val[TARGET], m.predict(val[cols])), params, est))
    best = min(scored, key=lambda s: s[0])
    return best[1], best[2], round(best[0], 4)


def evaluate_horizon(table: pd.DataFrame, h: int) -> dict:
    base_cols = feature_columns()
    cand_cols = base_cols + LAG_FEATURES
    table = table.dropna(subset=LAG_FEATURES)
    fit, val = _span(table, "fit", h), _span(table, "val", h)
    refit, test, sept = _span(table, "refit", h), _span(table, "test", h, False), _span(table, "september", h, False)
    out: dict = {"rows": {"fit": len(fit), "val": len(val), "refit": len(refit), "test": len(test),
                          "september": len(sept)}}
    preds: dict = {}
    for name, cols in (("control", base_cols), ("candidate", cand_cols)):
        params, est, val_mae = _tune(fit, val, cols)
        model = clone(est).fit(refit[cols], refit[TARGET])
        preds[name] = {"test": model.predict(test[cols]), "september": model.predict(sept[cols])}
        out[name] = {"params": params, "val_mae_c": val_mae}
    ridge = make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-2, 3, 12))).fit(
        refit[cand_cols], refit[TARGET] - refit[TEMP])
    for split, rows in (("test", test), ("september", sept)):
        preds.setdefault("persistence", {})[split] = rows[TEMP].to_numpy()
        preds.setdefault("lagged_ifs_forecast", {})[split] = rows["ifs_prev2_target"].to_numpy()
        preds.setdefault("ridge_candidate_features", {})[split] = ridge.predict(rows[cand_cols]) + rows[TEMP].to_numpy()
    for split, rows in (("test", test), ("september", sept)):
        y = rows[TARGET].to_numpy()
        out[split] = {name: {"mae_c": round(mae(y, p[split]), 4), "rmse_c": round(rmse(y, p[split]), 4),
                             "bias_c": round(float(np.mean(p[split] - y)), 4), "n": len(y)}
                      for name, p in preds.items()}
        out[split]["candidate_minus_control"] = block_bootstrap_mae_diff(
            rows.index, y, preds["candidate"][split], preds["control"][split])
        out[split]["skill_vs_control"] = round(1 - out[split]["candidate"]["mae_c"] / out[split]["control"]["mae_c"], 4)
    return out


def decide(results: dict) -> dict:
    """Apply the pre-registered rule (see module docstring)."""
    lower_everywhere = all(r["test"]["candidate"]["mae_c"] < r["test"]["control"]["mae_c"] for r in results.values())
    significant = sum(r["test"]["candidate_minus_control"]["ci95_high_c"] < 0 for r in results.values())
    sept_ok = all(r["september"]["candidate"]["mae_c"] - r["september"]["control"]["mae_c"] <= 0.05
                  for r in results.values())
    adopt = lower_everywhere and significant >= 4 and sept_ok
    return {"adopt": adopt, "lower_mae_at_every_horizon": lower_everywhere,
            "horizons_with_significant_gain": significant, "september_no_regression_over_0_05": sept_ok}


def run() -> dict:
    ref, _ = load_source(config.REANALYSIS, date(2023, 1, 1), date(2026, 9, 30))
    live, _ = load_source(config.LIVE_ARCHIVE, date(2023, 1, 1), date(2026, 9, 30))
    prev = load_previous_runs()
    results = {}
    for h in HORIZONS:
        table = with_lag_features(serving_table(ref, live, h), prev, h)
        results[h] = evaluate_horizon(table, h)
        r = results[h]
        logger.info("h=%2d test MAE control %.3f candidate %.3f (diff %+.3f [%+.3f, %+.3f]) lagged IFS %.3f",
                    h, r["test"]["control"]["mae_c"], r["test"]["candidate"]["mae_c"],
                    r["test"]["candidate_minus_control"]["mae_diff_c"], r["test"]["candidate_minus_control"]["ci95_low_c"],
                    r["test"]["candidate_minus_control"]["ci95_high_c"], r["test"]["lagged_ifs_forecast"]["mae_c"])
    report = {"spans": SPANS, "lag_features": LAG_FEATURES, "lagged_source": f"{PREV_MODEL} {PREV_VAR}",
              "horizons": results, "decision": decide(results),
              "provenance": {"created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                             "command": "python -m src.mos", "previous_runs_file": PREV_FILE.name,
                             "previous_runs_url": PREV_URL}}
    REPORT_PATH.write_text(json.dumps(report, indent=1, default=str))
    return report


def deploy(today: date | None = None, bundle_path: Path | None = None, allow_worse: bool = False) -> dict:
    """Train the adopted candidate on all data before the latest complete month and install it if it holds up."""
    from src.artifacts import file_sha256
    from src.refit import holdout_start, install_bundle, refit_end_date
    from src.service import SERVING_BUNDLE
    bundle_path = Path(bundle_path or SERVING_BUNDLE)
    report = json.loads(REPORT_PATH.read_text())
    if not report["decision"]["adopt"]:
        raise RuntimeError("The experiment's decision rule did not adopt the candidate; nothing to deploy.")
    today = today or datetime.now(UTC).date()
    end = refit_end_date(today)
    hold = holdout_start(end)
    current = joblib.load(bundle_path)
    start = date.fromisoformat(START)
    ref, _ = load_source(config.REANALYSIS, date(2023, 1, 1), end)
    live, _ = load_source(config.LIVE_ARCHIVE, date(2023, 1, 1), end)
    prev = fetch_previous_runs(date(2024, 1, 1), end)
    cols = feature_columns() + LAG_FEATURES
    grid = {json.dumps(p, sort_keys=True): est for p, est in compact_grid()}
    horizons, scores = {}, {}
    for h in HORIZONS:
        table = with_lag_features(serving_table(ref, live, h), prev, h).dropna(subset=LAG_FEATURES)
        cut = pd.Timestamp(hold, tz="UTC")
        fit = table[(table.index >= pd.Timestamp(start, tz="UTC")) & (table.index + pd.Timedelta(hours=h) < cut)]
        held = table[table.index >= cut]
        params = report["horizons"][str(h)]["candidate"]["params"]
        model = clone(grid[json.dumps(params, sort_keys=True)]).fit(fit[cols], fit[TARGET])
        y = held[TARGET].to_numpy()
        base_cols = [c for c in current["feature_columns"] if c not in LAG_FEATURES]
        new_mae = round(mae(y, model.predict(held[cols])), 4)
        cur_mae = round(mae(y, current["horizons"][h]["model"].predict(held[base_cols])), 4)
        scores[h] = {"new_mae_c": new_mae, "current_mae_c": cur_mae, "holdout_rows": len(held)}
        horizons[h] = {"model": model, "params": params, "holdout_mae_c": new_mae,
                       "deployed_model_trained_on": [fit.index.min().isoformat(), fit.index.max().isoformat(), len(fit)]}
        logger.info("h=%2d held-out %s: new %.3f current %.3f", h, hold.strftime("%Y-%m"), new_mae, cur_mae)
    new_mean = float(np.mean([s["new_mae_c"] for s in scores.values()]))
    cur_mean = float(np.mean([s["current_mae_c"] for s in scores.values()]))
    install = new_mean <= cur_mean or allow_worse
    test = {h: report["horizons"][str(h)]["test"] for h in HORIZONS}
    bundle = {
        "kind": current["kind"], "inputs_source": current["inputs_source"], "target_source": current["target_source"],
        "feature_columns": cols, "feature_config": current["feature_config"], "horizons": horizons,
        "lagged_forecast": LAGGED_SPEC,
        "typical_error_c": {h: test[h]["candidate"]["mae_c"] for h in HORIZONS},
        "typical_error_period": f"{SPANS['test'][0][:7]} to 2026-08 test",
        "test_provider_lagged_mae_c": {h: test[h]["lagged_ifs_forecast"]["mae_c"] for h in HORIZONS},
        "holdout_mae_c": {h: s["new_mae_c"] for h, s in scores.items()},
        "provenance": {
            "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"), "command": "python -m src.mos --deploy",
            "data_start_date": START, "data_end_date": end.isoformat(), "holdout_month": [hold.isoformat(), end.isoformat()],
            "refit_data_end_exclusive": hold.isoformat(), "experiment_report": REPORT_PATH.name,
            "experiment_decision": report["decision"], "hyperparameters_from": "validation in src.mos",
            "holdout_rows": {h: s["holdout_rows"] for h, s in scores.items()},
            "previous_bundle": {"file": bundle_path.name, "holdout_mae_c": {h: s["current_mae_c"] for h, s in scores.items()}},
            "python": platform.python_version(), "platform": platform.platform(),
            "packages": {p: version(p) for p in ("numpy", "pandas", "scikit-learn", "joblib", "requests")}},
    }
    previous = {"file": bundle_path.name, "artifact_version": f"{bundle_path.stem}@{file_sha256(bundle_path)[:12]}",
                "replaced_on": today.isoformat(),
                "reason": f"src.mos experiment adopted the lagged-ECMWF-forecast candidate ({REPORT_PATH.name})"}
    backup = install_bundle(bundle, bundle_path, today, {"previous_version": previous}) if install else None
    return {"installed": install, "backup": backup.name if backup else None,
            "mean_holdout_mae_c": {"new": round(new_mean, 4), "current": round(cur_mean, 4)}, "holdout": scores}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deploy", action="store_true", help="install the adopted candidate (see module docstring)")
    args = parser.parse_args()
    if args.deploy:
        print(json.dumps(deploy(), indent=1, default=str))
        return
    rep = run()
    print(json.dumps(rep["decision"], indent=1))


if __name__ == "__main__":
    main()
