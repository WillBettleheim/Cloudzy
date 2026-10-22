"""Deployment readiness: the committed serving bundle and app entry point (offline)."""

import importlib
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src import compact, config
from src.features import feature_columns
from src.service import CloudzyForecaster
from tests.test_phase_b import frame

BUNDLE = config.MODELS_DIR / "cloudzy_core_delhi_serving_compact.joblib"
ROOT = Path(__file__).resolve().parent.parent


def test_compact_grid_is_residual_gradient_boosting_only():
    grid = compact.compact_grid()
    assert grid and all(p["residual"] for p, _ in grid)
    assert all(type(est.estimator).__name__ == "HistGradientBoostingRegressor" for _, est in grid)


def test_size_mb_measures_compressed_size():
    assert compact.size_mb(list(range(10))) < 0.01
    assert compact.size_mb(np.random.default_rng(0).random(200_000)) > 1.0


def test_committed_bundle_is_small_complete_and_loadable():
    assert BUNDLE.exists(), "run `python -m src.compact` and commit the bundle"
    assert BUNDLE.stat().st_size / 1e6 < compact.MAX_BUNDLE_MB
    f = CloudzyForecaster(BUNDLE)
    assert f.horizons == [1, 2, 3, 6, 12, 24]
    lag = list((f.bundle.get("lagged_forecast") or {}).get("features", []))
    assert f.bundle["feature_columns"] == feature_columns() + lag
    assert set(f.typical_error) == set(f.horizons) and f.typical_error_period


def test_committed_bundle_predicts_from_history():
    f = CloudzyForecaster(BUNDLE)
    history = frame(start="2026-10-10", hours=48)
    lagged = None
    if f.uses_lagged_forecast:          # a lagged forecast close to the inputs, as live
        times = pd.date_range(history.index[-1], periods=30, freq="h")
        lagged = pd.Series(history["temperature_2m"].iloc[-1] + 0.5, index=times)
    out = f.predict(history, history.index[-1], lagged)
    values = [p["temperature_2m_c"] for p in out]
    assert len(values) == 6 and np.isfinite(values).all()
    assert all(5 < v < 45 for v in values)  # synthetic history is ~14-26 °C


def test_service_defaults_to_compact_bundle_and_env_overrides(monkeypatch, tmp_path):
    import src.service as service
    monkeypatch.delenv("CLOUDZY_SERVING_BUNDLE", raising=False)
    assert importlib.reload(service).SERVING_BUNDLE.name == BUNDLE.name
    monkeypatch.setenv("CLOUDZY_SERVING_BUNDLE", str(tmp_path / "other.joblib"))
    assert importlib.reload(service).SERVING_BUNDLE == tmp_path / "other.joblib"
    monkeypatch.delenv("CLOUDZY_SERVING_BUNDLE")
    importlib.reload(service)


def test_bundle_keys_match_what_refit_expects():
    b = joblib.load(BUNDLE)
    for key in ("horizons", "feature_columns", "feature_config"):
        assert key in b
    assert "typical_error_c" in b or "test_2025_mae_c" in b
    for entry in b["horizons"].values():
        assert {"model", "params", "deployed_model_trained_on"} <= set(entry)


def test_requirements_pin_every_runtime_import():
    reqs = {line.split("==")[0].lower() for line in (ROOT / "requirements.txt").read_text().splitlines()
            if line and not line.startswith("#")}
    for pkg in ("pandas", "numpy", "scikit-learn", "requests", "joblib", "streamlit", "plotly", "matplotlib"):
        assert pkg in reqs, pkg


def test_app_entry_point_and_pages_exist():
    for p in ("streamlit_app.py", "app_pages/weather.py", "app_pages/lab.py", ".streamlit/config.toml"):
        assert (ROOT / p).exists(), p
    lab = ROOT / "reports" / "lab"
    for p in ("phase_a_test_2025.csv.gz", "serving_test_2025.csv.gz", "shift_bias_by_hour.csv", "manifest.json"):
        assert (lab / p).exists(), p
