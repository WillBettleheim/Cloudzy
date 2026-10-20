"""Cloudzy Core forecast service: live inputs in, labelled predictions out.

What it does
1. Fetches recent hourly ECMWF IFS values for the location from the Open-Meteo
   Forecast API (past 2 days plus the next 2 days), and validates them with the
   same checks as the training data.
2. Uses the latest complete hour at or before "now" as the issue time t. Hours
   up to t are model inputs. Later hours are the provider's own forecast.
3. Predicts temperature at t+1, 2, 3, 6, 12 and 24 h with the direct
   multi-horizon models (src.multistep), trained to map IFS inputs to the
   reanalysis temperature.
4. Returns Cloudzy's predictions and the provider forecast side by side, with
   explicit labels. They are never mixed.
5. Optionally saves the raw API response to data/live_snapshots/, so the
   difference between real-time inputs and the archive replay can be measured
   later.

Input requirements: at least 25 consecutive complete hours ending at t, with the
five variables in config.HOURLY_VARIABLES, on a UTC hourly grid.

Limitations
- Inputs are NWP model output, not observations.
- Accuracy figures come from a 2025 replay, which is a best case for live
  inputs. They are average errors (MAE), not confidence intervals.
- Single location (Delhi). Experimental; not an operational forecast.

Run:  python -m src.service [--no-snapshot]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from src import config
from src.data_collection import INTERACTIVE, check_response, fetch_json
from src.data_validation import validate_and_clean, validate_metadata
from src.features import TEMP, FeatureConfig, add_features

logger = logging.getLogger(__name__)

# The deployed models: size-constrained gradient boosting (src.compact), about 2 MB
# and committed to git. The research random-forest bundle (src.multistep) is
# 1.8 GB. It is no more accurate on 2025, but can be selected with
# CLOUDZY_SERVING_BUNDLE=/path/to/bundle.joblib.
SERVING_BUNDLE = Path(os.environ.get(
    "CLOUDZY_SERVING_BUNDLE", config.MODELS_DIR / f"cloudzy_core_{config.DELHI.slug}_serving_compact.joblib"))
SNAPSHOT_DIR = config.DATA_DIR / "live_snapshots"
SNAPSHOT_KEEP_DAYS = 30   # older raw snapshots are deleted, so the folder can't grow without limit
MIN_HISTORY_HOURS = 25
CLOUDZY_LABEL = "Cloudzy Core ML prediction (experimental)"
PROVIDER_LABEL = "ECMWF IFS forecast via Open-Meteo (provider)"
LIMITATIONS = [
    "Inputs are ECMWF IFS model values, not station observations.",
    "Cloudzy is trained to predict ERA5/ERA5-Land reanalysis temperature for one grid cell, "
    "not a specific thermometer.",
    "typical_error_c is the mean absolute error on a 2025 replay of archived inputs, "
    "a best case for real-time use; it is not a confidence interval.",
    "Experimental project; not an operational meteorological forecast.",
]


class ServiceError(RuntimeError):
    """Live data cannot support a prediction."""


def fetch_live(location: config.Location = config.DELHI, session: requests.Session | None = None,
               snapshot_dir: Path | None = SNAPSHOT_DIR) -> tuple[pd.DataFrame, str]:
    """Return (validated hourly frame covering past 2 and next 2 days, retrieval time)."""
    variables = list(config.HOURLY_VARIABLES)
    params = {
        "latitude": location.latitude, "longitude": location.longitude,
        "hourly": ",".join(variables), "models": config.LIVE_MODEL, "timezone": "GMT",
        "past_days": 2, "forecast_days": 2,
    }
    payload = fetch_json(config.FORECAST_API_URL, params, session=session, **INTERACTIVE)
    retrieved = datetime.now(UTC).isoformat(timespec="seconds")
    check_response(payload, variables)
    meta = {k: v for k, v in payload.items() if k != "hourly"} | {"file": "live"}
    validate_metadata([meta], config.HOURLY_VARIABLES)
    frame, report = validate_and_clean(pd.DataFrame(payload["hourly"]), variables, config.PLAUSIBLE_RANGES)
    for w in report.warnings:
        logger.warning("Live data: %s", w)
    if snapshot_dir is not None:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        stamp = retrieved.replace(":", "").replace("+0000", "Z")
        (snapshot_dir / f"{location.slug}_{config.LIVE_MODEL}_{stamp}.json").write_text(
            json.dumps({"retrieved_at_utc": retrieved, "request_params": params, "response": payload}))
        prune_snapshots(snapshot_dir)
    return frame, retrieved


def prune_snapshots(snapshot_dir: Path, keep_days: int = SNAPSHOT_KEEP_DAYS, now: float | None = None) -> int:
    """Delete raw live snapshots older than `keep_days`. Never raises; returns how many were removed."""
    import time
    cutoff = (now if now is not None else time.time()) - keep_days * 86400
    removed = 0
    for f in snapshot_dir.glob(f"*_{config.LIVE_MODEL}_*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("Couldn't remove old snapshot %s: %s", f.name, exc)
    return removed


class CloudzyForecaster:
    def __init__(self, bundle_path: Path = SERVING_BUNDLE):
        from src.artifacts import load_trusted
        if not Path(bundle_path).exists():
            raise FileNotFoundError(f"{bundle_path} not found; run `python -m src.compact` first")
        # Verified, then cached until the file changes: no re-reading on every request.
        self.bundle = load_trusted(Path(bundle_path), {"horizons", "feature_columns", "feature_config",
                                                       "test_2025_mae_c"})
        from src.artifacts import file_sha256, read_metadata
        meta = read_metadata(Path(bundle_path))
        self.version = (meta or {}).get("artifact_version") or f"{Path(bundle_path).stem}@{file_sha256(Path(bundle_path))[:12]}"
        cfg = self.bundle["feature_config"]
        self.cfg = FeatureConfig(**{k: tuple(v) if isinstance(v, list) else v for k, v in cfg.items()})

    @property
    def horizons(self) -> list[int]:
        return sorted(int(h) for h in self.bundle["horizons"])

    def predict(self, history: pd.DataFrame, issue_time: pd.Timestamp) -> list[dict]:
        """Cloudzy predictions for every horizon, from history up to and including issue_time."""
        past = history.loc[:issue_time]
        if len(past) < MIN_HISTORY_HOURS or past.index[-1] != issue_time:
            raise ServiceError(f"Need {MIN_HISTORY_HOURS} complete hours ending at {issue_time}")
        window = past.iloc[-MIN_HISTORY_HOURS:]
        X = add_features(window, self.cfg)[self.bundle["feature_columns"]].iloc[[-1]]
        if X.isna().any(axis=None):
            raise ServiceError(f"Incomplete inputs: {X.columns[X.isna().iloc[0]].tolist()}")
        out = []
        for h in self.horizons:
            value = float(self.bundle["horizons"][h]["model"].predict(X)[0])
            if not np.isfinite(value):
                raise ServiceError(f"Model produced a non-finite value at h={h}")
            out.append({"horizon_h": h, "temperature_2m_c": round(value, 2),
                        "typical_error_c": self.bundle["test_2025_mae_c"][h]})
        return out


def build_forecast(frame: pd.DataFrame, forecaster: CloudzyForecaster, now: pd.Timestamp,
                   retrieved_at: str, location: config.Location = config.DELHI) -> dict:
    issue = now.floor("h")
    if issue not in frame.index:
        raise ServiceError(f"Live data does not contain the issue hour {issue}")

    def times(t: pd.Timestamp) -> dict:
        return {"valid_time_utc": t.isoformat(),
                "valid_time_local": t.tz_convert(location.local_timezone).isoformat()}

    cloudzy = []
    for row in forecaster.predict(frame, issue):
        cloudzy.append({**row, **times(issue + pd.Timedelta(hours=row["horizon_h"]))})
    provider = []
    for row in cloudzy:
        t = pd.Timestamp(row["valid_time_utc"])
        val = frame[TEMP].get(t, np.nan)
        provider.append({"horizon_h": row["horizon_h"], **times(t),
                         "temperature_2m_c": None if pd.isna(val) else float(val)})
    return {
        "location": location.name,
        "issue_time_utc": issue.isoformat(),
        "issue_time_local": issue.tz_convert(location.local_timezone).isoformat(),
        "retrieved_at_utc": retrieved_at,
        "inputs": {
            "source": PROVIDER_LABEL, "kind": "numerical weather model output (not observations)",
            "temperature_2m_at_issue_c": float(frame.loc[issue, TEMP]),
            # Snapshot of the model inputs at issue time (the Delhi grid cell; no user data).
            "values": {v: (None if pd.isna(frame.loc[issue, v]) else float(frame.loc[issue, v]))
                       for v in config.HOURLY_VARIABLES},
        },
        "model_version": forecaster.version,
        "cloudzy": {"label": CLOUDZY_LABEL, "predictions": cloudzy},
        "provider": {"label": PROVIDER_LABEL, "forecasts": provider},
        "limitations": LIMITATIONS,
    }


def forecast_now(snapshot: bool = True, session: requests.Session | None = None) -> dict:
    frame, retrieved = fetch_live(session=session, snapshot_dir=SNAPSHOT_DIR if snapshot else None)
    return build_forecast(frame, CloudzyForecaster(), pd.Timestamp.now(tz="UTC"), retrieved)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-snapshot", action="store_true", help="do not save the raw live response")
    parser.add_argument("--json", action="store_true", help="print the full JSON result")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    result = forecast_now(snapshot=not args.no_snapshot)
    if args.json:
        print(json.dumps(result, indent=2))
        return
    print(f"\n☁️  Cloudzy — {result['location']}   issued {result['issue_time_local']}")
    print(f"   input temperature now (IFS model value): {result['inputs']['temperature_2m_at_issue_c']:.1f} °C\n")
    print(f"   {'ahead':>6}  {'local time':<17} {'Cloudzy':>8}  {'2025 MAE*':>9}  {'IFS':>6}")
    for c, p in zip(result["cloudzy"]["predictions"], result["provider"]["forecasts"]):
        ifs = "—" if p["temperature_2m_c"] is None else f"{p['temperature_2m_c']:.1f}"
        print(f"   {c['horizon_h']:>4} h  {c['valid_time_local'][:16]:<17} {c['temperature_2m_c']:>7.1f}°  "
              f"{c['typical_error_c']:>8.2f}°  {ifs:>6}")
    print("\n   Cloudzy = experimental ML prediction of reanalysis temperature. IFS = provider forecast.")
    print("   * Cloudzy's average error on a 2025 replay (best case); not a confidence interval.")


if __name__ == "__main__":
    main()
