"""Tests for local preferences, regional unit defaults and interactive request handling."""

import json

import pytest
import requests

from src import data_collection as dc
from src import preferences as P
from src import units as U
from src.provider import Place

PARIS_FR = Place("Paris", 48.8534, 2.3488, "Europe/Paris", "France", "Île-de-France", 2138551, "FR")
PARIS_TX = Place("Paris", 33.6609, -95.5555, "America/Chicago", "United States", "Texas", 24782, "US")


def test_missing_file_gives_defaults(tmp_path):
    p = P.load(tmp_path / "none.json")
    assert p.units is None and p.favourites == [] and p.alerts.uv_index == 8.0


def test_round_trip(tmp_path):
    path = tmp_path / "prefs.json"
    p = P.Preferences(units={"temperature": "°F", "wind": "kn", "precipitation": "in"})
    p.toggle_favourite(PARIS_TX)
    p.alerts.uv_index = 6.0
    P.save(p, path)
    q = P.load(path)
    assert q.unit_override() == U.Units("°F", "kn", "in")
    assert q.favourite_places() == [PARIS_TX]
    assert q.alerts.uv_index == 6.0
    assert not path.with_suffix(".json.tmp").exists()  # atomic write cleaned up


def test_same_name_favourites_are_kept_apart():
    p = P.Preferences()
    assert p.toggle_favourite(PARIS_FR) is True
    assert p.toggle_favourite(PARIS_TX) is True
    assert len(p.favourite_places()) == 2
    assert p.toggle_favourite(PARIS_FR) is False      # removes only the French Paris
    assert p.favourite_places() == [PARIS_TX]


def test_favourite_limit():
    p = P.Preferences()
    for i in range(P.MAX_FAVOURITES):
        p.toggle_favourite(Place(f"P{i}", float(i), float(i), "UTC"))
    with pytest.raises(ValueError, match="up to"):
        p.toggle_favourite(Place("one more", 80.0, 80.0, "UTC"))


@pytest.mark.parametrize("content", ["{not json", "[]", '"text"'])
def test_corrupt_file_falls_back_to_defaults(tmp_path, content):
    path = tmp_path / "prefs.json"
    path.write_text(content)
    p = P.load(path)
    assert p.units is None and p.favourites == []


def test_invalid_parts_are_dropped_individually(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({
        "units": {"temperature": "K", "wind": "km/h", "precipitation": "mm"},     # invalid unit
        "favourites": [{"name": "Ok", "latitude": 1.0, "longitude": 2.0, "timezone": "UTC"}, "junk"],
        "alerts": {"uv_index": 99, "rain_probability_pct": 70},                    # 99 is out of range
    }))
    p = P.load(path)
    assert p.units is None
    assert [f.name for f in p.favourite_places()] == ["Ok"]
    assert p.alerts.uv_index == 8.0 and p.alerts.rain_probability_pct == 70.0


def test_prefs_path_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDZY_PREFS_PATH", str(tmp_path / "x.json"))
    assert P.prefs_path() == tmp_path / "x.json"


@pytest.mark.parametrize("code, expected", [("US", U.IMPERIAL), ("us", U.IMPERIAL), ("LR", U.IMPERIAL),
                                            ("GB", U.UK), ("IN", U.METRIC), (None, U.METRIC)])
def test_regional_unit_defaults(code, expected):
    assert U.default_for_country(code) == expected


def test_inverse_conversions():
    assert U.to_celsius(212, "°F") == pytest.approx(100)
    assert U.to_kmh(U.wind(50, "kn"), "kn") == pytest.approx(50)
    assert U.to_celsius(None, "°F") is None


# --- Interactive request handling ----------------------------------------------------------

class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers, self.text = status, body, headers or {}, ""

    def json(self):
        return self._body


class Session:
    def __init__(self, items):
        self.items, self.timeouts = list(items), []

    def get(self, url, params=None, timeout=None):
        self.timeouts.append(timeout)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(dc.time, "sleep", calls.append)
    return calls


def test_rate_limit_respects_retry_after(sleeps):
    s = Session([Resp(429, {"reason": "slow down"}, {"Retry-After": "3"}), Resp(200, {"ok": 1})])
    assert dc.fetch_json("u", {}, session=s, **dc.INTERACTIVE) == {"ok": 1}
    assert sleeps == [3.0]


def test_rate_limit_with_long_wait_fails_fast(sleeps):
    s = Session([Resp(429, {}, {"Retry-After": "120"})])
    with pytest.raises(dc.DataCollectionError, match="Rate limited.*120 s"):
        dc.fetch_json("u", {}, session=s, **dc.INTERACTIVE)
    assert sleeps == []


def test_interactive_timeout_and_clear_message(sleeps):
    s = Session([requests.Timeout(), requests.Timeout()])
    with pytest.raises(dc.DataCollectionError, match="didn't respond within 10 s"):
        dc.fetch_json("u", {}, session=s, **dc.INTERACTIVE)
    assert s.timeouts == [10, 10] and len(sleeps) == 1


def test_connection_error_message(sleeps):
    s = Session([requests.ConnectionError("dns"), requests.ConnectionError("dns")])
    with pytest.raises(dc.DataCollectionError, match="couldn't connect"):
        dc.fetch_json("u", {}, session=s, **dc.INTERACTIVE)


def test_server_error_then_recovery(sleeps):
    s = Session([Resp(503, {"reason": "busy"}), Resp(200, {"ok": 2})])
    assert dc.fetch_json("u", {}, session=s, **dc.INTERACTIVE) == {"ok": 2}


def test_new_settings_round_trip(tmp_path):
    import dataclasses
    path = tmp_path / "prefs.json"
    p = P.load(path)
    assert p.enabled_alert_kinds() == set(P.ALERT_KINDS) and p.summary_style == "detailed" and not p.notifications
    p.alert_categories["uv"] = False
    p.summary_style = "brief"
    p.notifications = True
    p.home = dataclasses.asdict(PARIS_FR)
    p.dismiss("rain|15:00–18:00 Mon", "2026-10-20T00:00:00+00:00")
    P.save(p, path)
    q = P.load(path)
    assert "uv" not in q.enabled_alert_kinds() and q.summary_style == "brief" and q.notifications
    assert q.home_place() == PARIS_FR
    assert q.dismissed_alerts == {"rain|15:00–18:00 Mon": "2026-10-20T00:00:00+00:00"}
    assert "latitude" not in json.dumps({k: v for k, v in json.loads(path.read_text()).items() if k != "home"
                                         and k != "favourites"})  # no coordinates outside saved cities


def test_dismissals_expire(tmp_path):
    import pandas as pd
    p = P.load(tmp_path / "x.json")
    p.dismiss("a", "2026-10-19T12:00:00+00:00")
    p.dismiss("b", "not a time")
    assert p.active_dismissals(pd.Timestamp("2026-10-19T11:00Z")) == {"a"}
    assert p.active_dismissals(pd.Timestamp("2026-10-19T13:00Z")) == set() and p.dismissed_alerts == {}


def test_bad_new_settings_fall_back(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({"summary_style": "poetic", "alert_categories": {"rain": False, "evil": True},
                                "home": {"name": "x"}, "notifications": "yes"}))
    p = P.load(path)
    assert p.summary_style == "detailed" and p.enabled_alert_kinds() == set(P.ALERT_KINDS) - {"rain"}
    assert p.home is None and p.notifications is False
