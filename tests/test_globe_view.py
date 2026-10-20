"""The 3D globe component: vendored assets, licences and the contract with the Explorer page.

The rendering itself runs in the browser (verified there); these checks keep the pieces it depends on
present, unmodified in name, and correctly attributed."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src import globe_view as GV

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def test_vendored_three_js_is_present_with_its_licence():
    three = STATIC / "vendor" / "three"
    module = (three / "three.module.js").read_text(encoding="utf-8")
    assert "./three.core.js" in module                      # the module build imports its core file
    assert (three / "three.core.js").stat().st_size > 1_000_000
    assert "MIT License" in (three / "LICENSE").read_text(encoding="utf-8")


def test_earth_texture_is_present_and_attributed():
    earth = STATIC / "vendor" / "earth"
    jpg = (earth / "blue-marble-4096.jpg").read_bytes()
    assert jpg[:3] == b"\xff\xd8\xff"                       # a JPEG
    readme = (earth / "README.md").read_text(encoding="utf-8")
    assert "GIBS" in readme and "Blue Marble" in readme


def test_component_loads_assets_from_static_serving_paths():
    for rel in ("vendor/three/three.module.js", "vendor/earth/blue-marble-4096.jpg"):
        assert f"app/static/{rel}" in GV.JS
        assert (STATIC / rel).exists()


def test_component_reports_fallback_and_picks_and_cleans_up():
    js = GV.JS
    assert 'setTriggerValue("fallback"' in js and 'setTriggerValue("picked"' in js
    assert "webglcontextlost" in js and "webglAvailable" in js
    assert "renderer.dispose()" in js and "isConnected" in js
    assert "prefers-reduced-motion" in js                    # no inertia under reduced motion
    assert "innerHTML = `" in js and "root.appendChild(wrap)" in js   # never wipes the component's CSS


def test_temperature_scale_matches_the_2d_map():
    from src import explorer_view as EV
    stops = GV.JS.split("const TEMP_STOPS = ")[1].split(";")[0]
    assert stops.replace(" ", "") in EV.JS.replace(" ", "")


def test_night_texture_is_present_and_attributed():
    earth = STATIC / "vendor" / "earth"
    assert (earth / "black-marble-4096.jpg").read_bytes()[:3] == b"\xff\xd8\xff"
    assert "Black Marble" in (earth / "README.md").read_text(encoding="utf-8")
    assert "app/static/vendor/earth/black-marble-4096.jpg" in GV.JS


def test_globe_layers_use_the_map_scales_and_existing_fields():
    from src import explorer_data as E
    from src import explorer_view as EV
    for name in ("WIND_STOPS", "GUST_STOPS", "HUM_STOPS", "CAPE_STOPS", "PRESS_STOPS", "SNOWD_STOPS", "WAVE_STOPS",
                 "RAIN_STOPS", "SNOW_STOPS", "MIXED_STOPS"):
        stops = GV.JS.split(f"const {name} = ")[1].split(";")[0]
        assert stops.replace(" ", "") in EV.JS.replace(" ", ""), name
    for var in ("t2m", "u10", "v10", "prate", "ptype", "tcc", "prmsl", "gust", "rh2m", "cape", "snod", "swh"):
        assert f'"{var}"' in GV.JS and var in E.STORED                 # every globe variable is a stored field
    for layer in ("temperature", "wind", "precipitation", "clouds", "pressure", "gusts", "humidity", "cape",
                  "snowdepth", "waves", "none"):
        assert f"  {layer}: {{ label:" in GV.JS
        if layer != "none":
            assert f"{layer}:" in EV.JS                                   # the 2D map has the same layers


def test_wind_animation_pauses_off_screen_and_under_reduced_motion():
    js = GV.JS
    assert "IntersectionObserver" in js and "document.hidden" in js
    assert "S.motion = !S.reduce" in js                    # reduced motion → static streamlines


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the component's JavaScript")
def test_sun_position_matches_known_dates():
    js = GV.JS[GV.JS.index("function subsolar"):GV.JS.index("// Sphere UVs")]
    script = js + """
      const out = {};
      for (const t of ["2026-03-20T14:46:00Z", "2026-06-21T12:00:00Z", "2026-12-21T12:00:00Z"]) out[t] = subsolar(new Date(t));
      console.log(JSON.stringify(out));"""
    got = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    eq, jun, dec = got.values()
    assert abs(eq["lat"]) < 0.05                           # March equinox: sun over the equator
    assert abs(eq["lon"] + 39.6) < 0.5                     # 14:46 UTC, equation of time about -7.5 min
    assert abs(jun["lat"] - 23.44) < 0.05 and abs(jun["lon"]) < 1
    assert abs(dec["lat"] + 23.44) < 0.05 and abs(dec["lon"]) < 1


def test_controls_playback_fullscreen_and_panel_are_present_and_cleaned_up():
    js = GV.JS
    for act in ("play", "prev", "next", "now", "reset", "fs", "panel", "zoomin", "zoomout", "city"):
        assert f'data-act="{act}"' in js
    assert "requestFullscreen" in js and "fullscreenchange" in js and "expanded" in js   # with a CSS fallback
    assert "clearTimeout(S.playTimer)" in js and 'removeEventListener("fullscreenchange"' in js
    assert "S.frameIdx >= S.frames.length - 1) return stopPlay(S)" in js   # playback stops at the last step
    assert "layerOk" in js and "not in this run" in js                       # missing layers are marked, not faked


def test_search_focus_flies_once_per_token():
    assert "d.focus.token !== S.focusToken" in GV.JS


def test_geography_loads_lazily_and_regions_are_hit_tested_on_real_outlines():
    js = GV.JS
    assert 'GEO_URL = "app/static/vendor/geo"' in js and (STATIC / "vendor" / "geo" / "countries.json").exists()
    assert "loadGeo(S);" in js and "await loadGeo" not in js                 # never blocks the first render
    assert "`${GEO_URL}/admin1/${a3}.json`" in js and "G.adm1.size <= 8" in js  # per-country files, bounded cache
    assert "function inRings" in js and "countryAt(S, lon, lat)" in js        # point-in-polygon, not screen guesses
    assert 'S.trigger("region"' in js and "on_region_change" in Path(GV.__file__).read_text()


def test_labels_are_progressive_bounded_and_collision_checked():
    js = GV.JS
    assert "if (p < 11) for (const c of S.geo.conts)" in js                  # continents only when far away
    assert "STATE_PPD = 10" in js and "p >= STATE_PPD" in js and "shown.size >= 110" in js               # states when close; at most 110 labels
    assert "placed.some(" in js and "v.z < horizon" in js                     # no overlaps, nothing behind the globe
    assert "key === S.geoKey && !S.geoDirty" in js                            # recomputed only when something changed


def test_regional_statistics_are_area_weighted_and_refuse_tiny_regions():
    js = GV.JS
    assert "MIN_REGION_CELLS = 3" in js and "Smaller than the 1° forecast grid" in js
    assert "Math.cos(lat * Math.PI / 180)" in js and "area-weighted" in js
