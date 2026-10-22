"""The static GitHub Pages edition in a real browser, served under /Cloudzy/ as GitHub Pages will serve it.

Builds the site with a live hazard snapshot and uses the network (Open-Meteo, map tiles), so it is opt-in with the
other browser checks:  python -m pytest -m responsive
"""

from __future__ import annotations

import functools
import importlib.util
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.responsive

SIZES = {"phone": (375, 812), "phone landscape": (844, 390), "tablet": (768, 1024), "desktop": (1440, 900)}
PAGES = ["", "weather.html", "earth.html", "lab.html"]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def site_url(tmp_path_factory):
    pytest.importorskip("playwright")
    bp, serve = _load("build_pages"), _load("serve_pages")
    out = tmp_path_factory.mktemp("pages") / "_site"
    bp.build(out, live=True)
    assert bp.verify(out, need_snapshot=True) == []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(serve.Handler, directory=str(out)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/Cloudzy/"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"Google Chrome not available: {exc}")
        yield b
        b.close()


def _open(browser, url, size):
    w, h = size
    ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=w < 768, has_touch=w < 1024)
    page = ctx.new_page()
    problems: list[str] = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" or "Content Security Policy" in m.text
            else None)
    resp = page.goto(url, wait_until="load")
    return ctx, page, resp, problems


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("label", list(SIZES))
def test_page_loads_cleanly_under_the_project_path(site_url, browser, path, label):
    ctx, page, resp, problems = _open(browser, site_url + path, SIZES[label])
    try:
        assert resp.status == 200
        time.sleep(5 if path == "earth.html" else 2)
        m = page.evaluate("""() => ({ over: document.documentElement.scrollWidth - innerWidth,
          styled: getComputedStyle(document.body).backgroundColor !== 'rgba(0, 0, 0, 0)',
          icons: document.fonts.check('24px "Material Symbols Rounded"'), nav: document.querySelectorAll('.nav a').length })""")
        assert m["over"] <= 0 and m["styled"] and m["icons"] and m["nav"] == 4, m
        assert not problems, problems[:3]                       # includes Content Security Policy violations
        page.reload(wait_until="load")                            # refreshing a page works (no client routing)
        assert page.title().endswith("Cloudzy")
    finally:
        ctx.close()


def test_weather_loads_live_and_search_changes_the_place(site_url, browser):
    ctx, page, _, problems = _open(browser, site_url + "weather.html", SIZES["phone"])
    try:
        page.wait_for_selector("#wx-result:not([hidden])", timeout=30000)
        assert page.text_content("#wx-temp").endswith("°")
        page.fill("#wx-q", "Reykjavik")
        page.tap("#wx-form button[type=submit]")
        page.wait_for_selector("#wx-matches .match", timeout=20000)
        page.locator("#wx-matches .match").first.tap()
        # A function, not an expression string: the site's CSP correctly blocks eval, which strings would need.
        page.wait_for_function("() => document.querySelector('#wx-place').textContent.includes('Reykjav')",
                               timeout=30000)
        assert not problems, problems[:3]
    finally:
        ctx.close()


def test_globe_draws_the_snapshot(site_url, browser):
    ctx, page, _, problems = _open(browser, site_url + "earth.html", SIZES["desktop"])
    try:
        page.wait_for_function("""() => { const r = document.querySelector('#globe').shadowRoot;
          return r && r.querySelector('.cze-loading') && r.querySelector('.cze-loading').hidden && r.querySelector('canvas'); }""",
                               timeout=60000)
        assert "Snapshot taken" in page.text_content("main")
        assert not problems, problems[:3]
    finally:
        ctx.close()


def test_unknown_paths_get_the_styled_404_and_the_bare_path_redirects(site_url, browser):
    ctx, page, resp, _ = _open(browser, site_url + "no/such/page", SIZES["phone"])
    try:
        assert resp.status == 404 and "Page not found" in page.text_content("h1")
        assert page.evaluate("getComputedStyle(document.body).backgroundColor") != "rgba(0, 0, 0, 0)"
        resp = page.goto(site_url.rstrip("/"), wait_until="load")
        assert resp.status == 200 and page.url.endswith("/Cloudzy/")
    finally:
        ctx.close()
