"""Load a saved Cloudzy Core model and make predictions.

Input requirements:
- `predict` takes a DataFrame with exactly the bundle's feature columns. Order
  is enforced, and every value must be finite.
- `predict_next_hour` takes processed-style hourly history (a complete UTC
  hourly grid, with the raw variable columns) of at least 25 hours ending at
  the issue time. It builds the features itself.

Limitations: the model was trained on ERA5 reanalysis for one Delhi grid cell
and predicts exactly one hour ahead. It produces a point estimate, not a
probability or a confidence interval.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.features import FeatureConfig, add_features

REQUIRED_KEYS = {"model", "feature_columns", "feature_config", "target", "model_family"}


def load_bundle(path: Path) -> dict:
    """Load and verify a single-horizon model (see src.artifacts.load_trusted)."""
    from src.artifacts import load_trusted
    return load_trusted(path, REQUIRED_KEYS)


def predict(bundle: dict, X: pd.DataFrame) -> np.ndarray:
    cols = bundle["feature_columns"]
    missing = [c for c in cols if c not in X.columns]
    if missing:
        raise ValueError(f"Missing feature columns: {missing}")
    X = X[cols]
    if not np.isfinite(X.to_numpy(dtype=float)).all():
        raise ValueError("Features contain NaN or infinite values")
    pred = np.asarray(bundle["model"].predict(X), dtype=float)
    if pred.shape != (len(X),) or not np.isfinite(pred).all():
        raise RuntimeError("Model produced an invalid prediction array")
    return pred


def predict_next_hour(bundle: dict, history: pd.DataFrame) -> dict:
    """Predict the temperature one hour after the last row of `history`."""
    cfg = FeatureConfig(**{k: tuple(v) if isinstance(v, list) else v
                           for k, v in bundle["feature_config"].items()})
    feats = add_features(history, cfg)[bundle["feature_columns"]].iloc[[-1]]
    if feats.isna().any(axis=None):
        bad = feats.columns[feats.isna().iloc[0]].tolist()
        raise ValueError(f"Not enough complete history for features: {bad}")
    issue_time = feats.index[0]
    return {
        "issue_time_utc": issue_time.isoformat(),
        "valid_time_utc": (issue_time + pd.Timedelta(hours=cfg.horizon_hours)).isoformat(),
        "predicted_temperature_2m_c": float(predict(bundle, feats)[0]),
        "model_family": bundle["model_family"],
    }
