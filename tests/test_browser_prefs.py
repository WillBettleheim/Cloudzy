"""Per-visitor settings: browser storage instead of one shared file on the server."""

import json
from dataclasses import asdict

import pytest
from streamlit.testing.v1 import AppTest

from src import browser_prefs as B
from src import preferences as P
from tests.test_app import fake_network  # noqa: F401  (fixture)
from tests.test_preferences import PARIS_FR

PARIS = asdict(PARIS_FR)


@pytest.fixture
def browser_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUDZY_PREFS_PATH", raising=False)
    monkeypatch.setenv("CLOUDZY_PREFS_STORE", "browser")
    monkeypatch.chdir(tmp_path)  # any stray settings file would show up here
    return tmp_path


class FakeStreamlit:
    def __init__(self):
        self.session_state = {}


@pytest.fixture
def visitors(monkeypatch, browser_mode):
    """Two separate browser sessions on the same server."""
    a, b = FakeStreamlit(), FakeStreamlit()
    active = {"st": a}
    monkeypatch.setattr(B, "_st", lambda: active["st"])
    return a, b, active


# --- Store selection ------------------------------------------------------------------------------

def test_store_selection(monkeypatch):
    monkeypatch.delenv("CLOUDZY_PREFS_PATH", raising=False)
    monkeypatch.delenv("CLOUDZY_PREFS_STORE", raising=False)
    assert P.store() == "browser"                      # the default: per visitor
    monkeypatch.setenv("CLOUDZY_PREFS_PATH", "/tmp/x.json")
    assert P.store() == "file"                         # an explicit file path means file mode
    monkeypatch.setenv("CLOUDZY_PREFS_STORE", "browser")
    assert P.store() == "browser"                      # an explicit choice wins
    monkeypatch.setenv("CLOUDZY_PREFS_STORE", "nonsense")
    assert P.store() == "file"


# --- Untrusted stored data --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {**PARIS, "latitude": "48.8"},          # wrong type
    {**PARIS, "latitude": float("nan")},
    {**PARIS, "latitude": 123.0},           # out of range
    {**PARIS, "timezone": "Mars/Olympus"},  # unknown zone
    {**PARIS, "timezone": "../../etc"},
    {**PARIS, "name": ""},
    {**PARIS, "population": "lots"},
    {**PARIS, "evil": 1},                   # unknown field
    "Paris", None, 42,
])
def test_tampered_places_are_dropped(bad):
    prefs = P.from_dict({"favourites": [bad, PARIS], "home": bad})
    assert prefs.favourite_places() == [PARIS_FR] and prefs.home is None


def test_bad_or_oversized_json_gives_defaults():
    assert P.parse_json("{not json") == P.Preferences()
    assert P.parse_json("[1, 2]") == P.Preferences()
    assert P.parse_json(None) == P.Preferences()
    huge = json.dumps({"summary_style": "brief", "pad": "x" * (P.MAX_STORED_CHARS + 1)})
    assert P.parse_json(huge).summary_style == "detailed"


def test_threshold_booleans_are_not_numbers():
    assert P.from_dict({"alerts": {"uv_index": True}}).alerts.uv_index == 8.0


# --- Session flow ----------------------------------------------------------------------------------

def test_saved_settings_are_read_once_and_written_back():
    state = {}
    B.apply_loaded({"value": json.dumps({"summary_style": "brief", "home": PARIS}), "available": True}, state)
    assert B.ready(state) and B.storage_available(state) is True
    assert P.from_dict(B.current(state)).home_place() == PARIS_FR
    assert B.pending_write(state) is None                         # nothing changed yet
    B.stage({**B.current(state), "summary_style": "detailed"}, state)
    assert json.loads(B.pending_write(state))["summary_style"] == "detailed"


def test_first_visit_and_blocked_storage():
    state = {}
    B.apply_loaded({"value": None, "available": True}, state)
    assert B.current(state) == {}
    blocked = {}
    B.apply_loaded({"value": None, "available": False}, blocked)
    B.stage({"summary_style": "brief"}, blocked)
    assert B.ready(blocked) and B.pending_write(blocked) is None   # session-only, never written


def test_skipping_never_overwrites_stored_settings():
    state = {}
    B.skip(state)
    B.stage({"summary_style": "brief"}, state)
    assert B.ready(state) and B.pending_write(state) is None


def test_garbage_from_the_browser_is_safe():
    for payload in (None, "x", {"value": 12}, {"value": "{broken"}, {"available": "maybe"}):
        state = {}
        B.apply_loaded(payload, state)
        assert B.ready(state) and P.from_dict(B.current(state)) == P.Preferences()


def test_change_made_before_the_answer_arrives_is_kept():
    state = {}
    B.stage({"summary_style": "brief"}, state)
    B.apply_loaded({"value": json.dumps({"summary_style": "detailed"}), "available": True}, state)
    assert B.current(state)["summary_style"] == "brief" and B.pending_write(state)


# --- Two visitors, one server ------------------------------------------------------------------------

def test_visitors_never_share_settings(visitors, browser_mode):
    a, b, active = visitors
    for v in (a, b):
        B.apply_loaded({"value": None, "available": True}, v.session_state)
    active["st"] = a
    prefs = P.load()
    prefs.toggle_favourite(PARIS_FR)
    prefs.home = PARIS
    prefs.summary_style = "brief"
    P.save(prefs)
    active["st"] = b
    assert P.load() == P.Preferences()                  # B sees none of A's settings
    P.save(P.Preferences())                             # B resets: A is untouched
    active["st"] = a
    assert P.load().home_place() == PARIS_FR and P.load().summary_style == "brief"
    assert not list(browser_mode.rglob("*.json"))       # nothing written on the server


# --- The real entry point -----------------------------------------------------------------------------

def run_app(at=None):
    if at is None:  # not `at or ...`: an AppTest with no elements yet is falsy
        at = AppTest.from_file("../streamlit_app.py", default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_app_waits_for_the_browser_then_offers_to_continue(browser_mode, fake_network):  # noqa: F811
    at = run_app()
    assert any("Loading your settings" in i.value for i in at.info)
    assert not [t for t in at.text_input if t.label.startswith("Search for a city")]   # page not drawn yet
    next(b for b in at.button if b.label == "Continue without saved settings").click().run()
    assert not at.exception
    assert at.session_state["_cz_prefs_state"] == "skipped"
    assert [t for t in at.text_input if t.label.startswith("Search for a city")]


def test_saved_home_city_opens_first(browser_mode, fake_network):  # noqa: F811
    at = AppTest.from_file("../streamlit_app.py", default_timeout=60)
    at.session_state["_cz_prefs_state"] = "loaded"
    at.session_state["_cz_prefs_storage_ok"] = True
    at.session_state["_cz_prefs"] = P.to_dict(P.from_dict({"home": PARIS, "favourites": [PARIS]}))
    at = run_app(at)
    # The landing page is now Overview: it opens the saved home city, and Cloudzy Core says it's Delhi-only.
    assert any("Paris, Île-de-France, France" in m.value for m in at.markdown)
    body = " ".join(h.proto.body for h in at.html)
    assert "only predicts for New Delhi" in body


# --- Other shared server state stays bounded --------------------------------------------------------

def test_place_name_cache_is_bounded(monkeypatch):
    from src import geolocation as G
    G._cache.clear()
    monkeypatch.setattr(G, "CACHE_MAX", 3)
    for i in range(5):
        G._cache_put((float(i), 0.0), {"name": str(i)})
    assert list(G._cache) == [(2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]       # oldest dropped first
    assert G._cache_get((3.0, 0.0)) == (True, {"name": "3"})
    G._cache_put((5.0, 0.0), None)                                      # 2.0 is now the oldest
    assert (2.0, 0.0) not in G._cache and (3.0, 0.0) in G._cache
    G._cache.clear()


def test_old_live_snapshots_are_pruned(tmp_path):
    import os
    import time

    from src import config, service
    now = time.time()
    old = tmp_path / f"delhi_{config.LIVE_MODEL}_2026-08-01T000000Z.json"
    new = tmp_path / f"delhi_{config.LIVE_MODEL}_2026-10-19T000000Z.json"
    other = tmp_path / "notes.json"
    for f in (old, new, other):
        f.write_text("{}")
    os.utime(old, (now - 40 * 86400, now - 40 * 86400))
    os.utime(other, (now - 40 * 86400, now - 40 * 86400))
    assert service.prune_snapshots(tmp_path, now=now) == 1
    assert not old.exists() and new.exists() and other.exists()       # only snapshot files are touched
