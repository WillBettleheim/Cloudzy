"""Tests for splits, models, metrics, and model saving / loading / prediction."""

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.base import clone

from src.evaluate import block_bootstrap_mae_diff, mae, metrics, rmse
from src.features import TARGET, TEMP, FeatureConfig, add_features, build_features, feature_columns
from src.models import DiurnalChangeBaseline, PersistenceModel, ResidualRegressor, candidates
from src.predict import load_bundle, predict, predict_next_hour
from src.splits import SplitSpec, chronological_split


def processed_like(start="2023-12-25", hours=24 * 30, seed=0):
    """Synthetic processed data with a daily cycle plus noise."""
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


@pytest.fixture(scope="module")
def table():
    t, _ = build_features(processed_like())
    return t


# --- Splits ------------------------------------------------------------------

SPECS = (
    SplitSpec("train", "2023-12-26", "2024-01-08"),
    SplitSpec("validation", "2024-01-08", "2024-01-15"),
    SplitSpec("test", "2024-01-15", "2024-01-24"),
)


def test_splits_are_ordered_disjoint_and_never_shuffled(table):
    s = chronological_split(table, SPECS)
    assert list(s) == ["train", "validation", "test"]
    for part in s.values():
        assert part.index.is_monotonic_increasing
    assert s["train"].index.max() < s["validation"].index.min()
    assert s["validation"].index.max() < s["test"].index.min()
    assert not set(s["train"].index) & set(s["test"].index)


def test_split_purges_rows_whose_target_crosses_the_boundary(table):
    s = chronological_split(table, SPECS, horizon_hours=1)
    boundary = pd.Timestamp("2024-01-08", tz="UTC")
    # The last train row's target time (t + 1 h) must be before validation starts.
    assert s["train"].index.max() + pd.Timedelta("1h") < boundary
    assert s["train"].index.max() == boundary - pd.Timedelta("2h")
    assert s["validation"].index.min() == boundary


def test_overlapping_or_empty_splits_are_rejected(table):
    with pytest.raises(ValueError, match="overlaps"):
        chronological_split(table, (SplitSpec("a", "2024-01-01", "2024-01-10"),
                                    SplitSpec("b", "2024-01-05", "2024-01-20")))
    with pytest.raises(ValueError, match="empty"):
        chronological_split(table, (SplitSpec("a", "2030-01-01", "2030-02-01"),))


def test_unsorted_table_is_rejected(table):
    with pytest.raises(ValueError, match="sorted"):
        chronological_split(table.iloc[::-1], SPECS)


# --- Models ------------------------------------------------------------------

def test_persistence_predicts_current_temperature(table):
    X = table[feature_columns()]
    np.testing.assert_array_equal(PersistenceModel().fit(X).predict(X), X[TEMP].to_numpy())


def test_residual_regressor_adds_current_temperature_back(table):
    X, y = table[feature_columns()], table[TARGET]

    class Zero:  # predicts zero change -> must reduce to persistence
        def fit(self, X, y): return self
        def predict(self, X): return np.zeros(len(X))
        def get_params(self, deep=True): return {}

    model = ResidualRegressor(Zero()).fit(X, y)
    np.testing.assert_allclose(model.predict(X), X[TEMP].to_numpy())


def test_diurnal_baseline_learns_hourly_change_from_training_only(table):
    X, y = table[feature_columns()], table[TARGET]
    model = DiurnalChangeBaseline().fit(X, y)
    pred = model.predict(X)
    assert pred.shape == (len(X),) and np.isfinite(pred).all()
    # Synthetic data has a strong daily cycle, so this must beat persistence.
    assert mae(y, pred) < mae(y, X[TEMP])


def test_every_candidate_fits_and_predicts_finite_values(table):
    X, y = table[feature_columns()], table[TARGET]
    for family, configs in candidates().items():
        params, est = configs[0]
        pred = clone(est).fit(X, y).predict(X)
        assert pred.shape == (len(X),), family
        assert np.isfinite(pred).all(), family


def test_tree_models_are_reproducible_with_fixed_seed(table):
    X, y = table[feature_columns()], table[TARGET]
    for family in ("random_forest", "gradient_boosting"):
        _, est = candidates()[family][0]
        a = clone(est).fit(X, y).predict(X)
        b = clone(est).fit(X, y).predict(X)
        # Same trees every time. Parallel averaging (n_jobs=-1) can sum them in a
        # different order, which changes results by ~1e-14 (float rounding), so
        # compare with a tolerance far below any meaningful temperature difference.
        np.testing.assert_allclose(a, b, rtol=0, atol=1e-12)


# --- Metrics -----------------------------------------------------------------

def test_metrics_hand_computed():
    y, p = np.array([1.0, 2.0, 3.0, 4.0]), np.array([1.0, 3.0, 1.0, 4.0])
    assert mae(y, p) == pytest.approx(0.75)
    assert rmse(y, p) == pytest.approx(np.sqrt(5 / 4))
    m = metrics(y, p)
    assert m["n"] == 4 and m["bias_c"] == pytest.approx(-0.25) and m["abs_error_max_c"] == 2.0


def test_metrics_reject_non_finite():
    with pytest.raises(ValueError):
        metrics([1.0, 2.0], [1.0, np.nan])


def test_block_bootstrap_detects_clear_winner_and_tie():
    idx = pd.date_range("2025-01-01", periods=24 * 70, freq="h", tz="UTC")
    rng = np.random.default_rng(1)
    y = rng.normal(20, 5, len(idx))
    good, bad = y + rng.normal(0, 0.2, len(idx)), y + rng.normal(0, 1.0, len(idx))
    win = block_bootstrap_mae_diff(idx, y, good, bad)
    assert win["ci95_high_c"] < 0
    tie = block_bootstrap_mae_diff(idx, y, good, good)
    assert tie["mae_diff_c"] == 0 and tie["ci95_low_c"] == 0 and tie["ci95_high_c"] == 0


# --- Saving, loading and inference --------------------------------------------

@pytest.fixture
def bundle_path(tmp_path, table):
    X, y = table[feature_columns()], table[TARGET]
    _, est = candidates()["gradient_boosting"][0]
    bundle = {
        "model": clone(est).fit(X, y),
        "model_family": "gradient_boosting",
        "feature_columns": feature_columns(),
        "feature_config": FeatureConfig().to_dict(),
        "target": TARGET,
    }
    path = tmp_path / "model.joblib"
    joblib.dump(bundle, path)
    return path, bundle


def test_saved_model_reloads_and_predicts_identically(bundle_path, table):
    path, original = bundle_path
    loaded = load_bundle(path)
    X = table[feature_columns()]
    np.testing.assert_array_equal(predict(loaded, X), original["model"].predict(X))
    assert loaded["feature_columns"] == feature_columns()


def test_predict_enforces_column_order_and_rejects_bad_input(bundle_path, table):
    path, _ = bundle_path
    bundle = load_bundle(path)
    X = table[feature_columns()]
    shuffled = X[list(reversed(X.columns))]
    np.testing.assert_array_equal(predict(bundle, shuffled), predict(bundle, X))
    with pytest.raises(ValueError, match="Missing feature columns"):
        predict(bundle, X.drop(columns=[TEMP]))
    bad = X.copy()
    bad.iloc[0, 0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        predict(bundle, bad)


def test_bundle_missing_keys_is_rejected(tmp_path):
    path = tmp_path / "broken.joblib"
    joblib.dump({"model": PersistenceModel()}, path)
    with pytest.raises(ValueError, match="missing keys"):
        load_bundle(path)


def test_predict_next_hour_from_raw_history(bundle_path):
    path, _ = bundle_path
    bundle = load_bundle(path)
    history = processed_like(hours=48)
    out = predict_next_hour(bundle, history)
    assert out["issue_time_utc"] == history.index[-1].isoformat()
    assert pd.Timestamp(out["valid_time_utc"]) - history.index[-1] == pd.Timedelta("1h")
    assert np.isfinite(out["predicted_temperature_2m_c"])
    # Same answer as building features for the full table and taking the last row.
    expected = predict(bundle, add_features(history)[feature_columns()].iloc[[-1]])[0]
    assert out["predicted_temperature_2m_c"] == pytest.approx(expected)


def test_predict_next_hour_needs_enough_history(bundle_path):
    path, _ = bundle_path
    with pytest.raises(ValueError, match="Not enough complete history"):
        predict_next_hour(load_bundle(path), processed_like(hours=20))
