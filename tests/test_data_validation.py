"""Tests for src.data_validation."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.data_validation import (
    DataValidationError, load_processed, save_processed, validate_and_clean, validate_metadata,
)

VARS = ["temperature_2m", "relative_humidity_2m"]
RANGES = {"temperature_2m": (-10.0, 55.0), "relative_humidity_2m": (0.0, 100.0)}


def raw_frame(hours=6, start="2024-01-01T00:00"):
    times = pd.date_range(start, periods=hours, freq="h")
    return pd.DataFrame({
        "time": times.strftime("%Y-%m-%dT%H:%M"),
        "temperature_2m": np.linspace(10, 15, hours),
        "relative_humidity_2m": [50.0] * hours,
    })


def test_clean_frame_passes_unchanged():
    df, report = validate_and_clean(raw_frame(), VARS, RANGES)
    assert len(df) == 6
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing and df.index.is_unique
    assert not df["is_gap_fill"].any()
    assert report.missing_timestamps_inserted == 0
    assert report.exact_duplicates_dropped == 0
    assert report.missing_values == {"temperature_2m": 0, "relative_humidity_2m": 0}


def test_missing_timestamps_are_inserted_as_nan_and_flagged():
    raw = raw_frame().drop(index=[2, 3])
    df, report = validate_and_clean(raw, VARS, RANGES)
    assert len(df) == 6
    assert report.missing_timestamps_inserted == 2
    assert df["is_gap_fill"].sum() == 2
    assert df["temperature_2m"].isna().sum() == 2  # not imputed
    assert report.longest_missing_run_hours["temperature_2m"] == 2


def test_expected_range_reveals_missing_hours_at_edges():
    df, report = validate_and_clean(raw_frame(), VARS, RANGES,
                                    expected_start=date(2024, 1, 1), expected_end=date(2024, 1, 1))
    assert len(df) == 24
    assert report.missing_timestamps_inserted == 18


def test_exact_duplicates_are_dropped_and_counted():
    raw = raw_frame()
    raw = pd.concat([raw, raw.iloc[[1, 4]]], ignore_index=True)
    df, report = validate_and_clean(raw, VARS, RANGES)
    assert len(df) == 6 and df.index.is_unique
    assert report.exact_duplicates_dropped == 2


def test_conflicting_duplicates_are_fatal():
    raw = raw_frame()
    dup = raw.iloc[[1]].copy()
    dup["temperature_2m"] = 99.0
    with pytest.raises(DataValidationError, match="different values"):
        validate_and_clean(pd.concat([raw, dup]), VARS, RANGES)


def test_unsorted_input_is_sorted_and_reported():
    df, report = validate_and_clean(raw_frame().iloc[::-1], VARS, RANGES)
    assert df.index.is_monotonic_increasing
    assert report.was_sorted is False


def test_out_of_range_and_non_numeric_values_become_nan():
    raw = raw_frame().astype({"relative_humidity_2m": object})
    raw.loc[0, "temperature_2m"] = 80.0
    raw.loc[1, "relative_humidity_2m"] = "n/a"
    df, report = validate_and_clean(raw, VARS, RANGES)
    assert np.isnan(df["temperature_2m"].iloc[0])
    assert np.isnan(df["relative_humidity_2m"].iloc[1])
    assert report.out_of_range_set_to_nan == {"temperature_2m": 1}
    assert report.non_numeric_set_to_nan == {"relative_humidity_2m": 1}


def test_null_values_from_api_are_counted_not_filled():
    raw = raw_frame()
    raw.loc[3, "temperature_2m"] = None
    df, report = validate_and_clean(raw, VARS, RANGES)
    assert report.missing_values["temperature_2m"] == 1
    assert report.non_numeric_set_to_nan == {}


def test_temperature_jumps_are_flagged_but_kept():
    raw = raw_frame()
    raw.loc[3, "temperature_2m"] = 40.0
    df, report = validate_and_clean(raw, VARS, RANGES)
    assert report.suspicious_temperature_jumps == 2
    assert df["temperature_2m"].iloc[3] == 40.0


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.drop(columns="relative_humidity_2m"), "Missing columns"),
    (lambda d: d.assign(time=["garbage"] + d["time"].tolist()[1:]), "unparseable"),
    (lambda d: d.assign(time=d["time"].str.replace(":00", ":30")), "not on the hour"),
    (lambda d: d.iloc[0:0], "empty"),
])
def test_fatal_problems_raise(mutate, message):
    with pytest.raises(DataValidationError, match=message):
        validate_and_clean(mutate(raw_frame()), VARS, RANGES)


def meta(**overrides):
    m = {"file": "x.json", "latitude": 28.5, "longitude": 77.25, "utc_offset_seconds": 0,
         "hourly_units": {"time": "iso8601", "temperature_2m": "°C"}}
    m.update(overrides)
    return m


def test_metadata_accepts_expected_units():
    validate_metadata([meta(), meta()], {"temperature_2m": "°C"})


@pytest.mark.parametrize("bad, message", [
    ([meta(utc_offset_seconds=19800)], "UTC"),
    ([meta(hourly_units={"time": "iso8601", "temperature_2m": "°F"})], "°F"),
    ([meta(hourly_units={"time": "unixtime", "temperature_2m": "°C"})], "iso8601"),
    ([meta(), meta(latitude=28.75)], "different grid cells"),
    ([], "No source metadata"),
])
def test_metadata_problems_are_fatal(bad, message):
    with pytest.raises(DataValidationError, match=message):
        validate_metadata(bad, {"temperature_2m": "°C"})


def test_processed_csv_round_trip(tmp_path):
    raw = raw_frame().drop(index=[2])
    df, _ = validate_and_clean(raw, VARS, RANGES)
    path = tmp_path / "out.csv"
    save_processed(df, path)
    pd.testing.assert_frame_equal(load_processed(path), df, check_freq=False)
