"""The Overview landing page (Streamlit AppTest, network faked)."""

from streamlit.testing.v1 import AppTest

from src.air_quality import AirQualityError
from src.provider import ProviderError
from tests.test_app import DELHI, PARIS, fake_network  # noqa: F401  (fixture)


def run(place=None):
    at = AppTest.from_file("../app_pages/overview.py", default_timeout=60)
    if place is not None:
        at.session_state["_cz_place"] = place
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def body(at) -> str:
    return " ".join(h.proto.body for h in at.html)


def test_overview_shows_labelled_tiles_from_real_sources(fake_network):  # noqa: F811
    at = run(DELHI)
    html = body(at)
    for title in ("Next 24 hours", "Cloudzy alerts", "Air quality now", "Hazards within 500 km",
                  "Prediction tracking"):
        assert title in html
    for kind in ("Provider forecast", "Model estimate", "Rule-based guidance", "Cloudzy ML prediction",
                 "Tracked evaluation"):
        assert kind in html
    assert "not a station reading" in html and "India NAQI" in html
    assert "Cloudzy Core +1 h" in html                          # Delhi: the model's own tile
    assert len(at.get("plotly_chart")) == 1


def test_far_from_delhi_cloudzy_core_explains_its_scope(fake_network):  # noqa: F811
    html = body(run(PARIS))
    assert "only predicts for New Delhi" in html and "European AQI" in html


def test_one_failing_source_never_takes_the_page_down(fake_network):  # noqa: F811
    fake_network["aq_error"] = AirQualityError("couldn't connect")
    html = body(run(DELHI))
    assert "Not available right now: couldn&#x27;t connect" in html and "Next 24 hours" in html   # (escaped)
    fake_network["aq_error"] = None
    fake_network["forecast_error"] = ProviderError("HTTP 500")
    import streamlit as st
    st.cache_data.clear()
    at = run(DELHI)
    assert any("weather forecast isn't available" in e.value for e in at.error)
    assert "Air quality now" in body(at)


def test_hazard_tile_never_waits_and_says_when_feeds_are_loading(fake_network, monkeypatch):  # noqa: F811
    import src.hazards.store as STORE

    def slow(q):
        import time
        time.sleep(30)
    monkeypatch.setattr(STORE, "default_fetchers", lambda: {k: slow for k in ("usgs", "nws")})
    html = body(run(DELHI))
    assert "still loading" in html


def test_old_weather_links_are_forwarded(fake_network):  # noqa: F811
    at = AppTest.from_file("../app_pages/overview.py", default_timeout=60)
    at.query_params["q"] = "Paris"
    at.run()
    # Outside the multipage app the page isn't registered, so nav.switch_page explains instead of crashing.
    assert not at.exception
