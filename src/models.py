"""Model definitions and hyperparameter grids for Cloudzy Core.

All models follow the scikit-learn estimator interface and take a DataFrame
whose columns are features.feature_columns().

Residual learning: tree models can optionally learn the *change*
y - temperature_2m(t) instead of the absolute next-hour temperature. Persistence
already explains most of the signal, so the remaining change is a smaller,
better-behaved target. This is a learnable choice, chosen on validation data
like any other hyperparameter. It uses only the current temperature, which is a
feature known at t, so it does not leak.
"""

from __future__ import annotations

from itertools import product
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin, clone
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.features import TEMP

SEED = 42


class PersistenceModel(BaseEstimator, RegressorMixin):
    """Baseline: the temperature in one hour equals the temperature now."""

    def fit(self, X: pd.DataFrame, y=None):
        if TEMP not in X:
            raise ValueError(f"Persistence needs the '{TEMP}' column")
        self.n_features_in_ = X.shape[1]
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return X[TEMP].to_numpy(dtype=float)


class DiurnalChangeBaseline(BaseEstimator, RegressorMixin):
    """Stronger naive baseline: current temperature + the average 1-hour change
    seen in training data at the same local hour and time of year.

    It answers "how much of the gain is just knowing the daily cycle?". The
    hour and season are recovered from the cyclical features, and the season
    uses 12 equal day-of-year bins. Unseen (hour, bin) keys fall back to the
    hour-only mean, then to zero change.
    """

    n_season_bins: int = 12

    @staticmethod
    def _keys(X: pd.DataFrame, n_bins: int) -> tuple[np.ndarray, np.ndarray]:
        hour = np.mod(np.round(np.arctan2(X["hour_sin"], X["hour_cos"]) * 24 / (2 * np.pi) * 2) / 2, 24)
        frac = np.mod(np.arctan2(X["doy_sin"], X["doy_cos"]) / (2 * np.pi), 1.0)
        season = np.minimum((frac * n_bins).astype(int), n_bins - 1)
        return np.asarray(hour), np.asarray(season)

    def fit(self, X: pd.DataFrame, y):
        hour, season = self._keys(X, self.n_season_bins)
        change = pd.Series(np.asarray(y) - X[TEMP].to_numpy())
        self.by_hour_season_ = change.groupby([hour, season]).mean().to_dict()
        self.by_hour_ = change.groupby(hour).mean().to_dict()
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        hour, season = self._keys(X, self.n_season_bins)
        delta = [self.by_hour_season_.get((h, s), self.by_hour_.get(h, 0.0)) for h, s in zip(hour, season)]
        return X[TEMP].to_numpy() + np.asarray(delta)


class ResidualRegressor(BaseEstimator, RegressorMixin):
    """Fits `estimator` to y - X[TEMP] and adds X[TEMP] back when predicting."""

    def __init__(self, estimator: Any):
        self.estimator = estimator

    def fit(self, X: pd.DataFrame, y):
        self.estimator_ = clone(self.estimator).fit(X, np.asarray(y) - X[TEMP].to_numpy())
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.estimator_.predict(X) + X[TEMP].to_numpy()


def _wrap(estimator, residual: bool):
    return ResidualRegressor(estimator) if residual else estimator


def candidates() -> dict[str, list[tuple[dict, Any]]]:
    """Model family -> list of (params, unfitted estimator). Small, explicit grids."""
    grid: dict[str, list[tuple[dict, Any]]] = {
        "persistence": [({}, PersistenceModel())],
        "diurnal_baseline": [({}, DiurnalChangeBaseline())],
        # Scaling doesn't change OLS predictions, but it keeps coefficients
        # comparable. The scaler sits inside the pipeline, so it is fitted on
        # training rows only.
        "linear_regression": [({}, make_pipeline(StandardScaler(), LinearRegression()))],
        "random_forest": [],
        "gradient_boosting": [],
    }
    for residual, depth, leaf in product([False, True], [12, None], [1, 5]):
        params = {"residual": residual, "max_depth": depth, "min_samples_leaf": leaf}
        rf = RandomForestRegressor(
            n_estimators=200, max_depth=depth, min_samples_leaf=leaf,
            max_features=0.5, n_jobs=-1, random_state=SEED,
        )
        grid["random_forest"].append((params, _wrap(rf, residual)))
    for residual, lr, leaves, iters in product([False, True], [0.05, 0.1], [15, 31], [300, 800]):
        params = {"residual": residual, "learning_rate": lr, "max_leaf_nodes": leaves, "max_iter": iters}
        # early_stopping=False: sklearn's built-in early stopping holds out a
        # *random* subset of rows, which would break the chronological design.
        hgb = HistGradientBoostingRegressor(
            learning_rate=lr, max_leaf_nodes=leaves, max_iter=iters,
            early_stopping=False, random_state=SEED,
        )
        grid["gradient_boosting"].append((params, _wrap(hgb, residual)))
    return grid
