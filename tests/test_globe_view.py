"""The 3D globe component: vendored assets, licences and the contract with the Explorer page.

The rendering itself runs in the browser (verified there); these checks keep the pieces it depends on
present, unmodified in name, and correctly attributed."""

from pathlib import Path

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
