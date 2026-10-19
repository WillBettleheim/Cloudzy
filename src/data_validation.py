"""Validate and clean raw hourly weather data.

Philosophy: problems are either *fatal* (we refuse to continue, because any
fix would be a guess) or *recoverable* (we apply a documented, mechanical fix
and count it in the quality report). Nothing is silently changed, and we never
invent values: gaps stay as NaN so that later stages must deal with them
explicitly.

Fatal:        missing columns, wrong units, non-UTC timestamps, unparseable or
              off-the-hour timestamps, duplicate timestamps with *conflicting*
              values, empty data, a variable with no values at all.
Recoverable:  exact duplicate rows (dropped), unsorted rows (sorted), missing
              hours (inserted as NaN rows, flagged in `is_gap_fill`),
              non-numeric or physically implausible values (set to NaN).
Flag only:    suspiciously large hour-to-hour temperature jumps (counted, kept);
              variables more than 5% missing (warning).

Run:  python -m src.data_validation
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src import config
from src.data_collection import collect, load_raw_frames

logger = logging.getLogger(__name__)

TIME_FORMAT = "%Y-%m-%dT%H:%M"
MAX_PLAUSIBLE_TEMP_JUMP_C = 8.0  # per hour; flagged, not removed
MISSING_WARN_FRACTION = 0.05


class DataValidationError(ValueError):
    """A data problem that cannot be fixed without guessing."""


@dataclass
class QualityReport:
    rows_raw: int = 0
    rows_clean: int = 0
    exact_duplicates_dropped: int = 0
    was_sorted: bool = True
    missing_timestamps_inserted: int = 0
    non_numeric_set_to_nan: dict[str, int] = field(default_factory=dict)
    out_of_range_set_to_nan: dict[str, int] = field(default_factory=dict)
    missing_values: dict[str, int] = field(default_factory=dict)
    longest_missing_run_hours: dict[str, int] = field(default_factory=dict)
    suspicious_temperature_jumps: int = 0
    start_utc: str | None = None
    end_utc: str | None = None
    summary_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    source_metadata: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_metadata(metadata: list[dict], expected_units: dict[str, str]) -> list[str]:
    """Check time zone, units and grid consistency for each raw chunk.

    Raises DataValidationError on fatal problems; returns non-fatal warnings.
    """
    if not metadata:
        raise DataValidationError("No source metadata supplied")
    warnings = []
    for meta in metadata:
        name = meta.get("file", "<unknown>")
        if meta.get("utc_offset_seconds") != 0:
            raise DataValidationError(
                f"{name}: expected UTC timestamps, got utc_offset_seconds={meta.get('utc_offset_seconds')}"
            )
        units = meta.get("hourly_units", {})
        if units.get("time") != "iso8601":
            raise DataValidationError(f"{name}: expected time unit 'iso8601', got {units.get('time')!r}")
        for var, unit in expected_units.items():
            if units.get(var) != unit:
                raise DataValidationError(f"{name}: expected {var} in {unit!r}, got {units.get(var)!r}")
    grid = {(m.get("latitude"), m.get("longitude")) for m in metadata}
    if len(grid) > 1:
        raise DataValidationError(f"Chunks come from different grid cells: {sorted(grid)}")
    return warnings


def _longest_nan_run(series: pd.Series) -> int:
    is_nan = series.isna().to_numpy()
    if not is_nan.any():
        return 0
    # Lengths of consecutive True runs.
    padded = np.concatenate(([False], is_nan, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return int((edges[1::2] - edges[::2]).max())


def validate_and_clean(
    df: pd.DataFrame,
    variables: list[str],
    plausible_ranges: dict[str, tuple[float, float]] | None = None,
    expected_start: date | None = None,
    expected_end: date | None = None,
) -> tuple[pd.DataFrame, QualityReport]:
    """Return a cleaned hourly frame indexed by UTC timestamp, plus a report."""
    report = QualityReport(rows_raw=len(df))
    if df.empty:
        raise DataValidationError("Dataset is empty")

    missing_cols = [c for c in ["time", *variables] if c not in df.columns]
    if missing_cols:
        raise DataValidationError(f"Missing columns: {missing_cols}")
    extra = sorted(set(df.columns) - {"time", *variables})
    if extra:
        report.warnings.append(f"Ignoring unexpected columns: {extra}")
    df = df[["time", *variables]].copy()

    # --- Timestamps ---------------------------------------------------------
    parsed = pd.to_datetime(df["time"], format=TIME_FORMAT, errors="coerce", utc=True)
    bad = parsed.isna()
    if bad.any():
        examples = df.loc[bad, "time"].head(3).tolist()
        raise DataValidationError(f"{int(bad.sum())} unparseable timestamps, e.g. {examples}")
    if (parsed != parsed.dt.floor("h")).any():
        raise DataValidationError("Some timestamps are not on the hour")
    df["time"] = parsed

    # --- Numeric values -----------------------------------------------------
    for var in variables:
        original_null = df[var].isna()
        df[var] = pd.to_numeric(df[var], errors="coerce").astype("float64")
        newly_null = int((df[var].isna() & ~original_null).sum())
        if newly_null:
            report.non_numeric_set_to_nan[var] = newly_null

    # --- Duplicates ---------------------------------------------------------
    exact_dupes = df.duplicated(keep="first")
    report.exact_duplicates_dropped = int(exact_dupes.sum())
    df = df[~exact_dupes]
    conflicting = df["time"].duplicated(keep=False)
    if conflicting.any():
        times = df.loc[conflicting, "time"].drop_duplicates().head(3).tolist()
        raise DataValidationError(
            f"{int(conflicting.sum())} rows share a timestamp but have different values, e.g. {times}"
        )

    # --- Ordering -----------------------------------------------------------
    report.was_sorted = bool(df["time"].is_monotonic_increasing)
    df = df.sort_values("time").set_index("time")

    # --- Complete hourly index ---------------------------------------------
    start = (
        pd.Timestamp(datetime.combine(expected_start, time(0), tzinfo=timezone.utc))
        if expected_start else df.index.min()
    )
    end = (
        pd.Timestamp(datetime.combine(expected_end, time(23), tzinfo=timezone.utc))
        if expected_end else df.index.max()
    )
    outside = (df.index < start) | (df.index > end)
    if outside.any():
        report.warnings.append(f"Dropped {int(outside.sum())} rows outside {start} – {end}")
        df = df[~outside]
    full_index = pd.date_range(start, end, freq="h", tz="UTC", name="time")
    observed_index = df.index
    report.missing_timestamps_inserted = int(len(full_index.difference(observed_index)))
    df = df.reindex(full_index)
    df["is_gap_fill"] = ~df.index.isin(observed_index)

    # --- Physical plausibility ---------------------------------------------
    for var, (lo, hi) in (plausible_ranges or {}).items():
        if var not in df:
            continue
        bad_range = df[var].notna() & ~df[var].between(lo, hi)
        if bad_range.any():
            report.out_of_range_set_to_nan[var] = int(bad_range.sum())
            df.loc[bad_range, var] = np.nan

    if "temperature_2m" in df:
        jumps = df["temperature_2m"].diff().abs() > MAX_PLAUSIBLE_TEMP_JUMP_C
        report.suspicious_temperature_jumps = int(jumps.sum())

    # --- Summary ------------------------------------------------------------
    for var in variables:
        n_missing = int(df[var].isna().sum())
        if n_missing == len(df):
            raise DataValidationError(
                f"'{var}' has no values at all; the selected weather model may not provide it"
            )
        if n_missing > MISSING_WARN_FRACTION * len(df):
            report.warnings.append(f"'{var}' is {100 * n_missing / len(df):.1f}% missing")
        report.missing_values[var] = n_missing
        report.longest_missing_run_hours[var] = _longest_nan_run(df[var])
        s = df[var]
        report.summary_stats[var] = {
            "min": float(s.min()), "max": float(s.max()),
            "mean": round(float(s.mean()), 3), "std": round(float(s.std()), 3),
        }
    report.rows_clean = len(df)
    report.start_utc = df.index.min().isoformat()
    report.end_utc = df.index.max().isoformat()
    return df, report


def save_processed(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.reset_index()
    out["time"] = out["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(path, index=False)


def load_processed(path: Path) -> pd.DataFrame:
    """Load a processed CSV back into the exact shape validate_and_clean returns."""
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], format="%Y-%m-%dT%H:%M:%SZ", utc=True)
    df["is_gap_fill"] = df["is_gap_fill"].astype(bool)
    return df.set_index("time")


def processed_path(
    location: config.Location = config.DELHI, source: config.DataSource = config.REANALYSIS,
    start: date = config.START_DATE, end: date = config.END_DATE,
) -> Path:
    return config.PROCESSED_DIR / f"{location.slug}_hourly_{source.model}_{start}_{end}.csv"


def run_pipeline(
    force_download: bool = False, source: config.DataSource = config.REANALYSIS,
    location: config.Location = config.DELHI, start: date = config.START_DATE, end: date = config.END_DATE,
) -> tuple[pd.DataFrame, QualityReport]:
    paths = collect(location=location, start=start, end=end, source=source, force=force_download)
    raw, metadata = load_raw_frames(paths)
    variables = list(config.HOURLY_VARIABLES)
    validate_metadata(metadata, config.HOURLY_VARIABLES)
    clean, report = validate_and_clean(
        raw, variables, config.PLAUSIBLE_RANGES, start, end
    )
    report.source_metadata = metadata

    out = processed_path(location, source, start, end)
    save_processed(clean, out)
    suffix = "" if (start, end) == (config.START_DATE, config.END_DATE) else f"_{start}_{end}"
    report_path = config.REPORTS_DIR / f"data_quality_{location.slug}_{source.model}{suffix}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    logger.info("Processed data: %s (%d rows, %s → %s)", out.name, report.rows_clean,
                report.start_utc, report.end_utc)
    logger.info("Quality report: %s", report_path.name)
    for w in report.warnings:
        logger.warning(w)
    return clean, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=sorted(config.SOURCES), default=config.REANALYSIS.model)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _, report = run_pipeline(source=config.SOURCES[args.source])
    summary = {k: v for k, v in report.to_dict().items() if k not in ("summary_stats", "source_metadata")}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
