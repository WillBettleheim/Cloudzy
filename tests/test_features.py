"""Tests for feature construction, target alignment and leakage prevention."""

import numpy as np
import pandas as pd
import pytest

from src.features import (
    TARGET, TEMP, FeatureConfig, add_features, build_features, check_hourly_grid, feature_columns,
)

CFG = FeatureConfig()
MAX_LOOKBACK = 24  # largest lag / window in CFG


def hourly_frame(hours=96, seed=0):
    """Synthetic processed data. Temperature is a ramp (value = hour number) so that
    alignment errors show up as exact integer offsets."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-03-01", periods=hours, freq="h", tz="UTC", name="time")
    return pd.DataFrame({
        TEMP: np.arange(hours, dtype=float),
        "relative_humidity_2m": rng.uniform(20, 90, hours),
        "surface_pressure": rng.uniform(980, 1000, hours),
        "wind_speed_10m": rng.uniform(0, 20, hours),
        "precipitation": rng.choice([0.0, 0.0, 0.0, 1.5], hours),
        "is_gap_fill": False,
    }, index=idx)


def test_target_is_next_hour_temperature():
    df = hourly_frame()
    full = add_features(df)
    # Ramp: temp(t) = t, so target(t) must equal t + 1.
    expected = df[TEMP].shift(-1)
    pd.testing.assert_series_equal(full[TARGET], expected, check_names=False)
    assert np.isnan(full[TARGET].iloc[-1])  # no future value for the final hour


def test_target_uses_configured_horizon():
    full = add_features(hourly_frame(), FeatureConfig(horizon_hours=3))
    assert full[TARGET].iloc[0] == 3.0


@pytest.mark.parametrize("k", CFG.temp_lags)
def test_lags_look_exactly_k_hours_back(k):
    full = add_features(hourly_frame())
    t = 30
    assert full[f"temp_lag_{k}h"].iloc[t] == t - k


def test_rolling_windows_end_at_issue_time():
    full = add_features(hourly_frame())
    t = 40
    # Mean of ramp values t-2..t is t-1; a window leaking t+1 would give t.
    assert full["temp_roll_mean_3h"].iloc[t] == pytest.approx(t - 1)
    assert full["temp_roll_max_24h"].iloc[t] == t
    assert full["temp_roll_min_24h"].iloc[t] == t - 23
    assert full["temp_change_1h"].iloc[t] == 1.0


def test_features_at_t_do_not_depend_on_future_values():
    """Leakage test: scramble everything after t and recompute. No feature at
    or before t may change. Only the target is allowed to look ahead."""
    df = hourly_frame(hours=120)
    base = add_features(df)
    t = 60
    tampered = df.copy()
    rng = np.random.default_rng(42)
    for col in feature_source_columns():
        tampered.iloc[t + 1:, tampered.columns.get_loc(col)] = rng.uniform(-50, 50, len(df) - t - 1)
    after = add_features(tampered)

    cols = feature_columns()
    pd.testing.assert_frame_equal(base[cols].iloc[: t + 1], after[cols].iloc[: t + 1])
    assert base[TARGET].iloc[t] != after[TARGET].iloc[t]  # the sanity check that tampering worked


def feature_source_columns():
    return [TEMP, *CFG.current_vars]


def test_target_is_not_a_feature():
    assert TARGET not in feature_columns()
    full = add_features(hourly_frame())
    for col in feature_columns():
        # A feature identical to the target would mean the future leaked in.
        assert not full[col].equals(full[TARGET]), col


def test_build_drops_warmup_and_last_row_only():
    df = hourly_frame(hours=100)
    table, report = build_features(df)
    # First 24 hours lack a 24 h lag; last hour lacks a target.
    assert report.rows_output == 100 - MAX_LOOKBACK - 1
    assert report.rows_dropped_incomplete == MAX_LOOKBACK + 1
    assert table.index[0] == df.index[MAX_LOOKBACK]
    assert table.index[-1] == df.index[-2]
    assert list(table.columns) == feature_columns() + [TARGET]
    assert table.notna().all().all()


def test_gap_removes_every_row_that_touches_it():
    df = hourly_frame(hours=120)
    gap = 70
    df.iloc[gap, df.columns.get_loc(TEMP)] = np.nan
    df.iloc[gap, df.columns.get_loc("is_gap_fill")] = True
    table, _ = build_features(df)
    issue_hours = set(((table.index - df.index[0]) / pd.Timedelta("1h")).astype(int))
    # The row before the gap has a NaN target. Rows gap..gap+24 have the gap in a lag or window.
    affected = {gap - 1} | set(range(gap, gap + MAX_LOOKBACK + 1))
    assert issue_hours.isdisjoint(affected)
    assert gap + MAX_LOOKBACK + 1 in issue_hours  # the next clean row is kept


def test_cyclical_time_features_use_local_time_and_are_bounded():
    full = add_features(hourly_frame())
    # 2024-03-01 00:00 UTC is 05:30 IST.
    expected_hour = 5.5
    assert full["hour_sin"].iloc[0] == pytest.approx(np.sin(2 * np.pi * expected_hour / 24))
    for col in ["hour_sin", "hour_cos", "doy_sin", "doy_cos"]:
        assert full[col].between(-1, 1).all()
    radius = full["hour_sin"] ** 2 + full["hour_cos"] ** 2
    assert np.allclose(radius, 1.0)


@pytest.mark.parametrize("index, message", [
    (pd.date_range("2024-01-01", periods=5, freq="h"), "time-zone-aware"),
    (pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC")[::-1], "sorted"),
    (pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC").delete(2), "complete hourly grid"),
])
def test_irregular_index_is_rejected(index, message):
    with pytest.raises(ValueError, match=message):
        check_hourly_grid(index)


def test_feature_list_is_stable():
    """Changing the feature set must be a deliberate act: saved models depend on it."""
    cols = feature_columns()
    assert len(cols) == len(set(cols)) == 27
    assert cols[0] == TEMP
