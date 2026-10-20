"""Cloudzy's design system, "Atmospheric Intelligence": tokens, global styles and HTML building blocks.

Art direction
- A premium weather product with a scientific-instrument precision: deep midnight and ink
  surfaces, electric sky and cobalt accents used sparingly, cloud-white and silver text, and
  amber/gold reserved for sunlight, warmth and highlights.
- Hierarchy over boxes: one focal point per screen, editorial page headers, hairline dividers
  and instrument-style readouts instead of a card around everything.
- Type: Inter for the interface (tabular figures for data), Outfit for display figures such as the
  temperature. Both SIL OFL, self-hosted (static/vendor/fonts) and registered in config.toml.

Tokens are exposed as CSS custom properties (`--cz-*`). Surfaces and borders are translucent so they
read on both Streamlit themes. Motion uses shared duration/easing tokens and is switched off under
`prefers-reduced-motion`. Icons are Material Symbols (shipped with Streamlit).

Presentation only: nothing here fetches data or decides anything.
"""

from __future__ import annotations

from html import escape

# --- Tokens ---------------------------------------------------------------------------------------

COLORS = {
    "ink": "#040914",        # immersive surfaces (map, night sky)
    "midnight": "#060d1f",   # app background (dark theme)
    "navy": "#0c1730",       # raised surfaces
    "navy-2": "#13224a",
    "cobalt": "#2563d9",     # primary actions
    "sky": "#4aa3ff",        # electric sky: accents, focus, active states
    "sky-soft": "#bfe0ff",
    "cloud": "#eef3fb",      # primary text on dark
    "silver": "#a9b6cc",     # secondary text on dark
    "amber": "#f2a93b",      # sunlight, warmth, highlights
    "gold": "#d9b45a",
    "violet": "#8b7cf6",     # Cloudzy Core accents
    "sun": "#f5b800",
    "sun-ink": "#8a5a00",
    # Meteorological colours
    "rain": "#4fb3ff", "snow": "#dce9ff", "wind": "#5cd6c0", "pressure": "#a58cff",
    # Status (always paired with an icon and text)
    "ok": "#34b37a", "warn": "#e39a1c", "risk": "#e2566a",
}
SPACE = {"1": "4px", "2": "8px", "3": "12px", "4": "16px", "5": "20px", "6": "24px", "8": "32px", "10": "40px"}
RADII = {"sm": "8px", "md": "12px", "lg": "18px", "xl": "26px", "pill": "999px"}
TYPE = {"xs": ".75rem", "sm": ".875rem", "md": "1rem", "lg": "1.15rem", "xl": "1.45rem", "2xl": "2rem",
        "3xl": "2.6rem", "display": "clamp(5rem, 14vw, 8.5rem)"}
SHADOWS = {
    "1": "0 1px 2px rgba(2,6,18,.25), 0 4px 16px rgba(2,6,18,.18)",
    "2": "0 2px 8px rgba(2,6,18,.30), 0 18px 48px rgba(2,6,18,.35)",
    "glow": "0 0 0 1px rgba(74,163,255,.45), 0 0 24px rgba(74,163,255,.25)",
}
MOTION = {"fast": "140ms", "base": "240ms", "slow": "600ms", "ease": "cubic-bezier(.16,1,.3,1)",
          "ease-in-out": "cubic-bezier(.65,0,.35,1)"}
NEUTRAL = {"surface": "rgba(140,160,195,.07)", "surface-2": "rgba(140,160,195,.12)",
           "border": "rgba(140,160,195,.20)", "border-strong": "rgba(140,160,195,.38)"}
FONTS = {"ui": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
         "display": "Outfit, Inter, sans-serif"}


def _vars() -> str:
    groups = (("c", COLORS), ("s", SPACE), ("r", RADII), ("t", TYPE), ("sh", SHADOWS), ("m", MOTION), ("n", NEUTRAL),
              ("f", FONTS))
    return "".join(f"--cz-{p}-{k}:{v};" for p, d in groups for k, v in d.items())


GLOBAL_CSS = f"""
<style>
:root{{{_vars()}}}

/* Type: refined weights, tabular figures wherever numbers are compared. */
.stApp{{font-feature-settings:"cv11" 1,"ss01" 1;-webkit-font-smoothing:antialiased}}
h1,h2,h3,h4{{letter-spacing:-.015em}}
[data-testid="stMarkdownContainer"] h4{{margin-top:.4rem}}
[data-testid="stMetricValue"],[data-testid="stDataFrame"],.cz-num{{font-variant-numeric:tabular-nums}}

/* App shell: an ink-to-midnight sidebar with a gold "you are here" marker. */
[data-testid="stSidebar"]{{background:linear-gradient(180deg,var(--cz-c-ink) 0%,#0a1630 60%,#0e1d3f 100%);
  border-right:1px solid rgba(140,160,195,.12)}}
[data-testid="stSidebar"] *{{color:#e6edf8}}
[data-testid="stSidebarNav"] a{{border-radius:var(--cz-r-md);transition:background var(--cz-m-fast) var(--cz-m-ease)}}
[data-testid="stSidebarNav"] a:hover{{background:rgba(74,163,255,.12)}}
[data-testid="stSidebarNav"] a[aria-current="page"]{{background:linear-gradient(90deg,rgba(74,163,255,.22),rgba(74,163,255,.06));
  box-shadow:inset 3px 0 0 var(--cz-c-amber)}}
[data-testid="stSidebarNav"] [data-testid="stNavSectionHeader"]{{opacity:.55;text-transform:uppercase;
  letter-spacing:.12em;font-size:.68rem;font-weight:600}}

/* Instrument readouts instead of boxed tiles: label, then a display figure, on a hairline. */
[data-testid="stMetric"]{{background:none;border:none;border-top:1px solid var(--cz-n-border);border-radius:0;
  padding:var(--cz-s-3) 2px var(--cz-s-1)}}
[data-testid="stMetricLabel"] p{{font-size:.7rem;text-transform:uppercase;letter-spacing:.09em;font-weight:600;opacity:.75}}
[data-testid="stMetricValue"]{{font-family:var(--cz-f-display);font-weight:400;letter-spacing:-.01em}}
/* Never clip a label or value with "…": wrap onto a second line instead. */
[data-testid="stMetricLabel"],[data-testid="stMetricLabel"] *,[data-testid="stMetricValue"],[data-testid="stMetricValue"] *,
[data-testid="stMetricDelta"],[data-testid="stMetricDelta"] *{{
  white-space:normal !important;overflow:visible !important;text-overflow:clip !important}}
[data-testid="stMetricValue"]{{line-height:1.15}}
[data-testid="stVerticalBlockBorderWrapper"]{{border-radius:var(--cz-r-lg)}}
[data-testid="stExpander"] details{{border-radius:var(--cz-r-md);border-color:var(--cz-n-border)}}
[data-testid="stExpander"] summary:hover{{color:var(--cz-c-sky)}}

/* Controls: clear hover, focus, pressed and disabled states, one easing. */
.stButton button,.stDownloadButton button,[data-testid="stFormSubmitButton"] button{{
  transition:transform var(--cz-m-fast) var(--cz-m-ease),box-shadow var(--cz-m-fast) var(--cz-m-ease),
  background var(--cz-m-fast) var(--cz-m-ease)}}
.stButton button:hover:not(:disabled){{box-shadow:var(--cz-sh-glow)}}
.stButton button:active:not(:disabled){{transform:translateY(1px)}}
:focus-visible{{outline:3px solid var(--cz-c-sky) !important;outline-offset:2px}}

/* Layout: long names wrap, phones get a 16 px gutter, no horizontal overflow. */
.cz-hero .place,.cz-place-card h4{{overflow-wrap:anywhere}}
[data-testid="stMainBlockContainer"]{{padding-top:4rem;padding-bottom:3rem}}
@media (max-width:640px){{[data-testid="stMainBlockContainer"]{{padding-top:3.6rem;padding-left:1rem;padding-right:1rem}}}}

/* Building blocks. */
.cz-ico{{font-family:"Material Symbols Rounded";font-weight:normal;font-style:normal;line-height:1;
  letter-spacing:normal;text-transform:none;white-space:nowrap;direction:ltr;display:inline-block;
  vertical-align:-.18em;font-feature-settings:"liga";-webkit-font-smoothing:antialiased;
  font-variation-settings:"FILL" 1,"wght" 400,"GRAD" 0,"opsz" 24}}
.cz-page{{margin:0 0 var(--cz-s-5)}}
.cz-page .eyebrow{{display:flex;align-items:center;gap:6px;margin:0 0 6px;font-size:.72rem;font-weight:600;
  letter-spacing:.14em;text-transform:uppercase;color:var(--cz-c-sky)}}
.cz-page .eyebrow .cz-ico{{font-size:1rem}}
.cz-page h1{{font-family:var(--cz-f-display);font-weight:500;font-size:var(--cz-t-3xl);line-height:1.05;margin:0;
  letter-spacing:-.02em;padding:0}}
.cz-page p.sub{{margin:var(--cz-s-2) 0 0;max-width:70ch;font-size:var(--cz-t-md);opacity:.78;line-height:1.5}}
.cz-eyebrow{{display:flex;align-items:center;gap:6px;margin:var(--cz-s-2) 0 var(--cz-s-3);font-size:.72rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--cz-c-amber)}}
.cz-eyebrow .cz-ico{{font-size:1rem}}
.cz-section{{display:flex;align-items:baseline;gap:var(--cz-s-2);margin:var(--cz-s-8) 0 var(--cz-s-3);
  padding-bottom:var(--cz-s-2);border-bottom:1px solid var(--cz-n-border)}}
.cz-section h3{{margin:0;font-family:var(--cz-f-display);font-size:var(--cz-t-xl);font-weight:500;letter-spacing:-.01em}}
.cz-section .cz-ico{{font-size:1.25rem;color:var(--cz-c-sky);align-self:center}}
.cz-section p{{margin:0 0 0 auto;font-size:var(--cz-t-sm);opacity:.65}}
.cz-chip{{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:var(--cz-r-pill);
  font-size:var(--cz-t-xs);font-weight:600;border:1px solid currentColor;line-height:1.5;white-space:nowrap}}
.cz-chip .cz-ico{{font-size:1rem}}
.cz-chip.ok{{color:var(--cz-c-ok);background:rgba(52,179,122,.10)}}
.cz-chip.wait{{color:var(--cz-c-sky);background:rgba(74,163,255,.10)}}
.cz-chip.warn{{color:var(--cz-c-warn);background:rgba(227,154,28,.12)}}
.cz-chip.risk{{color:var(--cz-c-risk);background:rgba(226,86,106,.10)}}
.cz-chip.muted{{color:inherit;opacity:.8;background:var(--cz-n-surface)}}
.cz-panel{{background:var(--cz-n-surface);border:1px solid var(--cz-n-border);border-radius:var(--cz-r-lg);
  padding:var(--cz-s-4) var(--cz-s-5)}}
.cz-stat{{display:flex;flex-direction:column;gap:2px}}
.cz-stat .k{{font-size:.7rem;text-transform:uppercase;letter-spacing:.09em;font-weight:600;opacity:.7}}
.cz-stat .v{{font-family:var(--cz-f-display);font-size:var(--cz-t-2xl);font-weight:400;line-height:1.1;
  font-variant-numeric:tabular-nums}}
.cz-stat .n{{font-size:var(--cz-t-sm);opacity:.7}}
.cz-skel{{border-radius:var(--cz-r-lg);background:linear-gradient(90deg,var(--cz-n-surface) 0%,
  var(--cz-n-surface-2) 50%,var(--cz-n-surface) 100%);background-size:200% 100%;
  animation:czshimmer 1.4s linear infinite}}
@keyframes czshimmer{{from{{background-position:200% 0}}to{{background-position:-200% 0}}}}
.cz-fade{{animation:czfade var(--cz-m-slow) var(--cz-m-ease) both}}
@keyframes czfade{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:none}}}}
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


def page_header(title: str, eyebrow: str, icon_name: str, subtitle: str = "") -> str:
    """An editorial page header: small icon eyebrow, display title, one-line subtitle."""
    sub = f'<p class="sub">{escape(subtitle)}</p>' if subtitle else ""
    return (f'<header class="cz-page cz-fade"><p class="eyebrow">{icon(icon_name)}{escape(eyebrow)}</p>'
            f"<h1>{escape(title)}</h1>{sub}</header>")


def eyebrow(text: str, icon_name: str) -> str:
    """A small uppercase label that introduces a column or group without a box around it."""
    return f'<p class="cz-eyebrow">{icon(icon_name)}{escape(text)}</p>'


def section(title: str, icon_name: str, note: str = "") -> str:
    """A section heading with an icon, a hairline underneath and an optional right-aligned note."""
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
