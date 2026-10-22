"""HTML for the Overview page: tiles that summarise one feature each, labelled by the kind of data they show.

Data kinds (one chip per tile, never colour alone):
- Provider forecast: Open-Meteo weather (model forecasts and analyses).
- Model estimate: CAMS air quality.
- Official warning: national agencies (via Earth Intelligence).
- Cloudzy ML prediction: Cloudzy Core (Delhi only).
- Satellite detection, Automatic estimate (GDACS), Rule-based guidance (Outdoor Intelligence).
Presentation only: data in, HTML out.
"""

from __future__ import annotations

from html import escape

from src.theme import chip, icon

KIND_CHIPS = {
    "provider": ("Provider forecast", "muted", "cloud"),
    "model_estimate": ("Model estimate", "muted", "deployed_code"),
    "official": ("Official warning", "warn", "campaign"),
    "ml": ("Cloudzy ML prediction", "wait", "science"),
    "rules": ("Rule-based guidance", "muted", "rule"),
    "mixed": ("Several sources", "muted", "layers"),
    "tracking": ("Tracked evaluation", "ok", "query_stats"),
}

CSS = """
<style>
.cz-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:var(--cz-s-4)}
.cz-tile{border:1px solid var(--cz-n-border);border-radius:var(--cz-r-lg);padding:var(--cz-s-4);
  background:linear-gradient(180deg,var(--cz-n-surface-2),var(--cz-n-surface));display:flex;flex-direction:column;gap:8px;
  min-height:150px}
.cz-tile .th{display:flex;align-items:center;gap:8px;justify-content:space-between;flex-wrap:wrap}
.cz-tile .th h3{margin:0;font-size:.74rem;text-transform:uppercase;letter-spacing:.1em;font-weight:600;opacity:.8;
  display:flex;align-items:center;gap:6px}
.cz-tile .th h3 .cz-ico{font-size:1.05rem;color:var(--cz-c-sky)}
.cz-tile .big{font-family:var(--cz-f-display);font-size:2rem;line-height:1.05;margin:0;font-variant-numeric:tabular-nums}
.cz-tile .big small{font-family:var(--cz-f-ui);font-size:.85rem;opacity:.75;margin-left:6px}
.cz-tile p{margin:0;font-size:var(--cz-t-sm);line-height:1.45}
.cz-tile ul{margin:0;padding-left:18px;font-size:var(--cz-t-sm);line-height:1.45}
.cz-tile .foot{margin-top:auto;font-size:var(--cz-t-xs);opacity:.68}
.cz-tile .sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:baseline;
  box-shadow:0 0 0 1px var(--cz-n-border-strong)}
.cz-legend-kinds{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 var(--cz-s-3)}
</style>
"""


def kind_chip(kind: str) -> str:
    label, k, ico = KIND_CHIPS[kind]
    return chip(label, k, ico)


def tile(title: str, icon_name: str, kind: str, big: str = "", body: str = "", foot: str = "",
         big_note: str = "") -> str:
    """One summary tile. `big`, `big_note` and `foot` are plain text (escaped); `body` is trusted HTML."""
    big_html = f'<p class="big">{escape(big)}<small>{escape(big_note)}</small></p>' if big else ""
    foot_html = f'<p class="foot">{escape(foot)}</p>' if foot else ""
    return (f'<section class="cz-tile cz-fade" aria-label="{escape(title)}"><div class="th"><h3>{icon(icon_name)}'
            f'{escape(title)}</h3>{kind_chip(kind)}</div>{big_html}{body}{foot_html}</section>')


def kinds_legend() -> str:
    return '<div class="cz-legend-kinds" aria-label="Kinds of data">' + "".join(
        kind_chip(k) for k in ("provider", "model_estimate", "official", "ml", "rules", "tracking")) + "</div>"
