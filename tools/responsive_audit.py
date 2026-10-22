"""Responsive audit: load every Cloudzy page at every viewport of the test matrix in a real browser and measure
what breaks.

For each (page, viewport) it records:
- horizontal page overflow (document wider than the viewport) and the outermost elements that cause it;
- charts (Plotly), maps and canvases wider than the viewport;
- interactive controls smaller than 32 × 32 CSS px at touch sizes (44 × 44 is the goal; 32 is the hard floor);
- console errors and failed requests;
- the visibility of navigation (sidebar or menu button);
- a screenshot of the first screen.

Needs a running app (`streamlit run streamlit_app.py`) and Playwright, driving the installed Google Chrome
(no browser download): `pip install -r requirements-dev.txt`.

Run:  python tools/responsive_audit.py --base http://localhost:8501 [--pages / /earth] [--sizes 375x812]
      → <out>/audit.json and <out>/<page>_<w>x<h>.png
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

PAGES = ["/", "/weather", "/air-quality", "/outdoor", "/earth", "/explorer", "/history", "/predictions", "/lab",
         "/alerts", "/places", "/settings", "/terms"]
SIZES = [(320, 568), (375, 812), (430, 932), (844, 390), (768, 1024), (1024, 768), (1280, 720), (1440, 900),
         (1920, 1080), (2560, 1080)]
SLOW_PAGES = {"/earth": 45, "/explorer": 40}      # first loads fetch live feeds or model grids

MEASURE = r"""
() => {
  const vw = window.innerWidth;
  const doc = document.documentElement;
  const visible = (el) => { const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0; };
  const clipped = (el) => { // inside a container that scrolls or clips horizontally on purpose
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const ox = getComputedStyle(p).overflowX;
      if (ox === "auto" || ox === "scroll" || ox === "hidden" || ox === "clip") return true;
    }
    return false; };
  const label = (el) => {
    const tid = el.closest("[data-testid]");
    const cls = (el.className && typeof el.className === "string") ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "";
    return `${el.tagName.toLowerCase()}${cls}${tid ? ` in [${tid.dataset.testid}]` : ""}`; };
  const all = [...document.querySelectorAll("body *")];
  // Collect every shadow root too (the globe is a components-v2 element with its own shadow DOM).
  for (const el of all.slice()) if (el.shadowRoot) all.push(...el.shadowRoot.querySelectorAll("*"));
  const over = [];
  for (const el of all) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 && !clipped(el)) {
      const parentOver = el.parentElement && el.parentElement.getBoundingClientRect().right > vw + 1 && !clipped(el.parentElement);
      if (!parentOver) over.push({ el: label(el), right: Math.round(r.right), width: Math.round(r.width) });
    }
  }
  const touchSmall = [];
  const coarse = matchMedia("(pointer: coarse)").matches;   // 44 px targets matter for touch; mice are precise
  for (const el of (coarse ? all : [])) {
    if (!el.matches || !el.matches("button, a[href], input, select, textarea, [role=button], [role=tab], [role=slider], summary, label.tog")) continue;
    // Exempt (WCAG 2.5.8): links inside running text. Not targets: hidden inputs behind Streamlit's date and
    // slider widgets (the visible field and the slider's handle are measured instead).
    if (el.matches('[data-testid="stMarkdownContainer"] p a, [data-testid="stMarkdownContainer"] li a, [data-testid="stCaptionContainer"] a')) continue;
    if (el.matches('[data-testid="stSlider"] input, [data-testid="hidden-dateinput-container"] input')) continue;
    if (!visible(el)) continue;
    // The tap target of a checkbox or radio is its label; the skip link is visible only on keyboard focus.
    if (el.matches('input[type=checkbox], input[type=radio], [data-testid="stSkipToContent"]')) continue;
    const r = el.getBoundingClientRect();
    if (r.right <= 0 || r.left >= vw || r.bottom <= 0 || r.top >= window.innerHeight * 6) continue;  // off-screen
    if ((r.width < 32 || r.height < 32) && r.width * r.height > 0) {
      const txt = (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 40);
      touchSmall.push(`${label(el)} "${txt}" ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
  }
  const charts = [...document.querySelectorAll(".js-plotly-plot, canvas, iframe")].filter(visible).map((el) => {
    const r = el.getBoundingClientRect();
    return { el: label(el), width: Math.round(r.width), height: Math.round(r.height), overflows: r.right > vw + 1 && !clipped(el) };
  });
  const sidebar = document.querySelector('[data-testid="stSidebar"]');
  const sidebarOpen = !!sidebar && sidebar.getAttribute("data-collapsed") !== "true" && sidebar.getBoundingClientRect().right > 50;
  const menuButton = document.querySelector('[data-testid="stExpandSidebarButton"], [data-testid="stSidebarCollapsedControl"] button, [data-testid="stMainMenu"]');
  const main = document.querySelector('[data-testid="stMainBlockContainer"]');
  const exc = [...document.querySelectorAll('[data-testid="stException"]')].map((e) => e.innerText.slice(0, 200));
  return { vw, docWidth: doc.scrollWidth, overflowPx: Math.max(0, doc.scrollWidth - vw), offenders: over.slice(0, 12),
           touchSmall: touchSmall.slice(0, 25), touchSmallCount: touchSmall.length, charts,
           sidebarOpen, menuButton: !!menuButton, mainWidth: main ? Math.round(main.getBoundingClientRect().width) : null,
           exceptions: exc, coarsePointer: matchMedia("(pointer: coarse)").matches,
           bottomNav: (() => { const n = document.querySelector(".st-key-cz_mobile_nav");
                               return !!n && getComputedStyle(n).display !== "none"; })() };
}
"""


async def settle(page, budget_s: float) -> None:
    """Wait until Streamlit has finished running the script (no status widget for ~1.5 s)."""
    start, quiet_since = time.monotonic(), None
    await page.wait_for_selector('[data-testid="stMain"]', timeout=budget_s * 1000)
    while time.monotonic() - start < budget_s:
        running = await page.locator('[data-testid="stStatusWidget"]').count()
        if running:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since > 1.5:
            return
        await asyncio.sleep(0.3)


async def audit_one(browser, base: str, path: str, size: tuple[int, int], out: Path) -> dict:
    w, h = size
    touch = w < 1024 or h < 500
    ctx = await browser.new_context(viewport={"width": w, "height": h}, has_touch=touch, is_mobile=w < 768,
                                    device_scale_factor=1)
    page = await ctx.new_page()
    errors, failed = [], []
    page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
    page.on("requestfailed", lambda r: failed.append(f"{r.url[:120]} {r.failure}"))
    try:
        await page.goto(base + path, wait_until="domcontentloaded", timeout=60000)
        await settle(page, SLOW_PAGES.get(path, 25))
        await asyncio.sleep(1.0)
        result = await page.evaluate(MEASURE)
        name = (path.strip("/") or "overview").replace("/", "_")
        await page.screenshot(path=str(out / f"{name}_{w}x{h}.png"))
    except Exception as exc:   # a page that fails to load is a finding, not a crash of the audit
        result = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        await ctx.close()
    # Streamlit's own health probes on sub-paths 404 harmlessly; ignore them.
    errors = [e for e in errors if "_stcore/health" not in e and "_stcore/host-config" not in e
              and "status of 404" not in e]
    return {"page": path, "size": f"{w}x{h}", **result, "console_errors": errors[:10],
            "failed_requests": [f for f in failed if "_stcore" not in f][:10]}


async def main_async(base: str, pages: list[str], sizes: list[tuple[int, int]], out: Path, workers: int) -> list[dict]:
    from playwright.async_api import async_playwright
    out.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(workers)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)

        async def run(path, size):
            async with sem:
                r = await audit_one(browser, base, path, size, out)
                flag = "ERROR" if "error" in r else ("OVERFLOW" if r.get("overflowPx") else "ok")
                print(f"{path:<14} {r['size']:>10}  {flag}  overflow={r.get('overflowPx')}  "
                      f"small-targets={r.get('touchSmallCount')}  console-errors={len(r['console_errors'])}", flush=True)
                return r
        # Warm the slow pages once so their feeds are cached before the matrix runs in parallel.
        for slow in [p_ for p_ in pages if p_ in SLOW_PAGES]:
            await run(slow, (1280, 720))
        results = await asyncio.gather(*(run(pg, s) for pg in pages for s in sizes))
        await browser.close()
    return list(results)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8501")
    ap.add_argument("--pages", nargs="*", default=PAGES)
    ap.add_argument("--sizes", nargs="*", default=[f"{w}x{h}" for w, h in SIZES])
    ap.add_argument("--out", default="responsive_audit")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    sizes = [tuple(int(x) for x in s.split("x")) for s in args.sizes]
    out = Path(args.out)
    results = asyncio.run(main_async(args.base.rstrip("/"), args.pages, sizes, out, args.workers))
    (out / "audit.json").write_text(json.dumps(results, indent=1))
    bad = [r for r in results if r.get("error") or r.get("overflowPx") or r.get("exceptions") or r["console_errors"]]
    print(f"\n{len(results)} page loads, {len(bad)} with overflow, errors or exceptions → {out / 'audit.json'}")


if __name__ == "__main__":
    main()
