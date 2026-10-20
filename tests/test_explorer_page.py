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


# --- Fly to a place (search and saved places) -----------------------------------------------------------

def test_ambiguous_search_waits_for_a_choice_then_flies_and_names_the_place(fake_network, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(G, "reverse_geocode", lambda lat, lon, session=None: calls.append((lat, lon)))
    at = run_page()
    at.text_input(key="explorer_q").set_value("Paris").run()
    assert not at.exception
    assert "_explorer_focus" not in at.session_state                     # several matches: nothing picked for you
    box = at.selectbox(key="explorer_match_Paris")
    labels = list(box.options)                                            # already formatted
    assert any(lbl.startswith("City · Paris, Île-de-France, France") for lbl in labels)
    assert any(lbl.startswith("City · Paris, Texas") for lbl in labels)
    box.select_index(next(i for i, lbl in enumerate(labels) if "France" in lbl and lbl.startswith("City"))).run()
    focus = at.session_state["_explorer_focus"]
    assert (focus["kind"], focus["lat"], focus["lon"], focus["token"]) == ("city", 48.8534, 2.3488, 1)
    assert at.session_state["_explorer_pick"] == (48.853, 2.349)
    assert any("Paris" in m.value and "48.85°N" in m.value for m in at.markdown)
    assert calls == []                                                    # the searched name is used
    region = at.session_state["_explorer_region"]
    assert region["a3"] == "FRA"                                          # the region it lies in, from Natural Earth
    at.run()                                                              # a rerun doesn't fly again
    assert at.session_state["_explorer_focus"]["token"] == 1


def test_search_with_no_match_or_a_failing_service_says_so(fake_network, no_reverse):  # noqa: F811
    at = run_page()
    at.text_input(key="explorer_q").set_value("Xqzzyq").run()
    assert any("Nothing matches" in c.value for c in at.caption)
    assert "_explorer_focus" not in at.session_state
    from src.provider import ProviderError
    fake_network["search_error"] = ProviderError("timeout")
    at.text_input(key="explorer_q").set_value("Maharashtra").run()         # regions still found without the service
    assert not at.exception and any("City search is unavailable" in c.value for c in at.caption)
    assert at.session_state["_explorer_focus"]["kind"] == "admin1"


def test_saved_places_can_be_flown_to(fake_network, no_reverse):  # noqa: F811
    from tests.test_app import PARIS
    prefs = P.load()
    prefs.toggle_favourite(PARIS)
    P.save(prefs)
    at = run_page()
    at.button_group(key="explorer_fav").set_value(PARIS.label).run()
    assert at.session_state["_explorer_focus"]["label"] == PARIS.label


# --- Regions (browse, search, globe events) ---------------------------------------------------------------

def prepared_run():
    E.prepare_run(RUN, s3_for((0, 3)), Path(os.environ["CLOUDZY_EXPLORER_DIR"]), fhours=(0, 3))


def test_browsing_to_a_state_shows_a_labelled_regional_aggregate(fake_network, no_reverse):  # noqa: F811
    prepared_run()
    at = run_page()
    at.selectbox(key="explorer_b_cont").select("Asia").run()
    ctry = at.selectbox(key="explorer_b_ctry_Asia")
    ctry.select_index(list(ctry.options).index("India")).run()
    adm = at.selectbox(key="explorer_b_adm_IND")
    adm.select_index(list(adm.options).index("Maharashtra")).run()
    assert not at.exception
    assert at.session_state["_explorer_region"]["name"] == "Maharashtra"
    assert at.session_state["_explorer_focus"]["kind"] == "admin1"
    labels = [m.label for m in at.metric]
    assert labels[:4] == ["Average temperature", "Area with precipitation", "Average wind", "Average cloud cover"]
    cap = " ".join(c.value for c in at.caption)
    assert "area-weighted average over the" in cap and "not a city forecast" in cap
    assert any("State of India" in m.value or "state of India" in m.value for m in at.markdown)


def test_a_region_smaller_than_the_grid_gets_no_regional_value(fake_network, no_reverse):  # noqa: F811
    prepared_run()
    at = run_page()
    at.text_input(key="explorer_q").set_value("Delhi").run()
    box = at.selectbox(key="explorer_match_Delhi")
    box.select_index(next(i for i, o in enumerate(box.options) if o.startswith("State/province · Delhi"))).run()
    assert any("smaller than the 1° forecast grid" in i.value for i in at.info)
    assert not [m for m in at.metric if m.label == "Average temperature"]
    btn = next(b for b in at.button if b.label.startswith("Point forecast for the centre of Delhi"))
    btn.click().run()
    assert at.session_state["_explorer_pick"] is not None


def test_coordinates_search_selects_the_point_and_its_region(fake_network, no_reverse):  # noqa: F811
    at = run_page()
    at.text_input(key="explorer_q").set_value("19.02, 72.86").run()
    assert at.session_state["_explorer_focus"]["kind"] == "point"
    assert at.session_state["_explorer_region"]["name"] == "Maharashtra"
    assert at.session_state["_explorer_pick"] == (19.02, 72.86)
