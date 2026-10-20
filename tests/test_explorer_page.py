"""The Weather Explorer page (Streamlit AppTest). The map itself runs in the browser; here we test
what the page does around it: data status, the selected-point panel, units, and Cloudzy Core scope."""

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import explorer_data as E
from src import geolocation as G
from src import preferences as P
from tests.test_app import fake_network  # noqa: F401  (fixture)
from tests.test_explorer_data import RUN, s3_for


def run_page(pick=None):
    at = AppTest.from_file("../app_pages/explorer.py", default_timeout=60)
    if pick:
        at.session_state["_explorer_pick"] = pick
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.fixture
def no_reverse(monkeypatch):
    monkeypatch.setattr(G, "reverse_geocode", lambda lat, lon, session=None: None)


def test_first_visit_says_data_is_being_prepared(fake_network, no_reverse):  # noqa: F811
    at = run_page()
    assert any("Preparing global forecast data" in i.value for i in at.info)
    assert any("not radar or observations" in c.value for c in at.caption)


def test_download_error_is_explained(fake_network, no_reverse, monkeypatch):  # noqa: F811
    monkeypatch.setattr(E, "_state", {"thread": None, "last_check": 0.0, "error": "ConnectionError: offline"})
    at = run_page()
    assert any("Couldn't prepare the global forecast data" in e.value for e in at.error)


def test_with_a_prepared_run_the_layer_guide_names_the_run(fake_network, no_reverse):  # noqa: F811
    E.prepare_run(RUN, s3_for((0, 3)), Path(os.environ["CLOUDZY_EXPLORER_DIR"]), fhours=(0, 3))
    at = run_page()
    assert not at.info and not at.error
    guide = next(e for e in at.expander if e.label == "About the layers and data")
    assert "2026-10-20T00:00Z" in " ".join(m.value for m in guide.markdown)


def test_partial_run_shows_progress(fake_network, no_reverse):  # noqa: F811
    d = Path(os.environ["CLOUDZY_EXPLORER_DIR"])
    E.write_manifest(E.manifest_for(RUN, [{"fhr": 0, "valid_utc": "2026-10-20T00:00Z", "ready": True, "missing": []},
                                          {"fhr": 3, "valid_utc": "2026-10-20T03:00Z", "ready": False, "missing": []}],
                                    "preparing"), d)
    at = run_page()
    assert any("1 of 2 forecast steps ready" in c.value for c in at.caption)


def test_selected_point_shows_a_labelled_point_forecast_and_no_cloudzy_far_from_delhi(fake_network, no_reverse):  # noqa: F811
    at = run_page(pick=(48.85, 2.35))
    labels = [m.label for m in at.metric]
    assert labels == ["Temperature", "Wind", "Cloud cover", "Condition"]
    assert any("point forecast, not the GFS grid value" in c.value for c in at.caption)
    assert any("Cloudzy Core isn't shown here" in c.value for c in at.caption)
    assert not [b for b in at.button if b.label.startswith("Open ")]       # no named place → no "open" button


def test_point_near_delhi_shows_the_cloudzy_point_prediction(fake_network, no_reverse):  # noqa: F811
    at = run_page(pick=(28.61, 77.21))
    assert any("Cloudzy Core point prediction" in m.value for m in at.markdown)


def test_named_point_can_open_on_the_weather_page_without_saving_it(fake_network, monkeypatch):  # noqa: F811
    monkeypatch.setattr(G, "reverse_geocode", lambda lat, lon, session=None: {
        "name": "Paris", "admin1": "Île-de-France", "country": "France", "country_code": "FR"})
    at = run_page(pick=(48.85, 2.35))
    btn = next(b for b in at.button if b.label == "Open Paris on the Weather page")
    btn.click().run()
    assert at.session_state["_open_place"].name == "Paris"
    assert P.load().favourites == [] and P.load().home is None           # nothing saved or overwritten


def test_units_follow_settings(fake_network, no_reverse):  # noqa: F811
    prefs = P.load()
    prefs.units = {"temperature": "°F", "wind": "mph", "precipitation": "in"}
    P.save(prefs)
    at = run_page(pick=(48.85, 2.35))
    temp = next(m for m in at.metric if m.label == "Temperature")
    assert temp.value.endswith("°F")


# --- Globe / map view ---------------------------------------------------------------------------------

def view_control(at):
    return next(c for c in at.button_group if c.label == "View")


def test_globe_is_the_default_view_and_the_map_is_one_click_away(fake_network, no_reverse):  # noqa: F811
    at = run_page()
    assert view_control(at).value == "globe"
    view_control(at).set_value("map").run()
    assert not at.exception
    assert view_control(at).value == "map"


def test_globe_fallback_switches_to_the_map_and_says_why(fake_network, no_reverse):  # noqa: F811
    at = AppTest.from_file("../app_pages/explorer.py", default_timeout=60)
    at.session_state["_explorer_globe_fallback"] = "This browser or device doesn't support WebGL."
    at.run()
    assert not at.exception
    assert any("3D globe isn't available here" in i.value and "WebGL" in i.value for i in at.info)


def test_selected_point_panel_works_in_globe_view(fake_network, no_reverse):  # noqa: F811
    at = run_page(pick=(48.85, 2.35))
    assert view_control(at).value == "globe"
    assert [m.label for m in at.metric] == ["Temperature", "Wind", "Cloud cover", "Condition"]
