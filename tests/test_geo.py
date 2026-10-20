"""Geographic data (Natural Earth, India's point of view), region lookup, search and regional aggregation."""

import numpy as np
import pytest

from src import explorer_data as E
from src import geo
from src import region_weather as R
from tests.test_explorer_data import RUN, s3_for


def unit(a3, name):
    return next(u for u in geo.admin1(a3)["units"] if u["name"] == name)


# --- Data and coverage ---------------------------------------------------------------------------------

def test_india_has_its_28_states_and_8_union_territories():
    units = geo.admin1("IND")["units"]
    assert len(units) == 36
    uts = sorted(u["name"] for u in units if u["type"] == "Union territory")
    assert uts == ["Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
                   "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry"]
    assert sum(u["type"] == "State" for u in units) == 28
    assert unit("IND", "Himachal Pradesh")["type"] == "State"           # Natural Earth's error is corrected
    assert geo.admin1("IND")["lines"]                                    # internal state boundaries are present


def test_point_of_view_is_india_and_disputed_divisions_are_left_out():
    data = geo.countries()
    assert "India" in data["view"] and "Natural Earth" in data["source"]
    pak = {u["name"] for u in geo.admin1("PAK")["units"]}
    assert "Azad Kashmir" not in pak and "Northern Areas" not in pak and "Punjab" in pak
    assert geo.region_at(35.5, 79.5) == {"kind": "country", "a3": "IND", "id": "IND", "name": "India"}  # no state there
    assert geo.region_at(34.5, 74.5)["a3"] == "IND"                      # Kashmir valley


def test_every_country_has_outline_label_and_continent_and_files_exist():
    data = geo.countries()
    assert len(data["countries"]) > 200
    assert {c["name"] for c in data["continents"]} >= {"Africa", "Asia", "Europe", "North America", "Oceania",
                                                         "South America"}
    for c in data["countries"]:
        assert c["polys"] and len(c["bbox"]) == 4 and -90 <= c["lat"] <= 90 and -180 <= c["lon"] <= 180
        assert (geo.GEO_DIR / "admin1" / f"{c['a3']}.json").exists() == c["adm1"]
    assert (geo.GEO_DIR / "places.json").exists() and (geo.GEO_DIR / "README.md").exists()


# --- Lookups ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lat, lon, a3, name", [
    (19.019, 72.855, "IND", "Maharashtra"),       # coastal city: within the outline tolerance
    (28.6, 77.2, "IND", "Delhi"),
    (13.08, 80.27, "IND", "Tamil Nadu"),
    (40.71, -74.0, "USA", "New York"),
    (-33.87, 151.21, "AUS", "New South Wales"),
])
def test_region_at_finds_the_state_or_province(lat, lon, a3, name):
    r = geo.region_at(lat, lon)
    assert r["kind"] == "admin1" and r["a3"] == a3 and r["name"] == name


def test_open_ocean_has_no_region():
    assert geo.region_at(0.0, -30.0) is None


def test_search_keeps_ambiguous_names_apart_and_ignores_accents():
    punjab = [(e["kind"], e["country"]) for e in geo.search("punjab")]
    assert ("admin1", "India") in punjab and ("admin1", "Pakistan") in punjab
    georgia = {(e["kind"], e["country"]) for e in geo.search("georgia")}
    assert {("country", "Georgia"), ("admin1", "United States")} <= georgia
    assert [e["name"] for e in geo.search("sao paulo")] == ["São Paulo"]
    assert geo.search("x") == []


def test_coordinates_and_regions_from_the_browser_are_validated():
    assert geo.parse_coordinates("28.61, 77.21") == (28.61, 77.21)
    assert geo.parse_coordinates("91, 0") is None and geo.parse_coordinates("Paris") is None
    assert geo.clean_region({"kind": "country", "a3": "IND", "name": "anything", "fhr": 9})["name"] == "India"
    assert geo.clean_region({"kind": "admin1", "a3": "../IND", "id": "x"}) is None
    assert geo.clean_region({"kind": "admin1", "a3": "IND", "id": "nope"}) is None
    assert geo.clean_region("IND") is None


def test_breadcrumb():
    assert geo.breadcrumb("admin1", "IND", unit("IND", "Maharashtra")["id"]) == ["Earth", "Asia", "India", "Maharashtra"]
    assert geo.breadcrumb("country", "FRA") == ["Earth", "Europe", "France"]


def test_points_in_polys_even_odd_with_a_hole():
    square = [[0, 0, 10, 0, 10, 10, 0, 10, 0, 0], [2, 2, 4, 2, 4, 4, 2, 4, 2, 2]]
    got = geo.points_in_polys(np.array([1, 3, 5, 11]), np.array([1, 3, 5, 5]), square)
    assert got.tolist() == [True, False, True, False]


# --- Regional aggregation ----------------------------------------------------------------------------------

def test_region_masks_follow_the_grid_and_small_regions_have_too_few_cells():
    india = geo.region_mask("country", "IND")[1]
    assert 250 < india < 330                                              # ~3.3 million km² of ~12,000 km² cells
    assert geo.region_mask("admin1", "IND", unit("IND", "Delhi")["id"])[1] < geo.MIN_CELLS
    assert geo.region_mask("admin1", "IND", unit("IND", "Maharashtra")["id"])[1] >= 20


def test_regional_summary_is_an_area_weighted_mean_over_the_cells_inside(tmp_path):
    E.prepare_run(RUN, s3_for((0, 3)), tmp_path, fhours=(0, 3))
    mh = unit("IND", "Maharashtra")
    s = R.summary("admin1", "IND", mh["id"], fhr=3, directory=tmp_path)
    mask, n = geo.region_mask("admin1", "IND", mh["id"])
    lat = (90.0 - np.arange(181.0))[:, None] * np.ones((1, 360))
    w = np.where(mask, np.cos(np.radians(lat)), 0)
    expected = float((w * (30 - np.abs(lat) * 0.5)).sum() / w.sum())     # the synthetic field: 30 - |lat| / 2 (°C)
    assert s["cells"] == n and s["fhr"] == 3 and s["valid_utc"] == "2026-10-20T03:00Z"
    assert s["temperature"]["mean"] == pytest.approx(expected, abs=0.01)
    assert s["precipitation"]["cover"] == pytest.approx(1.0)             # synthetic rain everywhere north of the equator
    assert s["wind"]["mean"] == pytest.approx(np.hypot(5, -2), abs=0.01)
    small = R.summary("admin1", "IND", unit("IND", "Delhi")["id"], fhr=3, directory=tmp_path)
    assert small["cells"] < geo.MIN_CELLS and "temperature" not in small


def test_regional_summary_without_data_says_so(tmp_path):
    assert "error" in R.summary("country", "IND", None, directory=tmp_path)


def test_places_in_a_state_are_inside_it():
    names = [p["name"] for p in R.places_in("admin1", "IND", unit("IND", "Maharashtra")["id"])]
    assert names[:2] == ["Mumbai", "Pune"] and "Delhi" not in names
    assert R.places_in("country", "IND")[0]["name"] == "Mumbai"


def test_nearest_step_prefers_the_requested_step():
    m = {"frames": [{"fhr": 0, "valid_utc": "2026-10-20T00:00Z", "ready": True},
                    {"fhr": 3, "valid_utc": "2026-10-20T03:00Z", "ready": True}]}
    assert R.nearest_step(m, 3)["fhr"] == 3
    from datetime import UTC, datetime
    assert R.nearest_step(m, None, now=datetime(2026, 10, 20, 2, tzinfo=UTC))["fhr"] == 3



def test_land_borders_for_the_2d_map_follow_the_same_point_of_view():
    import json
    data = json.loads((geo.GEO_DIR / "borders.json").read_text())
    assert "India" in data["view"] and len(data["borders"]) > 300
    assert geo.country_at(35.92, 74.31)["a3"] == "IND"          # Gilgit: India in India's official view
    assert geo.country_at(33.68, 73.05)["a3"] == "PAK"           # Islamabad
    assert "geo/borders.json" in __import__("src.explorer_view", fromlist=["JS"]).JS
