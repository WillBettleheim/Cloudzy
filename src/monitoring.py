"""Live monitoring of Cloudzy Core: how do real predictions turn out?

Every live prediction the app makes is appended to a local log. So is every
refusal, when inputs were incomplete. The target, ERA5-Land reanalysis, is
published about 6 days late, so a prediction can only be scored once its valid
time is older than that. `evaluate` then fetches the reanalysis for exactly
those valid times and reports:

- error by horizon (Cloudzy, and the IFS value Cloudzy started from), next to
  the 2025 test MAE that the app shows as "average error";
- error by temperature range;
- how often a prediction was refused because inputs were missing.

Everything stays on this machine (`data/monitoring/`, ignored by git). Live
scoring is kept separate from the UI: the Lab only reads the saved summary.

Run:  python -m src.monitoring       (scores whatever has matured)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src import config
from src.data_collection import build_params, check_response, fetch_json

logger = logging.getLogger(__name__)

def mon_dir() -> Path:
    """Read at call time, so tests and users can redirect it with CLOUDZY_MONITORING_DIR."""
    return Path(os.environ.get("CLOUDZY_MONITORING_DIR", config.DATA_DIR / "monitoring"))


def enabled() -> bool:
    return os.environ.get("CLOUDZY_MONITORING", "1") == "1"
PREDICTIONS = "predictions.jsonl"
FAILURES = "failures.jsonl"
SUMMARY = "summary.json"
TEMP_BINS = [-50, 10, 20, 30, 60]
TEMP_LABELS = ["below 10 °C", "10–20 °C", "20–30 °C", "30 °C and above"]


def _append(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def log_prediction(result: dict, directory: Path | None = None) -> int:
    """Append a service.forecast_now() result. Returns the number of rows written."""
    directory = Path(directory or mon_dir())
    provider = {p["horizon_h"]: p["temperature_2m_c"] for p in result["provider"]["forecasts"]}
    rows = [{
        "logged_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "issue_time_utc": result["issue_time_utc"],
        "valid_time_utc": p["valid_time_utc"],
        "horizon_h": int(p["horizon_h"]),
        "cloudzy_c": float(p["temperature_2m_c"]),
        "ifs_c": provider.get(p["horizon_h"]),
        "expected_mae_c": p.get("typical_error_c"),
    } for p in result["cloudzy"]["predictions"]]
    _append(directory / PREDICTIONS, rows)
    return len(rows)


def log_failure(reason: str, directory: Path | None = None) -> None:
    _append(Path(directory or mon_dir()) / FAILURES,
            [{"time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": reason[:300]}])


def read_log(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            logger.warning("Skipping malformed monitoring line")
    return pd.DataFrame(rows)


def load_predictions(directory: Path | None = None) -> pd.DataFrame:
    df = read_log(Path(directory or mon_dir()) / PREDICTIONS)
    if df.empty:
        return df
    for col in ("issue_time_utc", "valid_time_utc"):
        df[col] = pd.to_datetime(df[col], utc=True)
    # The app may log the same issue time several times (cache refreshes): keep the first.
    return df.sort_values("logged_at_utc").drop_duplicates(["issue_time_utc", "horizon_h"]).reset_index(drop=True)


def matured(df: pd.DataFrame, today: date, lag_days: int = config.REANALYSIS.lag_days) -> pd.DataFrame:
    """Predictions whose valid time is old enough for the reanalysis to exist."""
    if df.empty:
        return df
    cutoff = pd.Timestamp(today - timedelta(days=lag_days), tz="UTC")
    return df[df["valid_time_utc"] < cutoff]


def fetch_reanalysis(start: date, end: date, session=None) -> pd.Series:
    """Hourly reanalysis 2 m temperature (°C) for the Delhi grid cell, indexed by UTC time."""
    params = build_params(config.DELHI, start, end, ["temperature_2m"], config.REANALYSIS.model)
    payload = fetch_json(config.REANALYSIS.url, params, session=session)
    check_response(payload, ["temperature_2m"])
    if payload.get("utc_offset_seconds") != 0:
        raise ValueError("Expected UTC reanalysis timestamps")
    idx = pd.to_datetime(payload["hourly"]["time"], format="%Y-%m-%dT%H:%M", utc=True)
    return pd.Series(payload["hourly"]["temperature_2m"], index=idx, dtype="float64")


def score(preds: pd.DataFrame, truth: pd.Series) -> dict:
    """Errors of matured predictions against the reanalysis at their exact valid times."""
    df = preds.copy()
    df["observed_c"] = truth.reindex(df["valid_time_utc"]).to_numpy()
    df = df.dropna(subset=["observed_c"])
    if df.empty:
        return {"n": 0}
    df["cloudzy_err"] = df["cloudzy_c"] - df["observed_c"]
    df["ifs_err"] = df["ifs_c"].astype(float) - df["observed_c"]
    by_h = []
    for h, g in df.groupby("horizon_h"):
        by_h.append({
            "horizon_h": int(h), "n": int(len(g)),
            "cloudzy_mae_c": round(float(g["cloudzy_err"].abs().mean()), 3),
            "cloudzy_rmse_c": round(float(np.sqrt((g["cloudzy_err"] ** 2).mean())), 3),
            "cloudzy_bias_c": round(float(g["cloudzy_err"].mean()), 3),
            "ifs_mae_c": round(float(g["ifs_err"].abs().mean()), 3) if g["ifs_err"].notna().any() else None,
            "expected_mae_c": round(float(g["expected_mae_c"].dropna().iloc[0]), 3) if g["expected_mae_c"].notna().any() else None,
        })
    df["temp_range"] = pd.cut(df["observed_c"], TEMP_BINS, labels=TEMP_LABELS)
    by_t = [{"range": str(k), "n": int(len(g)), "cloudzy_mae_c": round(float(g["cloudzy_err"].abs().mean()), 3)}
            for k, g in df.groupby("temp_range", observed=True)]
    return {
        "n": int(len(df)),
        "valid_from_utc": df["valid_time_utc"].min().isoformat(), "valid_to_utc": df["valid_time_utc"].max().isoformat(),
        "by_horizon": by_h, "by_temperature_range": by_t,
    }


def evaluate(today: date | None = None, directory: Path | None = None, session=None) -> dict:
    directory = Path(directory or mon_dir())
    today = today or date.today()
    preds = load_predictions(directory)
    failures = read_log(directory / FAILURES)
    ready = matured(preds, today)
    summary = {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "logged_predictions": int(len(preds)),
        "logged_issue_times": int(preds["issue_time_utc"].nunique()) if len(preds) else 0,
        "refusals": int(len(failures)),
        "refusal_rate": round(len(failures) / (len(failures) + (preds["issue_time_utc"].nunique() if len(preds) else 0)), 3)
        if len(failures) or len(preds) else None,
        "matured_predictions": int(len(ready)),
        "reanalysis_lag_days": config.REANALYSIS.lag_days,
        "scores": {"n": 0},
    }
    if len(ready):
        start = ready["valid_time_utc"].min().date()
        end = ready["valid_time_utc"].max().date()
        truth = fetch_reanalysis(start, end, session=session)
        summary["scores"] = score(ready, truth)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / SUMMARY).write_text(json.dumps(summary, indent=2))
    return summary


def load_summary(directory: Path | None = None) -> dict | None:
    path = Path(directory or mon_dir()) / SUMMARY
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = evaluate()
    print(json.dumps({k: v for k, v in s.items() if k != "scores"}, indent=2))
    if s["scores"]["n"]:
        for row in s["scores"]["by_horizon"]:
            print(f"  +{row['horizon_h']:>2} h  n={row['n']:<4} Cloudzy MAE {row['cloudzy_mae_c']:.2f} °C "
                  f"(2025 test: {row['expected_mae_c']}) · IFS value MAE {row['ifs_mae_c']}")
    else:
        print(f"No matured predictions yet: the reanalysis is published about {s['reanalysis_lag_days']} days late.")


if __name__ == "__main__":
    main()
