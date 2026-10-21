"""HTML and charts for the Air Quality and Outdoor Intelligence pages.

Pure functions, like src.weather_view: data in, HTML strings or Plotly figures out.
- Every index is printed with the name of its standard; every value with its unit and time.
- Category colours are the standards' own, and are never the only signal: each colour sits next
  to its text label, and the outdoor timeline uses a symbol in every cell as well.
- Earlier hours are model values, drawn in grey and labelled "model, earlier hours"; the forecast
  is drawn in the provider blue. Neither is ever called an observation. Missing hours stay gaps.
- The hero sits on a fixed ink surface (like the weather hero), so its text contrast doesn't
  depend on the Streamlit theme. The tint behind it is decoration at low opacity.
"""

from __future__ import annotations

from html import escape

import pandas as pd
import plotly.graph_objects as go

from src import aqi
from src import outdoor as O
from src import units as U
from src.air_quality import AirQuality, Reading
from src.theme import chip, icon
from src.ui import _layout, fmt

DASH = "—"

CSS = """
<style>
/* ===== Air quality & outdoor components. Tokens (--cz-*) come from src.theme. ===== */
.cz-aq-hero{position:relative;overflow:hidden;isolation:isolate;border-radius:var(--cz-r-xl);color:#f4f7fd;
  background:linear-gradient(165deg,#040914 0%,#0b1733 55%,#13224a 100%);box-shadow:var(--cz-sh-2);
  padding:clamp(20px,3vw,32px) clamp(20px,3.4vw,38px);margin:var(--cz-s-1) 0 var(--cz-s-2);
  display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:var(--cz-s-6)}
.cz-aq-hero .haze{position:absolute;inset:-30%;z-index:-1;pointer-events:none;opacity:.28;
  background:radial-gradient(closest-side at 22% 38%,var(--aq-tint) 0%,transparent 75%),
             radial-gradient(closest-side at 80% 85%,var(--aq-tint) 0%,transparent 70%);
  filter:blur(18px);animation:czhaze 40s ease-in-out infinite alternate}
@keyframes czhaze{from{transform:translate3d(-2%,0,0) scale(1)}to{transform:translate3d(3%,-2%,0) scale(1.06)}}
.cz-aq-hero .eyebrow{margin:0;font-size:.72rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;
  color:#bfe0ff;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.cz-aq-hero .place{margin:6px 0 0;font-size:var(--cz-t-lg);font-weight:600;overflow-wrap:anywhere}
.cz-aq-hero .reading{display:flex;align-items:flex-end;gap:var(--cz-s-4);margin:var(--cz-s-3) 0 var(--cz-s-2);flex-wrap:wrap}
.cz-aq-hero .num{font-family:var(--cz-f-display);font-weight:300;font-size:clamp(4.2rem,11vw,6.6rem);line-height:.9;
  letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.cz-aq-hero .cat{display:flex;flex-direction:column;gap:4px;padding-bottom:6px}
.cz-aq-hero .cat .lbl{display:flex;align-items:center;gap:8px;font-size:var(--cz-t-xl);font-family:var(--cz-f-display);
  font-weight:500}
.cz-aq-hero .sw{width:14px;height:14px;border-radius:4px;background:var(--aq-tint);box-shadow:0 0 0 2px rgba(255,255,255,.75);
  flex:none}
.cz-aq-hero .std{font-size:var(--cz-t-sm);color:#c9d6ea}
.cz-aq-hero .msg{margin:0;max-width:56ch;color:#dfe8f6;line-height:1.5}
.cz-aq-hero .side{display:flex;flex-direction:column;gap:var(--cz-s-3);justify-content:space-between}
.cz-aq-hero dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:6px 14px;font-size:var(--cz-t-sm)}
.cz-aq-hero dt{opacity:.72;display:flex;gap:6px;align-items:center}
.cz-aq-hero dd{margin:0;font-weight:600;overflow-wrap:anywhere}
.cz-aq-hero .chips{display:flex;gap:6px;flex-wrap:wrap}
.cz-aq-hero .cz-chip{color:#e6edf8;background:rgba(255,255,255,.06);border-color:rgba(230,237,248,.45)}
.cz-gauge{grid-column:1/-1}
.cz-gauge .bar{position:relative;display:flex;height:10px;border-radius:999px;overflow:visible;gap:2px}
.cz-gauge .seg{height:100%;border-radius:3px;opacity:.9}
.cz-gauge .seg:first-child{border-radius:999px 3px 3px 999px}.cz-gauge .seg:last-child{border-radius:3px 999px 999px 3px}
.cz-gauge .mark{position:absolute;top:-6px;width:4px;height:22px;margin-left:-2px;border-radius:2px;background:#fff;
  box-shadow:0 0 0 2px #040914,0 0 12px rgba(255,255,255,.6)}
.cz-gauge .labels{display:flex;gap:2px;margin-top:6px;font-size:.68rem;color:#c9d6ea}
.cz-gauge .labels span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media (max-width:760px){.cz-aq-hero{grid-template-columns:1fr}.cz-gauge .labels{display:none}}

.cz-pols{display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));gap:var(--cz-s-3) var(--cz-s-5)}
.cz-pol{border-top:1px solid var(--cz-n-border);padding:var(--cz-s-3) 0 var(--cz-s-1)}
.cz-pol .k{margin:0;font-size:.7rem;text-transform:uppercase;letter-spacing:.09em;font-weight:600;opacity:.72}
.cz-pol .v{margin:6px 0 2px;font-family:var(--cz-f-display);font-size:1.7rem;line-height:1.1;font-variant-numeric:tabular-nums}
.cz-pol .v small{font-family:var(--cz-f-ui);font-size:.8rem;opacity:.7;margin-left:4px}
.cz-pol .n{margin:0;font-size:var(--cz-t-sm);opacity:.78;line-height:1.45}
.cz-pol .ratio{height:4px;border-radius:2px;background:var(--cz-n-surface-2);margin:8px 0 4px;overflow:hidden}
.cz-pol .ratio i{display:block;height:100%;background:var(--cz-c-sky);border-radius:2px}
.cz-pol .ratio i.over{background:var(--cz-c-warn)}

.cz-facts{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.cz-facts li{display:flex;gap:10px;align-items:flex-start;line-height:1.5}
.cz-facts li .cz-ico{color:var(--cz-c-sky);font-size:1.15rem;margin-top:2px}
.cz-facts li .tag{font-size:.7rem;font-weight:600;letter-spacing:.06em;text-transform:uppercase;opacity:.6;margin-left:6px}

.cz-days{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:var(--cz-s-3)}
.cz-day{background:var(--cz-n-surface);border:1px solid var(--cz-n-border);border-radius:var(--cz-r-md);
  padding:var(--cz-s-3);position:relative;overflow:hidden}
.cz-day::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--aq-tint)}
.cz-day h4{margin:0;font-size:var(--cz-t-sm);font-weight:600}
.cz-day .rng{margin:4px 0;font-family:var(--cz-f-display);font-size:1.35rem;font-variant-numeric:tabular-nums}
.cz-day p{margin:0;font-size:var(--cz-t-xs);opacity:.8;line-height:1.45}

.cz-verdict{border-radius:var(--cz-r-lg);padding:var(--cz-s-4) var(--cz-s-5);border:1px solid var(--cz-n-border);
  background:var(--cz-n-surface);display:flex;flex-direction:column;gap:var(--cz-s-2)}
.cz-verdict h3{margin:0;font-family:var(--cz-f-display);font-weight:500;font-size:var(--cz-t-xl);display:flex;gap:10px;
  align-items:center;flex-wrap:wrap}
.cz-verdict p{margin:0;line-height:1.5}
.cz-verdict.ok{border-color:rgba(52,179,122,.55)}.cz-verdict.warn{border-color:rgba(227,154,28,.6)}
.cz-verdict.risk{border-color:rgba(226,86,106,.6)}

.cz-wins{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:var(--cz-s-4)}
.cz-win{border:1px solid var(--cz-n-border);border-radius:var(--cz-r-lg);padding:var(--cz-s-4);
  background:linear-gradient(180deg,var(--cz-n-surface-2),var(--cz-n-surface));display:flex;flex-direction:column;gap:10px}
.cz-win .cz-chip{align-self:flex-start}
.cz-win .when{margin:0;font-size:.72rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;opacity:.75}
.cz-win h4{margin:0;font-family:var(--cz-f-display);font-weight:500;font-size:1.55rem;font-variant-numeric:tabular-nums}
.cz-win .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 14px;margin:0}
.cz-win .grid div{font-size:var(--cz-t-sm)}
.cz-win .grid dt{font-size:.66rem;text-transform:uppercase;letter-spacing:.09em;opacity:.65;font-weight:600}
.cz-win .grid dd{margin:0;font-variant-numeric:tabular-nums}
.cz-win ul{margin:0;padding:0;list-style:none;display:grid;gap:4px;font-size:var(--cz-t-sm);line-height:1.45}
.cz-win li{display:flex;gap:6px}
.cz-win li .cz-ico{font-size:1rem;margin-top:2px}
.cz-win li.good .cz-ico{color:var(--cz-c-ok)}.cz-win li.warn .cz-ico{color:var(--cz-c-warn)}
.cz-win li.risk .cz-ico{color:var(--cz-c-risk)}.cz-win li.note{opacity:.75}

.cz-tl{overflow-x:auto;padding-bottom:6px}
.cz-tl table{border-collapse:separate;border-spacing:2px;font-size:.7rem;font-variant-numeric:tabular-nums}
.cz-tl th{font-weight:600;opacity:.7;padding:0 4px;text-align:left;white-space:nowrap}
.cz-tl thead th{text-align:center;padding:0}
.cz-tl td{width:24px;min-width:24px;height:24px;text-align:center;border-radius:5px;font-weight:700;color:#06101f}
.cz-tl td.ok{background:#34b37a}.cz-tl td.caution{background:#e9a636}.cz-tl td.avoid{background:#e2566a}
.cz-tl td.unavailable{background:transparent;border:1px dashed var(--cz-n-border-strong);color:inherit;opacity:.6}
.cz-tl td.none{background:transparent}
.cz-tl td.out{opacity:.35}
.cz-tl td.now{box-shadow:0 0 0 2px var(--cz-c-sky)}
.cz-legend{display:flex;gap:var(--cz-s-4);flex-wrap:wrap;font-size:var(--cz-t-sm);margin:var(--cz-s-2) 0 0}
.cz-legend span{display:inline-flex;gap:6px;align-items:center}
.cz-legend i{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:4px;font-style:normal;
  font-weight:700;font-size:.7rem;color:#06101f}
.cz-aq-strip{display:flex;align-items:center;gap:8px;margin:var(--cz-s-2) 0 0;font-size:var(--cz-t-sm)}
.cz-aq-strip .cz-ico{color:var(--cz-c-sky)}
.cz-aq-strip .sw{width:12px;height:12px;border-radius:3px;flex:none;box-shadow:0 0 0 1px var(--cz-n-border-strong)}
@media (prefers-reduced-motion:reduce){.cz-aq-hero .haze{animation:none}}
</style>
"""

SYMBOLS = {O.OK: "✓", O.CAUTION: "!", O.AVOID: "✕", O.UNAVAILABLE: "?"}


def _ago(retrieved_at_utc: str, now: pd.Timestamp) -> str:
    try:
        t = pd.Timestamp(retrieved_at_utc)
        t = t.tz_localize("UTC") if t.tzinfo is None else t
    except (ValueError, TypeError):
        return "retrieval time unknown"
    minutes = int((now - t).total_seconds() // 60)
    return "retrieved just now" if minutes < 1 else f"retrieved {minutes} min ago"


def pollutant_name(key: str | None) -> str:
    if not key:
        return DASH
    p = aqi.POLLUTANTS.get(key)
    return f"{p.symbol} ({p.name.lower()})" if p else key


# --- Hero ---------------------------------------------------------------------------------------------

def gauge_html(standard: aqi.Standard, value, open_ended: bool = False) -> str:
    """The standard's categories as a bar, with a marker at the value. Labelled for screen readers."""
    lower, segs, labels = 0.0, [], []
    for c in standard.categories:
        upper = standard.scale_max if c.upper is None else min(c.upper, standard.scale_max)
        width = max(upper - lower, 0) / standard.scale_max * 100
        segs.append(f'<span class="seg" style="width:{width:.2f}%;background:{c.color}" title="{escape(c.label)}"></span>')
        labels.append(f'<span style="width:{width:.2f}%">{escape(c.label)}</span>')
        lower = upper
    mark = ""
    if value is not None and not pd.isna(value):
        pos = min(float(value), standard.scale_max) / standard.scale_max * 100
        mark = f'<span class="mark" style="left:{pos:.2f}%"></span>'
    cat = standard.category(value)
    label = (f"{standard.name} scale. Current value {aqi.index_text(standard, value, open_ended)}, "
             f"category {cat.label}." if cat else f"{standard.name} scale. No current value.")
    return (f'<div class="cz-gauge" role="img" aria-label="{escape(label)}"><div class="bar">{"".join(segs)}{mark}</div>'
            f'<div class="labels" aria-hidden="true">{"".join(labels)}</div></div>')


def hero_html(place_label: str, r: Reading, data: AirQuality, now: pd.Timestamp, standard_note: str) -> str:
    st_ = r.standard
    cat = r.category
    tint = cat.color if cat else "#7c879a"
    when = f"{r.time:%a %d %b, %H:%M} {r.time.tzname() or ''}".strip() if r.time is not None else DASH
    if cat:
        num, lbl = escape(r.text), escape(cat.label)
        msg = escape(cat.general)
    else:
        num, lbl = DASH, "Not available"
        msg = escape(r.note or "No current value.")
    made_by = ("Calculated by Cloudzy with the CPCB method from modelled concentrations"
               if st_.computed_by == "Cloudzy" else "Index from Open-Meteo, calculated from modelled concentrations")
    return (
        f'<section class="cz-aq-hero cz-fade" style="--aq-tint:{tint}" aria-label="Air quality now">'
        f'<span class="haze" aria-hidden="true"></span>'
        f'<div><p class="eyebrow">{icon("air")}Air quality now · {escape(st_.name)}</p>'
        f'<p class="place">{escape(place_label)}</p>'
        f'<div class="reading"><span class="num" aria-label="{escape(st_.short)} {num}">{num}</span>'
        f'<span class="cat"><span class="lbl"><span class="sw" aria-hidden="true"></span>{lbl}</span>'
        f'<span class="std">{escape(st_.short)} · {escape(standard_note)}</span></span></div>'
        f'<p class="msg">{msg}</p></div>'
        f'<div class="side"><dl>'
        f'<dt>{icon("science")}Main pollutant</dt><dd>{escape(pollutant_name(r.dominant))}</dd>'
        f'<dt>{icon("schedule")}For the hour</dt><dd>{escape(when)}</dd>'
        f'<dt>{icon("database")}Source</dt><dd>{escape(data.source_label)}</dd>'
        f'<dt>{icon("update")}Freshness</dt><dd>{escape(_ago(data.retrieved_at_utc, now))}; forecast to '
        f'{data.coverage_end:%a %H:%M}</dd></dl>'
        f'<div class="chips">{chip("Model estimate, not a station reading", "muted", "deployed_code")}'
        f'{chip(made_by, "muted", "calculate")}</div></div>'
        f'{gauge_html(st_, r.value, r.open_ended)}</section>')


# --- Pollutants ---------------------------------------------------------------------------------------

def pollutants_html(data: AirQuality, now: pd.Timestamp, rolling_mean) -> str:
    """Hourly value and the mean to compare with the WHO 2021 guideline, for each pollutant with data."""
    row = data.at(now)
    cards = []
    for key, p in aqi.POLLUTANTS.items():
        value = None if row is None else row.get(key)
        if value is None or pd.isna(value):
            cards.append(f'<div class="cz-pol" role="group" aria-label="{escape(p.name)}"><p class="k">{escape(p.symbol)}'
                         f' · {escape(p.name)}</p><p class="v">{DASH}</p><p class="n">Not available here</p></div>')
            continue
        mean = rolling_mean(data, key, p.who_hours, now)
        if mean is None or not p.who_guideline:
            ratio_html, note = "", f"Not enough hours for a {p.who_period}."
        else:
            ratio = mean / p.who_guideline
            width = min(ratio, 1) * 100
            ratio_html = (f'<div class="ratio" aria-hidden="true"><i class="{"over" if ratio > 1 else ""}" '
                          f'style="width:{width:.0f}%"></i></div>')
            rel = f"{ratio:.1f}× the" if ratio >= 1.05 else "within the" if ratio <= 1 else "at the"
            note = f"{p.who_period} {mean:,.0f} μg/m³: {rel} WHO guideline ({p.who_guideline:,.0f})."
        cards.append(f'<div class="cz-pol" role="group" aria-label="{escape(p.name)}"><p class="k">{escape(p.symbol)} · '
                     f'{escape(p.name)}</p><p class="v">{value:,.0f}<small>μg/m³ this hour</small></p>{ratio_html}'
                     f'<p class="n">{escape(note)}</p></div>')
    return f'<div class="cz-pols">{"".join(cards)}</div>'


def facts_html(facts: list[tuple[str, str, str]]) -> str:
    """(icon, text, tag) rows, e.g. ('trending_up', 'Forecast to…', 'Forecast')."""
    items = "".join(f'<li>{icon(i)}<span>{escape(t)}<span class="tag">{escape(tag)}</span></span></li>'
                    for i, t, tag in facts)
    return f'<ul class="cz-facts">{items}</ul>'


def days_html(summary: pd.DataFrame, standard: aqi.Standard) -> str:
    cards = []
    for _, r in summary.iterrows():
        cat = standard.category(r["high"])
        partial = f" · {r['hours']} h of forecast" if r["hours"] < 24 else ""
        rng = (aqi.index_text(standard, r["low"]) + "–" + aqi.index_text(standard, r["high"], r["high_open"])
               if r["low"] != r["high"] else aqi.index_text(standard, r["high"], r["high_open"]))
        cards.append(
            f'<div class="cz-day" style="--aq-tint:{cat.color if cat else "#7c879a"}" role="group" '
            f'aria-label="{pd.Timestamp(r["date"]):%A}">'
            f'<h4>{pd.Timestamp(r["date"]):%a %d %b}</h4><p class="rng">{escape(rng)}</p>'
            f'<p>Worst: <b>{escape(r["worst_category"] or DASH)}</b> around {r["worst_time"]:%H:%M}</p>'
            f'<p>Main pollutant then: {escape(pollutant_name(r["dominant"]))}</p>'
            f'<p>PM2.5 mean {fmt(r["pm2_5_mean"], " μg/m³")}{escape(partial)}</p></div>')
    return f'<div class="cz-days">{"".join(cards)}</div>'


# --- Charts -------------------------------------------------------------------------------------------

def _bands(fig: go.Figure, standard: aqi.Standard, top: float) -> None:
    lower = 0.0
    for c in standard.categories:
        upper = top if c.upper is None else min(c.upper, top)
        if upper > lower:
            fig.add_hrect(y0=lower, y1=upper, fillcolor=c.color, opacity=0.09, line_width=0, layer="below",
                          annotation_text=c.label, annotation_position="top left",
                          annotation_font={"size": 10}, annotation_opacity=0.7)
        lower = upper
        if lower >= top:
            break


def aqi_trend_chart(data: AirQuality, standard: aqi.Standard, now: pd.Timestamp, colors: dict) -> go.Figure:
    """Earlier hours (model values) in grey, forecast in blue, category bands, and a 'now' line.

    Missing hours stay as gaps (no line is drawn across them)."""
    h = data.hourly
    col = standard.column
    start = now.tz_convert(h.index.tz).floor("h")
    past, future = h.loc[:start], h.loc[start:]
    fig = go.Figure()

    def trace(part: pd.DataFrame, name: str, color: str, dash: str | None):
        if part.empty:
            return
        cats = [standard.category(v) for v in part[col]]
        text = [f"{aqi.index_text(standard, v, bool(o) if standard is aqi.IN_NAQI else False)} · {c.label}"
                if c else "no value" for v, c, o in zip(part[col], cats,
                                                        part.get("naqi_open", pd.Series(False, index=part.index)))]
        fig.add_scatter(x=part.index, y=part[col], name=name, mode="lines", connectgaps=False,
                        line={"color": color, "width": 2.6, "dash": dash}, text=text,
                        hovertemplate=f"{standard.short} %{{text}}<extra>{name}</extra>")
    trace(past, "Earlier hours (model values, not observed)", colors["observed"], "dot")
    trace(future, "Forecast (CAMS)", colors["provider"], None)
    vals = h[col].dropna()
    top = max(float(vals.max()) * 1.15 if len(vals) else standard.categories[0].upper or 50,
              (standard.categories[1].upper or 100))
    _bands(fig, standard, top)
    fig.add_vline(x=start, line_width=1.5, line_dash="dash", line_color=colors["muted"])
    fig.add_annotation(x=start, y=1, yref="paper", text="Now", showarrow=False, yanchor="top", xanchor="left",
                       xshift=4, font={"size": 11})
    fig = _layout(fig, colors, f"{standard.name}, hourly", standard.short, height=320)
    fig.update_yaxes(range=[0, top])
    fig.update_xaxes(tickformat="%H:%M\n%a %d")
    return fig


def pollutant_chart(data: AirQuality, keys: tuple[str, str], now: pd.Timestamp, colors: dict, title: str) -> go.Figure:
    """Hourly concentrations of two pollutants (μg/m³): earlier model hours dotted, forecast solid."""
    h = data.hourly
    start = now.tz_convert(h.index.tz).floor("h")
    fig = go.Figure()
    for key, color in zip(keys, (colors["provider"], colors["third"])):
        sym = aqi.POLLUTANTS[key].symbol
        for part, suffix, dash, show in ((h.loc[:start], "earlier, model", "dot", True),
                                        (h.loc[start:], "forecast", None, True)):
            if part.empty:
                continue
            fig.add_scatter(x=part.index, y=part[key], name=f"{sym} ({suffix})", mode="lines", connectgaps=False,
                            line={"color": color, "width": 2.2, "dash": dash}, showlegend=show,
                            hovertemplate=f"%{{y:,.1f}} μg/m³<extra>{sym} {suffix}</extra>")
    fig.add_vline(x=start, line_width=1.5, line_dash="dash", line_color=colors["muted"])
    fig = _layout(fig, colors, title, "μg/m³", height=280)
    fig.update_xaxes(tickformat="%H:%M\n%a %d")
    fig.update_yaxes(rangemode="tozero")
    return fig


def aq_table(data: AirQuality, standards: list[aqi.Standard]) -> pd.DataFrame:
    """Accessible table of everything charted: index values per standard and concentrations."""
    h = data.hourly
    out = pd.DataFrame({"Time": [t.strftime("%a %d %b %H:%M") for t in h.index]})
    for s in standards:
        open_col = h["naqi_open"] if s is aqi.IN_NAQI else pd.Series(False, index=h.index)
        out[s.short] = [aqi.index_text(s, v, bool(o)) for v, o in zip(h[s.column], open_col)]
    for key, p in aqi.POLLUTANTS.items():
        out[f"{p.symbol} μg/m³"] = h[key].round(1).to_numpy()
    return out


# --- Outdoor ------------------------------------------------------------------------------------------

def verdict_html(level: str, title: str, lines: list[str]) -> str:
    kind, ico = O.LEVEL_CHIPS[level]
    body = "".join(f"<p>{escape(x)}</p>" for x in lines)
    return (f'<section class="cz-verdict {kind} cz-fade" aria-label="Right now"><h3>{chip(O.LEVEL_LABELS[level], kind, ico)}'
            f"{escape(title)}</h3>{body}</section>")


def _t(c, units: U.Units) -> str:
    v = U.temperature(c, units.temperature)
    return DASH if v is None else f"{v:.0f}{units.temperature}"


def window_html(w: O.Window, heading: str, standard: aqi.Standard, units: U.Units) -> str:
    s = w.summary
    kind, ico = O.LEVEL_CHIPS[w.level]
    cat = standard.category(s["aqi_max"])
    heat = (f"{_t(s['heat_index_max'], units)} ({O.heat_label(O.heat_category(s['heat_index_max']))})"
            if s["heat_index_max"] is not None else DASH)
    wind_txt = DASH if s["wind_max"] is None else (
        f"{U.wind(s['wind_max'], units.wind):.0f} {units.wind}" +
        ("" if s["gust_max"] is None else f", gusts {U.wind(s['gust_max'], units.wind):.0f}"))
    rain = DASH if s["precip_prob_max"] is None else f"{s['precip_prob_max']:.0f}%" + (
        "" if not s["precip_sum"] else
        f", {U.precipitation(s['precip_sum'], units.precipitation):.{U.precipitation_digits(units.precipitation)}f} "
        f"{units.precipitation}")
    temp = DASH if s["temp_min"] is None else (
        _t(s["temp_max"], units) if round(s["temp_min"]) == round(s["temp_max"])
        else f"{_t(s['temp_min'], units)}–{_t(s['temp_max'], units)}")
    grid = (f'<dl class="grid">'
            f'<div><dt>{escape(standard.short)}</dt><dd>{escape(O.aqi_range(standard, s))}'
            f'{" · " + escape(cat.label) if cat else ""}</dd></div>'
            f'<div><dt>PM2.5</dt><dd>{DASH if s["pm2_5_mean"] is None else fmt(s["pm2_5_mean"], " μg/m³") + " mean"}'
            f'</dd></div>'
            f'<div><dt>Temperature</dt><dd>{escape(temp)}</dd></div>'
            f'<div><dt>Heat index</dt><dd>{escape(heat)}</dd></div>'
            f'<div><dt>UV index</dt><dd>{fmt(s["uv_max"])}{" (" + O.uv_label(s["uv_max"]) + ")" if s["uv_max"] is not None else ""}</dd></div>'
            f'<div><dt>Rain chance</dt><dd>{escape(rain)}</dd></div>'
            f'<div><dt>Wind</dt><dd>{escape(wind_txt)}</dd></div></dl>')
    items = [f'<li class="good">{icon("check_circle")}<span>{escape(x)}</span></li>' for x in w.supporting]
    items += [f'<li class="{"risk" if f.level == O.AVOID else "warn"}">'
              f'{icon("block" if f.level == O.AVOID else "error")}<span>{escape(f.text)}</span></li>' for f in w.limiting]
    items += [f'<li class="note">{icon("info")}<span>{escape(n)}</span></li>' for n in w.notes]
    return (f'<article class="cz-win cz-fade" aria-label="{escape(heading)}"><p class="when">{escape(heading)}</p>'
            f'<h4>{w.start:%a %H:%M}–{w.end:%H:%M}</h4>{chip(O.LEVEL_LABELS[w.level], kind, ico)}{grid}'
            f'<ul>{"".join(items)}</ul></article>')


def timeline_html(assessed: list[O.HourAssessment], standard: aqi.Standard, start_hour: int, end_hour: int,
                  now: pd.Timestamp, units: U.Units) -> str:
    """Days × hours grid of results. Each cell has a symbol and a tooltip, so colour isn't the only cue."""
    if not assessed:
        return ""
    tz = assessed[0].time.tz
    cur = now.tz_convert(tz).floor("h")
    by_day: dict = {}
    for h in assessed:
        by_day.setdefault(h.time.date(), {})[h.time.hour] = h
    head = "".join(f'<th scope="col">{hr:02d}</th>' for hr in range(24))
    rows = []
    for day, hours in by_day.items():
        cells = []
        for hr in range(24):
            h = hours.get(hr)
            if h is None:
                cells.append('<td class="none"></td>')
                continue
            v = h.values
            tip = (f"{h.time:%a %H:%M} · {O.LEVEL_LABELS[h.level]} · {standard.short} "
                   f"{aqi.index_text(standard, v['aqi'], v['aqi_open'])} · {_t(v['temperature'], units)}")
            limiting = [f.kind for f in h.limiting()]
            if limiting:
                tip += " · limited by " + ", ".join(dict.fromkeys(limiting))
            cls = h.level + ("" if start_hour <= hr < end_hour else " out") + (" now" if h.time == cur else "")
            cells.append(f'<td class="{cls}" title="{escape(tip)}" aria-label="{escape(tip)}">{SYMBOLS[h.level]}</td>')
        rows.append(f'<tr><th scope="row">{pd.Timestamp(day):%a %d}</th>{"".join(cells)}</tr>')
    legend = "".join(f'<span><i class="cz-tl-key" style="background:{bg};{extra}">{SYMBOLS[lvl]}</i>'
                     f'{escape(O.LEVEL_LABELS[lvl])}</span>'
                     for lvl, bg, extra in ((O.OK, "#34b37a", ""), (O.CAUTION, "#e9a636", ""),
                                            (O.AVOID, "#e2566a", ""),
                                            (O.UNAVAILABLE, "transparent", "border:1px dashed currentColor;color:inherit")))
    return (f'<div class="cz-tl" role="region" aria-label="Hour-by-hour outdoor outlook" tabindex="0">'
            f'<table><thead><tr><th scope="col">Local time</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'</div><div class="cz-legend">{legend}<span style="opacity:.7">Faded: outside your time range · '
            f'outlined: now</span></div>')


def assessment_table(assessed: list[O.HourAssessment], standard: aqi.Standard, units: U.Units) -> pd.DataFrame:
    """Every hour's result and reasons, for the table view."""
    rows = []
    for h in assessed:
        v = h.values
        rows.append({"Time": h.time.strftime("%a %d %b %H:%M"), "Result": O.LEVEL_LABELS[h.level],
                     standard.short: aqi.index_text(standard, v["aqi"], v["aqi_open"]),
                     "PM2.5 μg/m³": None if v["pm2_5"] is None else round(v["pm2_5"]),
                     f"Temp {units.temperature}": None if v["temperature"] is None
                     else round(U.temperature(v["temperature"], units.temperature)),
                     f"Heat index {units.temperature}": None if v["heat_index"] is None
                     else round(U.temperature(v["heat_index"], units.temperature)),
                     "UV": None if v["uv"] is None else round(v["uv"]),
                     "Rain %": v["precip_prob"],
                     f"Gusts {units.wind}": None if v["gust"] is None else round(U.wind(v["gust"], units.wind)),
                     "Reasons": " · ".join(f.text for f in h.limiting()) or "No limiting factors"})
    return pd.DataFrame(rows)


def strip_html(r: Reading) -> str:
    """One line for the Weather page: swatch, index with its standard, category and the hour it describes."""
    if r.category is None:
        return (f'<p class="cz-aq-strip">{icon("air")} Air quality: {escape(r.note or "not available for this hour")}'
                "</p>")
    return (f'<p class="cz-aq-strip">{icon("air")}<span class="sw" style="background:{r.category.color}" '
            f'aria-hidden="true"></span><span><b>{escape(r.standard.short)} {escape(r.text)}</b> · '
            f'{escape(r.category.label)} · model estimate for {r.time:%H:%M}</span></p>')
