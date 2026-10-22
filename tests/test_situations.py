"""Disaster situations (src.hazards.situations): classification, presets, record layers, filters, and agreement with
the globe's JavaScript (src.earth_view), which applies the same rules in the browser."""

import re

import pandas as pd
import pytest

from src import earth_view
from src.hazards import nhc, nws, usgs
from src.hazards import situations as SIT
from src.hazards.schema import CATEGORIES, KINDS, HazardEvent
from src.hazards.store import map_record, merge
from tests.test_hazards import load

NOW = pd.Timestamp("2026-10-22T12:00Z")


def ev(category="severe", title="Tornado Warning", kind="warning", source="nws", rank=3, **kw):
    base = dict(id=f"{source}:{title}", source=source, category=category, kind=kind, title=title, lat=35.0, lon=-97.0,
                time=NOW - pd.Timedelta(hours=1), updated=NOW, severity_rank=rank, official=kind == "warning",
                extra={"product": nws.product_type(title)} if source == "nws" else {})
    base.update(kw)
    return HazardEvent(**base)


# --- Classification ------------------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Tornado Warning", "tornado"), ("Tornado Watch", "tornado"), ("Severe Thunderstorm Warning", "thunderstorm"),
    ("Severe Weather Statement", "thunderstorm"), ("Dust Storm Warning", "dust"), ("Blowing Dust Advisory", "dust"),
    ("High Wind Warning", "wind"), ("Wind Advisory", "wind"), ("Extreme Wind Warning", "wind"),
    ("Excessive Heat Warning", "heat"), ("Heat Advisory", "heat"), ("Extreme Cold Warning", "cold"),
    ("Wind Chill Advisory", "cold"), ("Freeze Warning", "cold"), ("Winter Storm Warning", "snow"),
    ("Blizzard Warning", "snow"), ("Ice Storm Warning", "snow"), ("Flash Flood Warning", "flood"),
    ("Red Flag Warning", "wildfire"), ("Hurricane Warning", "cyclone"), ("Tsunami Warning", "tsunami"),
    ("Small Craft Advisory", "other"), ("Dense Fog Advisory", "other"), ("Air Quality Alert", "other"),
])
def test_nws_products_land_in_the_right_situation(title, expected):
    assert SIT.situation_for(ev(category=nws.category_for(title), title=title)) == expected


def test_every_feed_category_and_record_kind_is_covered():
    for c in CATEGORIES:
        assert SIT.situation_for(ev(category=c, title="x")) in SIT.SITUATIONS
    assert set(SIT.LAYER_OF_KIND) == set(KINDS)
    assert set(SIT.LAYER_OF_KIND.values()) <= set(SIT.LAYERS)


def test_the_requested_situations_and_presets_exist():
    assert list(SIT.SITUATIONS) == ["earthquake", "flood", "cyclone", "drought", "wildfire", "thunderstorm", "tornado",
                                    "heat", "cold", "rain", "wind", "tsunami", "volcano", "landslide", "snow", "dust",
                                    "lightning", "other"]
    assert {label for label, _ in SIT.PRESETS.values()} == {"All available", "Geological", "Storms", "Water hazards",
                                                             "Climate and heat", "Fire and dryness"}
    for _, keys in SIT.PRESETS.values():
        assert set(keys) <= set(SIT.SITUATIONS)
    assert set(SIT.LAYERS) == {"observed", "official", "provider", "ml", "historical"}


def test_unconnected_situations_are_listed_honestly_and_never_on():
    for k in ("rain", "landslide", "lightning"):
        s = SIT.SITUATIONS[k]
        assert not s.sources and s.missing and k not in SIT.DEFAULT_ON
    assert set(SIT.PRESETS["all"][1]) == {k for k, s in SIT.SITUATIONS.items() if s.sources}
    data = {d["key"]: d for d in SIT.selector_data([])}
    assert data["lightning"]["connected"] is False and "lightning" in data["lightning"]["covers"].lower()
    assert all(d["n"] == 0 for d in data.values())                 # no records → zero, never invented


@pytest.mark.parametrize("lat,lon,area,expected", [
    (15, 130, None, "Western Pacific"), (15, 88, None, "North Indian"), (20, -110, None, "Eastern Pacific"),
    (12, -90, None, "Eastern Pacific"), (25, -75, None, "North Atlantic"), (18, -160, None, "Central Pacific"),
    (-15, 60, None, "South-West Indian"), (-15, 120, None, "Australian region"), (-18, 175, None, "South Pacific"),
    (25, -75, "EP2 basin", "Eastern Pacific"), (25, -75, "AT1 basin", "North Atlantic"),
])
def test_cyclone_basins(lat, lon, area, expected):
    e = ev(category="cyclone", title="Storm", source="gdacs", kind="impact_estimate", lat=lat, lon=lon, area=area)
    assert SIT.basin(e) == expected and expected in SIT.BASINS


# --- Filters --------------------------------------------------------------------------------------------

def test_view_filters_match_the_rules_the_globe_applies():
    quake = ev(category="earthquake", title="M 4.2", source="usgs", kind="observation", rank=1,
               extra={"mag": 4.2, "depth_km": 350})
    v = SIT.View()
    assert SIT.matches(quake, v, NOW)
    v.eq_min_mag = 5
    assert not SIT.matches(quake, v, NOW)
    v = SIT.View(eq_depths={"shallow"})
    assert not SIT.matches(quake, v, NOW)                           # 350 km is deep
    assert not SIT.matches(quake, SIT.View(layers={"official"}), NOW)
    assert not SIT.matches(quake, SIT.View(on={"flood"}), NOW)

    watch = ev(title="Tornado Watch", rank=2)
    assert not SIT.matches(watch, SIT.View(severe_product="Warning"), NOW)
    expired = ev(title="Tornado Warning", ends=NOW - pd.Timedelta(minutes=5))
    assert SIT.matches(expired, SIT.View(), NOW) and not SIT.matches(expired, SIT.View(severe_active=True), NOW)

    gd = ev(category="flood", title="Flood in Italy", source="gdacs", kind="impact_estimate", rank=2)
    assert SIT.matches(gd, SIT.View(flood="automatic"), NOW) and not SIT.matches(gd, SIT.View(flood="official"), NOW)
    tc = ev(category="cyclone", title="Storm", source="gdacs", kind="impact_estimate", rank=2, lat=15, lon=130)
    assert not SIT.matches(tc, SIT.View(tc_rank=3), NOW)
    assert not SIT.matches(tc, SIT.View(tc_basin="North Atlantic"), NOW)


def test_view_from_the_globe_ignores_unknown_keys():
    v = SIT.View.from_json({"on": ["tornado", "nonsense"], "layers": ["official", "bogus"],
                            "f": {"rank": 2, "eqMag": 5, "eqDepth": ["deep"], "tcRank": 3, "svProd": "Watch"}})
    assert v.on == {"tornado"} and v.layers == {"official"} and v.rank == 2 and v.eq_min_mag == 5
    assert v.eq_depths == {"deep"} and v.tc_rank == 3 and v.severe_product == "Watch"
    assert SIT.View.from_json(None).on == set(SIT.DEFAULT_ON)


# --- Agreement with the globe's JavaScript ------------------------------------------------------------------

def js_object(name: str) -> str:
    m = re.search(rf"const {name} = (\{{.*?\}});", earth_view.JS, re.S)
    assert m, name
    return m.group(1)


def test_globe_filter_keys_are_mirrored_or_overlay_only():
    keys = set(re.findall(r"(\w+):", js_object("F0")))
    assert keys == set(SIT.MIRRORED_FILTERS) | set(SIT.OVERLAY_FILTERS)
    assert set(re.findall(r"(\w+):", js_object("LK0"))) == set(SIT.LAYERS)
    assert set(re.findall(r'"(\w+)"', re.search(r"const WARNING_SITS = new Set\((\[.*?\])\)", earth_view.JS).group(1))) \
        == SIT.WARNING_SITUATIONS


def test_globe_trigger_names_dont_collide_with_dict_methods():
    names = set(re.findall(r'S\.trigger\("(\w+)"', earth_view.JS))
    assert names == {"select", "view", "chip"}
    assert not any(hasattr(dict, n) for n in names)                 # e.g. "clear" would shadow dict.clear


def test_globe_records_carry_situation_layer_and_filter_fields():
    events = merge(usgs.parse(load("usgs_all_day.json")).events + nws.parse(load("nws_active.json")).events
                   + nhc.parse_current(load("nhc_current.json")).events)
    records = [map_record(e) for e in events]
    assert all(r["sit"] in SIT.SITUATIONS and r["lk"] in SIT.LAYERS for r in records)
    assert any(r["sit"] == "earthquake" and "mag" in r and "depth" in r for r in records)
    assert any(r.get("prod") for r in records if r["s"] == "nws")
    assert any(r.get("basin") == "Eastern Pacific" for r in records if r["s"] == "nhc")
