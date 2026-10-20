"""Prediction tracking for Cloudzy Core: record live predictions, evaluate them later.

Storage: one local SQLite file (`data/monitoring/cloudzy_predictions.sqlite`; folder overridable
with CLOUDZY_MONITORING_DIR). It uses Python's standard library, so there's no server, account or
new dependency, and git ignores it. Tests point it at a temporary folder, so their records never
mix with real history.

Records
- **Only valid predictions are stored:** finite values, and a target time that
  equals the issue time + horizon. Refusals (incomplete inputs) go in a separate
  table.
- **Idempotent:** the ID is a hash of model version, location, issue time,
  horizon and variable. Recording the same prediction again (re-render, refresh,
  retry) is a no-op.
- **No user location:** the location is the model's *prediction point* (the Delhi
  training grid cell), not the user's location. The inputs snapshot holds the
  five weather values at that grid cell at issue time.

Times are stored in UTC: timestamps with any offset are converted, timestamps without a time zone are
rejected, so one prediction is always one record.

Evaluation (`evaluate`)
- **When:** only after the target time has passed *and* the reference data
  should exist. ERA5-Land reanalysis via Open-Meteo is published about 6 days
  late, so records are requested from 7 days after their (UTC) target date.
- **Retries:** a missing value leaves the record pending with a note; every
  later run retries it. A failed request changes no record. Each run is stored
  as the last check (`last_check()`), including failures.
- **Why pending:** `classify()` says what each record is waiting for.
- **Matching:** same location, variable (2 m temperature), unit (°C) and the
  exact UTC hour (tolerance: 0 minutes; both sides are hourly values on the
  hour).
- **Source:** recorded as reanalysis, a model-based estimate and **not a station
  observation**. Provider forecasts are never used as observations.
- **Missing reference:** the prediction stays `pending`. If the value still
  isn't there 30 days after the target time, it becomes `unavailable`. Nothing
  is invented.
- **Aggregates:** come only from `evaluated` records, with their sample count
  and date range.

Run:  python -m src.monitoring        (evaluates whatever has matured, prints a summary)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src import config
from src.data_collection import build_params, check_response, fetch_json

logger = logging.getLogger(__name__)

DB_NAME = "cloudzy_predictions.sqlite"
LEGACY_JSONL = "predictions.jsonl"
VARIABLE, UNIT = "temperature_2m", "°C"
LOCATION_ID = "delhi-grid"  # the model's prediction point (see config.DELHI), never the user's location
OBSERVATION_SOURCE = ("ERA5-Land reanalysis via Open-Meteo (era5_seamless): a model-based estimate, "
                      "not a station observation")
MATCH_TOLERANCE = "exact UTC hour (0 minutes)"
GIVE_UP_DAYS = 30
CONCLUSIVE_MIN_ISSUES = 30   # fewer distinct issue times than this: say the sample is too small
TEMP_BINS = [-50, 10, 20, 30, 60]
TEMP_LABELS = ["below 10 °C", "10–20 °C", "20–30 °C", "30 °C and above"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
  id TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  issue_time_utc TEXT NOT NULL,
  target_time_utc TEXT NOT NULL,
  horizon_h INTEGER NOT NULL,
  location_id TEXT NOT NULL,
  latitude REAL NOT NULL,
  longitude REAL NOT NULL,
  model_version TEXT NOT NULL,
  variable TEXT NOT NULL,
  predicted REAL NOT NULL,
  unit TEXT NOT NULL,
  provider_value REAL,
  provider_source TEXT,
  expected_mae REAL,
  inputs_json TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'evaluated', 'unavailable')),
  observed REAL,
  abs_error REAL,
  observation_source TEXT,
  evaluated_at_utc TEXT,
  status_note TEXT
);
CREATE INDEX IF NOT EXISTS ix_pred_target ON predictions(target_time_utc);
CREATE TABLE IF NOT EXISTS refusals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  time_utc TEXT NOT NULL,
  reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def mon_dir() -> Path:
    """Read at call time, so tests and users can redirect it with CLOUDZY_MONITORING_DIR."""
    return Path(os.environ.get("CLOUDZY_MONITORING_DIR", config.DATA_DIR / "monitoring"))


def enabled() -> bool:
    return os.environ.get("CLOUDZY_MONITORING", "1") == "1"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(directory: Path | None = None) -> sqlite3.Connection:
    directory = Path(directory or mon_dir())
    directory.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(directory / DB_NAME, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _add_missing_columns(con)
    _migrate_legacy(con, directory)
    return con


def _add_missing_columns(con: sqlite3.Connection) -> None:
    """Databases created before `status_note` existed get the column; no record is changed."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(predictions)")}
    if "status_note" not in cols:
        con.execute("ALTER TABLE predictions ADD COLUMN status_note TEXT")
        con.commit()


def prediction_id(model_version: str, location_id: str, issue_time_utc: str, horizon_h: int,
                  variable: str = VARIABLE) -> str:
    key = f"{model_version}|{location_id}|{pd.Timestamp(issue_time_utc).isoformat()}|{int(horizon_h)}|{variable}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _utc(value) -> pd.Timestamp | None:
    """A timezone-aware timestamp converted to UTC, or None (missing, unparseable or without a time zone).

    Storing every time in UTC keeps one prediction one record (the ID includes the issue time) and
    keeps the text comparisons used for date filters correct.
    """
    try:
        t = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if t is pd.NaT or t.tzinfo is None:
        return None
    return t.tz_convert("UTC")


def _valid_number(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def record_prediction(result: dict, directory: Path | None = None) -> dict:
    """Store a service.forecast_now() result. Returns {"inserted": n, "duplicates": m, "rejected": k}."""
    model_version = result.get("model_version") or "unversioned"
    issue = _utc(result["issue_time_utc"])
    if issue is None:
        raise ValueError("issue_time_utc must be a timezone-aware timestamp")
    provider = {p["horizon_h"]: p.get("temperature_2m_c") for p in result.get("provider", {}).get("forecasts", [])}
    inputs = json.dumps(result.get("inputs", {}).get("values")) if result.get("inputs", {}).get("values") else None
    counts = {"inserted": 0, "duplicates": 0, "rejected": 0}
    with closing(connect(directory)) as con, con:
        for p in result["cloudzy"]["predictions"]:
            h = int(p["horizon_h"])
            target = _utc(p.get("valid_time_utc"))
            value = p.get("temperature_2m_c")
            if target is None or not _valid_number(value) or target != issue + pd.Timedelta(hours=h):
                counts["rejected"] += 1
                logger.warning("Rejected an invalid prediction record (horizon %s)", h)
                continue
            pid = prediction_id(model_version, LOCATION_ID, issue.isoformat(), h)
            pv = provider.get(h)
            cur = con.execute(
                "INSERT OR IGNORE INTO predictions (id, created_at_utc, issue_time_utc, target_time_utc, horizon_h, "
                "location_id, latitude, longitude, model_version, variable, predicted, unit, provider_value, "
                "provider_source, expected_mae, inputs_json, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')",
                (pid, _now(), issue.isoformat(), target.isoformat(), h, LOCATION_ID, config.DELHI.latitude,
                 config.DELHI.longitude, model_version, VARIABLE, float(value), UNIT,
                 float(pv) if _valid_number(pv) else None, "ECMWF IFS via Open-Meteo (provider forecast)",
                 p.get("typical_error_c"), inputs if h == 1 else None))
            counts["inserted" if cur.rowcount else "duplicates"] += 1
    return counts


# Backwards-compatible name used by src.briefing.
def log_prediction(result: dict, directory: Path | None = None) -> int:
    return record_prediction(result, directory)["inserted"]


def log_failure(reason: str, directory: Path | None = None) -> None:
    with closing(connect(directory)) as con, con:
        con.execute("INSERT INTO refusals (time_utc, reason) VALUES (?, ?)", (_now(), reason[:300]))


def _migrate_legacy(con: sqlite3.Connection, directory: Path) -> None:
    """Import the old JSONL log once (it's kept on disk). Its model version wasn't recorded."""
    path = directory / LEGACY_JSONL
    done = con.execute("SELECT value FROM meta WHERE key='legacy_jsonl_imported'").fetchone()
    if done or not path.exists():
        return
    version = "unrecorded (logged before model versions were tracked)"
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            issue, target, h = _utc(r["issue_time_utc"]), _utc(r["valid_time_utc"]), int(r["horizon_h"])
            if issue is None or target is None or target != issue + pd.Timedelta(hours=h) \
                    or not _valid_number(r["cloudzy_c"]):
                continue
        except (ValueError, KeyError, TypeError):
            continue
        pid = prediction_id(version, LOCATION_ID, issue.isoformat(), h)
        cur = con.execute(
            "INSERT OR IGNORE INTO predictions (id, created_at_utc, issue_time_utc, target_time_utc, horizon_h, "
            "location_id, latitude, longitude, model_version, variable, predicted, unit, provider_value, "
            "provider_source, expected_mae, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')",
            (pid, r.get("logged_at_utc") or _now(), issue.isoformat(), target.isoformat(), h, LOCATION_ID,
             config.DELHI.latitude, config.DELHI.longitude, version, VARIABLE, float(r["cloudzy_c"]), UNIT,
             r.get("ifs_c"), "ECMWF IFS via Open-Meteo (provider forecast)", r.get("expected_mae_c")))
        n += cur.rowcount
    con.execute("INSERT OR REPLACE INTO meta VALUES ('legacy_jsonl_imported', ?)", (f"{_now()} ({n} records)",))
    con.commit()
    logger.info("Imported %d legacy prediction records from %s", n, path.name)


# --- Queries ---------------------------------------------------------------------------------

def query(directory: Path | None = None, location_id: str | None = None, start: pd.Timestamp | None = None,
          end: pd.Timestamp | None = None, model_version: str | None = None, status: str | None = None) -> pd.DataFrame:
    """Prediction records filtered by target time (UTC, inclusive), location, model version and status."""
    sql, args = "SELECT * FROM predictions WHERE 1=1", []
    for col, val in (("location_id", location_id), ("model_version", model_version), ("status", status)):
        if val:
            sql += f" AND {col} = ?"
            args.append(val)
    if start is not None:
        sql += " AND target_time_utc >= ?"
        args.append(pd.Timestamp(start).tz_convert("UTC").isoformat())
    if end is not None:
        sql += " AND target_time_utc <= ?"
        args.append(pd.Timestamp(end).tz_convert("UTC").isoformat())
    with closing(connect(directory)) as con:
        df = pd.read_sql_query(sql + " ORDER BY target_time_utc DESC, horizon_h", con, params=args)
    for col in ("created_at_utc", "issue_time_utc", "target_time_utc", "evaluated_at_utc"):
        df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
    return df


def load_predictions(directory: Path | None = None) -> pd.DataFrame:
    return query(directory)


def refusals(directory: Path | None = None) -> pd.DataFrame:
    with closing(connect(directory)) as con:
        return pd.read_sql_query("SELECT * FROM refusals ORDER BY time_utc DESC", con)


def distinct(column: str, directory: Path | None = None) -> list[str]:
    if column not in {"location_id", "model_version", "status"}:
        raise ValueError(column)
    with closing(connect(directory)) as con:
        return [r[0] for r in con.execute(f"SELECT DISTINCT {column} FROM predictions ORDER BY 1")]


def aggregate(df: pd.DataFrame) -> dict:
    """Metrics from evaluated records only, with sample size, date range and a conclusiveness flag."""
    ev = df[df["status"] == "evaluated"].copy() if len(df) else df
    if ev is None or ev.empty:
        return {"n": 0}
    ev["err"] = ev["predicted"] - ev["observed"]
    ev["ifs_err"] = ev["provider_value"] - ev["observed"]
    by_h = []
    for h, g in ev.groupby("horizon_h"):
        by_h.append({"horizon_h": int(h), "n": len(g),
                     "mae_c": round(float(g["err"].abs().mean()), 3),
                     "rmse_c": round(float(np.sqrt((g["err"] ** 2).mean())), 3),
                     "bias_c": round(float(g["err"].mean()), 3),
                     "ifs_mae_c": round(float(g["ifs_err"].abs().mean()), 3) if g["ifs_err"].notna().any() else None,
                     "expected_mae_c": round(float(g["expected_mae"].dropna().iloc[0]), 3)
                     if g["expected_mae"].notna().any() else None})
    ev["range"] = pd.cut(ev["observed"], TEMP_BINS, labels=TEMP_LABELS)
    by_t = [{"range": str(k), "n": len(g), "mae_c": round(float(g["err"].abs().mean()), 3)}
            for k, g in ev.groupby("range", observed=True)]
    issues = int(ev["issue_time_utc"].nunique())
    return {"n": len(ev), "issue_times": issues,
            "target_from_utc": ev["target_time_utc"].min().isoformat(), "target_to_utc": ev["target_time_utc"].max().isoformat(),
            "conclusive": issues >= CONCLUSIVE_MIN_ISSUES, "by_horizon": by_h, "by_temperature_range": by_t,
            "observation_source": OBSERVATION_SOURCE, "match_tolerance": MATCH_TOLERANCE}


# --- Evaluation --------------------------------------------------------------------------------

def fetch_reanalysis(start: date, end: date, session=None) -> pd.Series:
    """Hourly reanalysis 2 m temperature (°C) for the Delhi grid cell, indexed by UTC time."""
    params = build_params(config.DELHI, start, end, [VARIABLE], config.REANALYSIS.model)
    payload = fetch_json(config.REANALYSIS.url, params, session=session, timeout=30, retries=2, max_wait_s=10)
    check_response(payload, [VARIABLE])
    if payload.get("utc_offset_seconds") != 0:
        raise ValueError("Expected UTC reanalysis timestamps")
    if payload.get("hourly_units", {}).get(VARIABLE) != UNIT:
        raise ValueError(f"Expected {VARIABLE} in {UNIT}")
    idx = pd.to_datetime(payload["hourly"]["time"], format="%Y-%m-%dT%H:%M", utc=True)
    return pd.Series(payload["hourly"][VARIABLE], index=idx, dtype="float64")


def _today_utc() -> date:
    return datetime.now(UTC).date()


def eligibility_cutoff(today: date | None = None) -> pd.Timestamp:
    """Pending records with a target hour before this (UTC) are old enough to check.

    The reanalysis is published about `lag_days` late, so newer target hours aren't requested at all.
    """
    today = today or _today_utc()
    return pd.Timestamp(today - timedelta(days=config.REANALYSIS.lag_days), tz="UTC")


def _set_meta(con: sqlite3.Connection, key: str, value: dict) -> None:
    con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))


def last_check(directory: Path | None = None) -> dict | None:
    """The most recent evaluation attempt (success or failure), as stored, or None if none has run."""
    with closing(connect(directory)) as con:
        row = con.execute("SELECT value FROM meta WHERE key='last_check'").fetchone()
    try:
        return json.loads(row[0]) if row else None
    except ValueError:
        return None


def evaluate(today: date | None = None, directory: Path | None = None, session=None) -> dict:
    """Evaluate pending predictions whose reference data should now exist. Safe to run repeatedly.

    - Records whose target hour is too recent are left alone, with no request made.
    - One request covers every eligible record. A failed request changes no record; the failure
      is stored as the last check and the error is raised for the caller to show.
    - Each record is updated only while still `pending`, so overlapping runs can't double-count.
    """
    today = today or _today_utc()
    cutoff = eligibility_cutoff(today)
    pending = query(directory, status="pending")
    ready = pending[pending["target_time_utc"] < cutoff] if len(pending) else pending
    result: dict[str, Any] = {
        "evaluated_at_utc": _now(), "pending_before": len(pending), "matured": len(ready),
        "newly_evaluated": 0, "marked_unavailable": 0, "still_pending": len(pending),
        "reference_until_utc": None, "outcome": "ok", "error": None}
    if ready.empty:
        result["outcome"] = "nothing due"
        with closing(connect(directory)) as con, con:
            _set_meta(con, "last_check", result)
        return result
    try:
        truth = fetch_reanalysis(ready["target_time_utc"].min().date(), ready["target_time_utc"].max().date(),
                                 session=session)
    except Exception as exc:
        result.update(outcome="failed", error=f"{type(exc).__name__}: {exc}"[:300])
        with closing(connect(directory)) as con, con:
            _set_meta(con, "last_check", result)
        raise
    valid = truth.dropna()
    result["reference_until_utc"] = valid.index.max().isoformat() if len(valid) else None
    give_up = pd.Timestamp(today - timedelta(days=GIVE_UP_DAYS), tz="UTC")
    with closing(connect(directory)) as con, con:
        for r in ready.itertuples():
            obs = truth.get(r.target_time_utc)
            if obs is not None and np.isfinite(obs):
                cur = con.execute(
                    "UPDATE predictions SET status='evaluated', observed=?, abs_error=?, observation_source=?, "
                    "evaluated_at_utc=?, status_note=NULL WHERE id=? AND status='pending'",
                    (float(obs), abs(r.predicted - float(obs)), OBSERVATION_SOURCE, _now(), r.id))
                result["newly_evaluated"] += cur.rowcount
            elif r.target_time_utc < give_up:
                cur = con.execute(
                    "UPDATE predictions SET status='unavailable', evaluated_at_utc=?, status_note=? "
                    "WHERE id=? AND status='pending'",
                    (_now(), f"No reference value within {GIVE_UP_DAYS} days of the target hour "
                             f"({config.REANALYSIS.model} via Open-Meteo)", r.id))
                result["marked_unavailable"] += cur.rowcount
            else:
                con.execute("UPDATE predictions SET status_note=? WHERE id=? AND status='pending'",
                            (f"Checked {result['evaluated_at_utc'][:16]} UTC: no reference value for this hour yet",
                             r.id))
        result["still_pending"] = con.execute("SELECT COUNT(*) FROM predictions WHERE status='pending'").fetchone()[0]
        _set_meta(con, "last_check", result)
    return result


# --- Status breakdown ---------------------------------------------------------------------------

WAIT_TARGET = "Waiting: target hour not reached"
WAIT_REFERENCE = "Waiting: reference data not published yet"
DUE = "Due: checked on the next run"
CHECKED_MISSING = "Checked: reference value still missing"
EVALUATED = "Evaluated"
UNAVAILABLE = "Unavailable: no reference data"
DETAIL_ORDER = [EVALUATED, WAIT_TARGET, WAIT_REFERENCE, DUE, CHECKED_MISSING, UNAVAILABLE]


def classify(df: pd.DataFrame, now: pd.Timestamp | None = None, today: date | None = None) -> pd.DataFrame:
    """Add `detail` (why each record has its status) and `checkable_from_utc` (first date it is checked).

    Pending records are split by what they are waiting for, so legitimately waiting records can be
    told apart from ones that were due, checked and still have no reference value.
    """
    out = df.copy()
    if out.empty:
        out["detail"] = pd.Series(dtype="object")
        out["checkable_from_utc"] = pd.Series(dtype="object")
        return out
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    cutoff = eligibility_cutoff(today or now.date())
    out["checkable_from_utc"] = (out["target_time_utc"].dt.normalize()
                                 + pd.Timedelta(days=config.REANALYSIS.lag_days + 1)).dt.date

    def detail(r) -> str:
        if r.status == "evaluated":
            return EVALUATED
        if r.status == "unavailable":
            return UNAVAILABLE
        if r.target_time_utc > now:
            return WAIT_TARGET
        if r.target_time_utc >= cutoff:
            return WAIT_REFERENCE
        note = r.status_note if isinstance(r.status_note, str) else ""
        return CHECKED_MISSING if note.startswith("Checked") else DUE
    out["detail"] = [detail(r) for r in out.itertuples()]
    return out


def status_counts(df: pd.DataFrame, now: pd.Timestamp | None = None) -> dict[str, int]:
    """Record counts for every detail category (zeros included), in display order."""
    counts = classify(df, now)["detail"].value_counts() if len(df) else pd.Series(dtype=int)
    return {k: int(counts.get(k, 0)) for k in DETAIL_ORDER}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        r = evaluate()
    except Exception as exc:  # stored as the last check by evaluate(); report and signal failure to schedulers
        print(f"Evaluation failed, no record was changed: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from None
    print(json.dumps(r, indent=2))
    print("Status:", json.dumps(status_counts(query()), indent=2))
    agg = aggregate(query())
    if agg["n"] == 0:
        print(f"No evaluated predictions yet: the reanalysis is published about {config.REANALYSIS.lag_days} days late.")
    else:
        print(f"{agg['n']} evaluated predictions from {agg['issue_times']} issue times "
              f"({agg['target_from_utc'][:10]} → {agg['target_to_utc'][:10]})"
              + ("" if agg["conclusive"] else " — too few to be conclusive"))
        for row in agg["by_horizon"]:
            print(f"  +{row['horizon_h']:>2} h  n={row['n']:<4} MAE {row['mae_c']:.2f} °C  RMSE {row['rmse_c']:.2f} "
                  f"(2025 test MAE {row['expected_mae_c']})  IFS value MAE {row['ifs_mae_c']}")


if __name__ == "__main__":
    main()
