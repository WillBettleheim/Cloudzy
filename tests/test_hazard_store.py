"""Hazard store (caching, background refresh, failures, merging, static files), analysis and place search."""

import json
import os
import threading
from pathlib import Path

import pandas as pd

from src import earth_search as ES
from src.hazards import analysis as A
from src.hazards import store as STORE
from src.hazards.schema import DELAYED, LIVE, UNAVAILABLE, FeedResult, HazardEvent
from src.hazards.sources import SOURCES

T0 = pd.Timestamp("2026-10-21T21:00Z")
REAL_NOMINATIM = ES.nominatim           # captured at import, before conftest replaces it with a stub


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def quake(i, lat=28.6, lon=77.2, t=T0, mag=5.0):
    return HazardEvent(f"usgs:q{i}", "usgs", "earthquake", "observation", f"M {mag} quake {i}", lat, lon, t, t,
                       severity_rank=2, official=True, extra={"mag": mag, "depth_km": 10.0})


def ok_feed(events, data_time=T0, layers=None):
    return lambda q: FeedResult(events=list(events), data_time=data_time, layers=layers or {})


def make_store(fetchers, clock=None):
    return STORE.HazardStore(fetchers, clock=clock or Clock(), workers=2)


# --- Caching, refresh and failures --------------------------------------------------------------------------

def test_first_snapshot_waits_then_reuses_cache_until_ttl():
    calls = {"n": 0}

    def usgs_fetch(q):
        calls["n"] += 1
        return FeedResult(events=[quake(1)], data_time=T0)
    clock = Clock()
    s = make_store({"usgs": usgs_fetch}, clock)
    snap = s.snapshot()
    assert [e.id for e in snap.events] == ["usgs:q1"] and snap.statuses["usgs"].label == LIVE
    s.snapshot()
    assert calls["n"] == 1                                  # within the 2-minute TTL: no new request
    clock.t = T0 + SOURCES["usgs"].ttl + pd.Timedelta(seconds=1)
    s.snapshot()
    s._entries[("usgs", STORE.Query())].future and s._entries[("usgs", STORE.Query())].future.result(5)
    assert calls["n"] == 2


def test_concurrent_reads_share_one_request():
    gate, calls = threading.Event(), {"n": 0}

    def slow(q):
        calls["n"] += 1
        gate.wait(5)
        return FeedResult(events=[quake(1)], data_time=T0)
    s = make_store({"usgs": slow})
    threads = [threading.Thread(target=s.snapshot, kwargs={"wait_s": 5}) for _ in range(4)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join()
    assert calls["n"] == 1


def test_stale_data_is_served_while_refreshing():
    gate = threading.Event()
    clock = Clock()
    results = iter([FeedResult(events=[quake(1)], data_time=T0), None])

    def fetch(q):
        r = next(results)
        if r is None:
            gate.wait(5)
            return FeedResult(events=[quake(2)], data_time=T0)
        return r
    s = make_store({"usgs": fetch}, clock)
    s.snapshot()
    clock.t = T0 + pd.Timedelta(minutes=5)
    snap = s.snapshot(wait_s=0.1)                         # refresh is running; old data come back at once
    assert [e.id for e in snap.events] == ["usgs:q1"]
    gate.set()


def test_failed_refresh_keeps_last_good_data_marked_delayed():
    clock = Clock()
    state = {"fail": False}

    def fetch(q):
        if state["fail"]:
            raise STORE.http.FeedError("HTTP 503")
        return FeedResult(events=[quake(1)], data_time=T0)
    s = make_store({"usgs": fetch}, clock)
    s.snapshot()
    state["fail"] = True
    clock.t = T0 + pd.Timedelta(minutes=5)
    s.snapshot()
    s._entries[("usgs", STORE.Query())].future and s._entries[("usgs", STORE.Query())].future.result(5)
    snap = s.snapshot()
    st = snap.statuses["usgs"]
    assert [e.id for e in snap.events] == ["usgs:q1"] and st.using_previous and st.label == DELAYED
    assert "HTTP 503" in st.error


def test_a_source_that_never_loads_is_unavailable_and_others_still_work():
    def broken(q):
        raise STORE.http.FeedError("couldn't connect")
    s = make_store({"usgs": ok_feed([quake(1)]), "nws": broken})
    snap = s.snapshot()
    assert snap.statuses["nws"].label == UNAVAILABLE and "couldn't connect" in snap.statuses["nws"].error
    assert snap.statuses["usgs"].label == LIVE and len(snap.events) == 1


def test_failed_source_is_not_hammered():
    calls = {"n": 0}
    clock = Clock()

    def broken(q):
        calls["n"] += 1
        raise STORE.http.FeedError("down")
    s = make_store({"nws": broken}, clock)
    s.snapshot()
    clock.t += pd.Timedelta(seconds=10)
    s.snapshot()
    assert calls["n"] == 1                              # retried only after RETRY_AFTER
    clock.t += STORE.RETRY_AFTER
    s.snapshot()
    assert calls["n"] == 2


def test_query_dependent_sources_cache_per_query():
    seen = []
    s = make_store({"usgs": lambda q: (seen.append(q.window), FeedResult(events=[quake(1)]))[1],
                    "nhc": lambda q: (seen.append("nhc"), FeedResult())[1]})
    s.snapshot(STORE.Query("day"))
    s.snapshot(STORE.Query("week"))
    assert seen.count("nhc") == 1 and {"day", "week"} <= set(seen)


def test_custom_dates_are_a_historical_dataset():
    q = STORE.Query("custom", 4.5, T0 - pd.Timedelta(days=10), T0)
    snap = make_store({"usgs": ok_feed([quake(1)], data_time=None)}).snapshot(q)
    assert snap.statuses["usgs"].label == "Historical dataset"
    assert q.days() == 11


# --- Merging -------------------------------------------------------------------------------------------------

def gdacs_eq(lat=28.65, lon=77.25, t=T0 + pd.Timedelta(seconds=40)):
    return HazardEvent("gdacs:EQ1", "gdacs", "earthquake", "impact_estimate", "Earthquake in India", lat, lon, t, t,
                       severity_rank=3, severity_label="Orange alert (GDACS)", url="https://gdacs/eq1")


def test_gdacs_duplicate_of_a_usgs_quake_is_folded_into_the_official_record():
    out = STORE.merge([quake(1), gdacs_eq()])
    assert [e.id for e in out] == ["usgs:q1"]
    assert out[0].measurements["GDACS alert (automatic estimate)"] == "Orange alert (GDACS)"
    assert out[0].related == ["gdacs:EQ1"]


def test_distinct_quakes_are_not_merged():
    far = gdacs_eq(lat=10, lon=10)
    later = gdacs_eq(t=T0 + pd.Timedelta(minutes=30))
    assert len(STORE.merge([quake(1), far])) == 2 and len(STORE.merge([quake(1), later])) == 2


def test_cyclone_matched_by_name_and_merge_never_mutates_cached_records():
    nhc = HazardEvent("nhc:ep222026", "nhc", "cyclone", "warning", "Tropical Storm Vance", 9.4, -99.8, T0, T0,
                      extra={"name": "Vance"})
    gd = HazardEvent("gdacs:TC1", "gdacs", "cyclone", "impact_estimate", "Tropical Cyclone VANCE-26", 30, 30, T0, T0,
                     severity_label="Green alert (GDACS)", url="u")
    first = STORE.merge([nhc, gd])
    second = STORE.merge([nhc, gd])
    assert [e.id for e in first] == ["nhc:ep222026"] and second[0].related == ["gdacs:TC1"]
    assert nhc.related == [] and "GDACS alert (automatic estimate)" not in nhc.measurements


def test_duplicate_ids_keep_the_newest_update():
    old, new = quake(1, t=T0), quake(1, t=T0 + pd.Timedelta(minutes=5))
    new.title = "revised"
    assert [e.title for e in STORE.merge([old, new])] == ["revised"]


# --- Static files ----------------------------------------------------------------------------------------------

def test_static_files_are_content_hashed_reused_and_bounded():
    a = STORE.write_static("events", [1])
    assert STORE.write_static("events", [1]) == a and a.startswith(STORE.STATIC_URL)
    for i in range(6):
        STORE.write_static("events", [i, i])
    files = list(Path(os.environ["CLOUDZY_EARTH_DIR"]).glob("events-*.json"))
    assert len(files) <= STORE.KEEP_FILES


def test_map_records_carry_what_the_globe_needs():
    e = quake(1)
    e.extra["track"] = [{"lon": 1, "lat": 2, "time": "2026-10-21T18:00:00+00:00", "status": "observed"}]
    r = STORE.map_record(e)
    assert r["id"] == "usgs:q1" and r["mag"] == 5.0 and r["track"][0][3] == "observed" and "summary" not in r


def test_snapshot_writes_one_events_file_per_data_version():
    s = make_store({"usgs": ok_feed([quake(1)])})
    a = s.snapshot().layers["events"]
    b = s.snapshot().layers["events"]
    path = Path(os.environ["CLOUDZY_EARTH_DIR"]) / a.rsplit("/", 1)[1]
    assert a == b and json.loads(path.read_text())[0]["id"] == "usgs:q1"


# --- Analysis -------------------------------------------------------------------------------------------------

def test_places_near_inside_and_along_a_track():
    near = A.places_near(28.6139, 77.2090, 30)
    assert near[0].name in ("New Delhi", "Delhi") and near[0].distance_km < 15
    poly = {"type": "Polygon", "coordinates": [[[76.8, 28.4], [77.4, 28.4], [77.4, 28.9], [76.8, 28.9], [76.8, 28.4]]]}
    assert any(p.name in ("New Delhi", "Delhi") for p in A.places_inside(poly))
    track = [(28.0, 76.0), (29.0, 78.5)]
    assert any(p.name in ("New Delhi", "Delhi") for p in A.places_near_track(track, 50))


def test_context_wording_never_claims_impact():
    cyc = HazardEvent("nhc:x", "nhc", "cyclone", "warning", "Storm", 20, -60, T0, T0,
                      extra={"track": [{"lat": 20, "lon": -60, "status": "current"},
                                       {"lat": 22, "lon": -62, "status": "forecast"}]})
    ctx = A.context(cyc)
    text = " ".join(ctx["notes"])
    assert "not a certain path" in text and "doesn't mean a place is affected" in text
    eq = A.context(quake(1))
    assert "within 100 km" in eq["method"]


def test_filters_by_category_severity_official_region_marine_and_text():
    india = quake(1)
    japan = quake(2, lat=35.7, lon=139.7)
    marine = HazardEvent("nws:m", "nws", "other", "warning", "Small Craft Advisory", 40, -70, T0, T0, official=True,
                         extra={"marine": True})
    gd = HazardEvent("gdacs:WF1", "gdacs", "wildfire", "impact_estimate", "Forest fires in India", 20, 80, T0, T0,
                     severity_rank=1)
    events = [india, japan, marine, gd]
    assert A.filter_events(events, {"earthquake"}) == [india, japan]
    assert A.filter_events(events, min_rank=2) == [india, japan]
    assert gd not in A.filter_events(events, official_only=True)
    assert marine not in A.filter_events(events) and marine in A.filter_events(events, include_marine=True)
    region = {"kind": "country", "a3": "IND"}
    assert A.filter_events(events, region=region) == [india, gd]
    assert A.filter_events(events, text="japan") == [] and A.filter_events(events, text="quake 2") == [japan]
    assert {e.id for e in A.events_near(28.6, 77.2, events, 150)} == {"usgs:q1"}


def test_summary_counts_and_definitions():
    rows = {r["label"]: r for r in A.summary([quake(1), quake(2)], {}, 1234)}
    assert rows["Earthquakes"]["value"] == 2 and rows["Fire detections"]["value"] == 1234
    assert "not confirmed fires" in rows["Fire detections"]["definition"]
    assert "U.S." in rows["Official warnings"]["definition"]


# --- Search ---------------------------------------------------------------------------------------------------

def test_search_coordinates_countries_and_regions():
    t, err = ES.search("28.61, 77.21")
    assert t[0]["kind"] == "point" and t[0]["zoom"] == 10 and not err
    t, _ = ES.search("Japan", addresses=False)
    japan = next(x for x in t if x["kind"] == "country")
    assert japan["bbox"][0][0] < 130 < japan["bbox"][1][0] and japan["maxzoom"] == 8
    g = ES.region_geometry("country", "JPN")
    assert g["type"] == "MultiPolygon" and len(g["coordinates"]) > 1
    assert ES.search("x") == ([], [])


def test_nominatim_results_fit_their_box_and_failures_are_reported(monkeypatch):
    payload = [{"lat": "28.5541", "lon": "77.1945", "boundingbox": ["28.554", "28.5542", "77.1944", "77.1946"],
                "display_name": "Hauz Khas Village, New Delhi, India", "name": "Hauz Khas Village",
                "addresstype": "neighbourhood"}, {"lat": "bad"}]
    parsed = ES.parse_nominatim(payload)
    assert len(parsed) == 1 and parsed[0]["bbox"] == [[77.1944, 28.554], [77.1946, 28.5542]]
    assert parsed[0]["label"].startswith("Neighbourhood") and "OpenStreetMap" in parsed[0]["source"]

    def broken(q, session=None):
        raise ES.DataCollectionError("HTTP 429")
    monkeypatch.setattr(ES, "nominatim", broken)
    t, err = ES.search("Japan")
    assert any(x["kind"] == "country" for x in t) and err == ["address search: HTTP 429"]


def test_nominatim_is_cached_and_throttled(monkeypatch):
    calls = []
    monkeypatch.setattr(ES, "fetch_json", lambda url, params, **kw: calls.append(params) or [])
    monkeypatch.setattr(ES.time, "sleep", lambda s: calls.append(("sleep", s)))
    ES._cache.clear()
    REAL_NOMINATIM("Some Street 1")
    REAL_NOMINATIM("some street 1 ")
    assert sum(1 for c in calls if isinstance(c, dict)) == 1          # second call answered from cache
    assert calls[0]["format"] == "jsonv2" and calls[0]["limit"] == 5
