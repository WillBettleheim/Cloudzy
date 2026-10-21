"""Hazard providers and the shared schema: parsing real (trimmed) responses, validation, time handling,
observation/forecast labelling and errors. Fixtures in tests/fixtures/hazards are copies of live responses
captured on 2026-10-22, cut down to a few records."""

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.hazards import firms, gdacs, geometry, http, nhc, nws, tsunami, usdm, usgs, volcanoes
from src.hazards import schema as H

FIX = Path(__file__).parent / "fixtures" / "hazards"
NOW = pd.Timestamp("2026-10-21T21:10Z")


def load(name):
    return json.loads((FIX / name).read_text())


# --- Schema --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lat, lon, expected", [
    (28.6, 77.2, (28.6, 77.2)), ("10", "-20", (10.0, -20.0)), (0, 190, (0, -170)), (0, -181, (0, 179)),
    (91, 0, None), (None, 1, None), ("x", 1, None), (float("nan"), 1, None), (0, float("inf"), None),
])
def test_valid_coords(lat, lon, expected):
    assert H.valid_coords(lat, lon) == (pytest.approx(expected) if expected else None)


def test_utc_normalises_epochs_iso_and_naive_text():
    assert H.utc(1792614620350, "ms") == pd.Timestamp("2026-10-21T20:30:20.350Z")
    assert H.utc("2026-10-21T18:00:00") == pd.Timestamp("2026-10-21T18:00Z")          # naive → UTC
    assert H.utc("2026-10-21T13:00:00-05:00") == pd.Timestamp("2026-10-21T18:00Z")
    assert H.utc("not a date") is None and H.utc("") is None and H.utc(None) is None


@pytest.mark.parametrize("kw, label", [
    ({"ok": False, "has_data": False}, H.UNAVAILABLE),
    ({"ok": True, "has_data": True, "historical": True}, H.HISTORICAL),
    ({"ok": False, "has_data": True, "using_previous": True}, H.DELAYED),
    ({"ok": True, "has_data": True, "data_time": NOW - timedelta(minutes=2)}, H.LIVE),
    ({"ok": True, "has_data": True, "data_time": NOW - timedelta(minutes=10)}, H.DELAYED),   # 1-min feed, 10 min old
])
def test_freshness_labels_follow_data_age_and_cadence(kw, label):
    args = {"data_time": None, "now": NOW, "cadence": timedelta(minutes=1)} | kw
    assert H.freshness(**args) == label


def test_slow_feeds_are_recent_not_live_and_quiet_event_feeds_are_not_delayed():
    assert H.freshness(True, True, NOW - timedelta(hours=4), NOW, timedelta(hours=6)) == H.RECENT
    assert H.freshness(True, True, NOW - timedelta(days=30), NOW, timedelta(days=7)) == H.DELAYED
    # A tsunami feed with no bulletin for two days, checked a minute ago, is current, not delayed.
    assert H.freshness(True, True, NOW - timedelta(days=2), NOW, timedelta(minutes=5), event_driven=True,
                       fetched_at=NOW - timedelta(minutes=1)) == H.LIVE


def test_event_json_has_utc_iso_times():
    e = H.HazardEvent("x:1", "usgs", "earthquake", "observation", "t", 1, 2, NOW, NOW)
    assert e.to_json()["time"] == "2026-10-21T21:10:00Z"


# --- USGS ---------------------------------------------------------------------------------------------

def test_usgs_feed_parses_magnitude_depth_status_and_time():
    res = usgs.parse(load("usgs_all_day.json"))
    assert res.data_time == H.utc(load("usgs_all_day.json")["metadata"]["generated"], "ms")
    e = res.events[0]
    assert e.id.startswith("usgs:") and e.category == "earthquake" and e.kind == "observation" and e.official
    assert e.time.tzinfo is not None and e.url.startswith("https://earthquake.usgs.gov/")
    assert e.measurements["Depth"].endswith(" km") and e.measurements["Review status"] in ("automatic", "reviewed")
    assert e.severity_label.startswith("M ")


def test_usgs_skips_malformed_and_non_earthquakes():
    p = load("usgs_all_day.json")
    p["features"][0]["geometry"]["coordinates"] = [500, 100, 5]
    p["features"][1]["properties"]["type"] = "quarry blast"
    res = usgs.parse(p)
    assert len(res.events) == 4 and res.warnings and "valid coordinates" in res.warnings[0]
    with pytest.raises(http.FeedError):
        usgs.parse({"type": "FeatureCollection"})


def test_usgs_tsunami_flag_is_not_called_a_tsunami():
    p = load("usgs_all_day.json")
    p["features"][0]["properties"]["tsunami"] = 1
    m = usgs.parse(p).events[0].measurements["Tsunami flag"]
    assert "not a tsunami report" in m


@pytest.mark.parametrize("mag, rank", [(None, 0), (2.5, 1), (4.0, 2), (5.9, 2), (6.0, 3), (7.0, 4), (8.1, 4)])
def test_magnitude_rank(mag, rank):
    assert usgs.magnitude_rank(mag) == rank


def test_pager_alert_raises_rank_and_magnitudes_look_different():
    p = load("usgs_all_day.json")
    p["features"][0]["properties"].update(mag=5.2, alert="red")
    e = usgs.parse(p).events[0]
    assert e.severity_rank == 4 and "PAGER red" in e.severity_label


def test_usgs_fetch_filters_magnitude_and_archive_queries_are_not_live(monkeypatch):
    calls = []

    def fake(url, params=None, **kw):
        calls.append((url, params))
        return load("usgs_all_day.json")
    monkeypatch.setattr(http, "get", fake)
    mags = [e.extra["mag"] for e in usgs.fetch("day", min_mag=2.0).events]
    assert all(m >= 2.0 for m in mags)
    res = usgs.fetch(min_mag=4.5, start=NOW - pd.Timedelta(days=10), end=NOW)
    assert calls[-1][0] == usgs.FDSN_URL and calls[-1][1]["minmagnitude"] == 4.5 and res.data_time is None


# --- GDACS ---------------------------------------------------------------------------------------------

def test_gdacs_events_are_automatic_estimates_never_official():
    res = gdacs.parse_list(load("gdacs_list.json"))
    assert {e.category for e in res.events} >= {"flood", "cyclone", "earthquake", "wildfire"}
    for e in res.events:
        assert e.kind == "impact_estimate" and not e.official
        assert "Not an official warning" in e.severity_definition
        assert e.measurements["GDACS alert level"].endswith("(automatic impact estimate)")
    flood = next(e for e in res.events if e.category == "flood")
    assert "flood model" in flood.measurements.get("Data source", "") or flood.extra["source"] != "GLOFAS"


@pytest.mark.parametrize("label, ref, expected", [
    ("21/10 18:00 UTC", "2026-10-21T18:00Z", "2026-10-21T18:00Z"),
    ("02/01 06:00 UTC", "2026-12-30T00:00Z", "2027-01-02T06:00Z"),     # across New Year
    ("30/12 06:00 UTC", "2027-01-02T00:00Z", "2026-12-30T06:00Z"),
    ("nonsense", "2026-10-21T18:00Z", None),
])
def test_track_time_year(label, ref, expected):
    got = gdacs.track_time(label, pd.Timestamp(ref))
    assert got == (pd.Timestamp(expected) if expected else None)


def test_gdacs_cyclone_geometry_separates_observed_and_forecast_positions():
    advisory = pd.Timestamp("2026-10-21T18:00Z")
    g = gdacs.parse_cyclone_geometry(load("gdacs_tc_geometry.json"), advisory)
    pts = g["points"]
    assert [p["time"] for p in pts] == sorted(p["time"] for p in pts)
    assert all((p["status"] == "forecast") == (p["time"] > advisory) for p in pts)
    assert sum(p["status"] == "observed" for p in pts) == 30 and sum(p["status"] == "forecast" for p in pts) == 8
    assert g["cone"]["type"] in ("Polygon", "MultiPolygon")
    assert all("GDACS estimate" in w["label"] for w in g["wind"])


def test_gdacs_no_content_means_no_events(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, params=None, **kw: None)     # HTTP 204
    assert gdacs._fetch_type("VO", 30, NOW, None).events == []


# --- NHC -----------------------------------------------------------------------------------------------

def test_nhc_current_storm_wind_is_labelled_one_minute_sustained():
    res = nhc.parse_current(load("nhc_current.json"))
    e = res.events[0]
    assert e.title == "Tropical Storm Vance" and e.official and e.kind == "warning"
    assert e.measurements["Maximum sustained wind"] == "35 kt (65 km/h), 1-minute average"
    assert e.measurements["Minimum central pressure"] == "1003 mb" and e.severity_rank == 2


@pytest.mark.parametrize("kt, rank", [(None, 0), (30, 1), (34, 2), (64, 3), (96, 4)])
def test_nhc_wind_rank(kt, rank):
    assert nhc.wind_rank(kt) == rank


def test_nhc_cone_and_tracks_from_kmz():
    cone = nhc.parse_cone((FIX / "nhc_cone.kmz").read_bytes())
    assert cone["type"] == "Polygon" and len(cone["coordinates"][0]) > 10
    fc = nhc.parse_forecast_points((FIX / "nhc_track.kmz").read_bytes())
    assert fc and all(p["status"] == "forecast" for p in fc)
    assert fc[0]["time"] == pd.Timestamp("2026-10-22T06:00Z") and fc[0]["hours"] == 12      # 12:00 AM CST
    best = nhc.parse_best_track((FIX / "nhc_best.kmz").read_bytes())
    assert best and all(p["status"] == "observed" for p in best) and best[-1]["time"] < fc[0]["time"]


@pytest.mark.parametrize("text, expected", [
    ("12:00 AM CST October 22, 2026", "2026-10-22T06:00Z"), ("5:00 PM EDT August 03, 2026", "2026-08-03T21:00Z"),
    ("2:00 PM HST July 10, 2026", "2026-07-11T00:00Z"), ("8:00 AM XYZ May 1, 2026", None), ("", None),
])
def test_nhc_valid_time_zones(text, expected):
    assert nhc.parse_valid_time(text) == (pd.Timestamp(expected) if expected else None)


def test_nhc_fetch_keeps_the_storm_when_a_track_file_fails(monkeypatch):
    def fake(url, params=None, kind="json", **kw):
        if url == nhc.CURRENT_URL:
            return load("nhc_current.json")
        if "CONE" in url:
            return (FIX / "nhc_cone.kmz").read_bytes()
        raise http.FeedError("HTTP 404")
    monkeypatch.setattr(http, "get", fake)
    res = nhc.fetch()
    e = res.events[0]
    assert e.geometry["type"] == "Polygon" and [p["status"] for p in e.extra["track"]] == ["current"]
    assert any("track unavailable" in w for w in res.warnings)


# --- NWS -----------------------------------------------------------------------------------------------

def test_nws_polygons_are_drawn_and_zone_alerts_listed():
    res = nws.parse(load("nws_active.json"))
    drawn = [e for e in res.events if e.lat is not None]
    zone = [e for e in res.events if e.lat is None]
    assert len(drawn) == 2 and all(e.geometry for e in drawn) and all(e.category == "severe" for e in drawn)
    assert zone and all(e.measurements["Map"].startswith("Issued for forecast zones") for e in zone)
    assert "2 of 6 active alerts have a polygon" in res.note
    marine = [e for e in res.events if e.title == "Small Craft Advisory"]
    assert marine and all(e.category == "other" and e.extra["marine"] for e in marine)
    assert all(e.official and e.kind == "warning" and e.country == "United States" for e in res.events)


@pytest.mark.parametrize("event, cat", [
    ("Tornado Warning", "severe"), ("Flash Flood Warning", "flood"), ("Coastal Flood Advisory", "flood"),
    ("Hurricane Warning", "cyclone"), ("Hurricane Force Wind Warning", "other"), ("Storm Surge Watch", "cyclone"),
    ("Extreme Cold Warning", "temperature"), ("Excessive Heat Warning", "temperature"), ("Blizzard Warning", "winter"),
    ("Tsunami Warning", "tsunami"), ("Red Flag Warning", "wildfire"), ("Dust Storm Warning", "severe"),
    ("Gale Warning", "other"), ("Air Quality Alert", "other"), ("Storm Warning", "other"),
    ("Winter Storm Warning", "winter"), ("Tropical Storm Warning", "cyclone"), ("Ice Storm Warning", "winter"),
])
def test_nws_category_mapping(event, cat):
    assert nws.category_for(event) == cat


def test_nws_exercises_and_tests_are_never_shown():
    p = load("nws_active.json")
    p["features"][0]["properties"]["status"] = "Test"
    p["features"][1]["properties"]["status"] = "Exercise"
    assert len(nws.parse(p).events) == 4


# --- FIRMS ---------------------------------------------------------------------------------------------

def test_firms_detections_become_a_compact_layer_not_events():
    res = firms.parse((FIX / "firms_24h.csv").read_text())
    layer = res.layers["fires"]
    assert res.events == [] and len(layer["lat"]) == 40 == len(layer["conf"])
    assert set(layer["conf"]) <= {"l", "n", "h", "u"} and min(layer["minute"]) == 0
    assert isinstance(layer["lat"][0], int) and layer["scale"] == 1000
    assert res.data_time is not None and "detections" in res.note


def test_firms_bad_rows_are_skipped_and_missing_columns_fail():
    text = (FIX / "firms_24h.csv").read_text().splitlines()
    text.append("999,999,1,1,1,2026-10-21,1200,N20,n,2,1,5,D")
    res = firms.parse("\n".join(text))
    assert len(res.layers["fires"]["lat"]) == 40 and res.warnings
    with pytest.raises(http.FeedError):
        firms.parse("a,b\n1,2\n")


# --- USDM -----------------------------------------------------------------------------------------------

DROUGHT = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"DM": d}, "geometry": {"type": "Polygon", "coordinates": [[
        [-100 + d, 35], [-99.999 + d, 35.0004], [-99 + d, 35], [-99 + d, 36], [-100 + d, 36], [-100 + d, 35]]]}}
    for d in range(5)] + [{"type": "Feature", "properties": {"DM": 9}, "geometry": None}]}


def test_usdm_latest_tuesday():
    assert usdm.latest_tuesday(pd.Timestamp("2026-10-22T10:00Z")) == pd.Timestamp("2026-10-20", tz="UTC")
    assert usdm.latest_tuesday(pd.Timestamp("2026-10-20T01:00Z")) == pd.Timestamp("2026-10-20", tz="UTC")


def test_usdm_steps_back_until_a_map_is_released(monkeypatch):
    asked = []

    def fake(url, params=None, **kw):
        asked.append(url)
        if "20261020" in url:
            raise http.FeedError("HTTP 404: not released")
        return DROUGHT
    monkeypatch.setattr(http, "get", fake)
    res = usdm.fetch(now=pd.Timestamp("2026-10-22T10:00Z"))
    assert [u[-13:-5] for u in asked] == ["20261020", "20261013"]
    layer = res.layers["drought"]
    assert layer["valid"] == "2026-10-13" and [f["properties"]["dm"] for f in layer["features"]] == [0, 1, 2, 3, 4]
    assert len(layer["features"][0]["geometry"]["coordinates"][0]) < 6          # simplified


# --- Volcanoes and tsunami ------------------------------------------------------------------------------------

def test_usgs_volcano_alert_levels_are_official_and_ranked():
    res = volcanoes.parse_usgs(load("usgs_volcano_elevated.json"))
    assert res.events and all(e.official and e.kind == "warning" for e in res.events)
    for e in res.events:
        lvl, col = e.measurements["Volcano Alert Level"].upper(), e.measurements["Aviation colour code"].upper()
        assert e.severity_rank == max(volcanoes.LEVEL_RANK.get(lvl, 0), volcanoes.COLOR_RANK.get(col, 0))


def test_gvp_catalogue_is_a_layer_not_activity():
    res = volcanoes.parse_gvp(load("gvp.json"))
    assert res.events == [] and "not current activity" in res.note
    assert res.layers["volcano_catalogue"]["features"][0]["properties"]["url"].startswith("https://volcano.si.edu/")


def test_tsunami_bulletin_category_and_currency():
    text = (FIX / "tsunami_ntwc.xml").read_text()
    e = tsunami.parse(text, "NTWC", pd.Timestamp("2026-10-20T12:00Z")).events[0]
    assert e.severity_label == "Information (NTWC)" and e.severity_rank == 1 and e.current
    assert e.summary == "There is NO tsunami danger from this earthquake." and e.official
    later = tsunami.parse(text, "NTWC", pd.Timestamp("2026-10-23T12:00Z")).events[0]
    assert not later.current
    with pytest.raises(http.FeedError):
        tsunami.parse("<not xml", "NTWC", NOW)


# --- HTTP helper -----------------------------------------------------------------------------------------

class Resp:
    def __init__(self, code, body=None, headers=None):
        self.status_code, self._b, self.headers, self.text, self.content = code, body, headers or {}, str(body), b"x"

    def json(self):
        if self._b is None:
            raise ValueError
        return self._b


class Session:
    def __init__(self, *responses):
        self.r = list(responses)

    def get(self, *a, **k):
        r = self.r.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def real_get(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    return REAL_GET


REAL_GET = http.get


def test_http_rate_limit_with_long_wait_fails_fast(real_get):
    with pytest.raises(http.FeedError, match="rate-limited"):
        real_get("u", session=Session(Resp(429, {"reason": "slow down"}, {"Retry-After": "600"})))


def test_http_retries_server_errors_and_timeouts(real_get):
    import requests
    assert real_get("u", session=Session(Resp(503, {"reason": "busy"}), Resp(200, {"ok": 1}))) == {"ok": 1}
    with pytest.raises(http.FeedError, match="no response"):
        real_get("u", session=Session(requests.Timeout(), requests.Timeout()))
    with pytest.raises(http.FeedError, match="HTTP 404"):
        real_get("u", session=Session(Resp(404, {"reason": "nope"})))
    assert real_get("u", session=Session(Resp(204))) is None


# --- Geometry ------------------------------------------------------------------------------------------------

def test_haversine_known_distance():
    assert float(geometry.haversine_km(28.6139, 77.2090, 19.0760, 72.8777)) == pytest.approx(1150, abs=10)


def test_contains_handles_holes_and_multipolygons():
    square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    poly = {"type": "Polygon", "coordinates": [square, hole]}
    assert list(geometry.contains(poly, [2, 5, 20], [2, 5, 20])) == [True, False, False]
    multi = {"type": "MultiPolygon", "coordinates": [[square], [[[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]]]]}
    assert list(geometry.contains(multi, [25, 15], [25, 15])) == [True, False]


def test_simplify_keeps_closed_rings_and_distance_to_track():
    ring = [[0, 0], [0.5, 0.001], [1, 0], [1, 1], [0, 1], [0, 0]]
    s = geometry.simplify(ring, 0.01)
    assert s[0] == s[-1] and len(s) == 5
    d = geometry.distance_to_track_km(np.array([0.0, 1.0]), np.array([0.5, 0.5]), [(0, 0), (0, 1)])
    # Sampled every ~10 km along the track, so within about 5 km (documented); fine for 100 km radii.
    assert d[0] == pytest.approx(0, abs=6) and d[1] == pytest.approx(111, abs=6)
