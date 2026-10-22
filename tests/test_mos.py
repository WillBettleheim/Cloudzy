"""The lagged-ECMWF-forecast upgrade (src.mos): leakage-safe alignment, response checks, the pre-registered
decision rule, serving with the extra input (and refusing without it), and the refit path."""

import json
import logging

import joblib
import numpy as np
import pandas as pd
import pytest

from src import mos, refit, service
from src.features import TARGET, TEMP, FeatureConfig, feature_columns
from src.multistep import HORIZONS
from tests.test_phase_b import FakeSession, frame, live_payload


class LaggedEcho:
    """Predicts the lagged forecast itself, so tests can see which value reached the model."""

    def predict(self, X):
        return X["ifs_prev2_target"].to_numpy()


@pytest.fixture
def lagged_bundle(tmp_path):
    path = tmp_path / "lagged.joblib"
    joblib.dump({
        "horizons": {h: {"model": LaggedEcho()} for h in (1, 24)},
        "feature_columns": feature_columns() + mos.LAG_FEATURES,
        "feature_config": FeatureConfig().to_dict(),
        "lagged_forecast": mos.LAGGED_SPEC,
        "typical_error_c": {1: 0.93, 24: 1.07},
        "typical_error_period": "2026-01 to 2026-08 test",
    }, path)
    return path


def previous_runs_payload(times, values, units="°C", offset=0):
    return {"utc_offset_seconds": offset, "hourly_units": {"time": "iso8601", mos.PREV_VAR: units},
            "hourly": {"time": list(times), mos.PREV_VAR: list(values)}}


# --- Features and data -----------------------------------------------------------------------------------

def test_lag_features_take_the_forecast_for_the_target_hour_not_the_issue_hour():
    idx = pd.date_range("2025-01-01", periods=48, freq="h", tz="UTC")
    table = pd.DataFrame({TEMP: np.full(48, 10.0)}, index=idx)
    prev = pd.Series(np.arange(48, dtype=float), index=idx)            # value = hours since start
    for h in (1, 6, 24):
        out = mos.with_lag_features(table, prev, h)
        assert out["ifs_prev2_target"].iloc[0] == h                     # forecast valid at t + h
        assert out["ifs_prev2_minus_now"].iloc[0] == h - 10.0
        assert np.isnan(out["ifs_prev2_target"].iloc[-1])               # beyond the series: missing, not filled


def test_lagged_run_is_published_before_every_issue_time():
    # previous_day2 = the run from about 48 h before the valid hour; valid only while that is before t.
    assert mos.PREV_VAR.endswith("previous_day2") and max(HORIZONS) <= 24
    assert mos.LAGGED_SPEC["features"] == mos.LAG_FEATURES


def test_previous_runs_response_is_validated():
    times = ["2026-10-22T00:00", "2026-10-22T01:00"]
    s = mos.parse_previous_runs(previous_runs_payload(times, [20.0, None]))
    assert str(s.index.tz) == "UTC" and s.iloc[0] == 20.0 and np.isnan(s.iloc[1])
    with pytest.raises(ValueError, match="UTC"):
        mos.parse_previous_runs(previous_runs_payload(times, [20.0, 21.0], offset=19800))
    with pytest.raises(ValueError, match="°C"):
        mos.parse_previous_runs(previous_runs_payload(times, [68.0, 69.0], units="°F"))
    with pytest.raises(ValueError, match="length"):
        mos.parse_previous_runs(previous_runs_payload(times, [20.0]))
    with pytest.raises(ValueError, match="unusable"):
        mos.parse_previous_runs({"error": True, "reason": "Parameter models invalid"})


def test_fetch_live_lagged_requests_the_pinned_model_and_variable():
    session = FakeSession(previous_runs_payload(["2026-10-22T00:00"], [20.0]))
    mos.fetch_live_lagged(session=session)
    assert session.params["models"] == "ecmwf_ifs025" and session.params["hourly"] == mos.PREV_VAR
    assert session.params["timezone"] == "GMT" and session.params["past_days"] == 2


# --- Decision rule ----------------------------------------------------------------------------------------

def result(control, candidate, ci_high, sept_control=1.0, sept_candidate=1.0):
    return {"test": {"control": {"mae_c": control}, "candidate": {"mae_c": candidate},
                     "candidate_minus_control": {"ci95_high_c": ci_high}},
            "september": {"control": {"mae_c": sept_control}, "candidate": {"mae_c": sept_candidate}}}


def test_decision_rule_adopts_only_a_consistent_significant_gain():
    good = {h: result(1.0, 0.9, -0.01) for h in HORIZONS}
    assert mos.decide(good)["adopt"]
    one_worse = good | {24: result(1.0, 1.01, 0.02)}
    assert not mos.decide(one_worse)["adopt"]
    few_significant = {h: result(1.0, 0.99, -0.01 if i < 3 else 0.01) for i, h in enumerate(HORIZONS)}
    assert mos.decide(few_significant)["horizons_with_significant_gain"] == 3
    assert not mos.decide(few_significant)["adopt"]
    sept_regression = good | {6: result(1.0, 0.9, -0.01, sept_control=0.9, sept_candidate=0.96)}
    assert not mos.decide(sept_regression)["adopt"]


def test_committed_experiment_report_is_consistent_with_its_decision():
    rep = json.loads(mos.REPORT_PATH.read_text())
    assert rep["decision"] == mos.decide({int(h): r for h, r in rep["horizons"].items()})
    for r in rep["horizons"].values():
        t = r["test"]
        assert t["candidate"]["n"] == t["control"]["n"] > 5000                 # same rows for both
        d = t["candidate_minus_control"]
        assert d["ci95_low_c"] <= d["mae_diff_c"] <= d["ci95_high_c"]


# --- Serving ----------------------------------------------------------------------------------------------

def test_service_feeds_the_lagged_forecast_for_each_target_hour(lagged_bundle):
    f = service.CloudzyForecaster(lagged_bundle)
    history = frame(start="2026-10-10", hours=48)
    t = history.index[-1]
    lagged = pd.Series([30.0, 31.0], index=[t + pd.Timedelta(hours=1), t + pd.Timedelta(hours=24)])
    out = {p["horizon_h"]: p for p in f.predict(history, t, lagged)}
    assert out[1]["temperature_2m_c"] == 30.0 and out[24]["temperature_2m_c"] == 31.0
    assert out[24]["typical_error_c"] == 1.07 and f.typical_error_period == "2026-01 to 2026-08 test"


def test_service_refuses_without_the_lagged_forecast_instead_of_switching_models(lagged_bundle):
    f = service.CloudzyForecaster(lagged_bundle)
    history = frame(start="2026-10-10", hours=48)
    t = history.index[-1]
    with pytest.raises(service.ServiceError, match="lagged ECMWF forecast"):
        f.predict(history, t)
    with pytest.raises(service.ServiceError, match=r"\+24 h"):
        f.predict(history, t, pd.Series([30.0], index=[t + pd.Timedelta(hours=1)]))


def test_failed_lagged_fetch_is_logged_and_refused(monkeypatch, caplog):
    def boom(session=None):
        raise ConnectionError("previous-runs API down")
    monkeypatch.setattr(mos, "fetch_live_lagged", boom)
    with caplog.at_level(logging.WARNING), pytest.raises(service.ServiceError, match="unavailable"):
        service.fetch_lagged()
    assert "refused" in caplog.text


def test_lagged_bundle_end_to_end_with_live_payloads(lagged_bundle):
    df, _ = service.fetch_live(session=FakeSession(live_payload()), snapshot_dir=None)
    now = df.index[60]
    times = pd.date_range(now - pd.Timedelta(hours=48), periods=96, freq="h")
    lagged = mos.parse_previous_runs(previous_runs_payload(times.strftime("%Y-%m-%dT%H:%M"), [25.0] * 96))
    res = service.build_forecast(df, service.CloudzyForecaster(lagged_bundle), now, "now", lagged=lagged)
    assert [p["temperature_2m_c"] for p in res["cloudzy"]["predictions"]] == [25.0, 25.0]
    assert res["typical_error_period"] == "2026-01 to 2026-08 test"


def test_old_bundles_without_the_lagged_input_still_serve(tmp_path):
    f = service.CloudzyForecaster(_tiny(tmp_path))
    assert not f.uses_lagged_forecast and f.typical_error_period == "2025 test"
    history = frame(start="2026-10-10", hours=48)
    assert len(f.predict(history, history.index[-1])) == 3


def _tiny(tmp_path):
    from src.models import PersistenceModel
    path = tmp_path / "old.joblib"
    joblib.dump({"horizons": {h: {"model": PersistenceModel().fit(pd.DataFrame({TEMP: [1.0]}))} for h in (1, 3, 24)},
                 "feature_columns": feature_columns(), "feature_config": FeatureConfig().to_dict(),
                 "test_2025_mae_c": {1: 1.1, 3: 1.15, 24: 1.37}}, path)
    return path


# --- Refit and interval tables ------------------------------------------------------------------------------

def test_bundle_table_adds_lag_features_and_drops_rows_before_the_archive(monkeypatch):
    idx = pd.date_range("2024-02-03", periods=24 * 5, freq="h", tz="UTC")
    base = pd.DataFrame({TEMP: np.full(len(idx), 15.0), TARGET: np.full(len(idx), 16.0)}, index=idx)
    monkeypatch.setattr(refit, "serving_table", lambda ref, live, h: base)
    prev = pd.Series(20.0, index=idx[24:])                         # archive starts a day later
    spec = {"lagged_forecast": mos.LAGGED_SPEC}
    out = refit.bundle_table(None, None, 6, spec, prev)
    assert out.index.min() >= pd.Timestamp(mos.START, tz="UTC")
    assert out["ifs_prev2_target"].notna().all() and (out["ifs_prev2_minus_now"] == 5.0).all()
    with pytest.raises(refit.RefitError, match="previous-runs"):
        refit.bundle_table(None, None, 6, spec, None)
    assert refit.bundle_table(None, None, 6, {}, None) is base     # old bundles: unchanged


def test_refit_carries_the_lagged_spec_and_typical_error_forward():
    current = {"kind": "k", "inputs_source": "i", "target_source": "t", "feature_columns": [], "feature_config": {},
               "lagged_forecast": mos.LAGGED_SPEC, "typical_error_c": {1: 0.9},
               "typical_error_period": "2026-01 to 2026-08 test"}
    b = refit.build_bundle(current, {}, {}, {})
    assert b["lagged_forecast"] == mos.LAGGED_SPEC and b["typical_error_c"] == {1: 0.9}
    assert "test_2025_mae_c" not in b
