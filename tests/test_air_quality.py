"""The Air Quality API client: validation, units, time zones, coverage, errors and readings (network mocked)."""

import numpy as np
import pandas as pd
import pytest
import requests

from src import air_quality as A
from src import air_view as AV
from src import aqi, ui
from src.provider import Place
from tests.aq_fixtures import aq_payload

DELHI = Place("New Delhi", 28.6358, 77.2244, "Asia/Kolkata", "India", "Delhi", 317797, "IN")
PARIS = Place("Paris", 48.8534, 2.3488, "Europe/Paris", "France", "Île-de-France", 2138551, "FR")
NOW = pd.Timestamp("2026-10-19T06:40Z")      # 12:10 IST, 2 days + 12 h into the default payload
REAL_FETCH = A.fetch_air_quality             # captured at import, before the offline block replaces it


def parsed(**kw):
    return A.parse_air_quality(aq_payload(**kw), DELHI, "2026-10-19T06:30:00+00:00")


# --- Parsing and validation ----------------------------------------------------------------------

def test_parses_hourly_table_in_local_time_with_all_three_indices():
    d = parsed()
    assert str(d.hourly.index.tz) == "Asia/Kolkata" and d.hourly.index[0].hour == 0
    assert {"pm2_5", "us_aqi", "european_aqi", "naqi", "naqi_open", "naqi_dominant"} <= set(d.hourly.columns)
    assert d.current["us_aqi"] == 68.0 and d.current["time"].endswith("+05:30")


def test_trailing_hours_after_the_model_ends_are_trimmed_and_reported_as_coverage():
    d = parsed(model_hours=24 * 7 + 5)
    assert len(d.hourly) == 24 * 7 + 5
    assert d.coverage_end == d.hourly.index[-1]
    assert d.forecast_hours(NOW).index[-1] == d.coverage_end


def test_gap_inside_the_forecast_stays_missing_and_is_reported():
    pm = [20.0] * 216
    for i in range(100, 103):
        pm[i] = None
    over = {v: pm for v in ("pm2_5", "pm10", "us_aqi", "european_aqi")}
    d = parsed(overrides=over)
    assert d.hourly["pm2_5"].iloc[100:103].isna().all()
    assert any("3 hour(s) inside the forecast have no values" in w for w in d.warnings)


def test_both_micro_sign_spellings_are_accepted():
    p = aq_payload()
    p["hourly_units"]["pm2_5"] = "µg/m³"      # U+00B5 instead of U+03BC
    assert A.parse_air_quality(p, DELHI, "now").hourly["pm2_5"].iloc[0] == 20.0


def test_unexpected_unit_is_an_error_not_a_guess():
    p = aq_payload()
    p["hourly_units"]["pm2_5"] = "ppb"
    with pytest.raises(A.AirQualityError, match="expected unit"):
        A.parse_air_quality(p, DELHI, "now")
    p = aq_payload()
    p["hourly_units"]["us_aqi"] = "EAQI"
    with pytest.raises(A.AirQualityError, match="us_aqi"):
        A.parse_air_quality(p, DELHI, "now")


def test_pollutant_missing_for_a_location_is_reported_not_filled():
    p = aq_payload()
    p["hourly"]["sulphur_dioxide"] = [None] * len(p["hourly"]["time"])
    p["hourly_units"]["sulphur_dioxide"] = "undefined"
    d = A.parse_air_quality(p, DELHI, "now")
    assert d.hourly["sulphur_dioxide"].isna().all()
    assert any("sulphur_dioxide: not available" in w for w in d.warnings)


def test_implausible_values_are_removed_and_counted():
    pm = [20.0] * 216
    pm[10] = -5.0
    pm[11] = 99999.0
    d = parsed(overrides={"pm2_5": pm})
    assert d.hourly["pm2_5"].iloc[10:12].isna().all()
    assert any("pm2_5: 2 implausible values removed" in w for w in d.warnings)


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.update(error=True, reason="Latitude must be in range"), "Latitude must be in range"),
    (lambda p: p.pop("hourly"), "no hourly data"),
    (lambda p: p["hourly_units"].update(time="iso8601"), "unixtime"),
    (lambda p: p["hourly"].update(time=[]), "no hourly timestamps"),
    (lambda p: p["hourly"]["pm2_5"].pop(), "length does not match"),
    (lambda p: p["hourly"]["time"].__setitem__(5, p["hourly"]["time"][4]), "consecutive hourly"),
])
def test_unusable_responses_raise_a_clear_error(mutate, message):
    p = aq_payload()
    mutate(p)
    with pytest.raises(A.AirQualityError, match=message):
        A.parse_air_quality(p, DELHI, "now")


def test_location_with_no_model_values_is_unsupported():
    p = aq_payload(model_hours=0)
    with pytest.raises(A.AirQualityError, match="no values for this location"):
        A.parse_air_quality(p, DELHI, "now")


def test_daylight_saving_change_keeps_hours_consecutive_and_local():
    # Paris leaves summer time on Sun 25 Oct 2026: 02:00 local happens twice.
    p = aq_payload(start="2026-10-24T00:00", tz="Europe/Paris", days=3, model_hours=72)
    d = A.parse_air_quality(p, PARIS, "now")
    idx = d.hourly.index
    assert len(idx) == 72 and ((idx[1:] - idx[:-1]) == pd.Timedelta("1h")).all()
    assert idx[0].utcoffset() == pd.Timedelta(hours=2) and idx[-1].utcoffset() == pd.Timedelta(hours=1)
    assert sum(t.hour == 2 and t.date().day == 25 for t in idx) == 2


# --- Network errors and rate limits --------------------------------------------------------------

class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers, self.text = status, body or {}, headers or {}, str(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def real_fetch(monkeypatch):
    """The real client with a fake session, so nothing leaves the machine."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    return REAL_FETCH


def test_request_asks_for_unixtime_local_zone_history_and_all_variables(real_fetch):
    s = FakeSession([FakeResponse(200, aq_payload())])
    real_fetch(DELHI, session=s)
    url, params = s.calls[0]
    assert url == A.AIR_QUALITY_URL and params["timeformat"] == "unixtime" and params["timezone"] == "auto"
    assert params["past_days"] == 2 and "us_aqi" in params["hourly"] and "european_aqi" in params["hourly"]
    assert "apikey" not in params                       # the free API needs no key; none is ever embedded


def test_rate_limit_with_long_retry_after_fails_fast_with_reason(real_fetch):
    s = FakeSession([FakeResponse(429, {"reason": "Too many requests"}, {"Retry-After": "3600"})])
    with pytest.raises(A.AirQualityError, match="Rate limited"):
        real_fetch(DELHI, session=s)
    assert len(s.calls) == 1


def test_server_error_then_success_is_retried(real_fetch):
    s = FakeSession([FakeResponse(503, {"reason": "busy"}), FakeResponse(200, aq_payload())])
    assert real_fetch(DELHI, session=s).current["us_aqi"] == 68.0


def test_timeouts_become_a_readable_error(real_fetch):
    s = FakeSession([requests.Timeout(), requests.Timeout()])
    with pytest.raises(A.AirQualityError, match="didn't respond"):
        real_fetch(DELHI, session=s)


# --- Readings ------------------------------------------------------------------------------------

def test_reading_names_its_standard_and_hour():
    d = parsed()
    r = A.reading(d, aqi.US_EPA, NOW)
    assert r.value == 68.0 and r.category.label == "Moderate" and r.dominant == "pm2_5"
    assert r.time == pd.Timestamp("2026-10-19T12:00", tz="Asia/Kolkata")
    eu = A.reading(d, aqi.EU_EAQI, NOW)
    assert eu.value == 38.0 and eu.category.label == "Fair"


def test_us_value_is_never_shown_as_india_naqi():
    d = parsed(overrides={"ozone": 30.0, "pm10": 30.0})
    us, naqi = A.reading(d, aqi.US_EPA, NOW), A.reading(d, aqi.IN_NAQI, NOW)
    # 20 μg/m³ PM2.5 is NAQI 33 (Good) but US AQI 68 (Moderate): different scales, separately computed.
    assert naqi.value == 33 and naqi.category.label == "Good" and naqi.dominant == "pm2_5" and us.value == 68
    assert naqi.standard is aqi.IN_NAQI and us.standard is aqi.US_EPA


def test_reading_outside_the_data_says_so():
    d = parsed()
    r = A.reading(d, aqi.US_EPA, pd.Timestamp("2027-01-01T00:00Z"))
    assert r.value is None and r.category is None and "don't cover" in r.note


def test_naqi_reading_explains_why_it_is_missing():
    d = parsed(overrides={"pm2_5": None, "pm10": None})
    r = A.reading(d, aqi.IN_NAQI, NOW)
    assert r.value is None and "PM2.5 and PM10" in r.note


def test_past_and_forecast_hours_never_overlap():
    d = parsed()
    past, future = d.past_hours(NOW), d.forecast_hours(NOW)
    assert past.index.max() < future.index.min()
    assert future.index.min() == pd.Timestamp("2026-10-19T12:00", tz="Asia/Kolkata")


def test_rolling_mean_needs_three_quarters_of_the_hours():
    pm = [10.0] * 216
    for i in range(55, 61):
        pm[i] = None
    d = parsed(overrides={"pm2_5": pm})
    assert A.rolling_mean(d, "pm2_5", 24, NOW) == pytest.approx(10.0)      # 18 of 24 present
    pm[37:55] = [None] * 18
    assert A.rolling_mean(parsed(overrides={"pm2_5": pm}), "pm2_5", 24, NOW) is None


def test_daily_summary_and_category_changes_follow_the_forecast():
    us = [40.0] * 70 + [120.0] * 30 + [60.0] * 116      # Good → USG → Moderate
    d = parsed(overrides={"us_aqi": us, "us_aqi_pm2_5": us})
    changes = A.category_changes(d, aqi.US_EPA, NOW)
    assert [(c["from"].label, c["to"].label, c["direction"]) for c in changes] == [
        ("Good", "Unhealthy for Sensitive Groups", "worse"), ("Unhealthy for Sensitive Groups", "Moderate", "better")]
    assert changes[0]["time"] == d.hourly.index[70]
    days = A.daily_summary(d, aqi.US_EPA, NOW)
    assert days.iloc[0]["hours"] == 12                                       # today from 12:00 only
    assert days.iloc[0]["low"] == 40 and days.iloc[0]["high"] == 120         # worsens at 22:00 today
    assert days.iloc[1]["worst_category"] == "Unhealthy for Sensitive Groups"


def test_current_factors_restate_values_and_claim_no_causes():
    us = [40.0] * 62 + [160.0] * 154
    d = parsed(overrides={"us_aqi": us, "us_aqi_pm2_5": us})
    facts = A.current_factors(d, aqi.US_EPA, NOW)
    text = " ".join(t for _, t, _ in facts)
    assert "PM2.5 sets the US AQI" in text and "Good to Unhealthy" in text
    assert not any(w in text.lower() for w in ("because", "due to", "caused", "disperse", "wash"))
    assert {tag for _, _, tag in facts} <= {"Now · model estimate", "Forecast", "Method", "Data"}


# --- Chart: earlier model hours vs forecast --------------------------------------------------------

def test_trend_chart_separates_earlier_model_hours_from_forecast_and_keeps_gaps():
    us = [50.0] * 216
    us[80:83] = [np.nan] * 3
    d = parsed(overrides={"us_aqi": [None if np.isnan(v) else v for v in us]})
    fig = AV.aqi_trend_chart(d, aqi.US_EPA, NOW, ui.palette())
    names = [t.name for t in fig.data]
    assert names == ["Earlier hours (model values, not observed)", "Forecast (CAMS)"]
    past, future = fig.data
    assert max(past.x) <= min(future.x)                                      # nothing drawn twice
    assert past.line.dash == "dot" and future.line.color == ui.palette()["provider"]
    assert all(t.connectgaps is False for t in fig.data)
    assert sum(pd.isna(list(future.y))) == 3                                 # the gap is still a gap
    assert any(s.type == "line" for s in fig.layout.shapes)                  # the "now" marker
    assert not any("observ" in n.lower() and "not" not in n.lower() for n in names)


def test_hero_and_gauge_always_carry_standard_and_text_labels():
    d = parsed()
    html = AV.hero_html(DELHI.label, A.reading(d, aqi.IN_NAQI, NOW), d, NOW, "regional default")
    assert "India National AQI (CPCB)" in html and "Good" in html and "not a station reading" in html
    assert "Calculated by Cloudzy" in html and 'aria-label="India National AQI (CPCB) scale' in html


def test_table_view_shows_every_standard_separately():
    t = AV.aq_table(parsed(), list(aqi.STANDARDS.values()))
    assert {"US AQI", "European AQI", "India NAQI", "PM2.5 μg/m³"} <= set(t.columns)
