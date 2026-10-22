"""Reproducibility check for every described Cloudzy Core model: reload the artifact, rebuild its recorded test rows
from the cached data, predict, and compare the metric with the one in its metadata.

Nothing is retrained. A model "reproduces" when the recomputed MAE matches the recorded one within TOLERANCE_C and
on the same number of rows. The check covers:
- research models (Phase A, one horizon): their recorded test split, from the processed CSV named in provenance;
- the deployed bundle: its held-out month, per horizon, from the cached raw files (and the lagged-forecast
  archive if the bundle uses it).

Files without a metadata sidecar are not checked: there is nothing recorded to compare with (src.registry lists
them as backups or undocumented).

Run:  python -m src.reproduce      → reports/reproducibility.json
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import joblib
import pandas as pd

from src import config
from src.artifacts import feature_config_of, file_sha256
from src.data_validation import load_processed
from src.evaluate import mae
from src.features import TARGET, build_features
from src.registry import DEPLOYED

logger = logging.getLogger(__name__)

REPORT_PATH = config.REPORTS_DIR / "reproducibility.json"
TOLERANCE_C = 0.0005


def _research(side: dict, art: Path) -> dict:
    bundle = joblib.load(art)
    data = (side.get("provenance") or {}).get("data") or {}
    processed = config.PROCESSED_DIR / str(data.get("processed_file", ""))
    if not data.get("processed_file") or not processed.is_file():
        return {"status": "not checked", "reason": f"processed data {processed.name} not in this checkout"}
    table, _ = build_features(load_processed(processed), feature_config_of(bundle))
    test = side["splits"]["test"]
    rows = table.loc[pd.Timestamp(test["first_issue_time_utc"]):pd.Timestamp(test["last_issue_time_utc"])]
    got = round(mae(rows[TARGET].to_numpy(), bundle["model"].predict(rows[bundle["feature_columns"]])), 4)
    want = side["metrics"]["test_metrics"]["mae_c"]
    n_ok = len(rows) == test["rows"]
    return {"metric": "test MAE +1 h", "period": f"{test['first_issue_time_utc'][:10]} to {test['last_issue_time_utc'][:10]}",
            "recorded_c": want, "recomputed_c": got, "rows": len(rows), "recorded_rows": test["rows"],
            "status": "reproduced" if n_ok and abs(got - want) <= TOLERANCE_C else "differs"}


def _deployed(side: dict, art: Path) -> dict:
    from src.refit import bundle_table, lagged_spec, load_source
    bundle = joblib.load(art)
    prov = side.get("provenance") or {}
    hold, end = (date.fromisoformat(d) for d in prov["holdout_month"])
    ref, _ = load_source(config.REANALYSIS, date(2023, 1, 1), end)
    live, _ = load_source(config.LIVE_ARCHIVE, date(2023, 1, 1), end)
    prev = None
    if lagged_spec(bundle):
        from src.mos import fetch_previous_runs
        prev = fetch_previous_runs(date(2024, 1, 1), end)
    per_h, ok = {}, True
    for h, want in side["metrics"]["holdout_mae_c"].items():
        h = int(h)
        table = bundle_table(ref, live, h, bundle, prev)
        rows = table[table.index >= pd.Timestamp(hold, tz="UTC")]
        got = round(mae(rows[TARGET].to_numpy(), bundle["horizons"][h]["model"].predict(rows[bundle["feature_columns"]])), 4)
        per_h[h] = {"recorded_c": want, "recomputed_c": got, "rows": len(rows)}
        ok &= abs(got - want) <= TOLERANCE_C
    return {"metric": "held-out month MAE", "period": hold.strftime("%Y-%m"), "horizons": per_h,
            "status": "reproduced" if ok else "differs"}


def run(models_dir: Path = config.MODELS_DIR) -> dict:
    out = {}
    for side_path in sorted(models_dir.glob("*.json")):
        side = json.loads(side_path.read_text())
        art = models_dir / side.get("artifact_file", "")
        if not art.is_file():
            out[side_path.stem] = {"status": "not checked", "reason": "artifact not in this checkout"}
            continue
        if file_sha256(art) != side.get("artifact_sha256"):
            out[side_path.stem] = {"status": "checksum mismatch"}
            continue
        res = _deployed(side, art) if art.name == DEPLOYED else _research(side, art)
        out[side_path.stem] = {"artifact_version": side.get("artifact_version"), **res}
        logger.info("%s: %s", side_path.stem, res["status"])
    report = {"created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"), "tolerance_c": TOLERANCE_C,
              "command": "python -m src.reproduce", "models": out}
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(run(), indent=1))


if __name__ == "__main__":
    main()
