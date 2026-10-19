"""Tests for Phase D: UI helpers and the Streamlit app's states (network faked)."""

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import src.briefing
import src.provider
from src import ui
from src.provider import Place, ProviderError, parse_forecast
from tests.test_provider import payload

DELHI = Place("New Delhi", 28.6358, 77.2244, "Asia/Kolkata", "India", "National Capital Territory of Delhi", 317797)
PARIS = Place("Paris", 48.8534, 2.3488, "Europe/Paris", "France", "Île-de-France", 2138551)


# --- Helpers -------------------------------------------------------------------

@pytest.mark.parametrize("code, is_day, icon", [
    (0, 1, "☀️"), (0, 0, "🌙"), (3, 1, "☁️"), (45, 1, "🌫️"), (63, 1, "🌧️"),
    (75, 1, "🌨️"), (95, 0, "⛈️"), (42, 1, "❔"), (None, 1, "❔"), (np.nan, 1, "❔"),
])
def test_weather_icon(code, is_day, icon):
    assert ui.weather_icon(code, is_day) == icon


@pytest.mark.parametrize("deg, label", [(0, "N"), (11, "N"), (12, "NNE"), (90, "E"), (225, "SW"),
                                        (359, "N"), (360, "N"), (None, "—")])
def test_compass(deg, label):
    assert ui.compass(deg) == label


def test_fmt_never_turns_missing_into_zero():
    assert ui.fmt(None, "°") == "—" and ui.fmt(np.nan, " km/h") == "—"
    assert ui.fmt(0, "%") == "0%" and ui.fmt(21.46, " °C", 1) == "21.5 °C"


def test_palette_switches_and_defaults_to_light():
    assert ui.palette("dark")["surface"] == "#0b1630"
    assert ui.palette("light") == ui.palette(None) == ui.palette("unknown")


def test_charts_use_provider_and_cloudzy_colours_consistently():
    f = parse_forecast(payload(), DELHI, "now")
    colors = ui.palette("light")
    temp = ui.temperature_chart(f.hourly.iloc[:48], colors)
    assert temp.data[0].line.color == colors["provider"]
    assert ui.rain_chart(f.hourly.iloc[:48], colors).layout.yaxis.range == (0, 100)
    preds = [{"horizon_h": 1, "valid_time_local": "2026-10-19T13:00:00+05:30", "temperature_2m_c": 28.0}]
    prov = [{"horizon_h": 1, "valid_time_local": "2026-10-19T13:00:00+05:30", "temperature_2m_c": 28.5}]
    cz = ui.cloudzy_chart(preds, prov, colors)
    assert {t.name: t.line.color for t in cz.data} == {"Provider (ECMWF IFS)": colors["provider"],
                                                       "Cloudzy Core": colors["cloudzy"]}


def test_daily_chart_handles_missing_values():
    daily = parse_forecast(payload(), DELHI, "now").daily.copy()
    daily.iloc[2, daily.columns.get_loc("temperature_2m_max")] = np.nan
    fig = ui.daily_range_chart(daily, ui.palette("dark"))
    assert "—" in list(fig.data[1].text)


def test_tables_have_expected_columns():
    f = parse_forecast(payload(), DELHI, "now")
    t = ui.hourly_table(f.hourly.iloc[:5])
    assert list(t.columns)[:3] == ["Time", "", "Temp °C"] and len(t) == 5
    preds = [{"horizon_h": 3, "valid_time_local": "2026-10-19T15:00:00+05:30", "temperature_2m_c": 29.04,
              "typical_error_c": 1.154}]
    prov = [{"horizon_h": 3, "valid_time_local": "2026-10-19T15:00:00+05:30", "temperature_2m_c": 29.1}]
    row = ui.cloudzy_table(preds, prov).iloc[0]
    assert row["Cloudzy °C"] == 29.0 and row["Provider °C"] == 29.1 and row["Cloudzy avg error °C (2025)"] == 1.15


# --- App states ------------------------------------------------------------------

def today_payload():
    start = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%dT00:00")
    return payload(start=start)


@pytest.fixture
def fake_network(monkeypatch):
    """Default: search finds Delhi and Paris, forecasts parse a synthetic payload, Cloudzy works."""
    st.cache_data.clear()
    state = {"places": [DELHI, PARIS], "payload": today_payload(), "search_error": None,
             "forecast_error": None,
             "cloudzy": {"available": True, "prediction_point": "New Delhi, India training grid cell",
                         "distance_km_from_place": 3.0, "issue_time_local": "2026-10-19T12:00:00+05:30",
                         "cloudzy": {"predictions": [
                             {"horizon_h": 1, "valid_time_local": "2026-10-19T13:00:00+05:30",
                              "temperature_2m_c": 28.0, "typical_error_c": 1.1}]},
                         "provider": {"forecasts": [
                             {"horizon_h": 1, "valid_time_local": "2026-10-19T13:00:00+05:30",
                              "temperature_2m_c": 28.5}]}}}

    def search(query, count=5, session=None):
        if state["search_error"]:
            raise state["search_error"]
        return [p for p in state["places"] if query.lower() in p.name.lower()]

    def forecast(place, days=7, session=None):
        if state["forecast_error"]:
            raise state["forecast_error"]
        return parse_forecast(state["payload"], place, "now")

    monkeypatch.setattr(src.provider, "search_locations", search)
    monkeypatch.setattr(src.provider, "fetch_forecast", forecast)
    monkeypatch.setattr(src.briefing, "cloudzy_block", lambda place, session=None, snapshot=True: state["cloudzy"])
    yield state
    st.cache_data.clear()


def run_app(query=None):
    at = AppTest.from_file("../streamlit_app.py", default_timeout=30)
    at.run()
    if query is not None:
        at.text_input[0].set_value(query).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def texts(elements):
    return " ".join(str(e.value) for e in elements)


def test_happy_path_delhi_shows_provider_and_cloudzy(fake_network):
    at = run_app()
    assert [m.label for m in at.metric][:3] == ["Feels like", "Humidity", "Cloud cover"]
    assert "No rain expected" in texts(at.success)
    assert [t.label for t in at.tabs] == ["Next 48 hours", "7-day outlook", "Cloudzy Core 🧪"]
    assert len(at.dataframe) == 2  # hourly table + Cloudzy table
    assert "not a station reading" in texts(at.caption)
    assert "Open-Meteo" in texts(at.caption)


def test_too_short_query_shows_hint(fake_network):
    at = run_app(" a ")
    assert "at least two letters" in texts(at.info)
    assert not at.metric


def test_no_matches_shows_empty_state(fake_network):
    at = run_app("Atlantis")
    assert "No places match" in texts(at.warning)
    assert not at.metric


def test_search_outage_shows_error_and_retry(fake_network):
    fake_network["search_error"] = ProviderError("HTTP 503: overloaded")
    at = run_app()
    assert "Location search is unavailable" in texts(at.error)
    assert any(b.label == "Try again" for b in at.button)


def test_forecast_outage_shows_error_and_retry(fake_network):
    fake_network["forecast_error"] = ProviderError("timed out")
    at = run_app()
    assert "couldn't get the forecast" in texts(at.error)
    assert any(b.label == "Try again" for b in at.button)


def test_far_place_explains_cloudzy_is_delhi_only(fake_network):
    at = run_app("Paris")
    assert "trained only for" in texts(at.info)
    assert at.tabs[2].label == "Cloudzy Core"
    assert len(at.dataframe) == 1  # hourly table only


def test_cloudzy_failure_keeps_provider_forecast(fake_network):
    fake_network["cloudzy"] = {"available": False, "reason": "Live inputs could not be used: HTTP 503"}
    at = run_app()
    assert "Cloudzy Core is unavailable right now" in texts(at.warning)
    assert len(at.metric) == 6


def test_rain_expected_banner(fake_network):
    p = fake_network["payload"]
    now_local = pd.Timestamp.now(tz="Asia/Kolkata")
    idx = int(now_local.hour) + 2  # two hours from now
    p["hourly"]["precipitation_probability"][idx] = 80
    at = run_app()
    assert "Rain likely from" in texts(at.warning)


def test_data_warnings_are_disclosed(fake_network):
    fake_network["payload"]["daily"]["uv_index_max"] = [None] * 7
    at = run_app()
    assert any("Data notes" in e.label for e in at.expander)
