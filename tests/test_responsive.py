"""Responsive design: the shared foundation (offline) and real-browser checks at phone, landscape and desktop sizes.

The browser checks start the app, drive the installed Google Chrome through Playwright and need the network for
live data, so they are marked `responsive` and run on request:

    python -m pip install -r requirements-dev.txt
    python -m pytest -m responsive

The full matrix (13 pages × 10 viewports, with screenshots) is `python tools/responsive_audit.py`.
"""

from __future__ import annotations

import contextlib
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src import earth_view, explorer_view, globe_view, shell, theme

ROOT = Path(__file__).resolve().parent.parent
VIEWS = {"earth": earth_view.CSS, "globe": globe_view.CSS, "explorer": explorer_view.CSS}


# --- Offline: the foundation ------------------------------------------------------------------------------

def test_breakpoints_are_shared_tokens_not_scattered_values():
    css = theme.GLOBAL_CSS
    assert theme.BREAKPOINTS == {"sm": 480, "md": 768, "lg": 1024, "xl": 1280, "xxl": 1600}
    phone_queries = re.findall(r"@media \(max-width:(\d+)px\)", css)
    assert phone_queries and set(phone_queries) <= {str(theme.BREAKPOINTS["md"] - 1)}
    assert f"max-width:{theme.CONTENT_MAX}" in css                      # ultrawide content is capped and centred
    assert f"(max-height:{theme.SHORT_SCREEN}px) and (orientation:landscape)" in css


def test_responsive_overrides_come_after_the_rules_they_override():
    css = theme.GLOBAL_CSS
    base = css.index(".cz-page h1{font-family")
    assert css.index("Responsive overrides") > base
    assert css.rindex(".cz-page h1{font-size:clamp(") > base


def test_touch_targets_and_safe_areas():
    css = theme.GLOBAL_CSS
    assert "@media (pointer:coarse)" in css and f"min-height:{theme.TOUCH_TARGET}" in css
    assert "env(safe-area-inset-bottom)" in css
    assert "viewport-fit=cover" in shell.JS
    for name, view_css in VIEWS.items():
        assert "@media (pointer:coarse)" in view_css, name
        assert "prefers-reduced-motion" in view_css, name
        assert "dvh" in view_css, name                                  # fits the visible area on phones


def test_layout_decisions_never_sniff_the_user_agent():
    for src in (shell.JS, earth_view.JS, globe_view.JS, explorer_view.JS, theme.GLOBAL_CSS):
        assert "userAgent" not in src and "navigator.platform" not in src


def test_maps_resize_with_their_container_and_clean_up():
    for name, js in (("earth", earth_view.JS), ("globe", globe_view.JS), ("explorer", explorer_view.JS)):
        assert "new ResizeObserver" in js and ".disconnect()" in js, name


def test_mobile_bottom_navigation_links_real_pages():
    app = (ROOT / "streamlit_app.py").read_text()
    block = app[app.index('st.container(key="cz_mobile_nav")'):app.index("shell.mount(")]
    targets = re.findall(r'"(app_pages/[a-z_]+\.py)"', block)
    assert len(targets) == 5 and all((ROOT / t).exists() for t in targets)
    assert ".st-key-cz_mobile_nav{display:none !important}" in theme.GLOBAL_CSS   # hidden from tablet width up


def test_two_up_rows_use_the_shared_container():
    used = sum(p.read_text().count("T.two_up(") for p in (ROOT / "app_pages").glob("*.py"))
    assert used >= 20
    assert '[class*="st-key-cz2up"]' in theme.GLOBAL_CSS and '[class*="st-key-cz2upf"]' in theme.GLOBAL_CSS


# --- Real browser (opt-in) ----------------------------------------------------------------------------------

SIZES = {"small phone": (320, 568), "phone": (375, 812), "phone landscape": (844, 390), "desktop": (1440, 900)}
PAGES = ["/", "/weather", "/air-quality", "/earth", "/lab", "/settings"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def app_url():
    pytest.importorskip("playwright")
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.headless", "true",
                             "--server.port", str(port)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
                break
            time.sleep(0.5)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:      # background feed threads can delay a graceful shutdown
            proc.kill()
            proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:                       # no installed Chrome: skip rather than download a browser
            pytest.skip(f"Google Chrome not available: {exc}")
        yield b
        b.close()


def _open(browser, url: str, size: tuple[int, int], wait_s: float = 30):
    w, h = size
    ctx = browser.new_context(viewport={"width": w, "height": h}, has_touch=w < 1024 or h < 500, is_mobile=w < 768)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "404" not in m.text else None)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector('[data-testid="stMain"]', timeout=wait_s * 1000)
    deadline, quiet = time.monotonic() + wait_s, 0
    while time.monotonic() < deadline and quiet < 4:
        quiet = quiet + 1 if not page.locator('[data-testid="stStatusWidget"]').count() else 0
        time.sleep(0.4)
    return ctx, page, errors


@pytest.mark.responsive
@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("label", list(SIZES))
def test_page_fits_the_viewport_with_usable_navigation(app_url, browser, path, label):
    ctx, page, errors = _open(browser, app_url + path, SIZES[label], wait_s=45 if path == "/earth" else 30)
    try:
        m = page.evaluate("""() => {
          const nav = document.querySelector('.st-key-cz_mobile_nav');
          const sb = document.querySelector('[data-testid="stSidebar"]');
          return { over: document.documentElement.scrollWidth - innerWidth,
                   nav: !!nav && getComputedStyle(nav).display !== 'none',
                   sidebar: !!sb && sb.getAttribute('data-collapsed') !== 'true' && sb.getBoundingClientRect().right > 50,
                   menu: !!document.querySelector('[data-testid="stExpandSidebarButton"]'),
                   exc: document.querySelectorAll('[data-testid="stException"]').length };
        }""")
        assert m["over"] <= 0, f"{path} overflows by {m['over']} px at {label}"
        assert m["exc"] == 0 and not errors, (m["exc"], errors[:3])
        w, h = SIZES[label]
        if w < theme.BREAKPOINTS["md"]:
            assert m["nav"] and not m["sidebar"], m                     # phones: bottom bar, sidebar closed
        elif h < theme.SHORT_SCREEN:
            assert not m["nav"] and not m["sidebar"] and m["menu"], m   # landscape phone: room for content
        else:
            assert not m["nav"] and m["sidebar"], m                     # desktop: the sidebar
    finally:
        ctx.close()


@pytest.mark.responsive
def test_globe_canvas_follows_its_container_on_resize(app_url, browser):
    ctx, page, _ = _open(browser, app_url + "/earth", (1280, 800), wait_s=45)
    try:
        # A fresh server fetches every hazard feed first; wait for the globe itself, not just the page.
        page.wait_for_function("""() => {
          const host = [...document.querySelectorAll('*')].find((e) => e.shadowRoot && e.shadowRoot.querySelector('.cze'));
          return !!host && host.shadowRoot.querySelector('.cze-loading').hidden && !!host.shadowRoot.querySelector('canvas');
        }""", timeout=90000)
        sizes = []
        for w, h in ((1280, 800), (375, 812), (1024, 768)):
            page.set_viewport_size({"width": w, "height": h})
            time.sleep(1.5)
            sizes.append(page.evaluate("""() => {
              const host = [...document.querySelectorAll('*')].find((e) => e.shadowRoot && e.shadowRoot.querySelector('.cze'));
              const root = host.shadowRoot, box = root.querySelector('.cze').getBoundingClientRect();
              const canvas = root.querySelector('canvas').getBoundingClientRect();
              return [Math.round(box.width), Math.round(canvas.width), Math.round(box.right), innerWidth];
            }"""))
        for box_w, canvas_w, right, vw in sizes:
            assert abs(box_w - canvas_w) <= 2 and right <= vw, sizes      # canvas = container, inside the viewport
        assert sizes[1][0] < sizes[0][0]
    finally:
        ctx.close()
