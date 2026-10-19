"""Tests for Phase C: provider forecast parsing, location search, rain outlook, briefing."""

import copy

import pandas as pd
import pytest

from src import briefing, provider
from src.provider import (
    CURRENT_UNITS, DAILY_UNITS, HOURLY_UNITS, Place, ProviderError, parse_forecast, rain_outlook,
    search_locations, upcoming,
)

DELHI = Place("Delhi", 28.65195, 77.23149, "Asia/Kolkata", "India", "National Capital Territory of Delhi")
REYKJAVIK = Place("Reykjavik", 64.1355, -21.8954, "Atlantic/Reykjavik", "Iceland")


def payload(tz="Asia/Kolkata", offset=19800, start="2026-10-19T00:00", days=7, daily_epochs=None):
    """Forecast API payload in timeformat=unixtime, as the real API returns it."""
    local_start = pd.Timestamp(start).tz_localize(tz)
    hours = pd.date_range(local_start, periods=24 * days, freq="h")
    epochs = (hours.tz_convert("UTC") - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1s")
    n = len(hours)
    if daily_epochs is None:
        # The API builds daily instants from local midnight using one fixed offset.
        day0 = pd.Timestamp(start[:10], tz="UTC") - pd.Timedelta(seconds=offset)
        daily_epochs = [int((day0 + pd.Timedelta(days=i) - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds())
                        for i in range(days)]
    m = len(daily_epochs)
    current = {"time": int(epochs[12]), "interval": 900, **{v: CURRENT_DEFAULTS[v] for v in CURRENT_UNITS}}
    hourly = {"time": [int(e) for e in epochs],
              **{v: [HOURLY_DEFAULTS[v]] * n for v in HOURLY_UNITS}}
    hourly["temperature_2m"] = [20.0 + (i % 24) / 2 for i in range(n)]
    daily = {"time": daily_epochs, **{v: [DAILY_DEFAULTS[v]] * m for v in DAILY_UNITS if v not in ("sunrise", "sunset")}}
    daily["weather_code"] = [61] + [0] * (m - 1)
    daily["sunrise"] = [e + 6 * 3600 for e in daily_epochs]
    daily["sunset"] = [e + 18 * 3600 for e in daily_epochs]
    return {
        "latitude": 28.58, "longitude": 77.19, "elevation": 214.0, "timezone": tz, "utc_offset_seconds": offset,
        "current_units": {"time": "unixtime", "interval": "seconds", **CURRENT_UNITS}, "current": current,
        "hourly_units": {"time": "unixtime", **HOURLY_UNITS}, "hourly": hourly,
        "daily_units": {"time": "unixtime", **DAILY_UNITS}, "daily": daily,
    }


# Realistic defaults for every requested variable (new variables must be added here).
CURRENT_DEFAULTS = {"temperature_2m": 28.1, "relative_humidity_2m": 50, "apparent_temperature": 30.4, "is_day": 1,
                    "precipitation": 0.0, "weather_code": 0, "cloud_cover": 0, "wind_speed_10m": 7.0,
                    "wind_direction_10m": 312, "wind_gusts_10m": 20.2, "uv_index": 5.2, "pressure_msl": 1015.3,
                    "visibility": 9940.0, "dew_point_2m": 16.1}
HOURLY_DEFAULTS = {"temperature_2m": 20.0, "relative_humidity_2m": 50, "apparent_temperature": 21.0,
                   "precipitation_probability": 0, "precipitation": 0.0, "weather_code": 0, "wind_speed_10m": 5.0,
                   "wind_direction_10m": 180, "is_day": 1, "uv_index": 3.0, "cloud_cover": 20, "wind_gusts_10m": 12.0}
DAILY_DEFAULTS = {"weather_code": 0, "temperature_2m_max": 30.0, "temperature_2m_min": 19.0, "precipitation_sum": 0.0,
                  "precipitation_probability_max": 0, "wind_speed_10m_max": 9.0, "uv_index_max": 5.0,
                  "wind_direction_10m_dominant": 300, "daylight_duration": 41_000.0}


NOW = pd.Timestamp("2026-10-19T06:40Z")  # 12:10 IST


# --- Parsing -------------------------------------------------------------------

def test_parse_produces_local_hourly_and_daily_tables():
    f = parse_forecast(payload(), DELHI, "now")
    assert len(f.hourly) == 168 and len(f.daily) == 7
    assert str(f.hourly.index.tz) == "Asia/Kolkata"
    assert f.hourly.index[0] == pd.Timestamp("2026-10-19T00:00", tz="Asia/Kolkata")
    assert str(f.daily.index[0]) == "2026-10-19"
    assert f.daily["description"].iloc[0] == "Slight rain"
    assert f.current["description"] == "Clear sky"
    assert f.current["time"] == "2026-10-19T12:00:00+05:30"
    assert f.warnings == []


def test_daylight_saving_change_keeps_hours_and_daily_dates_correct():
    # New York, clocks go back on 2026-11-01. Daily instants use the fixed offset
    # in effect at request time (-4 h), exactly as the real API does.
    p = payload(tz="America/New_York", offset=-14400, start="2026-10-31T00:00", days=3)
    f = parse_forecast(p, Place("New York", 40.71, -74.01, "America/New_York"), "now")
    assert f.hourly.index.is_unique
    assert ((f.hourly.index[1:] - f.hourly.index[:-1]) == pd.Timedelta("1h")).all()
    assert [str(d) for d in f.daily.index] == ["2026-10-31", "2026-11-01", "2026-11-02"]
    one_am = [t for t in f.hourly.index if t.strftime("%m-%d %H") == "11-01 01"]
    assert len(one_am) == 2  # the repeated hour appears twice, with different UTC offsets


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.update(error=True, reason="Latitude must be in range"), "Latitude"),
    (lambda p: p.pop("daily"), "no 'daily'"),
    (lambda p: p["hourly_units"].update(temperature_2m="°F"), "°F"),
    (lambda p: p["hourly_units"].update(time="iso8601"), "unixtime"),
    (lambda p: p["hourly"]["precipitation"].pop(), "length"),
    (lambda p: p["hourly"]["time"].reverse(), "consecutive"),
    (lambda p: p["hourly"].update(temperature_2m=[None] * 168), "entirely missing"),
    (lambda p: p.pop("utc_offset_seconds"), "utc_offset"),
])
def test_bad_payloads_are_rejected(mutate, message):
    p = payload()
    mutate(p)
    with pytest.raises(ProviderError, match=message):
        parse_forecast(p, DELHI, "now")


def test_implausible_and_missing_values_are_removed_and_reported():
    p = payload()
    p["hourly"]["temperature_2m"][5] = 99.0
    p["hourly"]["precipitation_probability"][6] = None
    p["daily"]["uv_index_max"] = [None] * 7
    p["current"]["relative_humidity_2m"] = 140
    f = parse_forecast(p, DELHI, "now")
    assert pd.isna(f.hourly["temperature_2m"].iloc[5])
    assert f.current["relative_humidity_2m"] is None
    joined = " | ".join(f.warnings)
    assert "hourly.temperature_2m: 1 implausible" in joined
    assert "hourly.precipitation_probability: 1 of 168" in joined
    assert "daily.uv_index_max: 7 of 7" in joined
    assert "current.relative_humidity_2m" in joined


def test_unknown_weather_code_is_labelled_not_guessed():
    assert provider.describe_weather(42) == "Unknown (WMO 42)"
    assert provider.describe_weather(None) == "Unknown"


def test_to_dict_is_json_ready():
    import json
    d = parse_forecast(payload(), DELHI, "now").to_dict()
    json.dumps(d)
    assert d["source"] == provider.SOURCE_LABEL and "Open-Meteo" in d["attribution"]
    assert d["hourly"][0]["time"] == "2026-10-19T00:00:00+05:30"
    assert str(d["daily"][0]["date"]) == "2026-10-19"


# --- Upcoming hours and rain outlook ------------------------------------------------

def test_upcoming_starts_at_current_local_hour():
    f = parse_forecast(payload(), DELHI, "now")
    rows = upcoming(f, NOW, 5)
    assert rows.index[0] == pd.Timestamp("2026-10-19T12:00", tz="Asia/Kolkata") and len(rows) == 5


def test_rain_outlook_flags_probability_or_amount():
    p = payload()
    p["hourly"]["precipitation_probability"][15] = 60  # 15:00 IST
    p["hourly"]["precipitation"][20] = 0.8             # 20:00 IST
    p["hourly"]["precipitation"][3] = 5.0              # 03:00 IST: in the past, ignored
    r = rain_outlook(parse_forecast(p, DELHI, "now"), NOW, hours=24)
    assert r["rain_expected"] and r["rainy_hours"] == 2
    assert r["first_rain_hour_local"] == "2026-10-19T15:00:00+05:30"
    assert "not an official" in r["label"]


def test_rain_outlook_below_thresholds_is_not_flagged():
    p = payload()
    p["hourly"]["precipitation_probability"][15] = 49
    p["hourly"]["precipitation"][16] = 0.4
    r = rain_outlook(parse_forecast(p, DELHI, "now"), NOW)
    assert not r["rain_expected"] and r["first_rain_hour_local"] is None


# --- Location search --------------------------------------------------------------

class FakeSession:
    def __init__(self, body, status=200):
        self.body, self.status, self.params = body, status, None

    def get(self, url, params=None, timeout=None):
        self.params = params
        body, status = self.body, self.status

        class R:
            status_code = status
            text = ""

            def json(self):
                return body
        return R()


def test_search_parses_results_and_skips_malformed():
    body = {"results": [
        {"name": "Delhi", "latitude": 28.65, "longitude": 77.23, "timezone": "Asia/Kolkata",
         "country": "India", "admin1": "National Capital Territory of Delhi", "population": 11034555},
        {"name": "Broken"},
    ]}
    places = search_locations("Delhi", session=FakeSession(body))
    assert len(places) == 1 and places[0].label == "Delhi, National Capital Territory of Delhi, India"


def test_search_with_no_match_returns_empty_list():
    assert search_locations("zzqqxx", session=FakeSession({"generationtime_ms": 0.1})) == []


def test_search_rejects_too_short_query():
    with pytest.raises(ValueError, match="at least 2"):
        search_locations(" D ")


# --- Briefing ------------------------------------------------------------------

def test_distance_km_known_value():
    # Delhi to Mumbai is about 1,150 km great-circle.
    assert briefing.distance_km(28.6139, 77.2090, 19.0760, 72.8777) == pytest.approx(1150, abs=15)


def stub_forecast(monkeypatch):
    monkeypatch.setattr(briefing, "fetch_forecast", lambda place, session=None: parse_forecast(payload(), place, "now"))


def test_briefing_far_away_shows_provider_but_no_cloudzy(monkeypatch):
    stub_forecast(monkeypatch)
    b = briefing.build_briefing(REYKJAVIK, now=NOW)
    assert b["provider"]["current"]["temperature_2m"] == 28.1
    assert b["cloudzy"]["available"] is False and "km away" in b["cloudzy"]["reason"]
    assert len(b["provider"]["hourly"]) == 24
    assert b["provider"]["hourly"][0]["time"] == "2026-10-19T12:00:00+05:30"


def test_briefing_near_delhi_labels_prediction_point(monkeypatch):
    stub_forecast(monkeypatch)
    import src.service
    fake = {"cloudzy": {"label": "Cloudzy Core ML prediction (experimental)", "predictions": []}}
    monkeypatch.setattr(src.service, "forecast_now", lambda snapshot=True, session=None: copy.deepcopy(fake))
    b = briefing.build_briefing(DELHI, now=NOW)
    c = b["cloudzy"]
    assert c["available"] and "training grid cell" in c["prediction_point"]
    assert c["distance_km_from_place"] == pytest.approx(4.8, abs=0.2)
    assert b["provider"]["source"] != c["cloudzy"]["label"]


def test_briefing_survives_cloudzy_failure(monkeypatch):
    stub_forecast(monkeypatch)
    import src.service

    def boom(snapshot=True, session=None):
        raise src.service.ServiceError("Need 25 complete hours")
    monkeypatch.setattr(src.service, "forecast_now", boom)
    b = briefing.build_briefing(DELHI, now=NOW)
    assert b["provider"]["current"]["temperature_2m"] == 28.1
    assert b["cloudzy"]["available"] is False and "25 complete hours" in b["cloudzy"]["reason"]


def test_place_just_outside_radius_gets_no_cloudzy(monkeypatch):
    stub_forecast(monkeypatch)
    gurugram = Place("Gurugram", 28.4595, 77.0266, "Asia/Kolkata", "India", "Haryana")
    b = briefing.build_briefing(gurugram, now=NOW)
    assert b["cloudzy"]["available"] is False
