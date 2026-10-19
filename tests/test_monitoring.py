"""Tests for live model monitoring (offline: the reanalysis API is faked)."""

from datetime import date

import pandas as pd
import pytest

import src.briefing as briefing
from src import monitoring as M
from src.provider import Place

DELHI = Place("New Delhi", 28.6358, 77.2244, "Asia/Kolkata", "India", None, None, "IN")


def result(issue="2026-10-01T06:00:00+00:00", values=(30.0, 31.0), ifs=(29.0, 33.0), horizons=(1, 24)):
    t0 = pd.Timestamp(issue)
    return {
        "issue_time_utc": issue,
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
    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        body = self.payload

        class R:
            status_code, text, headers = 200, "", {}

            def json(self):
                return body
        return R()


def test_log_and_dedupe(tmp_path):
    M.log_prediction(result(), tmp_path)
    M.log_prediction(result(values=(99.0, 99.0)), tmp_path)  # same issue time logged again (cache refresh)
    df = M.load_predictions(tmp_path)
    assert len(df) == 2 and list(df["cloudzy_c"]) == [30.0, 31.0]  # first log kept
    assert str(df["valid_time_utc"].dt.tz) == "UTC"


def test_matured_respects_reanalysis_lag(tmp_path):
    M.log_prediction(result(issue="2026-10-01T06:00:00+00:00"), tmp_path)
    M.log_prediction(result(issue="2026-10-15T06:00:00+00:00"), tmp_path)
    df = M.load_predictions(tmp_path)
    ready = M.matured(df, date(2026, 10, 19), lag_days=6)  # cutoff 2026-10-13 00:00 UTC
    assert set(ready["issue_time_utc"].dt.day) == {1}


def test_score_against_exact_valid_times(tmp_path):
    M.log_prediction(result(), tmp_path)
    df = M.load_predictions(tmp_path)
    truth = pd.Series([30.5, 32.0], index=pd.to_datetime(["2026-10-01T07:00Z", "2026-10-02T06:00Z"]))
    s = M.score(df, truth)
    by_h = {r["horizon_h"]: r for r in s["by_horizon"]}
    assert s["n"] == 2
    assert by_h[1]["cloudzy_mae_c"] == 0.5 and by_h[1]["ifs_mae_c"] == 1.5 and by_h[1]["expected_mae_c"] == 1.1
    assert by_h[24]["cloudzy_mae_c"] == 1.0 and by_h[24]["cloudzy_bias_c"] == -1.0
    assert {r["range"] for r in s["by_temperature_range"]} == {"30 °C and above"}


def test_score_skips_times_without_truth(tmp_path):
    M.log_prediction(result(), tmp_path)
    s = M.score(M.load_predictions(tmp_path), pd.Series(dtype=float))
    assert s == {"n": 0}


def test_evaluate_end_to_end_with_refusals(tmp_path):
    M.log_prediction(result(), tmp_path)
    M.log_failure("ServiceError: Need 25 complete hours", tmp_path)
    session = Session(archive_payload())
    s = M.evaluate(date(2026, 10, 19), tmp_path, session=session)
    assert session.calls == 1
    assert s["logged_predictions"] == 2 and s["refusals"] == 1 and s["refusal_rate"] == 0.5
    assert s["matured_predictions"] == 2 and s["scores"]["n"] == 2
    assert M.load_summary(tmp_path)["scores"]["n"] == 2


def test_evaluate_with_nothing_matured_makes_no_api_call(tmp_path):
    M.log_prediction(result(issue="2026-10-18T06:00:00+00:00"), tmp_path)
    session = Session(archive_payload())
    s = M.evaluate(date(2026, 10, 19), tmp_path, session=session)
    assert session.calls == 0 and s["scores"] == {"n": 0}


def test_reanalysis_must_be_utc():
    p = archive_payload()
    p["utc_offset_seconds"] = 19800
    with pytest.raises(ValueError, match="UTC"):
        M.fetch_reanalysis(date(2026, 10, 1), date(2026, 10, 2), session=Session(p))


def test_malformed_log_lines_are_skipped(tmp_path):
    M.log_prediction(result(), tmp_path)
    with (tmp_path / M.PREDICTIONS).open("a") as fh:
        fh.write("{broken\n")
    assert len(M.load_predictions(tmp_path)) == 2


def test_cloudzy_block_logs_predictions_and_refusals(monkeypatch, tmp_path):
    import src.service
    monkeypatch.setenv("CLOUDZY_MONITORING_DIR", str(tmp_path))
    monkeypatch.setattr(src.service, "forecast_now", lambda snapshot=True, session=None: result())
    assert briefing.cloudzy_block(DELHI)["available"]

    def boom(snapshot=True, session=None):
        raise src.service.ServiceError("Need 25 complete hours")
    monkeypatch.setattr(src.service, "forecast_now", boom)
    assert not briefing.cloudzy_block(DELHI)["available"]
    assert len(M.load_predictions(tmp_path)) == 2
    assert "Need 25 complete hours" in M.read_log(tmp_path / M.FAILURES)["reason"].iloc[0]


def test_monitoring_problems_never_break_a_prediction(monkeypatch):
    import src.service
    monkeypatch.setattr(src.service, "forecast_now", lambda snapshot=True, session=None: result())

    def broken(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(M, "log_prediction", broken)
    assert briefing.cloudzy_block(DELHI)["available"]


def test_monitoring_can_be_disabled(monkeypatch, tmp_path):
    import src.service
    monkeypatch.setenv("CLOUDZY_MONITORING", "0")
    monkeypatch.setenv("CLOUDZY_MONITORING_DIR", str(tmp_path))
    monkeypatch.setattr(src.service, "forecast_now", lambda snapshot=True, session=None: result())
    briefing.cloudzy_block(DELHI)
    assert not (tmp_path / M.PREDICTIONS).exists()
