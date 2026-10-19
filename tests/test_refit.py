"""Tests for src.refit. No network access: the HTTP session is faked."""

from datetime import date

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.linear_model import LinearRegression

from src import config, refit
from src import data_collection as dc
from src.features import TARGET, TEMP, feature_columns
from src.multistep import HORIZONS
from src.service import CloudzyForecaster

START = date(2024, 11, 1)
TODAY = date(2025, 3, 10)  # → data end 2025-02-28, held-out month 2025-02
HOLDOUT = pd.Timestamp("2025-02-01", tz="UTC")


def synthetic_payload(params):
    """A plausible hourly response for exactly the requested range and model."""
    idx = pd.date_range(params["start_date"], f"{params['end_date']} 23:00", freq="h")
    hours = np.arange(len(idx))
    rng = np.random.default_rng(len(idx) + (params["models"] == config.LIVE_MODEL))
    temp = 18 + 7 * np.sin(2 * np.pi * (idx.hour - 3) / 24) + rng.normal(0, 0.3, len(idx))
    if params["models"] == config.LIVE_MODEL:
        temp = temp + 0.5  # the live source is biased against the reanalysis
    units = {"time": "iso8601", **config.HOURLY_VARIABLES}
    return {
        "latitude": 28.6, "longitude": 77.2, "utc_offset_seconds": 0, "timezone": "GMT",
        "hourly_units": units,
        "hourly": {
            "time": idx.strftime("%Y-%m-%dT%H:%M").tolist(),
            "temperature_2m": np.round(temp, 1).tolist(),
            "relative_humidity_2m": (50 + 20 * np.cos(hours / 12)).round(0).tolist(),
            "surface_pressure": (990 + np.sin(hours / 50)).round(1).tolist(),
            "wind_speed_10m": (5 + 3 * np.abs(np.sin(hours / 7))).round(1).tolist(),
            "precipitation": [0.0] * len(idx),
        },
    }


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    requests: list[dict] = []

    def get(self, url, params=None, timeout=None):
        FakeSession.requests.append({"url": url, **params})
        return FakeResponse(synthetic_payload(params))


class RecordingRegressor(BaseEstimator, RegressorMixin):
    """Linear model that records the issue times it was fitted on."""
    seen: list[pd.DatetimeIndex] = []

    def fit(self, X, y):
        RecordingRegressor.seen.append(X.index)
        self.model_ = LinearRegression().fit(X, y)
        return self

    def predict(self, X):
        return self.model_.predict(X)


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    FakeSession.requests = []
    RecordingRegressor.seen = []
    monkeypatch.setattr(dc.requests, "Session", FakeSession)


def fake_bundle(path, raw_dir):
    """A minimal serving bundle shaped like multistep.run()'s, fitted before the held-out month."""
    ref, _ = refit.load_source(config.REANALYSIS, START, date(2025, 1, 31), raw_dir)
    live, _ = refit.load_source(config.LIVE_ARCHIVE, START, date(2025, 1, 31), raw_dir)
    cols = feature_columns()
    horizons = {}
    for h in HORIZONS:
        table = refit.serving_table(ref, live, h)
        horizons[h] = {"model": RecordingRegressor().fit(table[cols], table[TARGET]),
                       "params": {"residual": True, "max_depth": None, "min_samples_leaf": 5},
                       "validation_mae_c": 1.0,
                       "deployed_model_trained_on": [table.index.min().isoformat(),
                                                     table.index.max().isoformat(), len(table)]}
    bundle = {"kind": "direct_multi_horizon", "inputs_source": config.LIVE_MODEL,
              "target_source": config.REANALYSIS.model, "feature_columns": cols,
              "feature_config": {"horizon_hours": 1, "temp_lags": [1, 2, 3, 6, 12, 24],
                                 "rolling_windows": [3, 6, 24],
                                 "current_vars": ["relative_humidity_2m", "surface_pressure",
                                                  "wind_speed_10m", "precipitation"],
                                 "local_timezone": "Asia/Kolkata"},
              "horizons": horizons, "test_2025_mae_c": {h: 1.0 for h in HORIZONS},
              "test_2025_provider_optimistic_mae_c": {h: 1.4 for h in HORIZONS}}
    joblib.dump(bundle, path)
    RecordingRegressor.seen = []
    return bundle


def run_refit(tmp_path, **kw):
    bundle_path = tmp_path / "serving.joblib"
    if not bundle_path.exists():
        fake_bundle(bundle_path, tmp_path / "raw")
    report = refit.run(today=TODAY, start=START, raw_dir=tmp_path / "raw", bundle_path=bundle_path,
                       report_path=tmp_path / "report.json", allow_worse=True, **kw)
    return report, bundle_path


# --- End date -------------------------------------------------------------------

@pytest.mark.parametrize("today, expected", [
    (date(2026, 10, 19), date(2026, 9, 30)),   # reanalysis complete to 10-13 → last full month September
    (date(2026, 10, 7), date(2026, 9, 30)),    # complete to 10-01
    (date(2026, 10, 6), date(2026, 9, 30)),    # complete exactly to 09-30, itself a month end
    (date(2026, 10, 5), date(2026, 8, 31)),    # complete only to 09-29
    (date(2026, 1, 3), date(2025, 11, 30)),    # crosses a year boundary
    (date(2024, 3, 6), date(2024, 2, 29)),     # leap year
])
def test_refit_end_date_is_last_complete_month(today, expected):
    assert refit.refit_end_date(today) == expected


def test_end_date_uses_the_slowest_source():
    slow = config.DataSource("x", "u", lag_days=20, kind="reanalysis")
    assert refit.refit_end_date(date(2026, 10, 19), (config.LIVE_ARCHIVE, slow)) == date(2026, 8, 31)
    assert refit.refit_end_date(date(2026, 10, 19), (config.LIVE_ARCHIVE,)) == date(2026, 9, 30)


def test_current_year_cache_is_refreshed_when_end_date_moves(tmp_path):
    first = dc.collect(start=date(2025, 1, 1), end=date(2025, 1, 31), raw_dir=tmp_path)
    again = dc.collect(start=date(2025, 1, 1), end=date(2025, 1, 31), raw_dir=tmp_path)
    assert first == again and len(FakeSession.requests) == 1  # same end date: cache hit
    later = dc.collect(start=date(2025, 1, 1), end=date(2025, 2, 28), raw_dir=tmp_path)
    assert later[0] != first[0] and "2025-02-28" in later[0].name
    assert len(FakeSession.requests) == 2


# --- Hold-out month ---------------------------------------------------------------

def test_split_keeps_issue_and_target_times_before_holdout():
    idx = pd.date_range("2025-01-01", "2025-02-28 23:00", freq="h", tz="UTC", name="time")
    table = pd.DataFrame({TEMP: 1.0, TARGET: 1.0}, index=idx)
    for h in HORIZONS:
        refit_rows, holdout_rows = refit.split_refit_holdout(table, date(2025, 1, 1), date(2025, 2, 1), h)
        assert (refit_rows.index + pd.Timedelta(hours=h) < HOLDOUT).all()
        assert refit_rows.index.max() == HOLDOUT - pd.Timedelta(hours=h + 1)
        assert (holdout_rows.index >= HOLDOUT).all() and holdout_rows.index.min() == HOLDOUT


def test_holdout_month_never_reaches_the_refitted_models(tmp_path):
    report, bundle_path = run_refit(tmp_path)
    assert report["data_end_date"] == "2025-02-28" and report["holdout_month"] == "2025-02"
    assert len(RecordingRegressor.seen) == len(HORIZONS)
    for h, seen in zip(HORIZONS, RecordingRegressor.seen):
        assert (seen + pd.Timedelta(hours=h) < HOLDOUT).all()
    bundle = joblib.load(bundle_path)
    for h in HORIZONS:
        last = pd.Timestamp(bundle["horizons"][h]["deployed_model_trained_on"][1])
        assert last + pd.Timedelta(hours=h) < HOLDOUT
        assert report["holdout_mae_c"][str(h)]["holdout_rows"] > 0


# --- Bundle --------------------------------------------------------------------------

def test_bundle_keys_and_provenance(tmp_path):
    _, bundle_path = run_refit(tmp_path)
    bundle = joblib.load(bundle_path)
    assert {"kind", "inputs_source", "target_source", "feature_columns", "feature_config", "horizons",
            "test_2025_mae_c", "holdout_mae_c", "provenance"} <= set(bundle)
    assert sorted(bundle["horizons"]) == list(HORIZONS)
    for h in HORIZONS:
        entry = bundle["horizons"][h]
        assert entry["params"] == {"residual": True, "max_depth": None, "min_samples_leaf": 5}
        assert entry["holdout_mae_c"] == bundle["holdout_mae_c"][h]
    p = bundle["provenance"]
    assert p["data_end_date"] == "2025-02-28" and p["data_start_date"] == "2024-11-01"
    assert p["holdout_month"] == ["2025-02-01", "2025-02-28"]
    assert {"numpy", "pandas", "scikit-learn", "joblib"} <= set(p["packages"]) and p["python"]
    assert p["sources"][config.REANALYSIS.model]["end_utc"].startswith("2025-02-28T23:00")
    assert p["sources"][config.LIVE_MODEL]["rows"] == p["sources"][config.REANALYSIS.model]["rows"]
    assert all(r["refit"] > 0 and r["holdout"] > 0 for r in p["rows_per_horizon"].values())
    # The service still loads it.
    assert CloudzyForecaster(bundle_path).horizons == list(HORIZONS)


def test_previous_bundle_is_kept_as_dated_backup(tmp_path):
    bundle_path = tmp_path / "serving.joblib"
    original = fake_bundle(bundle_path, tmp_path / "raw")
    report, _ = run_refit(tmp_path)
    backup = tmp_path / "serving.backup-2025-03-10.joblib"
    assert report["backup"] == backup.name and backup.exists()
    assert joblib.load(backup)["horizons"][1]["deployed_model_trained_on"] == \
        original["horizons"][1]["deployed_model_trained_on"]
    assert "provenance" in joblib.load(bundle_path)
    assert not list(tmp_path.glob("*.tmp"))

    report2, _ = run_refit(tmp_path)  # same day again: a second backup, nothing overwritten
    assert report2["backup"] == "serving.backup-2025-03-10-2.joblib"
    assert backup.exists()


def test_worse_models_do_not_replace_the_bundle(tmp_path, monkeypatch):
    bundle_path = tmp_path / "serving.joblib"
    fake_bundle(bundle_path, tmp_path / "raw")
    real = refit.refit_horizons

    def worse(*args):
        new, scores = real(*args)
        return new, {h: {**s, "new_mae_c": s["current_mae_c"] + 1} for h, s in scores.items()}

    monkeypatch.setattr(refit, "refit_horizons", worse)
    before = bundle_path.stat().st_mtime_ns
    report = refit.run(today=TODAY, start=START, raw_dir=tmp_path / "raw", bundle_path=bundle_path,
                       report_path=None)
    assert not report["replaced"] and report["backup"] is None
    assert bundle_path.stat().st_mtime_ns == before
    assert list(tmp_path.glob("serving.backup-*")) == []
