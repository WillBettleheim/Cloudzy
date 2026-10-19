"""Feature engineering and target construction for 1-hour-ahead temperature.

Definitions (t is the time a prediction is *issued*):
  target(t)  = temperature_2m at t + 1 h
  features(t) use only values at times <= t

Why `shift` is safe here: the processed data sits on a complete, regular
hourly UTC grid (missing hours are explicit NaN rows), so `shift(k)` always
means "exactly k hours earlier". build_features checks this before doing
anything, because on an irregular index shifts would silently misalign.

Rows whose features or target cannot be computed (the first 24 h of history,
any window touching a gap, the final hour, which has no future value) are
dropped and counted. Nothing is imputed.

"Observed" here means ERA5 reanalysis values. All features are observation-type.
Features that only a forecast provider could supply (such as a model's
predicted temperature for t+1) are deliberately excluded. They belong in a
separate, clearly labelled experiment.

Run:  python -m src.features
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from src import config

logger = logging.getLogger(__name__)

TEMP = "temperature_2m"
TARGET = "target_temperature_next_1h"
HOURS_PER_YEAR = 365.25 * 24


@dataclass(frozen=True)
class FeatureConfig:
    horizon_hours: int = 1
    temp_lags: tuple[int, ...] = (1, 2, 3, 6, 12, 24)
    rolling_windows: tuple[int, ...] = (3, 6, 24)
    current_vars: tuple[str, ...] = (
        "relative_humidity_2m", "surface_pressure", "wind_speed_10m", "precipitation",
    )
    local_timezone: str = config.DELHI.local_timezone

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FeatureReport:
    rows_input: int = 0
    rows_output: int = 0
    rows_dropped_incomplete: int = 0
    feature_columns: list[str] = field(default_factory=list)
    first_issue_time_utc: str | None = None
    last_issue_time_utc: str | None = None


def feature_columns(cfg: FeatureConfig = FeatureConfig()) -> list[str]:
    """The exact, ordered list of model inputs. Saved with every trained model."""
    cols = [TEMP]  # the current temperature at t, which is also the persistence forecast
    cols += [f"temp_lag_{k}h" for k in cfg.temp_lags]
    cols += ["temp_change_1h", "temp_change_24h"]
    for w in cfg.rolling_windows:
        cols += [f"temp_roll_mean_{w}h", f"temp_roll_std_{w}h"]
    cols += ["temp_roll_min_24h", "temp_roll_max_24h"]
    cols += list(cfg.current_vars)
    cols += ["pressure_change_3h", "precip_sum_3h"]
    cols += ["hour_sin", "hour_cos", "doy_sin", "doy_cos"]
    return cols


def check_hourly_grid(index: pd.Index) -> None:
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError("Index must be a time-zone-aware DatetimeIndex")
    if not index.is_monotonic_increasing or not index.is_unique:
        raise ValueError("Index must be sorted and unique")
    if len(index) > 1 and not ((index[1:] - index[:-1]) == pd.Timedelta("1h")).all():
        raise ValueError("Index must be a complete hourly grid (run data_validation first)")


def add_features(df: pd.DataFrame, cfg: FeatureConfig = FeatureConfig()) -> pd.DataFrame:
    """Return the input frame with feature and target columns added (NaNs kept).

    Every rolling window ends at t (pandas' default right-aligned window), so it
    includes t and never t+1.
    """
    check_hourly_grid(df.index)
    out = pd.DataFrame(index=df.index)
    temp = df[TEMP]

    out[TEMP] = temp
    for k in cfg.temp_lags:
        out[f"temp_lag_{k}h"] = temp.shift(k)
    out["temp_change_1h"] = temp - temp.shift(1)
    out["temp_change_24h"] = temp - temp.shift(24)
    for w in cfg.rolling_windows:
        roll = temp.rolling(w, min_periods=w)
        out[f"temp_roll_mean_{w}h"] = roll.mean()
        out[f"temp_roll_std_{w}h"] = roll.std()
    out["temp_roll_min_24h"] = temp.rolling(24, min_periods=24).min()
    out["temp_roll_max_24h"] = temp.rolling(24, min_periods=24).max()

    for var in cfg.current_vars:
        out[var] = df[var]
    out["pressure_change_3h"] = df["surface_pressure"] - df["surface_pressure"].shift(3)
    out["precip_sum_3h"] = df["precipitation"].rolling(3, min_periods=3).sum()

    for name, values in calendar_features(df.index, cfg.local_timezone).items():
        out[name] = values

    out[TARGET] = temp.shift(-cfg.horizon_hours)
    return out


def calendar_features(index: pd.DatetimeIndex, local_timezone: str) -> dict[str, np.ndarray]:
    """Cyclical hour-of-day and day-of-year encodings of the issue time.

    Calendar features are known in advance. Hour uses local clock time (IST) so
    the daily cycle lines up with sunrise and sunset. Cyclical encoding puts
    23:00 next to 00:00.
    """
    local = index.tz_convert(local_timezone)
    hour = np.asarray(local.hour + local.minute / 60.0, dtype=float)
    hours_into_year = (np.asarray(local.dayofyear) - 1) * 24 + hour
    return {
        "hour_sin": np.sin(2 * np.pi * hour / 24),
        "hour_cos": np.cos(2 * np.pi * hour / 24),
        "doy_sin": np.sin(2 * np.pi * hours_into_year / HOURS_PER_YEAR),
        "doy_cos": np.cos(2 * np.pi * hours_into_year / HOURS_PER_YEAR),
    }


def features_from_windows(
    temp_window: np.ndarray,
    exog: dict[str, np.ndarray],
    issue_times: pd.DatetimeIndex,
    cfg: FeatureConfig = FeatureConfig(),
) -> pd.DataFrame:
    """Vectorised features for many issue times at once (used by recursive forecasting).

    temp_window: shape (n, 25); the last column is temperature at the issue time.
    exog: current_vars at the issue time (shape (n,)), plus "surface_pressure_window"
          (n, 4) and "precipitation_window" (n, 3), whose last column is the issue time.
    Must match add_features exactly; a test enforces this.
    """
    w = np.asarray(temp_window, dtype=float)
    need = max(max(cfg.temp_lags), max(cfg.rolling_windows), 24) + 1
    if w.ndim != 2 or w.shape[1] < need:
        raise ValueError(f"temp_window must have shape (n, >= {need})")
    cur = w[:, -1]
    cols: dict[str, np.ndarray] = {TEMP: cur}
    for k in cfg.temp_lags:
        cols[f"temp_lag_{k}h"] = w[:, -1 - k]
    cols["temp_change_1h"] = cur - w[:, -2]
    cols["temp_change_24h"] = cur - w[:, -25]
    for win in cfg.rolling_windows:
        cols[f"temp_roll_mean_{win}h"] = w[:, -win:].mean(axis=1)
        cols[f"temp_roll_std_{win}h"] = w[:, -win:].std(axis=1, ddof=1)
    cols["temp_roll_min_24h"] = w[:, -24:].min(axis=1)
    cols["temp_roll_max_24h"] = w[:, -24:].max(axis=1)
    for var in cfg.current_vars:
        cols[var] = np.asarray(exog[var], dtype=float)
    pw = np.asarray(exog["surface_pressure_window"], dtype=float)
    cols["pressure_change_3h"] = pw[:, -1] - pw[:, -4]
    cols["precip_sum_3h"] = np.asarray(exog["precipitation_window"], dtype=float)[:, -3:].sum(axis=1)
    cols.update(calendar_features(issue_times, cfg.local_timezone))
    return pd.DataFrame(cols, index=issue_times)[feature_columns(cfg)]


def build_features(
    df: pd.DataFrame, cfg: FeatureConfig = FeatureConfig()
) -> tuple[pd.DataFrame, FeatureReport]:
    """Feature table with only complete rows: columns = feature_columns() + [TARGET]."""
    full = add_features(df, cfg)
    cols = feature_columns(cfg)
    complete = full[cols + [TARGET]].notna().all(axis=1)
    table = full.loc[complete, cols + [TARGET]]
    report = FeatureReport(
        rows_input=len(df),
        rows_output=len(table),
        rows_dropped_incomplete=int((~complete).sum()),
        feature_columns=cols,
        first_issue_time_utc=table.index.min().isoformat() if len(table) else None,
        last_issue_time_utc=table.index.max().isoformat() if len(table) else None,
    )
    return table, report


def main() -> None:
    from src.data_validation import load_processed, processed_path

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = FeatureConfig()
    table, report = build_features(load_processed(processed_path()), cfg)
    out = config.PROCESSED_DIR / f"{config.RUN_TAG}_features_h{cfg.horizon_hours}.csv"
    table.reset_index().assign(time=lambda d: d["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")).to_csv(out, index=False)
    report_path = config.REPORTS_DIR / f"features_{config.RUN_TAG}.json"
    report_path.write_text(json.dumps({"config": cfg.to_dict(), **asdict(report)}, indent=2))
    logger.info("Feature table: %s (%d rows, %d features, %d rows dropped)", out.name,
                report.rows_output, len(report.feature_columns), report.rows_dropped_incomplete)


if __name__ == "__main__":
    main()
