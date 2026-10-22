"""The Earth Intelligence page (Streamlit AppTest) with fixture-backed feeds. The globe runs in the browser; here a
stand-in records what the page sends it and can report a selection, to test globe ↔ page synchronisation."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import src.earth_view
from src.hazards import firms, gdacs, http, nhc, nws, tsunami, usgs, volcanoes
from src.hazards import store as STORE
from src.hazards.schema import FeedResult
from tests.test_app import fake_network  # noqa: F401  (fixture)
from tests.test_hazards import DROUGHT, FIX, load

SENT: list[dict] = []
PICK: dict = {}


def fixture_fetchers(broken: set[str] = frozenset()):
    def wrap(name, fn):
        def run(q):
            if name in broken:
                raise http.FeedError("provider unreachable (test)")
            return fn(q)
        return run
    from src.hazards import usdm
    return {k: wrap(k, f) for k, f in {
        "usgs": lambda q: usgs.parse(load("usgs_all_day.json")),
        "gdacs": lambda q: gdacs.parse_list(load("gdacs_list.json")),
        "nhc": lambda q: nhc.parse_current(load("nhc_current.json")),
        "nws": lambda q: nws.parse(load("nws_active.json")),
        "firms": lambda q: firms.parse((FIX / "firms_24h.csv").read_text()),
        "usdm": lambda q: usdm.parse(DROUGHT, pd.Timestamp("2026-10-13", tz="UTC")),
        "usgs_volcano": lambda q: volcanoes.parse_usgs(load("usgs_volcano_elevated.json")),
        "gvp": lambda q: volcanoes.parse_gvp(load("gvp.json")),
        "tsunami": lambda q: tsunami.parse((FIX / "tsunami_ntwc.xml").read_text(), "NTWC",
                                           pd.Timestamp.now(tz="UTC")),
    }.items()}


@pytest.fixture
def earth(monkeypatch, fake_network):  # noqa: F811
    SENT.clear()
    PICK.clear()
    state = {"broken": set()}
    monkeypatch.setattr(STORE, "default_fetchers", lambda: fixture_fetchers(state["broken"]))

    def fake_globe(data, key="cloudzy_earth"):
        SENT.append(data)
        return SimpleNamespace(select=dict(PICK) or None)
    monkeypatch.setattr(src.earth_view, "earth_globe", fake_globe)
    yield state


def sent_events() -> list[dict]:
    """The event list the page last handed to the globe (read from the static file it points to)."""
    return json.loads((STORE.static_dir() / SENT[-1]["layers"]["events"].rsplit("/", 1)[1]).read_text())


def run(at=None):
    at = at if at is not None else AppTest.from_file("../app_pages/earth.py", default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def texts(at):
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [w.value for w in at.warning])


def test_page_shows_notice_feeds_summary_and_sends_layers_to_the_globe(earth):
    at = run()
    assert any("Situational awareness only" in w.value for w in at.warning)
    data = SENT[-1]
    assert set(data["layers"]) >= {"events", "fires", "drought", "volcano_catalogue"}
    assert all(u.startswith(STORE.STATIC_URL) for u in data["layers"].values())
    assert {s["label"] for s in data["statuses"]} <= {"Live feed", "Recently updated", "Delayed data",
                                                      "Historical dataset", "Temporarily unavailable"}
    assert data["period"]["end"] > data["period"]["start"]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Fire detections"] == "40" and int(metrics["Earthquakes"]) >= 1
    feeds = at.dataframe[0].value
    assert set(feeds["Source"]) >= {"USGS earthquakes", "NWS warnings", "NASA FIRMS fire detections"}


def test_one_broken_feed_is_reported_and_everything_else_works(earth):
    earth["broken"] = {"nws", "tsunami"}
    at = run()
    feeds = at.dataframe[0].value.set_index("Source")
    assert feeds.loc["NWS warnings", "Status"] == "Temporarily unavailable"
    assert "provider unreachable" in feeds.loc["NWS warnings", "Notes"]
    assert feeds.loc["USGS earthquakes", "Status"] != "Temporarily unavailable"
    events = sent_events()
    assert events and not any(e["s"] == "nws" for e in events)


def test_selecting_on_the_globe_shows_source_backed_details(earth):
    at = run()
    events = sent_events()
    storm = next(e for e in events if e["s"] == "nhc")
    PICK.update({"id": storm["id"], "t": 1})
    at = run(at)
    body = texts(at)
    assert "Tropical Storm Vance" in body and "Saffir-Simpson" in body
    assert any(b.label == "Open original source" for b in at.get("link_button"))
    facts = at.dataframe[1].value if len(at.dataframe) > 1 else None
    assert facts is not None
    # The page now tells the globe the same selection, so marker, list and details agree.
    PICK.clear()
    at = run(at)
    assert SENT[-1]["selected"] == storm["id"]


def test_selecting_a_fire_detection_never_calls_it_a_confirmed_fire(earth):
    at = run()
    PICK.update({"fire": {"lat": 28.6, "lon": 77.2, "frp": 12.5, "tms": 1792600000000, "conf": "nominal",
                          "night": True}, "t": 2})
    at = run(at)
    body = texts(at)
    assert "Satellite detection · not a confirmed fire" in body and "night overpass" in body


def test_table_selection_focuses_the_globe(earth):
    at = run()
    table = next(d for d in at.dataframe if "Event" in d.value.columns)
    assert len(table.value) >= 5 and "id" not in table.value.columns
    assert table.value["Official"].any() and (~table.value["Official"]).any()


def test_search_flies_to_a_country_with_its_outline(earth):
    at = run()
    at.text_input(key="earth_q").set_value("Japan").run()
    assert not at.exception
    data = SENT[-1]
    assert data["focus"]["bbox"] and data["region"]["type"] == "MultiPolygon"
    assert any("Natural Earth" in c.value for c in at.caption)


def test_events_are_filtered_to_the_searched_area(earth):
    at = run()
    everything = next(d for d in at.dataframe if "Event" in d.value.columns).value
    at.text_input(key="earth_q").set_value("United States").run()
    table = next(d for d in at.dataframe if "Event" in d.value.columns).value
    assert any(t.label.startswith("Only events in or near") for t in at.toggle)
    assert 0 < len(table) < len(everything)
    assert "Flood in Italy" in set(everything["Event"]) and "Flood in Italy" not in set(table["Event"])


def test_unsupported_and_empty_states_are_honest(earth):
    earth["broken"] = {"usgs", "gdacs", "nhc", "nws", "firms", "usdm", "usgs_volcano", "gvp", "tsunami"}
    at = run()
    assert any("doesn't mean nothing is happening" in i.value for i in at.info)
    assert any(e.label == "What isn't connected, and why" for e in at.expander)
    assert all(s["label"] == "Temporarily unavailable" for s in SENT[-1]["statuses"])


def test_custom_dates_are_limited_and_explained(earth):
    at = run()
    at.selectbox(key="earth_period").set_value("custom").run()
    assert any("USGS FDSN event service" in c.value for c in at.caption)
    at.date_input(key="earth_from").set_value(pd.Timestamp("2026-07-01").date()).run()
    assert any("at most 62 days" in e.value for e in at.error)


def test_page_is_listed_in_navigation():
    with open("streamlit_app.py") as f:
        assert '"app_pages/earth.py"' in f.read()


def test_feed_result_layers_never_include_invented_events():
    assert FeedResult().events == []


def test_earthquake_details_show_labelled_historical_seismicity(earth, monkeypatch):
    from src.hazards import seismicity
    from tests.test_seismicity import synthetic
    monkeypatch.setattr(seismicity, "fetch", lambda lat, lon, session=None, radius_km=300: synthetic())
    at = run()
    quake = next(e for e in sent_events() if e["s"] == "usgs")
    PICK.update({"id": quake["id"], "t": 5})
    at = run(at)
    body = texts(at)
    assert "historical analysis" in body and "not a prediction" in body and "b-value" in body
    assert {m.label for m in at.metric} >= {"M5+ recorded", "Largest recorded"}
    assert any(b.label == "GEM Global Seismic Hazard Map" for b in at.get("link_button"))


def test_seismicity_outage_is_explained(earth, monkeypatch):
    from src.hazards import seismicity

    def down(lat, lon, session=None, radius_km=300):
        raise http.FeedError("HTTP 503")
    monkeypatch.setattr(seismicity, "fetch", down)
    at = run()
    quake = next(e for e in sent_events() if e["s"] == "usgs")
    PICK.update({"id": quake["id"], "t": 6})
    at = run(at)
    assert "USGS catalogue isn't available right now" in texts(at)
