"""Tests for Phase D: UI helpers and the Streamlit app's states (network faked)."""

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import src.air_quality
import src.briefing
import src.provider
from src import ui
from src.provider import Place, ProviderError, parse_forecast
from tests.aq_fixtures import aq_today_payload
from tests.test_provider import payload

DELHI = Place("New Delhi", 28.6358, 77.2244, "Asia/Kolkata", "India", "National Capital Territory of Delhi", 317797, "IN")
PARIS = Place("Paris", 48.8534, 2.3488, "Europe/Paris", "France", "Île-de-France", 2138551, "FR")
PARIS_TX = Place("Paris", 33.6609, -95.5555, "America/Chicago", "United States", "Texas", 24782, "US")


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


def test_palette_is_theme_independent():
    # Streamlit doesn't rerun on a theme switch, so series colours must suit both themes.
    assert ui.palette("dark") == ui.palette("light") == ui.palette()
    assert ui.palette()["provider"] != ui.palette()["cloudzy"]


def test_charts_use_provider_and_cloudzy_colours_consistently():
    f = parse_forecast(payload(), DELHI, "now")
    colors = ui.palette("light")
    temp = ui.temperature_chart(f.hourly.iloc[:48], colors)
    assert temp.data[0].line.color == colors["provider"]
    assert ui.rain_chart(f.hourly.iloc[:48], colors).layout.yaxis.range == (0, 100)
    preds = [{"horizon_h": 1, "valid_time_local": "2026-10-19T13:00:00+05:30", "temperature_2m_c": 28.0}]
    prov = [{"horizon_h": 1, "valid_time_local": "2026-10-19T13:00:00+05:30", "temperature_2m_c": 28.5}]
    cz = ui.cloudzy_chart(preds, prov, colors)
    assert {t.name: t.line.color for t in cz.data} == {"ECMWF IFS (Cloudzy's input)": colors["provider"],
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
    assert row["Cloudzy °C"] == 29.0 and row["ECMWF IFS °C"] == 29.1
    assert row["Cloudzy avg error in 2025 test °C"] == 1.15
    assert "Temp °F" in ui.hourly_table(f.hourly.iloc[:2], "°F", "mph", "in").columns


# --- App states ------------------------------------------------------------------

def today_payload():
    start = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%dT00:00")
    return payload(start=start)


@pytest.fixture
def fake_network(monkeypatch, tmp_path):
    """Default: search finds Delhi and Paris, forecasts parse a synthetic payload, Cloudzy works."""
    st.cache_data.clear()
    monkeypatch.setenv("CLOUDZY_PREFS_PATH", str(tmp_path / "prefs.json"))  # never touch real preferences
    state = {"places": [DELHI, PARIS, PARIS_TX], "payload": today_payload(), "search_error": None,
             "forecast_error": None, "aq_payload": aq_today_payload(), "aq_error": None,
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

    def air(place, session=None):
        if state["aq_error"]:
            raise state["aq_error"]
        return src.air_quality.parse_air_quality(state["aq_payload"], place, pd.Timestamp.now(tz="UTC").isoformat())

    monkeypatch.setattr(src.air_quality, "fetch_air_quality", air)
    monkeypatch.setattr(src.provider, "search_locations", search)
    monkeypatch.setattr(src.provider, "fetch_forecast", forecast)
    monkeypatch.setattr(src.briefing, "cloudzy_block", lambda place, session=None, snapshot=True: state["cloudzy"])
    yield state
    st.cache_data.clear()


def run_app(query=None):
    at = AppTest.from_file("../app_pages/weather.py", default_timeout=30)
    at.run()
    if query is not None:
        at.text_input[0].set_value(query).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def texts(elements):
    return " ".join(str(e.value) for e in elements)


def html_text(at) -> str:
    """All HTML rendered by st.html (hero, hourly cards, 7-day list, detail cards)."""
    return " ".join(h.proto.body for h in at.html)


NUMBER_RE = __import__("re").compile(r"\d+°")


def assert_no_weather_numbers(at):
    """Error and empty states must not show any temperatures (stale or invented)."""
    assert not NUMBER_RE.search(html_text(at)), "temperature shown in an error/empty state"
    assert not at.dataframe


def test_happy_path_delhi_shows_full_app(fake_network):
    at = run_app()
    body = html_text(at)
    assert "cz-hero" in body and "28°" in body                        # current temperature from the payload
    assert body.count('class="cz-hour') == 24 and ">Now<" in body     # 24 hourly cards
    assert body.count('class="cz-day"') == 7 and "Today" in body      # 7-day list
    for title in ("UV index", "Humidity", "Wind", "Feels like", "Pressure", "Visibility", "Sunrise · Sunset"):
        assert f'aria-label="{title}"' in body
    assert "No rain expected" in texts(at.success)
    assert len(at.dataframe) == 3  # hourly table + daily details + Cloudzy table
    assert "Cloudzy says" in body and "Daily briefing" in body
    assert "location_on" in body and "Feels like" in body             # hero with its icons (mascot is the sidebar logo)
    assert "not a station reading" in texts(at.caption)
    assert "Open-Meteo" in texts(at.caption)
    assert dict(at.query_params) == {"q": ["New Delhi"], "i": ["0"]} or at.query_params["q"] == "New Delhi"


def test_units_switch_converts_every_temperature(fake_network):
    at = run_app()
    at.segmented_control(key="unit_temp").set_value("°F").run()
    assert not at.exception
    body = html_text(at)
    assert "83°" in body          # 28.1 °C current temperature
    assert ">28°<" not in body    # no Celsius value left in the hero
    at.segmented_control(key="unit_wind").set_value("mph").run()
    assert "4 mph" in html_text(at)  # 7.0 km/h


def test_location_change_without_restart(fake_network):
    at = run_app()
    at.text_input[0].set_value("Paris").run()
    assert not at.exception
    assert "Paris, Île-de-France, France" in html_text(at)
    assert at.query_params["q"] == "Paris" or at.query_params["q"] == ["Paris"]


def test_too_short_query_shows_hint(fake_network):
    at = run_app(" a ")
    assert "at least two letters" in texts(at.info)
    assert_no_weather_numbers(at)


def test_no_matches_shows_empty_state(fake_network):
    at = run_app("Atlantis")
    assert "No places match" in texts(at.warning)
    assert_no_weather_numbers(at)


def test_search_outage_shows_error_and_retry(fake_network):
    fake_network["search_error"] = ProviderError("HTTP 503: overloaded")
    at = run_app()
    assert "Location search is unavailable" in texts(at.error)
    assert any(b.label == "Try again" for b in at.button)
    assert_no_weather_numbers(at)


def test_forecast_outage_shows_error_and_retry(fake_network):
    fake_network["forecast_error"] = ProviderError("timed out")
    at = run_app()
    assert "couldn't get the forecast" in texts(at.error)
    assert any(b.label == "Try again" for b in at.button)
    assert_no_weather_numbers(at)


def test_recovery_after_outage(fake_network):
    fake_network["forecast_error"] = ProviderError("timed out")
    at = run_app()
    assert at.error
    fake_network["forecast_error"] = None
    next(b for b in at.button if b.label == "Try again").click().run()
    assert not at.exception and not at.error
    assert "cz-hero" in html_text(at)


def test_far_place_explains_cloudzy_is_delhi_only(fake_network):
    at = run_app("Paris")
    assert "trained and tested only for" in texts(at.info)
    assert len(at.dataframe) == 2  # hourly + daily tables only: no Cloudzy numbers for Paris


def test_cloudzy_failure_keeps_provider_forecast(fake_network):
    fake_network["cloudzy"] = {"available": False, "reason": "Live inputs could not be used: HTTP 503."}
    at = run_app()
    assert "No Cloudzy prediction right now" in texts(at.warning)
    assert "cz-hero" in html_text(at)
    assert len(at.dataframe) == 2  # no Cloudzy table


def test_cloudzy_values_follow_units(fake_network):
    at = run_app()
    at.segmented_control(key="unit_temp").set_value("°F").run()
    table = at.dataframe[2].value
    assert table["Cloudzy °F"].iloc[0] == pytest.approx(82.4)                       # 28.0 °C
    assert table["Cloudzy avg error in 2025 test °F"].iloc[0] == pytest.approx(1.98)  # 1.1 °C difference


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


# --- Phase 2-6 features -----------------------------------------------------------------

def test_same_name_places_are_distinguished_and_units_follow_region(fake_network):
    at = run_app("Paris")
    labels = at.selectbox[0].options
    assert any("Île-de-France, France" in o for o in labels) and any("Texas, United States" in o for o in labels)
    assert at.session_state["unit_temp"] == "°C"
    at.selectbox[0].select_index(1).run()
    assert not at.exception
    assert at.session_state["unit_temp"] == "°F" and at.session_state["unit_wind"] == "mph"
    assert "Paris, Texas, United States" in html_text(at)


def test_unit_override_is_saved_and_reused(fake_network, tmp_path):
    from src import preferences as P
    at = run_app()
    at.segmented_control(key="unit_temp").set_value("°F").run()
    assert P.load().unit_override().temperature == "°F"
    again = run_app()  # a fresh session reads the saved choice
    assert again.session_state["unit_temp"] == "°F"
    assert ">83°<" in html_text(again)


def test_favourite_save_and_pick(fake_network):
    from src import preferences as P
    at = run_app("Paris")
    next(b for b in at.button if b.label == "Save").click().run()
    assert [p.label for p in P.load().favourite_places()] == ["Paris, Île-de-France, France"]
    assert any(b.label == "Saved" for b in at.button)
    at.text_input[0].set_value("New Delhi").run()
    at.pills[0].set_value("Paris, Île-de-France, France").run()
    assert not at.exception
    assert at.session_state["search"] == "Paris"
    assert "Paris, Île-de-France, France" in html_text(at)


def test_alerts_render_with_reason_period_and_uncertainty(fake_network):
    p = fake_network["payload"]
    n = len(p["hourly"]["time"])
    p["hourly"]["apparent_temperature"] = [44.0] * n   # very hot all day
    at = run_app()
    body = html_text(at)
    assert "Very hot" in body and "When:" in body and "Why:" in body and "Uncertainty:" in body
    assert "not an official weather warning" in body


def test_error_state_shows_no_summary_or_alerts(fake_network):
    fake_network["forecast_error"] = ProviderError("timed out")
    at = run_app()
    body = html_text(at)
    assert "Cloudzy says" not in body and "cz-alert cz-fade" not in body and "Daily briefing" not in body
