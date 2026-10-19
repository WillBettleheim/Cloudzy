"""Tests for device location: validation, browser outcomes, request handling, reverse geocoding,
and the weather page flow. The browser and every API are faked; no real location is ever used."""

import json

import pytest
import requests
from streamlit.testing.v1 import AppTest

import src.geolocation as G
import src.provider
from src import preferences as P
from src.provider import ProviderError, parse_forecast
from tests.test_app import fake_network  # noqa: F401  (fixture)

DELHI_FIX = {"status": "ok", "latitude": 28.61392, "longitude": 77.20903, "accuracy": 35.0, "timestamp": 1}
NYC_FIX = {"status": "ok", "latitude": 40.712776, "longitude": -74.005974, "accuracy": 20.0, "timestamp": 2}


# --- Validation and interpretation --------------------------------------------------------

@pytest.mark.parametrize("lat, lon", [(0, 0), (-90, 180), (90, -180), ("28.6", "77.2"), (28.61392, 77.20903)])
def test_valid_coordinates(lat, lon):
    la, lo = G.validate_coordinates(lat, lon)
    assert isinstance(la, float) and isinstance(lo, float)


@pytest.mark.parametrize("lat, lon", [(91, 0), (-90.1, 0), (0, 180.5), (0, -181), (float("nan"), 0),
                                      (0, float("inf")), (None, 1), ("north", "east")])
def test_invalid_coordinates_rejected(lat, lon):
    with pytest.raises(ValueError):
        G.validate_coordinates(lat, lon)


def test_interpret_success():
    o = G.interpret(DELHI_FIX)
    assert o.ok and o.fix.latitude == 28.61392 and o.fix.longitude == 77.20903 and o.fix.accuracy_m == 35.0


@pytest.mark.parametrize("payload, code, retry", [
    ({"status": "error", "code": "permission_denied"}, "permission_denied", True),
    ({"status": "error", "code": "position_unavailable"}, "position_unavailable", True),
    ({"status": "error", "code": "timeout"}, "timeout", True),
    ({"status": "error", "code": "no_response"}, "no_response", True),
    ({"status": "unsupported"}, "unsupported", False),
    ({"status": "insecure"}, "insecure", False),
    ({"status": "ok", "latitude": 123, "longitude": 0}, "invalid_coordinates", True),
    ({"status": "error", "code": "weird"}, "unknown", True),
    ("not a dict", "unknown", True),
])
def test_interpret_failures(payload, code, retry):
    o = G.interpret(payload)
    assert not o.ok and o.code == code and o.retry is retry and o.title and o.detail and o.fix is None


def test_permission_denied_message_explains_site_settings():
    o = G.interpret({"status": "error", "code": "permission_denied"})
    assert "site settings" in o.detail and "search for a city" in o.detail


def test_insecure_message_never_suggests_weakening_security():
    o = G.interpret({"status": "insecure"})
    assert "HTTPS" in o.detail and "localhost" in o.detail
    assert "disable" not in o.detail.lower() and "flag" not in o.detail.lower()


# --- Request handling -------------------------------------------------------------------------

def test_duplicate_requests_reuse_the_pending_one():
    geo = G.state({})
    assert G.start(geo) == 1 and G.start(geo) == 1
    assert geo["nonce"] == 1


def test_result_applied_only_for_the_pending_request():
    geo = G.state({})
    n = G.start(geo)
    assert G.handle(geo, {**DELHI_FIX, "nonce": n + 5}) is None      # stale/foreign: ignored
    assert geo["pending"] == n and geo["mode"] == "manual"
    o = G.handle(geo, {**DELHI_FIX, "nonce": n})
    assert o.ok and geo["mode"] == "device" and geo["pending"] is None


def test_late_answer_after_manual_choice_is_ignored():
    geo = G.state({})
    n = G.start(geo)
    G.use_manual(geo)
    assert G.handle(geo, {**DELHI_FIX, "nonce": n}) is None
    assert geo["mode"] == "manual" and geo["fix"] is None


def test_cancel_stops_waiting_and_ignores_late_answer():
    geo = G.state({})
    n = G.start(geo)
    G.cancel(geo)
    assert geo["pending"] is None and G.handle(geo, {**DELHI_FIX, "nonce": n}) is None


def test_refresh_replaces_fix_and_old_answer_cannot_win():
    geo = G.state({})
    G.handle(geo, {**DELHI_FIX, "nonce": G.start(geo)})
    n2 = G.start(geo)
    assert G.handle(geo, {**DELHI_FIX, "nonce": n2 - 1}) is None     # an answer for request 1 arriving late
    G.handle(geo, {**NYC_FIX, "nonce": n2})
    assert geo["fix"].latitude == NYC_FIX["latitude"]


def test_error_keeps_previous_fix_and_new_request_clears_error():
    geo = G.state({})
    G.handle(geo, {**DELHI_FIX, "nonce": G.start(geo)})
    G.handle(geo, {"status": "error", "code": "timeout", "nonce": G.start(geo)})
    assert geo["mode"] == "device" and geo["fix"].latitude == DELHI_FIX["latitude"] and geo["error"].code == "timeout"
    G.start(geo)
    assert geo["error"] is None


def test_browser_script_is_one_shot_and_never_tracks():
    js = G.JS
    assert "getCurrentPosition" in js and "watchPosition" not in js
    assert "isSecureContext" in js and "enableHighAccuracy" in js and "timeout" in js
    assert "__cloudzyGeoNonce === nonce" in js  # no repeat lookup on re-render


def test_geo_options_are_configurable(monkeypatch):
    d = G.GeoOptions(timeout_ms=5000, high_accuracy=False).as_data(3)
    assert d == {"nonce": 3, "timeoutMs": 5000, "highAccuracy": False, "maximumAgeMs": 60000,
                 "permissionWaitMs": 60000}


# --- Reverse geocoding ----------------------------------------------------------------------

def nominatim(address):
    return {"place_id": 1, "address": address}


@pytest.mark.parametrize("address, name", [
    ({"city": "New Delhi", "state": "Delhi", "country": "India", "country_code": "in"}, "New Delhi"),
    ({"town": "Gurugram", "state": "Haryana", "country": "India", "country_code": "in"}, "Gurugram"),
    ({"village": "Ørsta", "country": "Norway", "country_code": "no"}, "Ørsta"),
])
def test_parse_reverse(address, name):
    r = G.parse_reverse(nominatim(address))
    assert r["name"] == name and r["country_code"] == address["country_code"].upper()


@pytest.mark.parametrize("payload", [{"error": "Unable to geocode"}, {"address": {"country": "Pacific"}}, None, []])
def test_parse_reverse_without_a_place_name(payload):
    assert G.parse_reverse(payload) is None


class FakeSession:
    def __init__(self, body=None, fail=False):
        self.body, self.fail, self.calls, self.headers = body, fail, [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        if self.fail:
            raise requests.ConnectionError("offline")
        body = self.body

        class R:
            status_code, text, headers = 200, "", {}

            def json(self):
                return body
        return R()


@pytest.fixture(autouse=True)
def fresh_reverse_cache(monkeypatch):
    G._cache.clear()
    monkeypatch.setattr(G, "MIN_INTERVAL_S", 0)  # no real waiting in tests
    yield
    G._cache.clear()


def test_reverse_geocode_sends_rounded_coordinates_and_identifies_itself():
    s = FakeSession(nominatim({"city": "New Delhi", "country": "India", "country_code": "in"}))
    r = G.reverse_geocode(28.613921, 77.209031, session=s)
    assert r["name"] == "New Delhi"
    assert s.calls[0]["lat"] == 28.61 and s.calls[0]["lon"] == 77.21     # ~1 km, not GPS precision
    assert s.headers["User-Agent"].startswith("Cloudzy/")                 # Nominatim policy


def test_reverse_geocode_caches_by_rounded_coordinates():
    s = FakeSession(nominatim({"city": "New Delhi", "country": "India", "country_code": "in"}))
    G.reverse_geocode(28.6139, 77.2090, session=s)
    G.reverse_geocode(28.6141, 77.2092, session=s)
    assert len(s.calls) == 1


def test_reverse_geocode_failure_returns_none_and_is_retried_later(monkeypatch):
    monkeypatch.setattr("src.data_collection.time.sleep", lambda s: None)
    assert G.reverse_geocode(28.6, 77.2, session=FakeSession(fail=True)) is None
    s = FakeSession(nominatim({"city": "New Delhi", "country": "India", "country_code": "in"}))
    assert G.reverse_geocode(28.6, 77.2, session=s)["name"] == "New Delhi"


def test_reverse_geocode_rejects_invalid_coordinates_before_any_request():
    s = FakeSession({})
    with pytest.raises(ValueError):
        G.reverse_geocode(200, 0, session=s)
    assert s.calls == []


def test_device_place_uses_exact_coordinates():
    fix = G.interpret(DELHI_FIX).fix
    named = G.device_place(fix, {"name": "New Delhi", "admin1": "Delhi", "country": "India", "country_code": "IN"})
    assert (named.latitude, named.longitude) == (28.61392, 77.20903) and named.label == "New Delhi, Delhi, India"
    neutral = G.device_place(fix, None)
    assert neutral.label == G.NEUTRAL_LABEL and neutral.country_code is None


# --- Weather page flow (browser and APIs faked) ----------------------------------------------

@pytest.fixture
def browser(monkeypatch, fake_network):  # noqa: F811
    """Fake browser: answers each new request number with the next queued payload."""
    queue, seen, forecast_coords = [], [], []

    def fake_browser(nonce, options=G.GeoOptions()):
        if nonce and nonce not in seen and queue:
            seen.append(nonce)
            item = queue.pop(0)
            return None if item is None else {**item, "nonce": item.get("nonce", nonce)}
        return None

    real = src.provider.fetch_forecast  # already faked by fake_network

    def recording_forecast(place, days=7, session=None):
        forecast_coords.append((place.latitude, place.longitude))
        return real(place)

    monkeypatch.setattr(G, "browser_location", fake_browser)
    monkeypatch.setattr(src.provider, "fetch_forecast", recording_forecast)
    monkeypatch.setattr(G, "reverse_geocode", lambda lat, lon, session=None: {
        "name": "New Delhi", "admin1": "Delhi", "country": "India", "country_code": "IN"})
    return {"queue": queue, "coords": forecast_coords, "net": fake_network}


def run_page():
    at = AppTest.from_file("../app_pages/weather.py", default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def click(at, label):
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def body(at):
    return " ".join(h.proto.body for h in at.html) + " ".join(m.value for m in at.markdown)


def test_use_my_location_loads_weather_for_exact_coordinates(browser):
    browser["queue"].append(DELHI_FIX)
    at = click(run_page(), "Use my location")
    assert (28.61392, 77.20903) in browser["coords"]                  # exactly what the browser returned
    assert "New Delhi, Delhi, India" in body(at) and "your device location" in body(at)
    assert any("Location detected" in c.value and "accuracy about ±35 m" in c.value for c in at.caption)
    assert not at.text_input                                           # dashboard is in device mode
    assert "q" not in at.query_params and "28.6" not in str(dict(at.query_params))  # no stale city, no coordinates


def test_pending_state_shows_progress_and_can_be_cancelled(browser):
    browser["queue"].append(None)  # browser hasn't answered yet
    at = click(run_page(), "Use my location")
    assert any("Finding your location" in i.value for i in at.info)
    assert at.text_input  # manual search and weather stay usable meanwhile
    at = click(at, "Cancel")
    assert not any("Finding your location" in i.value for i in at.info)


@pytest.mark.parametrize("payload, phrase, retry", [
    ({"status": "error", "code": "permission_denied"}, "Location permission was denied", True),
    ({"status": "error", "code": "position_unavailable"}, "couldn't determine its position", True),
    ({"status": "error", "code": "timeout"}, "took too long", True),
    ({"status": "unsupported"}, "doesn't support location", False),
    ({"status": "insecure"}, "secure connection", False),
    ({"status": "ok", "latitude": 999, "longitude": 0}, "invalid position", True),
])
def test_failures_explain_and_keep_manual_search(browser, payload, phrase, retry):
    browser["queue"].append(payload)
    at = click(run_page(), "Use my location")
    assert any(phrase in w.value for w in at.warning)
    assert at.text_input and at.selectbox                              # manual search still works
    assert any(b.label == "Try again" for b in at.button) is retry
    assert (999.0, 0.0) not in browser["coords"]                       # invalid values never reach the API


def test_retry_after_denial_can_succeed(browser):
    browser["queue"].extend([{"status": "error", "code": "permission_denied"}, DELHI_FIX])
    at = click(run_page(), "Use my location")
    at = click(at, "Try again")
    assert "your device location" in body(at)


def test_reverse_geocoding_failure_uses_neutral_label(browser, monkeypatch):
    monkeypatch.setattr(G, "reverse_geocode", lambda lat, lon, session=None: None)
    browser["queue"].append(NYC_FIX)
    at = click(run_page(), "Use my location")
    assert G.NEUTRAL_LABEL in body(at)
    assert (40.712776, -74.005974) in browser["coords"]                # weather still for the exact spot
    assert any("place name unavailable" in c.value for c in at.caption)


def test_weather_failure_keeps_location_and_offers_retry(browser):
    browser["queue"].append(DELHI_FIX)
    browser["net"]["forecast_error"] = ProviderError("timed out")
    at = click(run_page(), "Use my location")
    assert any("couldn't get the forecast" in e.value for e in at.error)
    assert "your device location" in body(at)                           # location result kept
    browser["net"]["forecast_error"] = None
    at = click(at, "Try again")
    assert "cz-hero" in body(at)


def test_manual_search_after_detection(browser):
    browser["queue"].append(DELHI_FIX)
    at = run_page()
    at.text_input[0].set_value("Paris").run()
    at.selectbox[0].select_index(1).run()                              # Paris, Texas
    at = click(at, "Use my location")
    at = click(at, "Search for a city instead")
    assert at.text_input and at.text_input[0].value == "Paris"           # back to the last manual choice
    assert "Paris, Texas, United States" in body(at)


def test_refresh_fetches_weather_for_new_coordinates(browser):
    browser["queue"].extend([DELHI_FIX, NYC_FIX])
    at = click(run_page(), "Use my location")
    at = click(at, "Refresh location")
    assert browser["coords"][-1] == (40.712776, -74.005974)


def test_stale_answer_from_older_request_is_ignored(browser):
    browser["queue"].append({**NYC_FIX, "nonce": 0})                   # answer for no current request
    at = click(run_page(), "Use my location")
    assert (40.712776, -74.005974) not in browser["coords"]
    assert any("Finding your location" in i.value for i in at.info)  # still waiting for the real answer


def test_cloudzy_core_available_near_delhi_only(browser):
    browser["queue"].append(DELHI_FIX)
    at = click(run_page(), "Use my location")
    assert any("ECMWF IFS" in str(df.value.columns.tolist()) for df in at.dataframe)  # Cloudzy table shown
    browser["queue"].append(NYC_FIX)
    at = click(at, "Refresh location")
    assert any("trained and tested only for" in i.value for i in at.info)


def test_auto_locate_is_opt_in_and_stores_no_coordinates(browser, tmp_path):
    browser["queue"].append(DELHI_FIX)
    run_page()
    assert browser["queue"], "must not look up the location unless the user opted in"
    p = P.load()
    p.auto_locate = True
    P.save(p)
    at = run_page()
    assert "your device location" in body(at)
    saved = json.loads(P.prefs_path().read_text())
    assert saved["auto_locate"] is True and "28.61" not in json.dumps(saved)


def test_auto_locate_respects_a_shared_city_link(browser):
    p = P.load()
    p.auto_locate = True
    P.save(p)
    browser["queue"].append(DELHI_FIX)
    at = AppTest.from_file("../app_pages/weather.py", default_timeout=30)
    at.query_params["q"] = "Paris"
    at.run()
    assert not at.exception and browser["queue"]                        # no lookup: the link named a city
