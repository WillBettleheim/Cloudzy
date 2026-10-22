"""Cloudzy Core engineering: the full prediction lifecycle, invalid observations, schema migration, scheduled
evaluation, validated intervals and the model registry."""

import json
import sqlite3
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import intervals as IV
from src import monitoring as M
from src import registry as R
from tests.test_monitoring import Session, archive_payload, result


def with_interval(res, lo=-1.5, hi=1.0, level=0.8):
    for p in res["cloudzy"]["predictions"]:
        v = p["temperature_2m_c"]
        p["interval"] = {"level": level, "lower_c": v + lo, "upper_c": v + hi, "validated_coverage": 0.84,
                         "validation_n": 719}
    return res


# --- Full lifecycle ------------------------------------------------------------------------------------

def test_full_lifecycle_from_prediction_to_evaluation_and_summary(tmp_path):
    """Create → persist (with interval) → wait → due → evaluate → summarise; each state checked on the way."""
    res = with_interval(result(issue="2026-10-01T06:00:00+00:00", values=(30.0, 31.0), ifs=(29.0, 33.0)))
    assert M.record_prediction(res, tmp_path) == {"inserted": 2, "duplicates": 0, "rejected": 0}
    assert M.record_prediction(res, tmp_path)["duplicates"] == 2                       # retry-safe

    df = M.query(tmp_path)
    assert set(df["status"]) == {"pending"} and df["interval_lower"].notna().all()
    before = M.classify(df, now=pd.Timestamp("2026-10-01T06:30Z"), today=date(2026, 10, 1))
    assert set(before["detail"]) == {M.WAIT_TARGET}                                  # awaiting target time
    waiting = M.classify(df, now=pd.Timestamp("2026-10-03T00:00Z"), today=date(2026, 10, 3))
    assert set(waiting["detail"]) == {M.WAIT_REFERENCE}                              # not published yet
    assert M.due_count(tmp_path, date(2026, 10, 3)) == 0
    due = M.classify(df, now=pd.Timestamp("2026-10-19T00:00Z"), today=date(2026, 10, 19))
    assert set(due["detail"]) == {M.DUE} and M.due_count(tmp_path, date(2026, 10, 19)) == 2

    # Observed 30.5 at both target hours: +1 h inside [28.5, 31.0]; +24 h inside [29.5, 32.0].
    out = M.evaluate(date(2026, 10, 19), tmp_path, session=Session(archive_payload(temp=lambda i: 30.5)))
    assert out["newly_evaluated"] == 2 and out["still_pending"] == 0
    agg = M.aggregate(M.query(tmp_path))
    assert agg["n"] == 2 and agg["interval_coverage"] == {"n": 2, "coverage": 1.0, "level": 0.8}
    # Cloudzy errors 0.5 and 0.5; IFS errors 1.5 and 2.5 → skill = 1 - 0.5/2.0 = 0.75.
    assert agg["skill"]["mae_skill_vs_provider"] == pytest.approx(0.75)
    assert M.evaluate(date(2026, 10, 20), tmp_path, session=Session(fail=True))["outcome"] == "nothing due"


def test_malformed_interval_is_not_stored(tmp_path):
    res = with_interval(result(), lo=0.5, hi=1.0)              # range doesn't contain the prediction
    M.record_prediction(res, tmp_path)
    assert M.query(tmp_path)["interval_lower"].isna().all()


def test_implausible_reference_value_is_invalid_and_never_scored(tmp_path):
    M.record_prediction(result(), tmp_path)
    out = M.evaluate(date(2026, 10, 19), tmp_path, session=Session(archive_payload(temp=lambda i: 75.0)))
    df = M.classify(M.query(tmp_path))
    assert out["marked_invalid"] == 2 and set(df["status"]) == {"invalid"} and set(df["detail"]) == {M.INVALID}
    assert df["abs_error"].isna().all() and "outside physical bounds" in df["status_note"].iloc[0]
    assert M.aggregate(M.query(tmp_path)) == {"n": 0}


# --- Schema migration ---------------------------------------------------------------------------------

def test_old_check_constraint_is_migrated_with_backup_and_no_data_loss(tmp_path):
    old = (M.SCHEMA.replace("'unavailable', 'invalid'", "'unavailable'")
           .replace(",\n  interval_lower REAL,\n  interval_upper REAL,\n  interval_level REAL", ""))
    con = sqlite3.connect(tmp_path / M.DB_NAME)
    con.executescript(old)
    for i in range(3):
        con.execute("INSERT INTO predictions (id, created_at_utc, issue_time_utc, target_time_utc, horizon_h, "
                    "location_id, latitude, longitude, model_version, variable, predicted, unit, status, observed, "
                    "abs_error) VALUES (?, '2026-10-01T06:00:00+00:00', '2026-10-01T06:00:00+00:00', "
                    "'2026-10-01T07:00:00+00:00', 1, 'delhi-grid', 28.6, 77.2, 'old', 'temperature_2m', ?, '°C', "
                    "'evaluated', 30.0, 1.0)", (f"id{i}", 31.0 + i))
    con.commit()
    con.close()
    df = M.query(tmp_path)
    assert len(df) == 3 and set(df["predicted"]) == {31.0, 32.0, 33.0} and "interval_lower" in df.columns
    backups = list(Path(tmp_path).glob("*.backup-before-invalid-status-*.sqlite"))
    assert len(backups) == 1
    with sqlite3.connect(tmp_path / M.DB_NAME) as c:
        sql = c.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()[0]
        meta = json.loads(c.execute("SELECT value FROM meta WHERE key='migration_invalid_status'").fetchone()[0])
    assert "'invalid'" in sql and meta["rows"] == 3
    M.query(tmp_path)                                         # second open: nothing to migrate, no new backup
    assert len(list(Path(tmp_path).glob("*.backup-*"))) == 1


# --- Scheduled evaluation --------------------------------------------------------------------------------

def test_scheduler_runs_only_when_due_and_not_too_often(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(M, "evaluate", lambda today=None, directory=None, session=None: calls.append(today))
    now = pd.Timestamp("2026-10-19T12:00Z")
    assert M.maybe_evaluate(tmp_path, now, background=False) == "not due"        # nothing recorded
    M.record_prediction(result(), tmp_path)
    assert M.maybe_evaluate(tmp_path, now, background=False) == "ran" and len(calls) == 1
    with sqlite3.connect(tmp_path / M.DB_NAME) as c:                            # a check 2 h ago
        c.execute("INSERT OR REPLACE INTO meta VALUES ('last_check', ?)",
                  (json.dumps({"evaluated_at_utc": (now - pd.Timedelta(hours=2)).isoformat()}),))
    assert M.maybe_evaluate(tmp_path, now, background=False) == "recent" and len(calls) == 1
    later = now + pd.Timedelta(hours=M.EVALUATE_EVERY_H)
    assert M.maybe_evaluate(tmp_path, later, background=False) == "ran" and len(calls) == 2


def test_scheduler_respects_the_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDZY_MONITORING", "0")
    assert M.maybe_evaluate(tmp_path) == "disabled"


# --- Intervals ---------------------------------------------------------------------------------------------

def test_conformal_quantiles_cover_exchangeable_data():
    rng = np.random.default_rng(0)
    cal, test = rng.normal(0.3, 1.0, 4000), rng.normal(0.3, 1.0, 20000)
    for level in (0.8, 0.95):
        q = IV.conformal_quantiles(cal, level)
        cov = IV.coverage(test, np.zeros_like(test), q)["coverage"]
        assert abs(cov - level) < 0.02 and q[0] < 0.3 < q[1]
    with pytest.raises(ValueError, match="too few"):
        IV.conformal_quantiles(np.zeros(10), 0.8)


def test_conformal_quantiles_are_asymmetric_for_skewed_errors():
    rng = np.random.default_rng(1)
    q = IV.conformal_quantiles(rng.exponential(1.0, 5000) - 0.5, 0.8)
    assert abs(q[1]) > abs(q[0])


def test_intervals_are_served_only_for_the_calibrated_bundle(tmp_path):
    bundle = tmp_path / "bundle.joblib"
    bundle.write_bytes(b"model v1")
    report = {"provenance": {"bundle_sha256": IV.bundle_sha256(str(bundle))},
              "stages": {"deployed": {"validate": ["2026-09-01", "2026-10-01"]}},
              "horizons": {"1": {"deployed": {"levels": {"0.80": {"q_lo_c": -1.5, "q_hi_c": 1.0,
                                                                  "validation": {"coverage": 0.84, "n": 719}}}}}}}
    path = tmp_path / "intervals.json"
    path.write_text(json.dumps(report))
    IV._report.cache_clear()
    iv = IV.interval(1, 25.0, path=path, bundle_path=bundle)
    assert iv["lower_c"] == 23.5 and iv["upper_c"] == 26.0 and iv["validated_coverage"] == 0.84
    assert IV.interval(2, 25.0, path=path, bundle_path=bundle) is None             # horizon not calibrated
    other = tmp_path / "bundle2.joblib"
    other.write_bytes(b"model v2 after a refit")
    IV.bundle_sha256.cache_clear()
    assert IV.interval(1, 25.0, path=path, bundle_path=other) is None            # different model: no range
    IV._report.cache_clear()


def test_committed_interval_study_is_conservative_and_matches_the_deployed_bundle():
    rep = json.loads(IV.REPORT_PATH.read_text())
    IV.bundle_sha256.cache_clear()
    assert rep["provenance"]["bundle_sha256"] == IV.bundle_sha256()
    for h, r in rep["horizons"].items():
        for lv in ("0.80", "0.95"):
            v = r["deployed"]["levels"][lv]["validation"]
            assert v["coverage"] >= float(lv) - 0.05, (h, lv, v)                 # never badly too narrow
            assert v["n"] > 600 and r["deployed"]["levels"][lv]["q_lo_c"] < 0 < r["deployed"]["levels"][lv]["q_hi_c"]
        assert r["method_check"]["calibration_rows"] > 4000 and r["method_check"]["validation_rows"] > 4000


def test_interval_study_periods_never_overlap():
    for stage in IV.STAGES.values():
        fit, cal, val = (tuple(pd.Timestamp(x) for x in stage[k]) for k in ("fit", "calibrate", "validate"))
        assert fit[1] <= cal[0] and cal[1] <= val[0]


# --- Registry ---------------------------------------------------------------------------------------------

def test_registry_lists_every_model_with_verified_artifacts_and_one_deployed():
    entries = R.model_entries()
    assert sum(e.status == "deployed" for e in entries) == 1
    dep = R.deployed_entry()
    assert dep.artifact == "verified" and dep.horizons_h == [1, 2, 3, 6, 12, 24]
    assert dep.inputs_source == "ecmwf_ifs" and dep.target_source == "era5_seamless" and dep.feature_count == 27
    assert all(m["period"] for m in dep.metrics) and any(m["n"] for m in dep.metrics)


def test_registry_flags_missing_and_tampered_artifacts(tmp_path):
    side = json.loads((Path("models") / "cloudzy_core_delhi_serving_compact.json").read_text())
    (tmp_path / "cloudzy_core_delhi_serving_compact.json").write_text(json.dumps(side))
    assert R.model_entries(tmp_path)[0].artifact == "metadata only"
    (tmp_path / "cloudzy_core_delhi_serving_compact.joblib").write_bytes(b"tampered")
    assert R.model_entries(tmp_path)[0].artifact == "checksum mismatch"


def test_registry_never_calls_providers_or_rules_ml():
    kinds = {s.name: (s.kind, s.ml) for s in R.SYSTEMS}
    assert kinds["Open-Meteo forecast (best_match)"] == ("provider forecast", False)
    assert kinds["Outdoor Intelligence"] == ("rule engine", False)
    assert kinds["CAMS air-quality forecast"][1] is False
