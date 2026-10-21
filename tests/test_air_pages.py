"""Air Quality and Outdoor Intelligence pages, and their integration with Weather and Alerts (network faked)."""

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from src import preferences as P
from src.air_quality import AirQualityError
from src.provider import ProviderError
from tests.aq_fixtures import aq_today_payload
from tests.test_app import DELHI, PARIS, fake_network  # noqa: F401  (fixture)


def run(page, at=None, fresh=False):
    """Run a page. `fresh` drops cached provider data, as a new forecast arriving would."""
    if fresh:
        st.cache_data.clear()
    if at is None:            # (an AppTest that hasn't run yet is falsy, so no `at or ...`)
        at = AppTest.from_file(f"../app_pages/{page}.py", default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def body(at) -> str:
    return " ".join(h.proto.body for h in at.html)


def captions(at) -> str:
    return " ".join(c.value for c in at.caption)


# --- Air Quality page ------------------------------------------------------------------------------

def test_air_quality_page_shows_standard_source_and_model_label(fake_network):  # noqa: F811
    at = run("air_quality")
    html = body(at)
    assert "India National AQI (CPCB)" in html                     # Delhi's regional default
    assert "Model estimate, not a station reading" in html and "Calculated by Cloudzy" in html
    assert "CAMS" in captions(at) and "not monitoring-station readings" in captions(at)
    assert len(at.get("plotly_chart")) == 3 and len(at.dataframe) >= 2
    assert any("Cloudzy Core doesn't predict air quality" in m.value for m in at.markdown)


def test_switching_standard_is_saved_and_relabels_every_number(fake_network):  # noqa: F811
    at = run("air_quality")
    at.segmented_control(key="aq_standard").set_value("us_epa").run()
    assert not at.exception
    assert P.load().aqi_standard == "us_epa"
    html = body(at)
    assert "Air quality now · US AQI (EPA)" in html and "Moderate" in html
    assert "Air quality now · India National AQI" not in html


def test_air_quality_failure_is_explained_with_retry_and_no_numbers(fake_network):  # noqa: F811
    fake_network["aq_error"] = AirQualityError("Rate limited: the weather service is rate-limiting requests")
    at = run("air_quality")
    assert any("couldn't get air-quality data" in e.value and "Rate limited" in e.value for e in at.error)
    assert any(b.label == "Try again" for b in at.button)
    assert '<section class="cz-aq-hero' not in body(at)


def test_unsupported_location_message(fake_network):  # noqa: F811
    p = aq_today_payload()
    for k in p["hourly"]:
        if k != "time":
            p["hourly"][k] = [None] * len(p["hourly"]["time"])
    fake_network["aq_payload"] = p
    at = run("air_quality")
    assert any("no values for this location" in e.value for e in at.error)


def test_european_place_defaults_to_european_index(fake_network):  # noqa: F811
    at = AppTest.from_file("../app_pages/air_quality.py", default_timeout=60)
    at.session_state["_cz_place"] = PARIS
    run("air_quality", at)
    assert "Air quality now · European AQI (EEA)" in body(at)


# --- Outdoor page ----------------------------------------------------------------------------------

def test_outdoor_page_shows_verdict_windows_timeline_and_disclaimer(fake_network):  # noqa: F811
    at = run("outdoor")
    html = body(at)
    assert "cz-verdict" in html and "Running now" in html
    assert "cz-win" in html and "cz-tl" in html
    assert "no time can be promised as safe" in captions(at)
    assert any(b.label == "Save this window" for b in at.button)


def test_outdoor_preferences_are_saved_without_medical_questions(fake_network):  # noqa: F811
    at = run("outdoor")
    at.segmented_control(key="od_activity").set_value("cycling").run()
    next(t for t in at.toggle if t.label == "More conservative guidance").set_value(True).run()
    assert not at.exception
    saved = P.load().outdoor
    assert saved.activity == "cycling" and saved.sensitivity == "conservative"
    raw = P.to_dict(P.load())
    assert set(raw["outdoor"]) == {"activity", "duration_min", "start_hour", "end_hour", "sensitivity", "indoor"}


def test_bad_air_suggests_indoor_alternative(fake_network):  # noqa: F811
    fake_network["aq_payload"] = aq_today_payload(overrides={"pm2_5": 300.0, "pm10": 400.0})
    at = run("outdoor")
    html = body(at)
    assert "Better to postpone or move indoors" in html and "Indoor alternative" in html
    assert any("Every suggested window has a limiting factor" in w.value for w in at.warning)


def test_outdoor_needs_both_forecasts(fake_network):  # noqa: F811
    fake_network["aq_error"] = AirQualityError("couldn't connect")
    at = run("outdoor")
    assert any("air quality is an essential check" in e.value for e in at.error)
    fake_network["aq_error"] = None
    fake_network["forecast_error"] = ProviderError("HTTP 500")
    at = run("outdoor", fresh=True)
    assert any("couldn't get the weather forecast" in e.value for e in at.error)


def test_saved_window_is_rechecked_and_alerts_when_forecast_worsens(fake_network):  # noqa: F811
    prefs = P.load()
    prefs.outdoor = P.OutdoorPrefs(start_hour=0, end_hour=24)
    P.save(prefs)
    at = run("outdoor")
    next(b for b in at.button if b.label == "Save this window").click().run()
    plans = P.load().plans
    assert len(plans) == 1 and plans[0]["activity"] == "running"
    assert "Same or better than when you saved it" in body(at)

    # A new forecast makes that window unhealthy.
    worse = aq_today_payload(overrides={"pm2_5": 300.0, "pm10": 400.0})
    fake_network["aq_payload"] = worse
    at = run("outdoor", fresh=True)
    assert "Less suitable than when you saved it" in body(at)
    alerts_at = run("alerts")
    assert "now less suitable" in body(alerts_at)


# --- Weather and Alerts integration ----------------------------------------------------------------

def test_weather_page_shows_air_quality_line_and_survives_air_quality_failure(fake_network):  # noqa: F811
    from tests.test_app import run_app
    at = run_app()
    assert "cz-aq-strip" in body(at) and "India NAQI" in body(at)
    fake_network["aq_error"] = AirQualityError("couldn't connect")
    st.cache_data.clear()
    at = run_app()
    assert "Air quality isn't available right now" in captions(at)
    assert "cz-hero" in body(at)                                    # the weather forecast is unaffected


def test_alerts_page_shows_air_and_particle_alerts_with_source(fake_network):  # noqa: F811
    fake_network["aq_payload"] = aq_today_payload(overrides={"pm2_5": 80.0, "pm10": 120.0})
    at = run("alerts")
    html = body(at)
    assert "Air quality: " in html and "High fine-particle levels" in html and "CAMS" in html


def test_alerts_page_still_shows_weather_alerts_when_air_quality_fails(fake_network):  # noqa: F811
    p = fake_network["payload"]
    p["hourly"]["apparent_temperature"] = [44.0] * len(p["hourly"]["time"])
    fake_network["aq_error"] = AirQualityError("couldn't connect")
    at = run("alerts")
    assert "Very hot" in body(at)
    assert "Air-quality alerts are unavailable right now" in captions(at)


def test_storm_alert_from_weather_codes(fake_network):  # noqa: F811
    p = fake_network["payload"]
    p["hourly"]["weather_code"] = [95] * len(p["hourly"]["time"])
    at = run("alerts")
    assert "Thunderstorms possible" in body(at)


def test_new_threshold_fields_are_saved(fake_network):  # noqa: F811
    at = run("alerts")
    next(s for s in at.selectbox if s.label.startswith("Air quality at least")).set_value(3)
    next(n for n in at.number_input if n.label.startswith("PM2.5")).set_value(50)
    next(b for b in at.button if b.label == "Save thresholds").click().run()
    assert not at.exception
    th = P.load().alerts
    assert th.aqi_category == 3 and th.pm25_24h_ugm3 == 50


def test_plans_are_validated_and_bounded_in_storage():
    good = {"name": "Delhi", "latitude": 28.6, "longitude": 77.2, "timezone": "Asia/Kolkata", "activity": "running",
            "start": "2026-10-20T07:00:00+05:30", "end": "2026-10-20T08:00:00+05:30", "level": "ok",
            "standard": "us_epa", "sensitivity": "standard", "saved_at": "2026-10-19T06:40:00+00:00"}
    bad = [good | {"activity": "skydiving"}, good | {"latitude": 999}, good | {"start": "2026-10-20T07:00"},
           good | {"timezone": "Mars/Base"}, good | {"end": "2026-10-21T07:00:00+05:30"}, "nonsense"]
    prefs = P.from_dict({"plans": bad + [good] * 8})
    assert len(prefs.plans) == P.MAX_PLANS and all(p == good for p in prefs.plans)
    assert P.from_dict({"outdoor": {"activity": "running", "start_hour": 20, "end_hour": 5}}).outdoor.start_hour == 6
    assert P.from_dict({"aqi_standard": "made_up"}).aqi_standard == "auto"
    assert pd.Timestamp(prefs.plans[0]["start"]).tzinfo is not None
