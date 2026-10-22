"""Cloudzy Core against real thermometer readings (Delhi airport METARs, src.stations).

Everything else in Cloudzy is scored against the ERA5-Land reanalysis, the model's own training target. This
evaluation asks how the predictions compare with what a station actually measured. Written before any result was
seen; nothing here changes, tunes or selects a model.

Primary questions (Palam/VIDP; issue times 2026-01-01 to 2026-08-31, the upgrade experiment's test period; the
same rows for every model; week-block bootstrap 95% intervals for MAE differences)
- Q1. Is the current Cloudzy configuration closer to the station than the raw ECMWF IFS value for the same hour?
- Q2. Is the current configuration closer to the station than the previous one?

Context, not decisions
- The reanalysis itself against the station: how far Cloudzy's training target is from a thermometer. Cloudzy
  learns to reproduce the reanalysis, so it inherits that gap.
- The lagged ECMWF forecast, and persistence of the station reading at t (a baseline Cloudzy can't use: it never
  sees station data).
- Bias by local hour of day and by month (urban, siting and elevation effects show up as systematic offsets).
- September 2026: the deployed bundle and the previous one (if its backup file is present), at Palam and at
  Safdarjung (VIDD, which has reports from July 2026).

The models are rebuilt exactly as in src.mos (same rows, same chosen hyperparameters); the script checks that
their reanalysis MAE reproduces the experiment report before comparing anything with stations.

Run:  python -m src.station_eval     → reports/station_eval_delhi.json
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone

from src import config, mos, stations
from src.compact import compact_grid
from src.evaluate import block_bootstrap_mae_diff, mae, rmse
from src.features import TARGET, TEMP, feature_columns
from src.multistep import HORIZONS
from src.refit import bundle_table, load_source, serving_table

logger = logging.getLogger(__name__)

REPORT_PATH = config.REPORTS_DIR / f"station_eval_{config.DELHI.slug}.json"
DATA_START, DATA_END = date(2023, 1, 1), date(2026, 9, 30)
STATION_SPAN = (date(2024, 2, 1), date(2026, 10, 1))
REPRO_TOLERANCE_C = 0.002
MIN_ROWS = 300


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    return {"mae_c": round(mae(y, p), 4), "rmse_c": round(rmse(y, p), 4), "bias_c": round(float(np.mean(p - y)), 4),
            "n": len(y)}


def compare(index: pd.DatetimeIndex, y: np.ndarray, preds: dict[str, np.ndarray]) -> dict:
    out = {name: metrics(y, p) for name, p in preds.items()}
    for a, b in (("candidate", "ifs_at_target"), ("candidate", "control"), ("candidate", "reanalysis")):
        if a in preds and b in preds:
            out[f"{a}_minus_{b}"] = block_bootstrap_mae_diff(index, y, preds[a], preds[b])
    return out


def bias_by(groups: np.ndarray, y: np.ndarray, preds: dict[str, np.ndarray]) -> dict:
    out: dict = {}
    for g in sorted(set(groups)):
        sel = groups == g
        out[str(g)] = {name: round(float(np.mean(p[sel] - y[sel])), 3) for name, p in preds.items()} | {"n": int(sel.sum())}
    return out


def experiment_models(table: pd.DataFrame, h: int, params: dict) -> dict:
    """Control and candidate refitted exactly as in src.mos (same rows, the hyperparameters it chose)."""
    grid = {json.dumps(p, sort_keys=True): est for p, est in compact_grid()}
    refit = mos._span(table, "refit", h)
    out = {}
    for name, cols in (("control", feature_columns()), ("candidate", feature_columns() + mos.LAG_FEATURES)):
        est = grid[json.dumps(params[name], sort_keys=True)]
        out[name] = (clone(est).fit(refit[cols], refit[TARGET]), cols)
    return out


def station_rows(rows: pd.DataFrame, h: int, obs: pd.Series) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Rows whose target hour (and issue hour, for persistence) has a station reading."""
    y = obs.reindex(rows.index + pd.Timedelta(hours=h)).to_numpy()
    at_t = obs.reindex(rows.index).to_numpy()
    keep = ~np.isnan(y) & ~np.isnan(at_t)
    return rows[keep], y[keep], at_t[keep]


def evaluate_period(rows: pd.DataFrame, h: int, models: dict, live: pd.DataFrame, obs: pd.Series) -> dict | None:
    rows, y, at_t = station_rows(rows, h, obs)
    if len(rows) < MIN_ROWS:
        return None
    target_times = rows.index + pd.Timedelta(hours=h)
    preds = {name: m.predict(rows[cols]) for name, (m, cols) in models.items()}
    preds |= {"ifs_at_target": live[TEMP].reindex(target_times).to_numpy(),
              "lagged_ifs_forecast": rows["ifs_prev2_target"].to_numpy(),
              "reanalysis": rows[TARGET].to_numpy(),
              "station_persistence": at_t}
    ok = np.all([~np.isnan(p) for p in preds.values()], axis=0)
    rows, y, preds = rows[ok], y[ok], {k: p[ok] for k, p in preds.items()}
    out = compare(rows.index, y, preds)
    local_hour = (rows.index + pd.Timedelta(hours=h)).tz_convert(config.DELHI.local_timezone).hour.to_numpy()
    month = (rows.index + pd.Timedelta(hours=h)).strftime("%Y-%m").to_numpy()
    keep = {k: preds[k] for k in ("candidate", "ifs_at_target", "reanalysis")}
    out["bias_by_local_hour"] = bias_by(local_hour, y, keep)
    out["mae_by_month"] = {m: {k: round(mae(y[month == m], p[month == m]), 3) for k, p in keep.items()}
                           | {"n": int((month == m).sum())} for m in sorted(set(month))}
    return out


def deployed(path: Path, ref, live, prev, h: int, obs: pd.Series, start: str, end: str) -> dict | None:
    if not path.exists():
        return None
    bundle = joblib.load(path)
    table = bundle_table(ref, live, h, bundle, prev)
    rows = table[(table.index >= pd.Timestamp(start, tz="UTC")) & (table.index < pd.Timestamp(end, tz="UTC"))]
    rows, y, _ = station_rows(rows, h, obs)
    if len(rows) < MIN_ROWS:
        return None
    return metrics(y, bundle["horizons"][h]["model"].predict(rows[bundle["feature_columns"]]))


def run() -> dict:
    ref, _ = load_source(config.REANALYSIS, DATA_START, DATA_END)
    live, _ = load_source(config.LIVE_ARCHIVE, DATA_START, DATA_END)
    prev = mos.load_previous_runs()
    exp = json.loads(mos.REPORT_PATH.read_text())
    obs, qc = {}, {}
    for icao in stations.STATIONS:
        obs[icao], qc[icao] = stations.load(icao, *STATION_SPAN)
        qc[icao]["coverage_by_month"] = stations.coverage_by_month(obs[icao])
    from src.service import SERVING_BUNDLE
    backups = sorted(SERVING_BUNDLE.parent.glob(f"{SERVING_BUNDLE.stem}.backup-*.joblib"))
    previous_bundle = backups[-1] if backups else None
    report: dict = {"stations": {k: vars(v) for k, v in stations.STATIONS.items()}, "primary": stations.PRIMARY,
                    "quality_control": qc, "spans": {"test": mos.SPANS["test"], "september": mos.SPANS["september"]},
                    "horizons": {}}
    for h in HORIZONS:
        params = {k: exp["horizons"][str(h)][k]["params"] for k in ("control", "candidate")}
        table = mos.with_lag_features(serving_table(ref, live, h), prev, h).dropna(subset=mos.LAG_FEATURES)
        models = experiment_models(table, h, params)
        test = mos._span(table, "test", h, False)
        # The rebuilt models must reproduce the experiment before anything is compared with stations.
        for name, (m, cols) in models.items():
            got = mae(test[TARGET].to_numpy(), m.predict(test[cols]))
            want = exp["horizons"][str(h)]["test"][name]["mae_c"]
            if abs(got - want) > REPRO_TOLERANCE_C:
                raise RuntimeError(f"h={h} {name}: rebuilt reanalysis MAE {got:.4f} != experiment {want:.4f}")
        res: dict = {"test": {icao: evaluate_period(test, h, models, live, obs[icao]) for icao in obs}}
        sept = {}
        for icao in obs:
            sept[icao] = {"deployed": deployed(SERVING_BUNDLE, ref, live, prev, h, obs[icao], *mos.SPANS["september"]),
                          "previous_deployed": deployed(previous_bundle, ref, live, prev, h, obs[icao],
                                                        *mos.SPANS["september"]) if previous_bundle else None}
        res["september_deployed"] = sept
        report["horizons"][h] = res
        p = res["test"][stations.PRIMARY]
        logger.info("h=%2d Palam MAE: Cloudzy %.3f  previous %.3f  IFS %.3f  reanalysis %.3f  (Q1 diff %+.3f [%+.3f, %+.3f])",
                    h, p["candidate"]["mae_c"], p["control"]["mae_c"], p["ifs_at_target"]["mae_c"],
                    p["reanalysis"]["mae_c"], p["candidate_minus_ifs_at_target"]["mae_diff_c"],
                    p["candidate_minus_ifs_at_target"]["ci95_low_c"], p["candidate_minus_ifs_at_target"]["ci95_high_c"])
    report["answers"] = answers(report)
    report["provenance"] = {"created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                            "command": "python -m src.station_eval", "observations": stations.ATTRIBUTION,
                            "experiment_report": mos.REPORT_PATH.name, "deployed_bundle": SERVING_BUNDLE.name,
                            "previous_bundle": previous_bundle.name if previous_bundle else None}
    REPORT_PATH.write_text(json.dumps(report, indent=1, default=str))
    return report


def answers(report: dict) -> dict:
    """Q1 and Q2 per horizon, from the primary station's test period."""
    out = {}
    for h, r in report["horizons"].items():
        p = r["test"][report["primary"]]
        q1, q2 = p["candidate_minus_ifs_at_target"], p["candidate_minus_control"]
        verdict = lambda d: ("closer" if d["ci95_high_c"] < 0 else "further" if d["ci95_low_c"] > 0  # noqa: E731
                             else "no significant difference")
        out[str(h)] = {"q1_vs_ifs": verdict(q1), "q1_diff_c": q1["mae_diff_c"],
                       "q2_vs_previous": verdict(q2), "q2_diff_c": q2["mae_diff_c"]}
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rep = run()
    print(json.dumps(rep["answers"], indent=1))


if __name__ == "__main__":
    main()
