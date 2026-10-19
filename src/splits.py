"""Chronological train / validation / test splits.

Rows are assigned by *issue time* t. Rows are never shuffled.

Purging: the last training row's target (temperature at t+1) is the first
validation hour's current temperature. Using that row would put a value from
the validation period into training. So every row whose target time falls in a
later split is dropped ("purged"). With a 1-hour horizon this removes one row
per boundary. It is cheap and keeps the separation strict.

Features may still *look back* into an earlier split (for example a 24 h lag
on 1 January). That is legitimate: in real use the past is always available.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SplitSpec:
    name: str
    start: str  # inclusive, UTC
    end: str    # exclusive, UTC


DEFAULT_SPLITS = (
    SplitSpec("train", "2023-01-01", "2024-01-01"),
    SplitSpec("validation", "2024-01-01", "2025-01-01"),
    SplitSpec("test", "2025-01-01", "2026-01-01"),
)


def chronological_split(
    table: pd.DataFrame,
    specs: tuple[SplitSpec, ...] = DEFAULT_SPLITS,
    horizon_hours: int = 1,
) -> dict[str, pd.DataFrame]:
    """Split a time-indexed table into non-overlapping, ordered periods."""
    if not table.index.is_monotonic_increasing:
        raise ValueError("Table must be sorted by time")
    bounds = [(s.name, pd.Timestamp(s.start, tz="UTC"), pd.Timestamp(s.end, tz="UTC")) for s in specs]
    for (_, _, prev_end), (name, start, _) in zip(bounds, bounds[1:]):
        if start < prev_end:
            raise ValueError(f"Split '{name}' overlaps the previous split")

    horizon = pd.Timedelta(hours=horizon_hours)
    splits = {}
    for name, start, end in bounds:
        part = table[(table.index >= start) & (table.index < end)]
        part = part[part.index + horizon < end]  # purge rows whose target lies in the next split
        if part.empty:
            raise ValueError(f"Split '{name}' is empty")
        splits[name] = part
    return splits


def describe_splits(splits: dict[str, pd.DataFrame]) -> dict[str, dict]:
    return {
        name: {
            "rows": len(df),
            "first_issue_time_utc": df.index.min().isoformat(),
            "last_issue_time_utc": df.index.max().isoformat(),
        }
        for name, df in splits.items()
    }
