"""Tests for rule-based summaries, alerts, recommendations and the daily briefing."""

import pandas as pd
import pytest

from src import insights as I
from src import units as U
from src.preferences import AlertThresholds
from src.provider import parse_forecast
from tests.test_provider import DELHI, payload

NOW = pd.Timestamp("2026-10-19T03:30Z")  # 09:00 IST: morning, start of the window


def forecast(mutate=None):
    p = payload()
    if mutate:
        mutate(p)
    return parse_forecast(p, DELHI, "now")


def hourly_set(p, var, fn):
    """Set an hourly variable from local hour-of-day (payload starts 2026-10-19 00:00 IST)."""
    p["hourly"][var] = [fn(i % 24, i) for i in range(len(p["hourly"]["time"]))]


# --- Summary -----------------------------------------------------------------------

def test_summary_quotes_real_values_and_trend():
    f = forecast()  # temperature = 20 + hour/2, current 28.1 °C
    s = I.summary(f, NOW, U.METRIC)
    assert s[0].startswith("It's 28° now (clear sky)")
    assert any("mostly clear skies" in x for x in s)
    assert any("Rain is unlikely" in x for x in s)


def test_summary_rain_timing_compares_periods():
    def m(p):
        hourly_set(p, "precipitation_probability", lambda h, i: 80 if 18 <= h < 24 else 5)
    s = I.summary(forecast(m), NOW)
    rain = next(x for x in s if x.startswith("Rain"))
    assert "more likely in the evening (up to 80%)" in rain


def test_summary_cloud_trend_and_units():
    def m(p):
        hourly_set(p, "cloud_cover", lambda h, i: 10 if i < 15 else 90)  # clears → cloudy after 15:00
    s = I.summary(forecast(m), NOW, U.IMPERIAL)
    assert any("cloud cover increasing" in x for x in s)
    assert s[0].startswith("It's 83° now")  # 28.1 °C in °F


def test_summary_tomorrow_comparison():
    def m(p):
        p["daily"]["temperature_2m_max"] = [30.0, 24.0] + [25.0] * 5
    s = I.summary(forecast(m), NOW)
    assert "Tomorrow is forecast to be cooler than today (high 24° vs 30°)." in s


def test_summary_without_enough_data_says_so():
    def m(p):
        p["hourly"]["temperature_2m"] = [20.0] * 3 + [None] * (len(p["hourly"]["time"]) - 3)
    s = I.summary(forecast(m), NOW)
    assert s == ["There isn't enough forecast data for this place to write a summary."]


# --- Alerts --------------------------------------------------------------------------

def test_no_alerts_on_a_calm_day():
    assert I.alerts(forecast(), NOW) == []


def test_alerts_each_have_reason_value_period_and_uncertainty():
    def m(p):
        hourly_set(p, "precipitation_probability", lambda h, i: 90 if 15 <= h < 18 else 0)
        hourly_set(p, "apparent_temperature", lambda h, i: 43 if 12 <= h < 16 else 30)
        hourly_set(p, "uv_index", lambda h, i: 9 if 11 <= h < 14 else 2)
        hourly_set(p, "wind_gusts_10m", lambda h, i: 60 if h in (10, 20) else 10)
        p["daily"]["temperature_2m_max"] = [30.0, 37.0] + [30.0] * 5
    found = {a.kind: a for a in I.alerts(forecast(m), NOW)}
    assert set(found) == {"rain", "heat", "uv", "wind", "change"}
    assert found["rain"].period == "15:00–18:00 Mon" and found["rain"].value == "up to 90%"
    assert found["heat"].value == "up to 43°"
    assert found["wind"].period == "between 10:00 and 21:00 Mon (2 hours)"  # gaps are not hidden
    assert found["change"].value == "30° → 37°"
    for a in found.values():
        assert a.reason and a.uncertainty and "not an official weather warning" in a.label


def test_thresholds_are_configurable():
    def m(p):
        hourly_set(p, "precipitation_probability", lambda h, i: 45)
    f = forecast(m)
    assert not [a for a in I.alerts(f, NOW) if a.kind == "rain"]                     # default 60%
    assert [a for a in I.alerts(f, NOW, AlertThresholds(rain_probability_pct=40)) if a.kind == "rain"]


def test_cold_alert_in_fahrenheit():
    def m(p):
        hourly_set(p, "apparent_temperature", lambda h, i: -5)
    cold = next(a for a in I.alerts(forecast(m), NOW, units=U.IMPERIAL) if a.kind == "cold")
    assert cold.value == "down to 23°"


# --- Recommendations ------------------------------------------------------------------

def test_umbrella_rules():
    def wet(p):
        hourly_set(p, "precipitation_probability", lambda h, i: 70 if 12 <= h < 15 else 0)
    recs = {r.kind: r for r in I.recommendations(forecast(wet), NOW)}
    assert recs["umbrella"].text == "An umbrella may be useful."
    assert "70%" in recs["umbrella"].why and "12:00–15:00 Mon" in recs["umbrella"].why
    dry = {r.kind: r for r in I.recommendations(forecast(), NOW)}
    assert dry["umbrella"].text == "An umbrella probably isn't needed."


@pytest.mark.parametrize("feels, expected", [(33, "Light, breathable"), (25, "T-shirt"), (18, "A light layer"),
                                             (10, "A sweater"), (3, "A warm coat"), (-8, "Heavy winter")])
def test_clothing_follows_feels_like(feels, expected):
    def m(p):
        hourly_set(p, "apparent_temperature", lambda h, i: feels)
    clothing = next(r for r in I.recommendations(forecast(m), NOW) if r.kind == "clothing")
    assert clothing.text.startswith(expected)


def test_outdoor_comfort_and_uv_rules_explain_themselves():
    def m(p):
        hourly_set(p, "apparent_temperature", lambda h, i: 22)
        hourly_set(p, "uv_index", lambda h, i: 7 if h == 13 else 1)
    recs = {r.kind: r for r in I.recommendations(forecast(m), NOW, U.IMPERIAL)}
    assert recs["outdoors"].text.startswith("Comfortable outdoor conditions")
    assert "59°–82°" in recs["outdoors"].why and "25 mph" in recs["outdoors"].why  # rule shown in user units
    assert "UV peaks at 7 around 13:00" in recs["uv"].why


# --- Briefing ---------------------------------------------------------------------------

def test_briefing_reports_range_conditions_and_precipitation():
    def m(p):
        p["daily"]["weather_code"] = [95] + [0] * 6       # most severe: thunderstorm
        p["daily"]["precipitation_probability_max"] = [40] + [0] * 6
        p["daily"]["precipitation_sum"] = [2.5] + [0.0] * 6
    b = I.briefing(forecast(m), NOW)
    assert b.temperature_range == "19° to 30°"
    assert b.conditions == "Mostly clear skies; most significant: thunderstorm"  # no contradiction
    assert b.precipitation == "Up to 40% chance; 2.5 mm expected in total."


def test_briefing_none_when_today_missing():
    assert I.briefing(forecast(), pd.Timestamp("2030-01-01T00:00Z")) is None


def test_period_ending_at_midnight_is_unambiguous():
    times = pd.date_range("2026-10-19T21:00", periods=3, freq="h", tz="Asia/Kolkata")
    assert I._span(times) == "21:00–midnight Mon"


def test_briefing_changes_exclude_static_lines():
    b = I.briefing(forecast(), NOW)  # calm, clear day: only the warming trend is a change
    assert all("Expect" not in c and "unlikely" not in c for c in b.changes)
    assert any("warming" in c or "cooling" in c for c in b.changes)
