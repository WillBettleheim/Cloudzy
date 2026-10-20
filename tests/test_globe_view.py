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
    for name in ("WIND_STOPS", "RAIN_STOPS", "SNOW_STOPS", "MIXED_STOPS"):
        stops = GV.JS.split(f"const {name} = ")[1].split(";")[0]
        assert stops.replace(" ", "") in EV.JS.replace(" ", ""), name
    for var in ("t2m", "u10", "v10", "prate", "ptype", "tcc"):     # every globe variable is a stored GFS field
        assert f'"{var}"' in GV.JS and var in E.STORED
    for layer in ("temperature", "wind", "precipitation", "clouds", "none"):
        assert f"  {layer}: {{ label:" in GV.JS


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
