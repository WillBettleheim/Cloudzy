"""Tests for prediction tracking (SQLite). The reference API is faked; tests use a temporary folder."""

import json
from datetime import date

import pandas as pd
import pytest

import src.briefing as briefing
from src import monitoring as M
from src.data_collection import DataCollectionError
from src.provider import Place

DELHI = Place("New Delhi", 28.6358, 77.2244, "Asia/Kolkata", "India", None, None, "IN")


def result(issue="2026-10-01T06:00:00+00:00", values=(30.0, 31.0), ifs=(29.0, 33.0), horizons=(1, 24),
           version="cloudzy_core_delhi_serving_compact@abc123def456"):
    t0 = pd.Timestamp(issue)
    return {
        "issue_time_utc": issue, "model_version": version,
        "inputs": {"values": {"temperature_2m": 29.5, "relative_humidity_2m": 40.0, "surface_pressure": 980.0,
                              "wind_speed_10m": 8.0, "precipitation": 0.0}},
        "cloudzy": {"predictions": [
            {"horizon_h": h, "valid_time_utc": (t0 + pd.Timedelta(hours=h)).isoformat(), "temperature_2m_c": v,
             "typical_error_c": 1.1 if h == 1 else 1.34} for h, v in zip(horizons, values)]},
        "provider": {"forecasts": [{"horizon_h": h, "temperature_2m_c": v} for h, v in zip(horizons, ifs)]},
    }


def archive_payload(start="2026-10-01", hours=48, temp=lambda i: 30.5):
    times = pd.date_range(start, periods=hours, freq="h")
    return {"utc_offset_seconds": 0, "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
            "hourly": {"time": times.strftime("%Y-%m-%dT%H:%M").tolist(), "temperature_2m": [temp(i) for i in range(hours)]}}


class Session:
    def __init__(self, payload=None, fail=False):
        self.payload, self.fail, self.calls, self.headers = payload, fail, 0, {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.fail:
            import requests
            raise requests.ConnectionError("offline")
        body = self.payload

        class R:
            status_code, text, headers = 200, "", {}

            def json(self):
                return body
        return R()


# --- Recording -----------------------------------------------------------------------------

def test_record_creates_pending_records_with_full_fields(tmp_path):
    counts = M.record_prediction(result(), tmp_path)
    assert counts == {"inserted": 2, "duplicates": 0, "rejected": 0}
    df = M.query(tmp_path)
    r = df[df["horizon_h"] == 24].iloc[0]
    assert r["status"] == "pending" and r["unit"] == "°C" and r["variable"] == "temperature_2m"
    assert r["target_time_utc"] == pd.Timestamp("2026-10-02T06:00Z")       # issue + 24 h
    assert r["location_id"] == "delhi-grid" and r["model_version"].endswith("abc123def456")
    assert r["provider_value"] == 33.0 and "provider forecast" in r["provider_source"]
    h1 = df[df["horizon_h"] == 1].iloc[0]
    assert json.loads(h1["inputs_json"])["temperature_2m"] == 29.5        # snapshot once per issue time


def test_recording_is_idempotent(tmp_path):
    M.record_prediction(result(), tmp_path)
    again = M.record_prediction(result(values=(99.0, 99.0)), tmp_path)    # re-render / refresh / retry
    assert again == {"inserted": 0, "duplicates": 2, "rejected": 0}
    assert list(M.query(tmp_path).sort_values("horizon_h")["predicted"]) == [30.0, 31.0]


def test_new_model_version_is_a_separate_record(tmp_path):
    M.record_prediction(result(), tmp_path)
    M.record_prediction(result(version="cloudzy_core_delhi_serving_compact@999999999999"), tmp_path)
    assert len(M.query(tmp_path)) == 4 and len(M.distinct("model_version", tmp_path)) == 2


@pytest.mark.parametrize("mutate", [
    lambda r: r["cloudzy"]["predictions"][0].update(temperature_2m_c=float("nan")),
    lambda r: r["cloudzy"]["predictions"][0].update(temperature_2m_c=None),
    lambda r: r["cloudzy"]["predictions"][0].update(valid_time_utc="2026-10-01T09:00:00+00:00"),  # not issue + 1 h
])
def test_invalid_predictions_are_not_recorded(tmp_path, mutate):
    r = result()
    mutate(r)
    counts = M.record_prediction(r, tmp_path)
    assert counts["rejected"] == 1 and counts["inserted"] == 1


def test_refusals_are_logged_separately(tmp_path):
    M.log_failure("ServiceError: Need 25 complete hours", tmp_path)
    assert M.query(tmp_path).empty and "25 complete hours" in M.refusals(tmp_path)["reason"].iloc[0]


# --- Evaluation ------------------------------------------------------------------------------

def test_evaluation_matches_exact_hour_and_records_source(tmp_path):
    M.record_prediction(result(), tmp_path)
    s = Session(archive_payload(temp=lambda i: 20.0 + i))  # 2026-10-01T00:00 = 20.0, 07:00 = 27.0, +30 h = 50
    out = M.evaluate(date(2026, 10, 19), tmp_path, session=s)
    assert out["newly_evaluated"] == 2 and out["still_pending"] == 0
    df = M.query(tmp_path).set_index("horizon_h")
    assert df.loc[1, "observed"] == 27.0 and df.loc[1, "abs_error"] == 3.0     # 07:00 UTC exactly
    assert df.loc[24, "observed"] == 50.0                                       # 2026-10-02 06:00 UTC
    assert "not a station observation" in df.loc[1, "observation_source"]


def test_not_yet_matured_predictions_stay_pending_without_any_request(tmp_path):
    M.record_prediction(result(issue="2026-10-18T06:00:00+00:00"), tmp_path)
    s = Session(archive_payload())
    out = M.evaluate(date(2026, 10, 19), tmp_path, session=s)
    assert s.calls == 0 and out["still_pending"] == 2


def test_missing_reference_stays_pending_then_becomes_unavailable(tmp_path):
    M.record_prediction(result(), tmp_path)
    empty = archive_payload(start="2026-09-01", hours=24)  # covers other hours only
    M.evaluate(date(2026, 10, 19), tmp_path, session=Session(empty))
    assert set(M.query(tmp_path)["status"]) == {"pending"}               # not invented
    M.evaluate(date(2026, 11, 15), tmp_path, session=Session(empty))      # > 30 days after target
    assert set(M.query(tmp_path)["status"]) == {"unavailable"}


def test_reference_failure_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data_collection.time.sleep", lambda s: None)
    M.record_prediction(result(), tmp_path)
    with pytest.raises(DataCollectionError):
        M.evaluate(date(2026, 10, 19), tmp_path, session=Session(fail=True))
    assert set(M.query(tmp_path)["status"]) == {"pending"}


def test_reference_must_be_utc_and_celsius():
    p = archive_payload()
    p["utc_offset_seconds"] = 19800
    with pytest.raises(ValueError, match="UTC"):
        M.fetch_reanalysis(date(2026, 10, 1), date(2026, 10, 2), session=Session(p))
    p = archive_payload()
    p["hourly_units"]["temperature_2m"] = "°F"
    with pytest.raises(ValueError, match="°C"):
        M.fetch_reanalysis(date(2026, 10, 1), date(2026, 10, 2), session=Session(p))


# --- Aggregates and queries ---------------------------------------------------------------------

def test_aggregates_use_only_evaluated_records(tmp_path):
    M.record_prediction(result(), tmp_path)
    M.record_prediction(result(issue="2026-10-18T06:00:00+00:00"), tmp_path)   # stays pending
    M.evaluate(date(2026, 10, 19), tmp_path, session=Session(archive_payload(temp=lambda i: 30.5)))
    agg = M.aggregate(M.query(tmp_path))
    by_h = {r["horizon_h"]: r for r in agg["by_horizon"]}
    assert agg["n"] == 2 and agg["issue_times"] == 1 and agg["conclusive"] is False
    assert by_h[1]["mae_c"] == 0.5 and by_h[1]["ifs_mae_c"] == 1.5 and by_h[1]["expected_mae_c"] == 1.1
    assert by_h[24]["bias_c"] == 0.5
    assert agg["target_from_utc"].startswith("2026-10-01T07:00")


def test_aggregate_of_nothing():
    assert M.aggregate(pd.DataFrame()) == {"n": 0}


def test_query_filters(tmp_path):
    M.record_prediction(result(), tmp_path)
    M.record_prediction(result(issue="2026-10-10T06:00:00+00:00", version="v2"), tmp_path)
    assert len(M.query(tmp_path, model_version="v2")) == 2
    assert len(M.query(tmp_path, start=pd.Timestamp("2026-10-05", tz="UTC"))) == 2
    assert len(M.query(tmp_path, end=pd.Timestamp("2026-10-01T12:00", tz="UTC"))) == 1
    assert len(M.query(tmp_path, status="evaluated")) == 0
    with pytest.raises(ValueError):
        M.distinct("predicted; DROP TABLE predictions", tmp_path)


# --- Legacy import and app integration ------------------------------------------------------------

def test_legacy_jsonl_is_imported_once_and_kept(tmp_path):
    rows = [{"logged_at_utc": "2026-10-19T08:12:06+00:00", "issue_time_utc": "2026-10-19T08:00:00+00:00",
             "valid_time_utc": "2026-10-19T09:00:00+00:00", "horizon_h": 1, "cloudzy_c": 28.9, "ifs_c": 29.3,
             "expected_mae_c": 1.12}] * 2 + ["{broken"]
    (tmp_path / M.LEGACY_JSONL).write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows))
    df = M.query(tmp_path)
    assert len(df) == 1 and df["model_version"].iloc[0].startswith("unrecorded")
    assert (tmp_path / M.LEGACY_JSONL).exists()
    assert len(M.query(tmp_path)) == 1  # second connect doesn't re-import


def test_cloudzy_block_records_predictions_and_refusals(monkeypatch, tmp_path):
    import src.service
    monkeypatch.setenv("CLOUDZY_MONITORING_DIR", str(tmp_path))
    monkeypatch.setattr(src.service, "forecast_now", lambda snapshot=True, session=None: result())
    assert briefing.cloudzy_block(DELHI)["available"]
    briefing.cloudzy_block(DELHI)  # same issue time again: no duplicate

    def boom(snapshot=True, session=None):
        raise src.service.ServiceError("Need 25 complete hours")
    monkeypatch.setattr(src.service, "forecast_now", boom)
    assert not briefing.cloudzy_block(DELHI)["available"]
    assert len(M.query(tmp_path)) == 2 and len(M.refusals(tmp_path)) == 1


def test_tracking_problems_never_break_a_prediction(monkeypatch):
    import src.service
    monkeypatch.setattr(src.service, "forecast_now", lambda snapshot=True, session=None: result())

    def broken(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(M, "log_prediction", broken)
    assert briefing.cloudzy_block(DELHI)["available"]


def test_tracking_can_be_disabled(monkeypatch, tmp_path):
    import src.service
    monkeypatch.setenv("CLOUDZY_MONITORING", "0")
    monkeypatch.setenv("CLOUDZY_MONITORING_DIR", str(tmp_path))
    monkeypatch.setattr(src.service, "forecast_now", lambda snapshot=True, session=None: result())
    briefing.cloudzy_block(DELHI)
    assert not (tmp_path / M.DB_NAME).exists()


# --- Timestamps: one instant, one record, always UTC ----------------------------------------------

def ist_result(issue_ist="2026-10-01T11:30:00+05:30", **kw):
    """The same predictions as result(), written with an IST offset instead of UTC."""
    r = result(**kw)
    t0 = pd.Timestamp(issue_ist)
    r["issue_time_utc"] = issue_ist
    for p in r["cloudzy"]["predictions"]:
        p["valid_time_utc"] = (t0 + pd.Timedelta(hours=p["horizon_h"])).isoformat()
    return r


def test_offsets_are_normalised_to_utc_and_never_duplicate(tmp_path):
    assert M.record_prediction(result(), tmp_path)["inserted"] == 2
    again = M.record_prediction(ist_result(), tmp_path)        # 11:30 IST == 06:00 UTC: the same predictions
    assert again == {"inserted": 0, "duplicates": 2, "rejected": 0}
    import sqlite3
    rows = sqlite3.connect(tmp_path / M.DB_NAME).execute("SELECT issue_time_utc, target_time_utc FROM predictions").fetchall()
    assert all(i.endswith("+00:00") and t.endswith("+00:00") for i, t in rows)


def test_ist_written_prediction_is_stored_in_utc_and_filters_correctly(tmp_path):
    M.record_prediction(ist_result(), tmp_path)
    df = M.query(tmp_path, start=pd.Timestamp("2026-10-01T07:00Z"), end=pd.Timestamp("2026-10-01T07:00Z"))
    assert list(df["horizon_h"]) == [1]                        # target 07:00 UTC, found by a UTC filter


def test_timestamps_without_a_time_zone_are_rejected(tmp_path):
    r = result()
    r["cloudzy"]["predictions"][0]["valid_time_utc"] = "2026-10-01T07:00:00"     # naive
    assert M.record_prediction(r, tmp_path)["rejected"] == 1
    r = result(issue="2026-10-01T06:00:00")
    with pytest.raises(ValueError, match="timezone-aware"):
        M.record_prediction(r, tmp_path)


# --- Delayed reference data, retries and repeated runs ------------------------------------------------

def test_delayed_reference_is_retried_then_evaluated(tmp_path):
    M.record_prediction(result(), tmp_path)
    nulls = archive_payload(temp=lambda i: None)               # hours present, values not published yet
    out = M.evaluate(date(2026, 10, 19), tmp_path, session=Session(nulls))
    assert out["newly_evaluated"] == 0 and out["still_pending"] == 2
    df = M.classify(M.query(tmp_path), now=pd.Timestamp("2026-10-19T12:00Z"))
    assert set(df["status"]) == {"pending"} and df["observed"].isna().all()      # never a fabricated value
    assert set(df["detail"]) == {M.CHECKED_MISSING}
    out = M.evaluate(date(2026, 10, 20), tmp_path, session=Session(archive_payload(temp=lambda i: 30.0)))
    df = M.query(tmp_path)
    assert out["newly_evaluated"] == 2 and set(df["status"]) == {"evaluated"} and df["status_note"].isna().all()


def test_repeated_evaluation_never_double_counts(tmp_path):
    M.record_prediction(result(), tmp_path)
    first = M.evaluate(date(2026, 10, 19), tmp_path, session=Session(archive_payload()))
    s = Session(archive_payload())
    second = M.evaluate(date(2026, 10, 19), tmp_path, session=s)
    assert first["newly_evaluated"] == 2 and second["newly_evaluated"] == 0 and s.calls == 0
    assert len(M.query(tmp_path)) == 2 and M.aggregate(M.query(tmp_path))["n"] == 2


def test_last_check_is_stored_for_success_nothing_due_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data_collection.time.sleep", lambda s: None)
    assert M.last_check(tmp_path) is None
    M.record_prediction(result(issue="2026-10-18T06:00:00+00:00"), tmp_path)
    M.evaluate(date(2026, 10, 19), tmp_path, session=Session(archive_payload()))
    assert M.last_check(tmp_path)["outcome"] == "nothing due"
    M.record_prediction(result(), tmp_path)
    with pytest.raises(DataCollectionError):
        M.evaluate(date(2026, 10, 19), tmp_path, session=Session(fail=True))
    failed = M.last_check(tmp_path)
    assert failed["outcome"] == "failed" and failed["error"].startswith("DataCollectionError")
    assert set(M.query(tmp_path)["status"]) == {"pending"}                       # nothing changed
    ok = M.evaluate(date(2026, 10, 19), tmp_path, session=Session(archive_payload()))
    stored = M.last_check(tmp_path)
    assert stored["outcome"] == "ok" and stored["newly_evaluated"] == ok["newly_evaluated"] == 2
    assert stored["reference_until_utc"].startswith("2026-10-02T23:00")


def test_unavailable_records_keep_a_reason(tmp_path):
    M.record_prediction(result(), tmp_path)
    M.evaluate(date(2026, 11, 15), tmp_path, session=Session(archive_payload(start="2026-09-01", hours=24)))
    df = M.classify(M.query(tmp_path))
    assert set(df["status"]) == {"unavailable"} and set(df["detail"]) == {M.UNAVAILABLE}
    assert df["status_note"].str.contains("No reference value within 30 days").all()
    assert M.aggregate(df) == {"n": 0}                                           # excluded from accuracy


# --- Status breakdown -----------------------------------------------------------------------------

def test_pending_records_are_classified_by_what_they_wait_for(tmp_path):
    now = pd.Timestamp("2026-10-20T04:00Z")
    M.record_prediction(result(issue="2026-10-20T03:00:00+00:00", horizons=(1, 24)), tmp_path)   # 04:00, +1 day
    M.record_prediction(result(issue="2026-10-18T06:00:00+00:00", horizons=(1, 2)), tmp_path)    # past, recent
    M.record_prediction(result(issue="2026-10-01T06:00:00+00:00", horizons=(1, 2)), tmp_path)    # old enough
    df = M.classify(M.query(tmp_path), now=now)
    by = dict(zip(df["target_time_utc"].dt.strftime("%m-%d %H"), df["detail"], strict=True))
    assert by["10-21 03"] == M.WAIT_TARGET
    assert by["10-18 07"] == M.WAIT_REFERENCE and by["10-18 08"] == M.WAIT_REFERENCE
    assert by["10-01 07"] == M.DUE
    counts = M.status_counts(M.query(tmp_path), now=now)
    assert by["10-20 04"] == M.WAIT_REFERENCE                  # exactly "now": the hour has been reached
    assert counts[M.WAIT_TARGET] == 1 and counts[M.WAIT_REFERENCE] == 3 and counts[M.DUE] == 2
    checkable = dict(zip(df["target_time_utc"].dt.strftime("%m-%d %H"), df["checkable_from_utc"], strict=True))
    assert str(checkable["10-18 07"]) == "2026-10-25"          # target date + publication delay + 1 day


def test_eligibility_uses_the_utc_date():
    assert M.eligibility_cutoff(date(2026, 10, 20)) == pd.Timestamp("2026-10-14", tz="UTC")


def test_older_databases_gain_the_note_column_without_losing_records(tmp_path):
    import sqlite3
    con = sqlite3.connect(tmp_path / M.DB_NAME)
    con.executescript(M.SCHEMA.replace(",\n  status_note TEXT", ""))
    con.execute("INSERT INTO predictions (id, created_at_utc, issue_time_utc, target_time_utc, horizon_h, location_id, "
                "latitude, longitude, model_version, variable, predicted, unit, status) VALUES "
                "('x', '2026-10-01T06:00:00+00:00', '2026-10-01T06:00:00+00:00', '2026-10-01T07:00:00+00:00', 1, "
                "'delhi-grid', 28.6, 77.2, 'old', 'temperature_2m', 30.0, '°C', 'pending')")
    con.commit()
    con.close()
    df = M.query(tmp_path)
    assert len(df) == 1 and "status_note" in df.columns and df["status_note"].isna().all()
