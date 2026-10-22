"""Experiment: train Cloudzy Core against real station readings (Palam, VIDP) instead of the reanalysis.

Why: src.station_eval found that against Palam's thermometer the raw ECMWF value (MAE ~1.21 °C) beats the
reanalysis-trained Cloudzy (1.40-1.63 °C), because the reanalysis runs ~1.5 °C colder than the city at night.
If Cloudzy learns the station directly, can it beat ECMWF?

Written, including the decision rules, before any model below was trained or scored (2026-10-22).

Target: the Palam METAR temperature at t + h (src.stations: the report at the hour, quality-controlled, whole
degrees). Horizons 1, 2, 3, 6, 12, 24 h.

Candidates (each tuned separately, over the deployed model's 8 residual-gradient-boosting configurations)
- A, "station target": the deployed 29 features (25 hours of ECMWF IFS inputs up to t, plus the lagged ECMWF
  forecast for t + h). No new live dependency.
- B, "station target + station inputs": A plus four station features, all at or before t: the reading at t,
  that reading minus the IFS value at t (the current local offset), the change over the last 3 hours, and the
  station-minus-IFS offset 24 hours before t. Live, Palam METARs are published within minutes of the hour
  (NOAA Aviation Weather Center API, checked 2026-10-22), so B is feasible, but it adds that dependency.

Leakage
- Every input is at or before t; the target is at t + h. Training rows have their target inside their own span.
- Missing station inputs stay missing (gradient boosting handles them); nothing is filled from the future.

Splits (identical to src.mos, so the results line up with the earlier reports)
- Fit 2024-02-05 → 2025-07-01; choose the configuration by MAE on 2025-07 → 2026-01.
- Refit on 2024-02-05 → 2026-01-01; score once on issue times 2026-01-01 → 2026-09-01 (test).
- September 2026 reported separately.

Rows: every model and baseline is scored on the same rows, where the station has a reading at t + h and at t.

Baselines on those rows
- Raw ECMWF: the archived IFS value at t + h (optimistic: the archive holds the newest run for each hour).
- Bias-corrected ECMWF: raw ECMWF plus the mean station-minus-IFS offset for that local hour of day, learned
  on the refit rows. ML has to beat this trivial correction to be worth having.
- Station persistence (the reading at t), and the current reanalysis-trained Cloudzy configuration.

Decision rules, per candidate (week-block bootstrap 95% intervals for MAE differences)
- H1: MAE lower than raw ECMWF at every horizon, and the interval excludes zero at 4 or more of 6.
- H2: MAE lower than bias-corrected ECMWF with the interval excluding zero at 4 or more of 6.
- H3: September 2026: no horizon worse than raw ECMWF by more than 0.05 °C.
- Verdict: H1, H2 and H3 → "beats ECMWF and the simple correction"; H1 and H3 only → "beats ECMWF, but not
  more than a simple bias correction does"; otherwise → "does not beat ECMWF".
- Both candidates are judged independently; testing two is stated, not hidden.

Not part of this experiment: deployment. A station-target model changes what Cloudzy predicts and how its
live predictions are evaluated (src.monitoring scores against the reanalysis), so adopting one would be a
separate, explicit step.

Secondary: the same predictions against Safdarjung (VIDD, reports from July 2026), to see whether a model trained
at the airport transfers to central Delhi.

Run:  python -m src.station_model     → reports/station_model_delhi.json
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sklearn.base import clone

from src import config, mos, stations
from src.compact import compact_grid
from src.evaluate import block_bootstrap_mae_diff, mae, rmse
from src.features import TARGET, TEMP, feature_columns
from src.multistep import HORIZONS
from src.refit import load_source, serving_table
from src.station_eval import DATA_END, DATA_START, STATION_SPAN

logger = logging.getLogger(__name__)

REPORT_PATH = config.REPORTS_DIR / f"station_model_{config.DELHI.slug}.json"
STATION_TARGET = "station_t_plus_h"
STATION_FEATURES = ["stn_now", "stn_minus_ifs_now", "stn_change_3h", "stn_minus_ifs_24h_ago"]
BASE_FEATURES = feature_columns() + mos.LAG_FEATURES
CANDIDATES = {"A": BASE_FEATURES, "B": BASE_FEATURES + STATION_FEATURES}
MIN_SIGNIFICANT = 4
SEPT_TOLERANCE_C = 0.05


def station_table(table: pd.DataFrame, obs: pd.Series, live: pd.DataFrame, h: int) -> pd.DataFrame:
    """Add the station target at t + h and station inputs at or before t. Rows without the target are dropped."""
    out = table.copy()
    idx = out.index
    ifs = live[TEMP]
    out[STATION_TARGET] = obs.reindex(idx + pd.Timedelta(hours=h)).to_numpy()
    out["stn_now"] = obs.reindex(idx).to_numpy()
    out["stn_minus_ifs_now"] = out["stn_now"] - ifs.reindex(idx).to_numpy()
    out["stn_change_3h"] = out["stn_now"] - obs.reindex(idx - pd.Timedelta(hours=3)).to_numpy()
    out["stn_minus_ifs_24h_ago"] = (obs.reindex(idx - pd.Timedelta(hours=24)).to_numpy()
                                    - ifs.reindex(idx - pd.Timedelta(hours=24)).to_numpy())
    out["ifs_at_target"] = ifs.reindex(idx + pd.Timedelta(hours=h)).to_numpy()
    return out.dropna(subset=[STATION_TARGET])


def _tune(fit: pd.DataFrame, val: pd.DataFrame, cols: list[str]):
    scored = []
    for params, est in compact_grid():
        m = clone(est).fit(fit[cols], fit[STATION_TARGET])
        scored.append((mae(val[STATION_TARGET], m.predict(val[cols])), params, est))
    best = min(scored, key=lambda s: s[0])
    return best[1], best[2], round(best[0], 4)


def hourly_offset(rows: pd.DataFrame, h: int) -> pd.Series:
    """Mean station-minus-IFS offset by local hour of the target (the bias-correction baseline)."""
    hours = (rows.index + pd.Timedelta(hours=h)).tz_convert(config.DELHI.local_timezone).hour
    return (rows[STATION_TARGET] - rows["ifs_at_target"]).groupby(hours).mean()


def score_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """The common evaluation rows: station reading at t + h (already required) and at t, and the IFS value."""
    return rows.dropna(subset=["stn_now", "ifs_at_target"])


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    return {"mae_c": round(mae(y, p), 4), "rmse_c": round(rmse(y, p), 4), "bias_c": round(float(np.mean(p - y)), 4),
            "n": len(y)}


def evaluate_horizon(table: pd.DataFrame, h: int, current_cfg: dict, vidd: pd.Series) -> dict:
    fit, val, refit = mos._span(table, "fit", h), mos._span(table, "val", h), mos._span(table, "refit", h)
    test, sept = score_rows(mos._span(table, "test", h, False)), score_rows(mos._span(table, "september", h, False))
    out: dict = {"rows": {"fit": len(fit), "val": len(val), "refit": len(refit), "test": len(test),
                          "september": len(sept)}}
    models = {}
    for name, cols in CANDIDATES.items():
        params, est, val_mae = _tune(fit, val, cols)
        models[name] = (clone(est).fit(refit[cols], refit[STATION_TARGET]), cols)
        out[f"candidate_{name}"] = {"params": params, "val_mae_c": val_mae}
    # The current reanalysis-trained configuration (src.mos candidate), refitted as in that experiment.
    grid = {json.dumps(p, sort_keys=True): e for p, e in compact_grid()}
    cur = clone(grid[json.dumps(current_cfg, sort_keys=True)]).fit(refit[BASE_FEATURES], refit[TARGET])
    offset = hourly_offset(refit, h)
    for split, rows in (("test", test), ("september", sept)):
        y = rows[STATION_TARGET].to_numpy()
        local_hour = (rows.index + pd.Timedelta(hours=h)).tz_convert(config.DELHI.local_timezone).hour
        preds = {f"candidate_{n}": m.predict(rows[c]) for n, (m, c) in models.items()}
        preds |= {"raw_ecmwf": rows["ifs_at_target"].to_numpy(),
                  "bias_corrected_ecmwf": rows["ifs_at_target"].to_numpy() + offset.reindex(local_hour).fillna(0).to_numpy(),
                  "station_persistence": rows["stn_now"].to_numpy(),
                  "current_cloudzy": cur.predict(rows[BASE_FEATURES])}
        res: dict = {k: metrics(y, p) for k, p in preds.items()}
        for n in CANDIDATES:
            for base in ("raw_ecmwf", "bias_corrected_ecmwf"):
                res[f"candidate_{n}_minus_{base}"] = block_bootstrap_mae_diff(rows.index, y, preds[f"candidate_{n}"],
                                                                              preds[base])
        if split == "test":
            res["bias_by_local_hour"] = {
                str(hr): {k: round(float(np.mean(preds[k][local_hour == hr] - y[local_hour == hr])), 3)
                          for k in ("candidate_A", "candidate_B", "raw_ecmwf")} for hr in sorted(set(local_hour))}
            # Secondary: the same predictions against Safdarjung, where it has readings.
            yv = vidd.reindex(rows.index + pd.Timedelta(hours=h)).to_numpy()
            ok = ~np.isnan(yv)
            res["safdarjung"] = ({k: metrics(yv[ok], p[ok]) for k, p in preds.items()} if ok.sum() >= 300 else None)
        out[split] = res
    return out


def decide(results: dict) -> dict:
    """Apply the rules in the module docstring to each candidate."""
    out = {}
    for n in CANDIDATES:
        lower = all(r["test"][f"candidate_{n}"]["mae_c"] < r["test"]["raw_ecmwf"]["mae_c"] for r in results.values())
        sig_raw = sum(r["test"][f"candidate_{n}_minus_raw_ecmwf"]["ci95_high_c"] < 0 for r in results.values())
        sig_bc = sum(r["test"][f"candidate_{n}_minus_bias_corrected_ecmwf"]["ci95_high_c"] < 0 for r in results.values())
        sept_ok = all(r["september"][f"candidate_{n}"]["mae_c"] - r["september"]["raw_ecmwf"]["mae_c"] <= SEPT_TOLERANCE_C
                      for r in results.values())
        h1, h2 = lower and sig_raw >= MIN_SIGNIFICANT, sig_bc >= MIN_SIGNIFICANT
        verdict = ("beats ECMWF and the simple correction" if h1 and h2 and sept_ok else
                   "beats ECMWF, but not more than a simple bias correction does" if h1 and sept_ok else
                   "does not beat ECMWF")
        out[n] = {"h1_lower_everywhere": lower, "h1_significant_horizons": sig_raw,
                  "h2_significant_vs_bias_corrected": sig_bc, "h3_september_ok": sept_ok, "verdict": verdict}
    return out


def run() -> dict:
    ref, _ = load_source(config.REANALYSIS, DATA_START, DATA_END)
    live, _ = load_source(config.LIVE_ARCHIVE, DATA_START, DATA_END)
    prev = mos.load_previous_runs()
    vidp, qc = stations.load("VIDP", *STATION_SPAN)
    vidd, _ = stations.load("VIDD", *STATION_SPAN)
    exp = json.loads(mos.REPORT_PATH.read_text())
    results = {}
    for h in HORIZONS:
        table = mos.with_lag_features(serving_table(ref, live, h), prev, h).dropna(subset=mos.LAG_FEATURES)
        table = station_table(table, vidp, live, h)
        results[h] = evaluate_horizon(table, h, exp["horizons"][str(h)]["candidate"]["params"], vidd)
        t = results[h]["test"]
        logger.info("h=%2d Palam MAE: A %.3f  B %.3f  raw ECMWF %.3f  bias-corr %.3f  persistence %.3f  current %.3f",
                    h, t["candidate_A"]["mae_c"], t["candidate_B"]["mae_c"], t["raw_ecmwf"]["mae_c"],
                    t["bias_corrected_ecmwf"]["mae_c"], t["station_persistence"]["mae_c"], t["current_cloudzy"]["mae_c"])
    report = {"spans": mos.SPANS, "station": "VIDP", "station_quality_control": qc, "candidates":
              {n: c[len(feature_columns()):] for n, c in CANDIDATES.items()}, "horizons": results,
              "decision": decide(results),
              "provenance": {"created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                             "command": "python -m src.station_model", "observations": stations.ATTRIBUTION}}
    REPORT_PATH.write_text(json.dumps(report, indent=1, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(run()["decision"], indent=1))


if __name__ == "__main__":
    main()
