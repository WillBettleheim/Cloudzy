"""Tests for Phase B: vectorised features, multi-step strategies, shift helpers, service."""

import joblib
import numpy as np
import pandas as pd
import pytest

from src import config, service
from src.data_validation import DataValidationError
from src.features import TARGET, TEMP, FeatureConfig, add_features, build_features, feature_columns, features_from_windows
from src.models import PersistenceModel
from src.multistep import HISTORY, MAX_H, eval_origins, recursive_forecast
from src.shift import compare_sources, tables

VARS = list(config.HOURLY_VARIABLES)


def frame(start="2024-12-20", hours=24 * 20, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=hours, freq="h", tz="UTC", name="time")
    hour = idx.tz_convert("Asia/Kolkata").hour.to_numpy()
    return pd.DataFrame({
        TEMP: 20 + 6 * np.sin(2 * np.pi * (hour - 9) / 24) + rng.normal(0, 0.3, hours),
        "relative_humidity_2m": rng.uniform(20, 90, hours),
        "surface_pressure": rng.uniform(980, 1000, hours),
        "wind_speed_10m": rng.uniform(0, 20, hours),
        "precipitation": rng.choice([0.0, 0.0, 0.0, 1.5], hours),
        "is_gap_fill": False,
    }, index=idx)


def windows(df, pos):
    pos = np.asarray(pos)
    return (
        df[TEMP].to_numpy()[pos[:, None] + np.arange(-24, 1)],
        {**{v: df[v].to_numpy()[pos] for v in FeatureConfig().current_vars},
         "surface_pressure_window": df["surface_pressure"].to_numpy()[pos[:, None] + np.arange(-3, 1)],
         "precipitation_window": df["precipitation"].to_numpy()[pos[:, None] + np.arange(-2, 1)]},
    )


# --- Vectorised features ------------------------------------------------------

def test_vectorised_features_match_reference_implementation():
    df = frame()
    pos = np.arange(30, len(df), 7)
    temp_w, exog = windows(df, pos)
    fast = features_from_windows(temp_w, exog, df.index[pos])
    ref = add_features(df)[feature_columns()].iloc[pos]
    assert list(fast.columns) == feature_columns()
    # pandas' rolling std uses an online algorithm; differences are float rounding only.
    pd.testing.assert_frame_equal(fast, ref, check_exact=False, atol=1e-6, rtol=0, check_freq=False)


def test_vectorised_features_reject_short_window():
    with pytest.raises(ValueError, match="shape"):
        features_from_windows(np.zeros((2, 10)), {}, pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC"))


# --- Multi-step ----------------------------------------------------------------

def test_recursive_persistence_stays_flat():
    df = frame()
    origins = df.index[[40, 100, 200]]
    out = recursive_forecast(PersistenceModel(), df, origins, steps=6)
    assert out.shape == (3, 6)
    np.testing.assert_allclose(out, np.repeat(df.loc[origins, TEMP].to_numpy()[:, None], 6, axis=1))


def test_recursive_first_step_equals_one_hour_model():
    df = frame()
    table, _ = build_features(df)
    from sklearn.linear_model import LinearRegression
    model = LinearRegression().fit(table[feature_columns()], table[TARGET])
    origins = table.index[[10, 50, 90]]
    rec = recursive_forecast(model, df, origins, steps=3)
    direct = model.predict(table.loc[origins, feature_columns()])
    np.testing.assert_allclose(rec[:, 0], direct, atol=1e-6)


def test_recursive_uses_only_history_and_its_own_predictions():
    """Changing anything after the origin must not change a recursive forecast."""
    df = frame()
    origins = df.index[[60]]
    base = recursive_forecast(PersistenceModel(), df, origins, steps=5)
    tampered = df.copy()
    tampered.iloc[61:, :5] = 999.0
    np.testing.assert_array_equal(base, recursive_forecast(PersistenceModel(), tampered, origins, steps=5))


def test_recursive_rejects_nan_history():
    df = frame()
    df.iloc[50, 0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        recursive_forecast(PersistenceModel(), df, df.index[[60]], steps=2)


def test_eval_origins_leave_room_for_longest_horizon():
    # Data ends mid-year: the last origin must still have its full 24 h future.
    df = frame(start="2024-12-01", hours=24 * 70)
    origins = eval_origins(df, year=2025)
    assert origins.min() == pd.Timestamp("2025-01-01", tz="UTC")
    assert origins.max() == df.index.max() - pd.Timedelta(hours=MAX_H)
    # Data runs past the year end: origins stop so that t + 24 h stays inside the year.
    df = frame(start="2024-12-01", hours=24 * 400)
    origins = eval_origins(df, year=2025)
    assert origins.max() == pd.Timestamp("2025-12-31T23:00Z") - pd.Timedelta(hours=MAX_H)


def test_eval_origins_need_history():
    df = frame(start="2025-01-01", hours=24 * 5)
    with pytest.raises(ValueError, match="history"):
        eval_origins(df, year=2025)


# --- Shift helpers -------------------------------------------------------------

def test_cross_source_table_uses_live_features_and_reference_target():
    ref, live = frame(seed=1), frame(seed=2)
    tabs = tables(ref, live, FeatureConfig())
    cross = tabs["live_ref"]
    t = cross.index[5]
    assert cross.loc[t, TEMP] == live.loc[t, TEMP]  # features from live
    assert cross.loc[t, TARGET] == ref.loc[t + pd.Timedelta("1h"), TEMP]  # target from reference
    assert "provider_next_hour_optimistic" not in feature_columns()


def test_compare_sources_reports_bias_sign():
    ref = frame()
    live = ref.copy()
    live[TEMP] += 1.5
    out = compare_sources(ref, live)
    assert out[TEMP]["all"]["bias"] == pytest.approx(1.5)
    assert out["relative_humidity_2m"]["all"]["mae"] == 0


# --- Service -------------------------------------------------------------------

NOW = pd.Timestamp("2026-10-19T06:28:00Z")


def live_payload(null_var=None):
    idx = pd.date_range("2026-10-17", periods=96, freq="h", tz="UTC")
    df = frame(start="2026-10-17", hours=96)
    hourly = {"time": idx.strftime("%Y-%m-%dT%H:%M").tolist()}
    for v in VARS:
        hourly[v] = [None] * 96 if v == null_var else df[v].round(2).tolist()
    return {"latitude": 28.58, "longitude": 77.19, "utc_offset_seconds": 0, "timezone": "GMT",
            "hourly_units": {"time": "iso8601", **config.HOURLY_VARIABLES}, "hourly": hourly}


class FakeSession:
    def __init__(self, payload):
        self.payload, self.params = payload, None

    def get(self, url, params=None, timeout=None):
        self.params = params

        class R:
            status_code = 200
            text = ""
            json = staticmethod(lambda: self.payload)
        return R()


@pytest.fixture
def tiny_bundle(tmp_path):
    X = pd.DataFrame({TEMP: [1.0]})
    path = tmp_path / "serving.joblib"
    joblib.dump({
        "horizons": {h: {"model": PersistenceModel().fit(X)} for h in (1, 3, 24)},
        "feature_columns": feature_columns(),
        "feature_config": FeatureConfig().to_dict(),
        "test_2025_mae_c": {1: 1.1, 3: 1.15, 24: 1.37},
    }, path)
    return path


def test_fetch_live_requests_pinned_model_validates_and_snapshots(tmp_path):
    session = FakeSession(live_payload())
    df, retrieved = service.fetch_live(session=session, snapshot_dir=tmp_path)
    assert session.params["models"] == "ecmwf_ifs" and session.params["timezone"] == "GMT"
    assert len(df) == 96 and str(df.index.tz) == "UTC"
    assert len(list(tmp_path.glob("delhi_ecmwf_ifs_*.json"))) == 1


def test_fetch_live_rejects_variable_with_no_values(tmp_path):
    with pytest.raises(DataValidationError, match="no values"):
        service.fetch_live(session=FakeSession(live_payload(null_var="wind_speed_10m")), snapshot_dir=None)


def test_forecast_separates_cloudzy_and_provider_and_aligns_times(tiny_bundle):
    df, _ = service.fetch_live(session=FakeSession(live_payload()), snapshot_dir=None)
    result = service.build_forecast(df, service.CloudzyForecaster(tiny_bundle), NOW, "now")
    issue = pd.Timestamp("2026-10-19T06:00Z")
    assert result["issue_time_utc"] == issue.isoformat()
    assert result["cloudzy"]["label"] != result["provider"]["label"]
    preds, prov = result["cloudzy"]["predictions"], result["provider"]["forecasts"]
    assert [p["horizon_h"] for p in preds] == [1, 3, 24]
    for p, q in zip(preds, prov):
        valid = issue + pd.Timedelta(hours=p["horizon_h"])
        assert p["valid_time_utc"] == q["valid_time_utc"] == valid.isoformat()
        assert q["temperature_2m_c"] == pytest.approx(df.loc[valid, TEMP])
        # Persistence stand-in: Cloudzy value equals the input at issue time, proving
        # the service used inputs up to t, not the provider's future values.
        assert p["temperature_2m_c"] == pytest.approx(round(df.loc[issue, TEMP], 2))
    assert result["limitations"]


def test_forecast_needs_issue_hour_and_enough_history(tiny_bundle):
    df, _ = service.fetch_live(session=FakeSession(live_payload()), snapshot_dir=None)
    f = service.CloudzyForecaster(tiny_bundle)
    with pytest.raises(service.ServiceError, match="issue hour"):
        service.build_forecast(df, f, pd.Timestamp("2030-01-01T00:00Z"), "now")
    with pytest.raises(service.ServiceError, match="complete hours"):
        f.predict(df, df.index[10])
    gappy = df.copy()
    gappy.loc[pd.Timestamp("2026-10-19T03:00Z"), TEMP] = np.nan
    with pytest.raises(service.ServiceError, match="Incomplete"):
        f.predict(gappy, pd.Timestamp("2026-10-19T06:00Z"))


def test_forecaster_rejects_missing_or_incomplete_bundle(tmp_path):
    with pytest.raises(FileNotFoundError):
        service.CloudzyForecaster(tmp_path / "nope.joblib")
    bad = tmp_path / "bad.joblib"
    joblib.dump({"horizons": {}}, bad)
    with pytest.raises(ValueError, match="missing"):
        service.CloudzyForecaster(bad)
