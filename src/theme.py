"""Cloudzy's design system: tokens, global styles and small HTML building blocks.

- **Tokens:** one place for colours, spacing, radii, type, shadows and motion,
  exposed as CSS custom properties (`--cz-*`). Component styles in
  `src.weather_view` and the pages use these instead of hard-coded values.
- **Themes:** surfaces and borders are translucent, so the same rules read well
  on Streamlit's light and dark backgrounds (`.streamlit/config.toml`). Brand
  colours are fixed. The viewer switches theme in Streamlit's ⋮ menu.
- **Motion:** short, purposeful transitions only; all of them are switched off
  under `prefers-reduced-motion`.
- **Icons:** Material Symbols, which Streamlit already ships, for crisp
  vector icons inside custom HTML (`icon()`), and `:material/…:` in widgets.

Presentation only: nothing here fetches data or decides anything.
"""

from __future__ import annotations

from html import escape

# --- Tokens ---------------------------------------------------------------------------------------

COLORS = {
    "midnight": "#0b1630",   # deepest navy (dark backgrounds, night hero)
    "navy": "#0f1d3a",       # brand ink
    "navy-2": "#1d2f5c",
    "sky": "#2a78d6",        # primary action and accents
    "sky-2": "#5aa2f0",      # primary on dark surfaces
    "sky-soft": "#d9edff",
    "cloud": "#f7fafd",      # cloud white
    "violet": "#7b6cf6",     # secondary gradient stop, Cloudzy Core accents
    "sun": "#f5b800",        # sunshine yellow, used sparingly
    "sun-ink": "#8a5a00",    # yellow that stays readable on light backgrounds
    "ok": "#2e9d6a",
    "warn": "#d98a00",
    "risk": "#d1495b",
}
SPACE = {"1": "4px", "2": "8px", "3": "12px", "4": "16px", "5": "20px", "6": "24px", "8": "32px"}
RADII = {"sm": "10px", "md": "14px", "lg": "20px", "xl": "28px", "pill": "999px"}
TYPE = {"xs": ".78rem", "sm": ".88rem", "md": "1rem", "lg": "1.15rem", "xl": "1.4rem", "2xl": "1.9rem"}
SHADOWS = {
    "1": "0 1px 2px rgba(15,29,58,.06), 0 2px 10px rgba(15,29,58,.05)",
    "2": "0 2px 6px rgba(15,29,58,.08), 0 10px 30px rgba(15,29,58,.10)",
}
MOTION = {"fast": "150ms", "base": "250ms", "ease": "cubic-bezier(.2,.7,.2,1)"}

# Translucent neutrals: readable on both Streamlit themes.
NEUTRAL = {"surface": "rgba(128,145,170,.08)", "surface-2": "rgba(128,145,170,.14)",
           "border": "rgba(128,145,170,.28)", "border-strong": "rgba(128,145,170,.45)"}


def _vars() -> str:
    groups = (("c", COLORS), ("s", SPACE), ("r", RADII), ("t", TYPE), ("sh", SHADOWS), ("m", MOTION), ("n", NEUTRAL))
    return "".join(f"--cz-{p}-{k}:{v};" for p, d in groups for k, v in d.items())


GLOBAL_CSS = f"""
<style>
:root{{{_vars()}}}

/* Type: tighter headings, comfortable body. */
h1,h2,h3,h4{{letter-spacing:-.01em}}
[data-testid="stMarkdownContainer"] h4{{margin-top:.4rem}}

/* Sidebar: a midnight panel in both themes, with the brand gradient. */
[data-testid="stSidebar"]{{background:linear-gradient(180deg,var(--cz-c-midnight) 0%,var(--cz-c-navy-2) 100%)}}
[data-testid="stSidebar"] *{{color:#e9f1fc}}
[data-testid="stSidebarNav"] a{{border-radius:var(--cz-r-md);transition:background var(--cz-m-fast) var(--cz-m-ease)}}
[data-testid="stSidebarNav"] a:hover{{background:rgba(90,162,240,.16)}}
[data-testid="stSidebarNav"] a[aria-current="page"]{{background:rgba(90,162,240,.26);
  box-shadow:inset 3px 0 0 var(--cz-c-sun)}}
[data-testid="stSidebarNav"] [data-testid="stNavSectionHeader"]{{opacity:.7;text-transform:uppercase;
  letter-spacing:.08em;font-size:var(--cz-t-xs)}}

/* Cards: metrics and bordered containers share one look. */
[data-testid="stMetric"]{{background:var(--cz-n-surface);border:1px solid var(--cz-n-border);
  border-radius:var(--cz-r-lg);padding:var(--cz-s-3) var(--cz-s-4)}}
[data-testid="stMetricLabel"] p{{font-size:var(--cz-t-xs);text-transform:uppercase;letter-spacing:.05em}}
[data-testid="stVerticalBlockBorderWrapper"]{{border-radius:var(--cz-r-lg)}}
[data-testid="stExpander"] details{{border-radius:var(--cz-r-md)}}

/* Visible focus for keyboard users, everywhere. */
:focus-visible{{outline:3px solid var(--cz-c-sky-2) !important;outline-offset:2px}}

/* Long names and narrow screens: wrap instead of overflowing. */
.cz-hero .place,.cz-place-card h4{{overflow-wrap:anywhere}}
[data-testid="stMainBlockContainer"]{{padding-top:4rem;padding-bottom:3rem}}
@media (max-width:640px){{[data-testid="stMainBlockContainer"]{{padding-top:3.6rem;padding-left:1rem;padding-right:1rem}}}}

/* Shared building blocks. */
.cz-ico{{font-family:"Material Symbols Rounded";font-weight:normal;font-style:normal;line-height:1;
  letter-spacing:normal;text-transform:none;white-space:nowrap;direction:ltr;display:inline-block;
  vertical-align:-.18em;font-feature-settings:"liga";-webkit-font-smoothing:antialiased;
  font-variation-settings:"FILL" 1,"wght" 500,"GRAD" 0,"opsz" 24}}
.cz-section{{display:flex;align-items:center;gap:var(--cz-s-2);margin:var(--cz-s-6) 0 var(--cz-s-2)}}
.cz-section h3{{margin:0;font-size:var(--cz-t-lg);font-weight:700}}
.cz-section .cz-ico{{font-size:1.3rem;color:var(--cz-c-sky)}}
.cz-section p{{margin:0 0 0 auto;font-size:var(--cz-t-sm);opacity:.75}}
.cz-chip{{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:var(--cz-r-pill);
  font-size:var(--cz-t-xs);font-weight:600;border:1px solid currentColor;line-height:1.5;white-space:nowrap}}
.cz-chip .cz-ico{{font-size:1rem}}
.cz-chip.ok{{color:var(--cz-c-ok);background:rgba(46,157,106,.10)}}
.cz-chip.wait{{color:var(--cz-c-sky);background:rgba(42,120,214,.10)}}
.cz-chip.warn{{color:var(--cz-c-warn);background:rgba(217,138,0,.12)}}
.cz-chip.risk{{color:var(--cz-c-risk);background:rgba(209,73,91,.10)}}
.cz-chip.muted{{color:inherit;opacity:.8;background:var(--cz-n-surface)}}
.cz-panel{{background:var(--cz-n-surface);border:1px solid var(--cz-n-border);border-radius:var(--cz-r-lg);
  padding:var(--cz-s-4) var(--cz-s-5)}}
.cz-skel{{border-radius:var(--cz-r-lg);background:linear-gradient(90deg,var(--cz-n-surface) 0%,
  var(--cz-n-surface-2) 50%,var(--cz-n-surface) 100%);background-size:200% 100%;
  animation:czshimmer 1.2s linear infinite}}
@keyframes czshimmer{{from{{background-position:200% 0}}to{{background-position:-200% 0}}}}
.cz-fade{{animation:czfade var(--cz-m-base) var(--cz-m-ease) both}}
@keyframes czfade{{from{{opacity:0;transform:translateY(4px)}}to{{opacity:1;transform:none}}}}
@media (prefers-reduced-motion:reduce){{
  .cz-fade,.cz-skel{{animation:none}}
  *{{transition:none !important;scroll-behavior:auto !important}}
}}
</style>
"""


# --- Small HTML building blocks -------------------------------------------------------------------

def icon(name: str, label: str | None = None) -> str:
    """A Material Symbols icon. Decorative unless `label` is given (then announced as an image)."""
    if label:
        return f'<span class="cz-ico" role="img" aria-label="{escape(label)}">{escape(name)}</span>'
    return f'<span class="cz-ico" aria-hidden="true">{escape(name)}</span>'


def section(title: str, icon_name: str, note: str = "") -> str:
    """A section heading with an icon and an optional right-aligned note."""
    note_html = f"<p>{escape(note)}</p>" if note else ""
    return f'<div class="cz-section">{icon(icon_name)}<h3>{escape(title)}</h3>{note_html}</div>'


def chip(text: str, kind: str = "muted", icon_name: str | None = None, title: str | None = None) -> str:
    """A status chip. Always text plus icon, never colour alone. `kind`: ok, wait, warn, risk, muted."""
    kind = kind if kind in {"ok", "wait", "warn", "risk", "muted"} else "muted"
    tip = f' title="{escape(title)}"' if title else ""
    ico = icon(icon_name) if icon_name else ""
    return f'<span class="cz-chip {kind}"{tip}>{ico}{escape(text)}</span>'


def skeleton(height_px: int, width: str = "100%") -> str:
    """A loading placeholder shown while data is being fetched (static under reduced motion)."""
    return (f'<div class="cz-skel" role="status" aria-label="Loading" '
            f'style="height:{int(height_px)}px;width:{escape(width)};margin:6px 0"></div>')
