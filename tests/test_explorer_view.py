"""The 2D weather map component (Leaflet). Rendering runs in the browser (checked there); these tests pin the
structure that keeps it usable: separate bands for toolbar, map and timeline, honest timestamps and values, resizing,
and its independence from the 3D globe."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src import explorer_view as EV
from src import globe_view as GV

JS, CSS = EV.JS, EV.CSS


def markup() -> str:
    return JS[JS.index("wrap.innerHTML = `"):JS.index("</div>`;") + 8]


def test_toolbar_map_and_timeline_are_separate_bands():
    html = markup()
    bar, stage, time = html.index('class="czx-bar"'), html.index('class="czx-stage"'), html.index('class="czx-time"')
    assert bar < stage < time
    stage_html = html[stage:time]
    assert "czx-legend" in stage_html and "czx-attr" in stage_html and "czx-ctrl" in stage_html
    assert "czx-slider" not in stage_html and "czx-layers" not in stage_html    # timeline and toolbar are outside
    assert "czx-readout" not in html                                            # values open in a point popup
    assert ".czx{display:flex;flex-direction:column" in CSS and ".czx-stage{position:relative;flex:1" in CSS


def test_point_values_open_in_a_popup_anchored_at_the_point():
    assert "S.L.popup(" in JS and ".setLatLng([S.picked.lat, S.picked.lon])" in JS
    assert "Not an observation." in JS and "Nearest grid point" in JS          # interpolated vs grid value, model data
    assert "sampler(f[v], near)" in JS                                         # grid-cell mode reads the cell's own value


def test_the_map_resizes_with_its_container_and_fullscreen():
    assert "new ResizeObserver" in JS and "map.invalidateSize({ pan: false })" in JS
    assert "requestFullscreen" in JS and "expanded" in JS and 'removeEventListener("fullscreenchange"' in JS
    assert ".czx.expanded{position:fixed;inset:0" in CSS


def test_the_time_label_describes_the_drawn_step_and_missing_steps_are_cleared():
    assert "S.shownIdx = idx" in JS and "loading this step…" in JS
    assert "clearWeather(S, idx)" in JS and "never leave another step's picture on screen" in JS
    assert "UTC" in JS and "resolvedOptions().timeZone" in JS
    assert 'tk${miss ? " miss" : ""}' in JS                                     # steps without the layer are marked


def test_every_layer_still_has_a_button_scale_and_fields():
    layers = re.findall(r"^  (\w+): \{ label:", JS[JS.index("const LAYER_INFO"):], re.M)
    assert set(layers) == {"none", "wind", "temperature", "precipitation", "clouds", "pressure", "gusts", "humidity",
                           "cape", "snowdepth", "waves"}
    for layer in layers:
        if layer != "none":
            assert f"{layer}:" in JS[JS.index("function varsFor"):JS.index("const OPTIONAL")]


def test_reduced_motion_and_keyboard():
    assert "prefers-reduced-motion: reduce" in JS and "zoomAnimation: !S.reduce" in JS
    assert '@media (prefers-reduced-motion:reduce)' in CSS
    assert 'e.key === "Enter"' in JS                                           # select the point at the view centre


def test_the_2d_map_and_the_3d_globe_stay_separate():
    assert "three" not in JS.lower() and "leaflet" not in GV.JS.lower()
    assert EV.explorer_map.__code__ is not GV.globe.__code__
    assert '"cloudzy_explorer"' in Path(EV.__file__).read_text() and '"cloudzy_globe"' in Path(GV.__file__).read_text()


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_component_javascript_parses(tmp_path):
    f = tmp_path / "map.mjs"
    f.write_text(JS)
    subprocess.run(["node", "--check", str(f)], check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_samplers_interpolate_and_read_grid_cells():
    js = JS[JS.index("function sampler"):JS.index("async function redraw")]
    script = js + """
      const g = new Float32Array(181 * 360);
      for (let y = 0; y < 181; y++) for (let x = 0; x < 360; x++) g[y * 360 + x] = x;   // value = longitude index
      console.log(JSON.stringify([sampler(g)(10, 20.25), sampler(g, true)(10, 20.25), sampler(g, true)(10, 20.75)]));"""
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out == [pytest.approx(20.25), 20, 21]
