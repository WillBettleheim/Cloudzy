"""Tests for the history explorer, Lab breakdowns/explainability, and the new Lab tabs."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import src.history as H
import src.provider
from src import lab_ui, monitoring
from src.features import feature_columns
from src.provider import Place, ProviderError

PARIS = Place("Paris", 48.8534, 2.3488, "Europe/Paris", "France", "Île-de-France", 2138551, "FR")


def archive(start_local="2024-03-24T23:00", hours=24 * 13, tz="Europe/Paris", fixed_offset=3600, **overrides):
    """Archive payload as the API returns it with timeformat=unixtime: hours anchored with one fixed offset."""
    t0 = pd.Timestamp(start_local) - pd.Timedelta(seconds=fixed_offset)
    times = pd.date_range(t0, periods=hours, freq="h", tz="UTC")
    epochs = [int(t.timestamp()) for t in times]
    hourly = {"time": epochs, "temperature_2m": [10.0 + (i % 24) / 4 for i in range(hours)],
              "relative_humidity_2m": [70] * hours, "precipitation": [0.5 if i % 24 == 15 else 0.0 for i in range(hours)],
              "wind_speed_10m": [10.0] * hours, "wind_gusts_10m": [25.0] * hours, "cloud_cover": [50] * hours,
              "weather_code": [3] * hours}
    hourly.update(overrides)
    return {"timezone": tz, "utc_offset_seconds": fixed_offset,
            "hourly_units": {"time": "unixtime", **H.VARIABLES}, "hourly": hourly}


# --- History ----------------------------------------------------------------------------

@pytest.mark.parametrize("start, end, message", [
    (date(2024, 2, 1), date(2024, 1, 1), "after the end"),
    (date(1939, 12, 31), date(1940, 1, 5), "starts on"),
    (date(2026, 10, 1), date(2026, 10, 25), "future"),
    (date(2020, 1, 1), date(2021, 6, 1), "at most 366"),
])
def test_invalid_ranges_are_rejected_before_any_request(start, end, message):
    with pytest.raises(ValueError, match=message):
        H.validate_range(start, end, today=date(2026, 10, 19))


def test_valid_range_passes():
    H.validate_range(date(1940, 1, 1), date(1940, 12, 31), today=date(2026, 10, 19))


def test_parse_and_trim_across_daylight_saving():
    df = H.within_local_dates(H.parse_history(archive()), date(2024, 3, 25), date(2024, 4, 5))
    assert str(df.index.tz) == "Europe/Paris"
    assert df.index[0] == pd.Timestamp("2024-03-25T00:00", tz="Europe/Paris")  # previous evening trimmed
    d = H.daily_summary(df)
    assert len(d) == 12
    assert d.loc["2024-03-31", "hours"] == 23  # clocks went forward
    assert d.loc["2024-03-26", "precipitation_mm"] == pytest.approx(0.5)


def test_unavailable_variable_is_empty_not_zero():
    df = H.parse_history(archive(wind_gusts_10m=[None] * (24 * 13)))
    assert df["wind_gusts_10m"].isna().all()
    assert H.daily_summary(df)["gust_max_kmh"].isna().all()


@pytest.mark.parametrize("payload, message", [
    ({"error": True, "reason": "out of allowed range"}, "out of allowed range"),
    ({"hourly": {"time": []}, "hourly_units": {"time": "unixtime"}}, "no hourly data"),
])
def test_bad_archive_responses(payload, message):
    with pytest.raises(ProviderError, match=message):
        H.parse_history(payload)


def test_wrong_unit_is_fatal():
    p = archive()
    p["hourly_units"]["temperature_2m"] = "°F"
    with pytest.raises(ProviderError, match="°F"):
        H.parse_history(p)


def test_no_temperature_at_all_is_an_error():
    with pytest.raises(ProviderError, match="no temperature"):
        H.parse_history(archive(temperature_2m=[None] * (24 * 13)))


def test_overview_numbers():
    d = H.daily_summary(H.parse_history(archive()))
    ov = H.overview(d)
    assert ov["days"] == len(d) and ov["wet_days"] == 0  # 0.5 mm/day is below the 1 mm wet-day rule
    assert ov["total_precip_mm"] == pytest.approx(0.5 * 13)


def test_fetch_history_validates_then_requests(monkeypatch):
    calls = []

    def fake_fetch(url, params, session=None, **kw):
        calls.append(params)
        return archive()
    monkeypatch.setattr(H, "fetch_json", fake_fetch)
    df = H.fetch_history(PARIS, date(2024, 3, 25), date(2024, 4, 5), today=date(2026, 10, 19))
    assert calls[0]["timezone"] == "auto" and calls[0]["timeformat"] == "unixtime"
    assert df.index.min().date() == date(2024, 3, 25)
    with pytest.raises(ValueError):
        H.fetch_history(PARIS, date(2024, 4, 5), date(2024, 3, 25), today=date(2026, 10, 19))
    assert len(calls) == 1  # the invalid range never reached the API


# --- Lab helpers ----------------------------------------------------------------------------

def preds():
    idx = pd.date_range("2025-01-01", periods=8, freq="h", tz="UTC")
    return pd.DataFrame({"observed": [5, 15, 25, 35, 5, 15, 25, 35.0],
                         "precip_at_issue_mm": [0, 0, 0, 0, 1, 1, 0, 0.0],
                         "random_forest": [6, 15, 25, 35, 7, 16, 25, 35.0],
                         "diurnal_baseline": [5, 16, 26, 37, 5, 15, 25, 35.0]}, index=idx)


def test_mae_by_temperature_and_rain():
    t = lab_ui.mae_by_group(preds(), ["random_forest", "diurnal_baseline"], "temperature")
    assert list(t.index) == ["below 10 °C", "10–20 °C", "20–30 °C", "30 °C and above"]
    assert t.loc["below 10 °C", "random_forest"] == pytest.approx(1.5) and t["hours"].sum() == 8
    r = lab_ui.mae_by_group(preds(), ["random_forest"], "rain")
    assert r.loc["raining when issued", "random_forest"] == pytest.approx(1.5)
    assert r.loc["raining when issued", "hours"] == 2


def test_every_feature_has_a_description():
    assert set(feature_columns()) <= set(lab_ui.FEATURE_DESCRIPTIONS)


def test_importance_chart_uses_requested_view():
    imp = pd.DataFrame({"feature": ["temperature_2m", "hour_cos", "temperature_2m", "hour_cos"],
                        "mae_increase_c": [8.4, 0.35, 0.001, 0.35], "std_c": [0.03, 0.004, 0.0, 0.004],
                        "view": ["full prediction"] * 2 + ["learned change"] * 2})
    fig = lab_ui.importance_chart(imp, lab_ui.lab_palette(), "learned change")
    assert list(fig.data[0].x) == [0.35, 0.001]
    assert fig.data[0].y[0] == lab_ui.FEATURE_DESCRIPTIONS["hour_cos"]


def test_committed_importance_is_genuine():
    imp = pd.read_csv("reports/lab/feature_importance.csv")
    assert set(imp["view"]) == {"full prediction", "learned change"}
    assert set(imp["feature"]) == set(feature_columns())
    assert (imp.groupby("view").size() == len(feature_columns())).all()


# --- Lab page tabs ---------------------------------------------------------------------------

@pytest.fixture
def lab_fakes(monkeypatch):
    st.cache_data.clear()
    monkeypatch.setattr(src.provider, "search_locations", lambda q, count=8, session=None: [PARIS] if "par" in q.lower() else [])
    monkeypatch.setattr(H, "fetch_history",
                        lambda place, start, end, today=None, session=None:
                        H.within_local_dates(H.parse_history(archive()), date(2024, 3, 25), date(2024, 4, 5)))
    yield
    st.cache_data.clear()


def run_lab():
    at = AppTest.from_file("../app_pages/lab.py", default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_lab_has_all_tabs_and_explainability(lab_fakes):
    at = run_lab()
    assert [t.label for t in at.tabs] == ["Scoreboard", "Predictions vs observed", "Error breakdown", "Live setting",
                                         "Multi-step", "Explainability", "Live tracking", "Learn", "Limitations"]
    text = " ".join(m.value for m in at.markdown)
    assert "Association, not causation" in text and "What do MAE and RMSE mean?" in text


def run_history():
    at = AppTest.from_file("../app_pages/history.py", default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_history_page_with_real_shaped_data(lab_fakes):
    at = run_history()
    at.text_input(key="hist_query").set_value("Paris").run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert labels[:4] == ["Mean temperature", "Hottest day", "Coldest night", "Total rain"]
    assert at.metric[3].value == "6.0 mm"  # 12 local days x 0.5 mm (the stray evening hour is trimmed)


def test_history_page_no_match(lab_fakes):
    at = run_history()
    at.text_input(key="hist_query").set_value("Atlantis").run()
    assert any("No places match" in w.value for w in at.warning)
    assert not [m for m in at.metric if m.label == "Mean temperature"]


def test_history_page_service_failure(lab_fakes, monkeypatch):
    def down(*a, **k):
        raise ProviderError("Couldn't load historical data: timed out")
    monkeypatch.setattr(H, "fetch_history", down)
    at = run_history()
    at.text_input(key="hist_query").set_value("Paris").run()
    assert any("Couldn't load historical data" in e.value for e in at.error)
    assert not [m for m in at.metric if m.label == "Mean temperature"]  # no numbers on failure


def test_live_tracking_tab_reads_recorded_predictions(lab_fakes, tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDZY_MONITORING_DIR", str(tmp_path))
    from tests.test_monitoring import result
    monitoring.record_prediction(result(), tmp_path)
    at = run_lab()
    assert any("No live predictions have been evaluated yet" in i.value for i in at.info)


# --- Monthly view and reference comparison --------------------------------------------------------

def daily_payload(start="2014-01-01", end="2024-01-31", temp=lambda d: 10.0, rain=lambda d: 1.0, missing=()):
    days = pd.date_range(start, end, freq="D")
    t = [None if d.date() in missing else temp(d) for d in days]
    return {"daily_units": {"time": "iso8601", **H.DAILY_VARIABLES},
            "daily": {"time": days.strftime("%Y-%m-%d").tolist(), "temperature_2m_mean": t,
                      "temperature_2m_max": t, "temperature_2m_min": t, "precipitation_sum": [rain(d) for d in days]}}


def test_monthly_summary_marks_partial_months():
    daily = H.daily_summary(H.within_local_dates(H.parse_history(archive()), date(2024, 3, 25), date(2024, 4, 5)))
    m = H.monthly_summary(daily)
    assert [str(p) for p in m.index] == ["2024-03", "2024-04"]
    assert list(m["days"]) == [7, 5] and list(m["days_in_month"]) == [31, 30]


def test_reference_windows_handle_leap_days_and_archive_start():
    w = H.reference_windows(date(2024, 2, 29), date(2024, 3, 2), 2)
    assert w == [(2023, date(2023, 2, 28), date(2023, 3, 2)), (2022, date(2022, 2, 28), date(2022, 3, 2))]
    assert H.reference_windows(date(1942, 1, 1), date(1942, 1, 5), 10)[-1][0] == 1940   # nothing before 1940


def test_reference_comparison_is_computed_from_complete_years_only():
    temp = lambda d: 15.0 if d.year == 2024 else 10.0 + (d.year - 2014) * 0.1  # noqa: E731
    df = H.parse_daily(daily_payload(temp=temp, missing={date(2015, 1, 10)}))
    cmp = H.reference_comparison(df, date(2024, 1, 1), date(2024, 1, 31), 10)
    assert cmp["valid"] and 2015 not in cmp["years"] and cmp["n_years"] == 9
    assert cmp["mean_c"] == 15.0 and cmp["warmer_than"] == 9
    years = [2014] + list(range(2016, 2024))
    assert cmp["ref_mean_c"] == pytest.approx(sum(10.0 + (y - 2014) * 0.1 for y in years) / 9)
    assert cmp["ref_low_c"] == 10.0 and cmp["ref_high_c"] == pytest.approx(10.9)
    assert cmp["rain_mm"] == 31.0 and cmp["ref_rain_mean_mm"] == 31.0


def test_reference_comparison_refuses_incomplete_selection_or_too_few_years():
    df = H.parse_daily(daily_payload(missing={date(2024, 1, 31)}))
    assert not H.reference_comparison(df, date(2024, 1, 1), date(2024, 1, 31), 10)["valid"]
    df = H.parse_daily(daily_payload(start="2022-01-01"))   # only 2 earlier years exist in the data
    out = H.reference_comparison(df, date(2024, 1, 1), date(2024, 1, 31), 10)
    assert not out["valid"] and "at least 3" in out["reason"]


def test_daily_archive_unit_is_checked():
    p = daily_payload()
    p["daily_units"]["temperature_2m_mean"] = "°F"
    with pytest.raises(ProviderError, match="expected unit"):
        H.parse_daily(p)
