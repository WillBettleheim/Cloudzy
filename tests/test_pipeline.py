"""Tests for the configurable training pipeline, model selection and artifact handling."""

from datetime import date

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.linear_model import LinearRegression

from src import artifacts as A
from src import config
from src import train as T
from src.features import TARGET, TEMP, FeatureConfig, build_features, feature_columns
from src.models import DiurnalChangeBaseline, PersistenceModel, ResidualRegressor
from src.provider import Place
from src.splits import SplitSpec, proportional_splits


def processed(start="2022-01-01", days=730, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=24 * days, freq="h", tz="UTC", name="time")
    hour = idx.hour.to_numpy()
    doy = idx.dayofyear.to_numpy()
    temp = 15 + 8 * np.sin(2 * np.pi * (doy - 100) / 365) + 6 * np.sin(2 * np.pi * (hour - 9) / 24) \
        + rng.normal(0, 0.3, len(idx))
    return pd.DataFrame({TEMP: temp, "relative_humidity_2m": rng.uniform(30, 90, len(idx)),
                         "surface_pressure": rng.uniform(990, 1020, len(idx)),
                         "wind_speed_10m": rng.uniform(0, 20, len(idx)),
                         "precipitation": rng.choice([0.0, 0.0, 0.0, 0.5], len(idx)), "is_gap_fill": False}, index=idx)


# --- Splits and checks -------------------------------------------------------------------

def test_proportional_splits_are_ordered_and_cover_the_period():
    s = proportional_splits(date(2022, 1, 1), date(2025, 12, 31))
    assert [x.name for x in s] == ["train", "validation", "test"]
    assert s[0].start == "2022-01-01" and s[-1].end == "2026-01-01"
    assert s[0].end == s[1].start and s[1].end == s[2].start
    days = [(pd.Timestamp(x.end) - pd.Timestamp(x.start)).days for x in s]
    assert days[0] / sum(days) == pytest.approx(0.70, abs=0.01)


@pytest.mark.parametrize("fractions", [(0.5, 0.5, 0.0), (0.7, 0.2, 0.2), (0.7, 0.3)])
def test_proportional_splits_reject_bad_fractions(fractions):
    with pytest.raises(ValueError):
        proportional_splits(date(2022, 1, 1), date(2023, 1, 1), fractions)


def test_split_rule_choice():
    assert T.splits_for(config.START_DATE, config.END_DATE) is T.DEFAULT_SPLITS
    assert T.splits_for(date(2022, 1, 1), date(2025, 12, 31))[1].name == "validation"


def test_too_little_data_fails_clearly():
    table, _ = build_features(processed(days=60))
    with pytest.raises(T.InsufficientDataError, match="Too little data"):
        T.select_and_evaluate(table, proportional_splits(date(2022, 1, 1), date(2022, 3, 1)), feature_columns())


def test_alignment_check_detects_misalignment():
    df = processed(days=40)
    table, _ = build_features(df)
    rows = T.alignment_check(table, df)
    assert all(r["target_c"] == r["raw_temperature_at_t_plus_1h_c"] for r in rows)
    broken = table.copy()
    broken[TARGET] = broken[TEMP]  # "target" = same hour: a leakage-style bug
    with pytest.raises(AssertionError, match="misaligned"):
        T.alignment_check(broken, df)


def test_global_plausibility_bounds_keep_real_extremes():
    lo, hi = config.PLAUSIBLE_RANGES["temperature_2m"]
    assert lo <= -20.9 and hi >= 50       # Reykjavik's winter, a desert summer
    assert config.PLAUSIBLE_RANGES["surface_pressure"][0] <= 650  # a high-altitude city


# --- Model selection uses validation, never test ----------------------------------------------

class PeriodBiased(BaseEstimator, RegressorMixin):
    """A linear regression that is shifted by 5 °C in every year except `good_year`."""

    def __init__(self, good_year=2023):
        self.good_year = good_year

    def fit(self, X, y):
        self.lr_ = LinearRegression().fit(X, y)
        return self

    def predict(self, X):
        base = self.lr_.predict(X)
        return np.where(X.index.year == self.good_year, base, base + 5)


def test_selection_follows_validation_even_when_test_disagrees(monkeypatch):
    df = processed(start="2022-01-01", days=3 * 365)
    table, _ = build_features(df)
    specs = (SplitSpec("train", "2022-01-01", "2023-01-01"), SplitSpec("validation", "2023-01-01", "2024-01-01"),
             SplitSpec("test", "2024-01-01", "2025-01-01"))
    monkeypatch.setattr(T, "candidates", lambda: {
        "good_on_validation": [({}, PeriodBiased(2023))], "good_on_test": [({}, PeriodBiased(2024))]})
    ev = T.select_and_evaluate(table, specs, feature_columns())
    assert ev["selected"]["family"] == "good_on_validation"
    assert ev["test_metrics"]["good_on_test"]["mae_c"] < ev["test_metrics"]["good_on_validation"]["mae_c"]
    # Every candidate is scored on exactly the same test rows.
    assert len({m["n"] for m in ev["test_metrics"].values()}) == 1


# --- End to end on synthetic data (fast grid) ---------------------------------------------------

@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for name in ("PROCESSED_DIR", "MODELS_DIR", "REPORTS_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
        (tmp_path / name.lower()).mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(T, "candidates", lambda: {
        "persistence": [({}, PersistenceModel())], "diurnal_baseline": [({}, DiurnalChangeBaseline())],
        "linear_regression": [({}, LinearRegression())],
        "random_forest": [({"residual": True}, ResidualRegressor(
            __import__("sklearn.ensemble", fromlist=["x"]).RandomForestRegressor(n_estimators=10, max_depth=6,
                                                                                random_state=0)))]})
    loc = config.Location("testville", "Testville", 10.0, 20.0, "UTC")
    start, end = date(2022, 1, 1), date(2023, 12, 31)
    from src.data_validation import processed_path, save_processed
    save_processed(processed(start="2022-01-01", days=730), processed_path(loc, config.REANALYSIS, start, end))
    return loc, start, end


def test_train_and_evaluate_end_to_end(sandbox):
    loc, start, end = sandbox
    report = T.run(loc, start, end)
    path = T.model_path(loc, config.REANALYSIS, start, end)
    assert path.exists() and A.sidecar_path(path).exists()
    meta = A.read_metadata(path)
    assert meta["artifact_sha256"] == A.file_sha256(path)
    assert [f["name"] for f in meta["features"]] == feature_columns()
    assert {f["unit"] for f in meta["features"]} >= {"°C", "%", "hPa", "km/h", "mm", "unitless"}
    assert report["split_rule"] == "70/15/15 by time" and report["target_alignment_check"]
    assert report["selected"]["reason"].startswith("lowest validation MAE")
    from src.pipeline import evaluate
    ev = evaluate(loc, start, end)
    assert ev["reloaded_test"]["matches_recorded"]
    assert np.isfinite(ev["example_prediction"]["predicted_temperature_2m_c"])


# --- Location resolution ---------------------------------------------------------------------

def test_resolve_location_default_is_delhi():
    from src.pipeline import resolve_location
    assert resolve_location() is config.DELHI


def test_resolve_location_by_city_and_country():
    from src.pipeline import resolve_location
    fake = lambda q, count=10: [Place("Paris", 48.85, 2.35, "Europe/Paris", "France", "IDF", 1, "FR"),  # noqa: E731
                                Place("Paris", 33.66, -95.56, "America/Chicago", "United States", "Texas", 1, "US")]
    loc = resolve_location("Paris", "US", search=fake)
    assert (loc.latitude, loc.local_timezone, loc.slug) == (33.66, "America/Chicago", "paris-us")


def test_resolve_location_by_coordinates_is_validated():
    from src.pipeline import resolve_location
    loc = resolve_location(lat=51.5, lon=-0.12, name="London", timezone="Europe/London")
    assert loc.slug == "london"
    with pytest.raises(ValueError):
        resolve_location(lat=95, lon=0, name="x", timezone="UTC")
    with pytest.raises(SystemExit, match="--timezone"):
        resolve_location(lat=51.5, lon=-0.12)


# --- Artifacts ----------------------------------------------------------------------------------

def bundle():
    X = build_features(processed(days=30))[0]
    return {"model": PersistenceModel().fit(X[feature_columns()]), "model_family": "persistence", "params": {},
            "feature_columns": feature_columns(), "feature_config": FeatureConfig().to_dict(), "target": TARGET,
            "test_metrics": {"mae_c": 1.0}}


KEYS = {"model", "feature_columns", "feature_config", "target", "model_family"}


def test_load_trusted_and_cache(tmp_path):
    path = tmp_path / "m.joblib"
    joblib.dump(bundle(), path)
    A.write_metadata(path, joblib.load(path))
    first = A.load_trusted(path, KEYS)
    assert A.load_trusted(path, KEYS) is first  # cached: no second read


def test_missing_corrupt_and_wrong_type_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        A.load_trusted(tmp_path / "none.joblib", KEYS)
    bad = tmp_path / "bad.joblib"
    bad.write_bytes(b"not a pickle at all")
    with pytest.raises(A.ArtifactError, match="corrupted"):
        A.load_trusted(bad, KEYS)
    lst = tmp_path / "list.joblib"
    joblib.dump([1, 2, 3], lst)
    with pytest.raises(A.ArtifactError, match="isn't a Cloudzy model"):
        A.load_trusted(lst, KEYS)


def test_missing_keys_and_schema_mismatch(tmp_path):
    b = bundle()
    del b["target"]
    p1 = tmp_path / "a.joblib"
    joblib.dump(b, p1)
    with pytest.raises(A.ArtifactError, match="missing keys"):
        A.load_trusted(p1, KEYS)
    b = bundle()
    b["feature_columns"] = list(reversed(b["feature_columns"]))
    p2 = tmp_path / "b.joblib"
    joblib.dump(b, p2)
    with pytest.raises(A.ArtifactError, match="different feature schema"):
        A.load_trusted(p2, KEYS)


def test_checksum_mismatch_is_rejected(tmp_path):
    path = tmp_path / "m.joblib"
    joblib.dump(bundle(), path)
    A.write_metadata(path, joblib.load(path))
    b = bundle()
    b["test_metrics"] = {"mae_c": 0.01}       # a different model written over the file
    joblib.dump(b, path)
    with pytest.raises(A.ArtifactError, match="doesn't match its metadata"):
        A.load_trusted(path, KEYS)


def test_unit_lookup():
    assert A.unit_of("temp_lag_24h") == "°C" and A.unit_of("pressure_change_3h") == "hPa"
    with pytest.raises(KeyError):
        A.unit_of("mystery_feature")


def test_committed_serving_model_has_matching_metadata():
    path = config.MODELS_DIR / "cloudzy_core_delhi_serving_compact.joblib"
    meta = A.read_metadata(path)
    assert meta is not None, "commit models/cloudzy_core_delhi_serving_compact.json"
    assert meta["artifact_sha256"] == A.file_sha256(path)
    assert meta["horizons_h"] == [1, 2, 3, 6, 12, 24] and "reanalysis" in meta["target"]


def test_app_reports_unusable_model_file_plainly(monkeypatch):
    import src.service
    from src.briefing import cloudzy_block

    def broken(snapshot=True, session=None):
        raise A.ArtifactError("Couldn't read cloudzy_core.joblib: the file may be corrupted")
    monkeypatch.setattr(src.service, "forecast_now", broken)
    b = cloudzy_block(Place("New Delhi", 28.6139, 77.209, "Asia/Kolkata"))
    assert not b["available"] and "model file can't be used" in b["reason"]
