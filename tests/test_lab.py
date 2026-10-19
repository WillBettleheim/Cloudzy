"""Tests for Phase E: Lab metrics, charts, data export and the Lab page."""

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import src.lab_data
from src import lab_ui
from src.lab_data import ReproductionError, check, estimator_for, load_predictions, shift_bias, write_predictions
from tests.test_app import fake_network  # noqa: F401  (fixture reused for the router test)

TZ = "Asia/Kolkata"


def preds(hours=24 * 40, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=hours, freq="h", tz="UTC", name="issue_time_utc")
    obs = 20 + 5 * np.sin(np.arange(hours) * 2 * np.pi / 24)
    return pd.DataFrame({
        "observed": obs,
        "random_forest": obs + rng.normal(0, 0.2, hours),
        "diurnal_baseline": obs + rng.normal(0, 0.5, hours),
        "persistence": obs + 1.0,
    }, index=idx)


# --- Metrics ---------------------------------------------------------------------

def test_summary_matches_hand_computation():
    df = preds()
    t = lab_ui.summary(df, ["persistence", "random_forest"]).set_index("model")
    assert t.loc["persistence", "MAE °C"] == pytest.approx(1.0)
    assert t.loc["persistence", "Bias °C"] == pytest.approx(1.0)
    assert t.loc["persistence", "RMSE °C"] == pytest.approx(1.0)
    assert list(t.index) == ["random_forest", "persistence"]  # sorted best first


def test_rolling_mae_needs_full_window_and_is_correct():
    r = lab_ui.rolling_mae(preds(), ["persistence"], TZ, days=7)
    assert r["persistence"].iloc[:6].isna().all()
    assert r["persistence"].dropna().round(9).eq(1.0).all()


def test_mae_by_local_hour_has_24_groups_and_month_groups():
    df = preds()
    assert len(lab_ui.mae_by(df, ["persistence"], TZ, "local_hour")) == 24
    assert set(lab_ui.mae_by(df, ["persistence"], TZ, "month").index) == {1, 2}


def test_worst_hours_sorted_by_absolute_error():
    df = preds()
    df.iloc[5, df.columns.get_loc("random_forest")] = df["observed"].iloc[5] - 9
    w = lab_ui.worst_hours(df, "random_forest", n=3)
    assert w["error"].iloc[0] == pytest.approx(-9)
    assert (w["error"].abs().diff().dropna() <= 0).all()


# --- Charts --------------------------------------------------------------------------

def test_colour_roles_are_fixed():
    c = lab_ui.lab_palette("light")
    fig = lab_ui.timeseries_chart(preds().iloc[:48], "diurnal_baseline", c, TZ)
    roles = {t.name: t.line.color for t in fig.data}
    assert roles["Observed (reanalysis)"] == c["observed"]
    assert roles["Random forest (Cloudzy)"] == c["cloudzy"]
    assert roles["Diurnal baseline"] == c["provider"]


def test_mae_bar_highlights_only_cloudzy():
    c = lab_ui.lab_palette("dark")
    fig = lab_ui.mae_bar_chart(lab_ui.summary(preds(), ["persistence", "random_forest", "diurnal_baseline"]), c)
    colors = dict(zip(fig.data[0].y, fig.data[0].marker.color))
    assert colors["Random forest (Cloudzy)"] == c["cloudzy"]
    assert {colors["Persistence"], colors["Diurnal baseline"]} == {c["muted"]}


def test_bias_chart_years_differ_by_line_style():
    bias = pd.DataFrame({"year": [2023] * 2 + [2024] * 2 + [2025] * 2, "local_hour": [0, 1] * 3,
                         "mean_ifs_minus_reanalysis_c": [0.1, 0.2, 0.0, 0.1, 1.0, 1.2]})
    c = lab_ui.lab_palette("light")
    fig = lab_ui.bias_chart(bias, c)
    assert [t.line.dash for t in fig.data] == ["dot", "dash", "solid"]
    assert {t.line.color for t in fig.data} == {c["year"]}


def test_horizon_chart_aggregates_per_horizon():
    idx = pd.date_range("2025-01-01", periods=4, freq="h", tz="UTC")
    serving = pd.DataFrame({"horizon_h": [1, 1, 24, 24], "observed": [10.0, 10, 10, 10],
                            "cloudzy": [11.0, 9, 12, 8], "provider_optimistic": [10.0, 10, 13, 7],
                            "persistence": [10.0, 10, 10, 10]}, index=idx)
    fig = lab_ui.horizon_chart(serving, lab_ui.lab_palette("light"))
    by_name = {t.name: list(t.y) for t in fig.data}
    assert by_name["Cloudzy (direct)"] == [1.0, 2.0]
    assert by_name["IFS value at t+h (optimistic)"] == [0.0, 3.0]
    assert "Persistence" not in by_name  # reported in text instead; it would squash the scale
    assert list(lab_ui.horizon_mae(serving)["persistence"]) == [0.0, 0.0]


# --- Data export -------------------------------------------------------------------

def test_estimator_lookup_and_unknown_params():
    est = estimator_for("random_forest", {"residual": True, "max_depth": 12, "min_samples_leaf": 5})
    assert est.estimator.max_depth == 12
    with pytest.raises(KeyError):
        estimator_for("random_forest", {"residual": True, "max_depth": 99, "min_samples_leaf": 5})


def test_reproduction_check_tolerance():
    assert check("x", 0.2424, 0.2423)["mae_c"] == 0.2424
    with pytest.raises(ReproductionError, match="does not reproduce"):
        check("x", 0.25, 0.2423)


def test_predictions_round_trip_with_rounding(tmp_path):
    df = preds(hours=10)
    df["horizon_h"] = 3
    write_predictions(df, tmp_path / "p.csv.gz")
    back = load_predictions(tmp_path / "p.csv.gz")
    assert back.index.equals(df.index)
    assert (back["horizon_h"] == 3).all()
    assert np.allclose(back["random_forest"], df["random_forest"], atol=0.005)


def test_shift_bias_by_year_and_local_hour():
    idx = pd.date_range("2024-12-31T00:00Z", periods=48, freq="h", tz="UTC")
    ref = pd.DataFrame({"temperature_2m": np.full(48, 20.0)}, index=idx)
    live = ref + 1.5
    out = shift_bias(ref, live)
    assert set(out["year"]) == {2024, 2025}
    assert (out["mean_ifs_minus_reanalysis_c"] == 1.5).all()
    assert out["hours"].sum() == 48


# --- Pages ------------------------------------------------------------------------

def test_lab_page_renders_from_committed_data():
    st.cache_data.clear()
    at = AppTest.from_file("../app_pages/lab.py", default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert [t.label for t in at.tabs] == ["Scoreboard", "Predictions vs observed", "Error trends",
                                         "Live setting", "Multi-step", "Limitations"]
    assert any("reproduce the experiment reports exactly" in c.value for c in at.caption)
    at.selectbox[0].set_value("persistence").run()
    assert not at.exception


def test_lab_page_explains_missing_data(monkeypatch, tmp_path):
    st.cache_data.clear()
    monkeypatch.setattr(src.lab_data, "LAB_DIR", tmp_path)
    at = AppTest.from_file("../app_pages/lab.py", default_timeout=30).run()
    assert not at.exception
    assert any("python -m src.lab_data" in e.value for e in at.error)
    st.cache_data.clear()


def test_router_opens_weather_page_by_default(fake_network):  # noqa: F811
    at = AppTest.from_file("../streamlit_app.py", default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) == 6
