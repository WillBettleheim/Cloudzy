"""Tests for unit conversion, optional API fields, time zones and the weather page HTML."""

import math
import re

import numpy as np
import pandas as pd
import pytest

from src import units as U
from src import weather_view as V
from src.provider import ProviderError, describe_weather, parse_forecast, upcoming
from tests.test_provider import DELHI, payload

NY = __import__("src.provider", fromlist=["Place"]).Place("New York", 40.71, -74.01, "America/New_York", "United States", "New York")


# --- Units ---------------------------------------------------------------------------

@pytest.mark.parametrize("c, f", [(0, 32), (100, 212), (-40, -40), (37, 98.6)])
def test_temperature_conversion(c, f):
    assert U.temperature(c, "°F") == pytest.approx(f)
    assert U.temperature(c, "°C") == c


def test_temperature_difference_has_no_offset():
    assert U.temperature_delta(1.0, "°F") == pytest.approx(1.8)
    assert U.temperature_delta(1.0, "°C") == 1.0


@pytest.mark.parametrize("unit, expected", [("km/h", 36), ("m/s", 10), ("mph", 22.369), ("kn", 19.438)])
def test_wind_conversion(unit, expected):
    assert U.wind(36, unit) == pytest.approx(expected, abs=0.001)


def test_precipitation_and_distance():
    assert U.precipitation(25.4, "in") == pytest.approx(1.0)
    assert U.distance(1609.344, "mi") == pytest.approx(1.0)
    assert U.distance(9940, "km") == pytest.approx(9.94)


@pytest.mark.parametrize("fn, unit", [(U.temperature, "°F"), (U.temperature_delta, "°F"), (U.wind, "mph"),
                                      (U.precipitation, "in"), (U.distance, "mi")])
def test_missing_stays_missing(fn, unit):
    assert fn(None, unit) is None
    assert fn(float("nan"), unit) is None


def test_units_validation_and_presets():
    with pytest.raises(ValueError):
        U.Units("K")
    assert U.IMPERIAL.distance == "mi" and U.METRIC.distance == "km"


def test_convert_frame_only_touches_unit_columns():
    df = pd.DataFrame({"temperature_2m": [0.0, np.nan], "wind_speed_10m": [36.0, 3.6],
                       "precipitation": [25.4, 0.0], "relative_humidity_2m": [50, 60]})
    out = U.convert_frame(df, U.Units("°F", "m/s", "in"))
    assert out["temperature_2m"].iloc[0] == 32 and math.isnan(out["temperature_2m"].iloc[1])
    assert list(out["wind_speed_10m"]) == pytest.approx([10, 1])
    assert out["precipitation"].iloc[0] == pytest.approx(1.0)
    assert list(out["relative_humidity_2m"]) == [50, 60]
    assert df["temperature_2m"].iloc[0] == 0  # input untouched


# --- Optional and missing API fields ---------------------------------------------------

def test_optional_variable_unavailable_is_reported_not_fatal():
    p = payload()
    p["hourly"]["uv_index"] = [None] * len(p["hourly"]["time"])
    p["hourly_units"]["uv_index"] = "undefined"
    p["current"]["visibility"] = None
    p["current_units"]["visibility"] = "undefined"
    f = parse_forecast(p, DELHI, "now")
    assert f.hourly["uv_index"].isna().all()
    assert f.current["visibility"] is None
    assert any("uv_index: not available" in w for w in f.warnings)


def test_optional_variable_missing_entirely_is_tolerated():
    p = payload()
    del p["daily"]["daylight_duration"]
    del p["daily_units"]["daylight_duration"]
    f = parse_forecast(p, DELHI, "now")
    assert f.daily["daylight_duration"].isna().all()


def test_wrong_unit_with_data_is_still_fatal():
    p = payload()
    p["hourly_units"]["wind_gusts_10m"] = "mph"  # values present: converting would be a guess
    with pytest.raises(ProviderError, match="wind_gusts_10m"):
        parse_forecast(p, DELHI, "now")


def test_required_variable_cannot_be_unavailable():
    p = payload()
    p["current"]["temperature_2m"] = None
    p["current_units"]["temperature_2m"] = "undefined"
    with pytest.raises(ProviderError, match="temperature_2m"):
        parse_forecast(p, DELHI, "now")


def test_documented_wmo_code_97():
    assert describe_weather(97) == "Heavy thunderstorm"


# --- Time zones -----------------------------------------------------------------------

def test_hourly_cards_use_the_selected_places_local_time():
    p = payload(tz="America/New_York", offset=-14400, start="2026-10-19T00:00", days=2)
    f = parse_forecast(p, NY, "now")
    now = pd.Timestamp("2026-10-19T14:20Z")  # 10:20 in New York (EDT)
    hours = upcoming(f, now, 3)
    assert hours.index[0] == pd.Timestamp("2026-10-19T10:00", tz="America/New_York")
    html = V.hourly_strip_html(hours, U.METRIC)
    assert ">Now<" in html and ">11:00<" in html and ">12:00<" in html


def test_updated_label_handles_zones_and_bad_values():
    assert V.updated_label("2026-10-19T07:38:00+00:00", "Asia/Kolkata") == "updated 13:08 local time"
    assert V.updated_label("2026-10-19T07:38:00", "Asia/Kolkata") == "updated 13:08 local time"  # naive = UTC
    assert V.updated_label("now-ish", "Asia/Kolkata") == "update time unknown"
    assert V.updated_label(None, "Asia/Kolkata") == "update time unknown"


# --- Rendering ------------------------------------------------------------------------

@pytest.fixture
def fc():
    return parse_forecast(payload(), DELHI, "now")


def test_hero_shows_real_values_in_chosen_units(fc):
    html = V.hero_html(DELHI.label, fc.current, fc.daily.iloc[0], U.IMPERIAL, "IST")
    assert ">83°<" in html                       # 28.1 °C
    assert "Feels like 87°" in html              # 30.4 °C
    assert "Clear sky" in html and 'aria-label="Clear sky"' in html
    assert "cz-hero day" in html


def test_hero_night_mode_and_missing_temperature(fc):
    cur = {**fc.current, "is_day": 0, "temperature_2m": None}
    html = V.hero_html(DELHI.label, cur, None, U.METRIC, "IST")
    assert "cz-hero night" in html
    assert '<div class="temp">—</div>' in html   # never a fake 0°
    assert "H — · L —" in html


def test_place_names_are_escaped(fc):
    html = V.hero_html('<script>alert("x")</script>', fc.current, fc.daily.iloc[0], U.METRIC, "IST")
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_hourly_strip_is_keyboard_scrollable_and_labelled(fc):
    html = V.hourly_strip_html(fc.hourly.iloc[:24], U.METRIC)
    assert html.count('class="cz-hour') == 24
    assert 'tabindex="0"' in html and 'aria-label="Hourly forecast for the next 24 hours' in html
    assert 'aria-label="Now: 20°, Clear sky, chance of rain 0%, wind 5 km/h from S"' in html
    assert ">air</span>" in html and "5 km/h" in html          # wind icon (Material Symbols), then value


def test_daily_list_today_and_missing_values(fc):
    daily = fc.daily.copy()
    daily.iloc[3, daily.columns.get_loc("temperature_2m_max")] = np.nan
    html = V.daily_list_html(daily, U.METRIC, daily.index[0])
    assert html.count('class="cz-day"') == 7 and ">Today<" in html
    assert html.count('<div class="cz-bar" aria-hidden="true"></div>') == 1  # no bar drawn for the gap
    assert '<span class="hi">—</span>' in html


def test_daily_rain_chance_shown_only_when_meaningful(fc):
    daily = fc.daily.copy()
    daily["precipitation_probability_max"] = [80, 5, 0, 0, 0, 0, 0]
    html = V.daily_list_html(daily, U.METRIC, daily.index[0])
    assert "80%" in html and "5%" not in html


def test_detail_cards_with_all_values(fc):
    html = V.detail_cards_html(fc.current, fc.daily.iloc[0], U.Units("°C", "km/h", "mm"))
    assert ">5<" in html and "Moderate" in html          # UV 5.2
    assert "45%" not in html and "50%" in html            # humidity from payload
    assert "7 km/h" in html and "From NW" in html
    assert "1015 hPa" in html and "9.9 km" in html
    assert "11 h 23 min of daylight" in html


def test_detail_cards_never_invent_missing_values(fc):
    cur = {k: None for k in fc.current}
    cur["time"] = fc.current["time"]
    html = V.detail_cards_html(cur, None, U.IMPERIAL)
    assert "nan" not in html.lower()
    assert "—%" not in html and "— mph" not in html and "— hPa" not in html
    assert not re.search(r">\s*0(\.0)?\s*(%|mph|hPa|mi|in)?<", html)  # no zeros standing in for missing
    assert "Not available here" in html                                # UV


@pytest.mark.parametrize("uv, label", [(0, "Low"), (2.9, "Low"), (3, "Moderate"), (6, "High"), (8, "Very high"),
                                       (11, "Extreme"), (None, "Not available here")])
def test_uv_categories(uv, label):
    assert V.uv_category(uv) == label
