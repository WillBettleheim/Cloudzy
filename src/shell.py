"""Cloudzy's responsive app shell: the small browser-side pieces Streamlit's own shell doesn't provide.

One invisible component (components v2), mounted on every page by streamlit_app.py:
- Adds `viewport-fit=cover` to the viewport meta tag, so `env(safe-area-inset-*)` works on phones with notches and
  home indicators (the theme pads the bottom navigation and sidebar with it).
- On short landscape screens (a phone turned sideways: narrower than 1024 and lower than 500 CSS px), collapses
  the sidebar once per browser session, with Streamlit's own collapse button so its state stays consistent. The
  menu button brings it back, and it is never collapsed again in that session.
- Marks the current page in the mobile bottom navigation (theme: `.st-key-cz_mobile_nav`).

Decisions use the viewport's size and orientation only, never the user agent.
"""

from __future__ import annotations

from src.theme import BREAKPOINTS, SHORT_SCREEN

JS = r"""
const LG = %LG%, SHORT = %SHORT%;
const FLAG = "cloudzy-sidebar-auto-collapsed";

function viewportFit() {
  const m = document.querySelector('meta[name="viewport"]');
  if (m && !/viewport-fit/.test(m.content)) m.content += ", viewport-fit=cover";
}

function collapseOnShortLandscape() {
  if (!(window.innerWidth < LG && window.innerHeight < SHORT && window.innerWidth > window.innerHeight)) return;
  let done = false;
  try { done = sessionStorage.getItem(FLAG) === "1"; } catch (e) { /* storage blocked: collapse once per load */ }
  if (done) return;
  const sidebar = document.querySelector('[data-testid="stSidebar"]');
  const btn = document.querySelector('[data-testid="stSidebarCollapseButton"] button');
  const open = sidebar && sidebar.getAttribute("data-collapsed") !== "true" && sidebar.getBoundingClientRect().right > 50;
  if (open && btn) {
    btn.click();
    try { sessionStorage.setItem(FLAG, "1"); } catch (e) { /* ignore */ }
  }
}

function markCurrent() {
  const here = window.location.pathname.replace(/\/+$/, "") || "/";
  document.querySelectorAll('.st-key-cz_mobile_nav a[href]').forEach((a) => {
    // Streamlit writes page links relative to the app root; the default page's link is empty.
    const path = ("/" + (a.getAttribute("href") || "").split(/[?#]/)[0].replace(/^\/+/, "")).replace(/\/+$/, "") || "/";
    const on = path === here;
    a.classList.toggle("cz-current", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
}

export default function (component) {
  viewportFit();
  // Streamlit draws the page around this component; wait a moment for the sidebar and links to exist.
  setTimeout(() => { collapseOnShortLandscape(); markCurrent(); }, 120);
  setTimeout(markCurrent, 800);
}
""".replace("%LG%", str(BREAKPOINTS["lg"])).replace("%SHORT%", str(SHORT_SCREEN))

_component = None


def mount(path: str) -> None:
    """Run the shell script for this page view (`path` makes each navigation a fresh call)."""
    global _component
    import streamlit as st
    if _component is None:
        _component = st.components.v2.component("cloudzy_shell", js=JS)
    _component(key="cloudzy_shell", data={"path": path})
