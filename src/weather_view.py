"""HTML building blocks for the Cloudzy weather page.

Pure functions: they take provider data (metric) plus the viewer's units and
return HTML strings. The page passes them to `st.html`. Rules:
- Every value comes from the provider response. Missing values render as "—"
  and are labelled as unavailable, never as zero or a guess.
- Weather icons and labels come only from WMO weather codes (src.provider.WMO_CODES).
- All text is HTML-escaped. Each icon has an accessible text label.
- Cards inherit the app theme's text colour on a translucent background, so they
  work in light and dark mode. The hero sets its own colour pair (sky blue by
  day, midnight navy at night), and both pairs are well above 4.5:1 contrast.
"""

from __future__ import annotations

from html import escape

import pandas as pd

from src import units as U
from src.provider import describe_weather
from src.theme import icon
from src.ui import compass, weather_symbol

DASH = "—"

CSS = """
<style>
/* Weather components. Tokens (--cz-*) come from src.theme.GLOBAL_CSS. */
.cz-hero{border-radius:var(--cz-r-xl);padding:var(--cz-s-5) var(--cz-s-6);margin:var(--cz-s-1) 0 var(--cz-s-2);
  box-shadow:var(--cz-sh-2);display:flex;flex-direction:column;gap:var(--cz-s-3);position:relative;overflow:hidden}
.cz-hero.day{background:linear-gradient(135deg,#d9edff 0%,#a9d0f7 55%,#c6c0fa 100%);color:var(--cz-c-navy)}
.cz-hero.night{background:linear-gradient(135deg,#1d2f5c 0%,#0b1630 55%,#2b2466 100%);color:#f2f6ff}
.cz-hero.wet{background:linear-gradient(135deg,#d3dde9 0%,#9fb3cb 60%,#a9a6d6 100%);color:var(--cz-c-navy)}
.cz-hero.wet.night{background:linear-gradient(135deg,#2b3a55 0%,#121c33 60%,#272352 100%);color:#f2f6ff}
.cz-hero.grey{background:linear-gradient(135deg,#e6ebf2 0%,#c0cad7 100%);color:var(--cz-c-navy)}
.cz-hero.grey.night{background:linear-gradient(135deg,#323d52 0%,#161f33 100%);color:#f2f6ff}
.cz-hero.snow{background:linear-gradient(135deg,#f6f9fd 0%,#d4e0ef 100%);color:var(--cz-c-navy)}
.cz-hero.snow.night{background:linear-gradient(135deg,#3a4a6b 0%,#1b2747 100%);color:#f2f6ff}
.cz-hero .top{display:flex;flex-wrap:wrap;justify-content:space-between;gap:var(--cz-s-1) var(--cz-s-4)}
.cz-hero .place{font-size:var(--cz-t-lg);font-weight:700;margin:0;display:flex;gap:6px;align-items:baseline}
.cz-hero .sub{font-size:var(--cz-t-sm);opacity:.88;margin:2px 0 0}
.cz-hero .now{display:flex;flex-wrap:wrap;align-items:center;gap:var(--cz-s-2) var(--cz-s-5)}
.cz-hero .icon .cz-ico{font-size:clamp(3.4rem,10vw,5rem)}
.cz-hero .temp{font-size:clamp(3.6rem,12vw,5.6rem);font-weight:800;line-height:1;letter-spacing:-.04em}
.cz-hero .cond{font-size:var(--cz-t-xl);font-weight:700;margin:0}
.cz-hero .hl{font-size:var(--cz-t-md);margin:4px 0 0;opacity:.92}
.cz-hero .sun{color:var(--cz-c-sun)}
.cz-hero.day .sun,.cz-hero.snow .sun,.cz-hero.grey .sun,.cz-hero.wet .sun{color:var(--cz-c-sun-ink)}
.cz-hero .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:var(--cz-s-2);margin:0;padding:0}
.cz-hero .stat{list-style:none;border-radius:var(--cz-r-md);padding:var(--cz-s-2) var(--cz-s-3);
  background:rgba(255,255,255,.32);border:1px solid rgba(255,255,255,.45)}
.cz-hero.night .stat{background:rgba(255,255,255,.07);border-color:rgba(255,255,255,.14)}
.cz-hero .stat .k{font-size:var(--cz-t-xs);opacity:.85;margin:0;display:flex;gap:4px;align-items:center}
.cz-hero .stat .v{font-size:var(--cz-t-lg);font-weight:700;margin:2px 0 0}
.cz-wx.sunny{color:var(--cz-c-sun)}
.cz-hero.day .cz-wx.sunny{color:#e0a100}
.cz-say{border-radius:var(--cz-r-lg);padding:var(--cz-s-4) var(--cz-s-5);background:linear-gradient(135deg,
  rgba(42,120,214,.10),rgba(123,108,246,.08));border:1px solid rgba(90,162,240,.32)}
.cz-say h3{margin:0 0 var(--cz-s-2);font-size:var(--cz-t-md);display:flex;gap:6px;align-items:center}
.cz-say h3 .cz-ico{color:var(--cz-c-sky)}
.cz-say ul{margin:0;padding-left:1.1rem}
.cz-say li{margin:4px 0;line-height:1.45}
.cz-say .src{font-size:var(--cz-t-xs);opacity:.75;margin:var(--cz-s-2) 0 0}
.cz-alert{border-radius:var(--cz-r-md);padding:var(--cz-s-3) var(--cz-s-4);margin:0 0 var(--cz-s-2);
  border:1px solid rgba(217,138,0,.55);border-left:5px solid var(--cz-c-sun);background:rgba(245,184,0,.10)}
.cz-alert h4{margin:0 0 4px;font-size:var(--cz-t-md);display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.cz-alert h4 .cz-ico{color:var(--cz-c-warn)}
.cz-alert p{margin:2px 0;font-size:var(--cz-t-sm)}
.cz-alert .lbl{font-size:var(--cz-t-xs);opacity:.85}
.cz-rec{border-radius:var(--cz-r-md);padding:var(--cz-s-3) var(--cz-s-4);background:var(--cz-n-surface);
  border:1px solid var(--cz-n-border);height:100%}
.cz-rec p{margin:0;display:flex;gap:8px;align-items:flex-start}
.cz-rec p .cz-ico{color:var(--cz-c-sky);font-size:1.25rem}
.cz-rec .why{font-size:var(--cz-t-sm);opacity:.85;margin-top:4px;display:block}
.cz-strip{display:flex;gap:var(--cz-s-2);overflow-x:auto;padding:4px 2px 10px;scroll-snap-type:x proximity}
.cz-strip:focus-visible{outline:3px solid var(--cz-c-sky-2);outline-offset:2px;border-radius:var(--cz-r-md)}
.cz-hour{flex:0 0 78px;scroll-snap-align:start;text-align:center;border-radius:var(--cz-r-lg);padding:10px 4px;
  background:var(--cz-n-surface);border:1px solid var(--cz-n-border)}
.cz-hour.first{border:2px solid var(--cz-c-sky);background:rgba(42,120,214,.10)}
.cz-hour .t{font-size:var(--cz-t-sm);font-weight:600;margin:0}
.cz-hour .i{margin:6px 0}
.cz-hour .i .cz-ico{font-size:1.7rem;color:var(--cz-c-sky)}
.cz-hour .v{font-size:var(--cz-t-lg);font-weight:700;margin:0}
.cz-hour .r,.cz-hour .w{font-size:var(--cz-t-xs);margin:3px 0 0;opacity:.9;display:flex;gap:2px;justify-content:center;align-items:center}
.cz-hour .r .cz-ico,.cz-hour .w .cz-ico{font-size:.95rem;opacity:.8}
.cz-days{border-radius:var(--cz-r-lg);padding:var(--cz-s-1) var(--cz-s-4);background:var(--cz-n-surface);
  border:1px solid var(--cz-n-border)}
.cz-day{display:grid;grid-template-columns:minmax(3.6rem,5rem) 2rem 3.6rem 2.8rem 1fr 2.8rem;align-items:center;
  gap:var(--cz-s-2);padding:10px 0;border-bottom:1px solid var(--cz-n-border)}
.cz-day:last-child{border-bottom:none}
.cz-day .d{font-weight:600}
.cz-day .i{text-align:center}
.cz-day .i .cz-ico{font-size:1.5rem;color:var(--cz-c-sky)}
.cz-day .r{font-size:var(--cz-t-sm);display:flex;align-items:center;gap:2px}
.cz-day .r .cz-ico{font-size:.95rem;color:var(--cz-c-sky)}
.cz-day .lo{text-align:right;opacity:.8}
.cz-day .hi{font-weight:700}
.cz-bar{position:relative;height:8px;border-radius:4px;background:var(--cz-n-surface-2)}
.cz-bar span{position:absolute;top:0;bottom:0;border-radius:4px;
  background:linear-gradient(90deg,var(--cz-c-sky-2),var(--cz-c-violet) 60%,var(--cz-c-sun))}
.cz-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:var(--cz-s-3)}
.cz-card{border-radius:var(--cz-r-lg);padding:var(--cz-s-3) var(--cz-s-4);background:var(--cz-n-surface);
  border:1px solid var(--cz-n-border);transition:border-color var(--cz-m-fast) var(--cz-m-ease)}
.cz-card:hover{border-color:var(--cz-n-border-strong)}
.cz-card .k{font-size:var(--cz-t-xs);text-transform:uppercase;letter-spacing:.06em;margin:0;opacity:.8;
  display:flex;gap:5px;align-items:center}
.cz-card .k .cz-ico{font-size:1rem;color:var(--cz-c-sky)}
.cz-card .v{font-size:1.55rem;font-weight:700;margin:6px 0 2px}
.cz-card .n{font-size:var(--cz-t-sm);margin:0;opacity:.9}
@media (max-width:640px){
  .cz-day{grid-template-columns:3.2rem 1.8rem 3rem 2.4rem 1fr 2.4rem;gap:6px}
  .cz-hero{padding:var(--cz-s-4)}
  .cz-hero .stats{grid-template-columns:repeat(2,minmax(0,1fr))}
}
</style>
"""


def _missing(value) -> bool:
    return value is None or pd.isna(value)


def _num(value, digits: int = 0) -> str:
    if _missing(value):
        return DASH
    out = f"{value:.{digits}f}"
    return f"{0:.{digits}f}" if float(out) == 0 else out  # never show "-0"


def _with_unit(value, unit: str, digits: int = 0, sep: str = " ") -> str:
    """'12 km/h', or a bare dash when missing (never '— km/h' or '—%')."""
    return DASH if _missing(value) else escape(f"{_num(value, digits)}{sep}{unit}")


def _icon(code, is_day=1) -> str:
    """The weather symbol for a WMO code, announced by its description (e.g. "Clear sky")."""
    label = describe_weather(None if code is None or pd.isna(code) else code)
    name = weather_symbol(code, is_day)
    sunny = " sunny" if name in ("clear_day", "partly_cloudy_day") else ""
    return (f'<span class="cz-ico cz-wx{sunny}" role="img" aria-label="{escape(label)}">{escape(name)}</span>')


def temp_text(celsius, units: U.Units, digits: int = 0) -> str:
    v = U.temperature(celsius, units.temperature)
    return DASH if v is None else f"{_num(v, digits)}°"


def uv_category(uv) -> str:
    """WHO UV index categories."""
    if _missing(uv):
        return "Not available here"
    if uv < 3:
        return "Low"
    if uv < 6:
        return "Moderate"
    if uv < 8:
        return "High"
    if uv < 11:
        return "Very high"
    return "Extreme"


def updated_label(retrieved_at_utc, tz) -> str:
    """'updated 13:08 local time', or a neutral phrase when the time is unknown."""
    try:
        t = pd.Timestamp(retrieved_at_utc)
        if pd.isna(t):
            raise ValueError
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        return f"updated {t.tz_convert(tz):%H:%M} local time"
    except (ValueError, TypeError):
        return "update time unknown"


def hero_mood(code) -> str:
    """Extra hero style from the *actual* weather code: wet, grey (fog/overcast) or snow."""
    if code is None or pd.isna(code):
        return ""
    c = int(code)
    if c in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 97, 99}:
        return " wet"
    if c in {71, 73, 75, 77, 85, 86}:
        return " snow"
    if c in {3, 45, 48}:
        return " grey"
    return ""


MASCOT_SVG = """<svg width="54" height="40" viewBox="0 0 54 40" aria-hidden="true" focusable="false">
<circle cx="42" cy="11" r="8" fill="#f5b800"/>
<path d="M14 36h26a9 9 0 0 0 0-18 12 12 0 0 0-23-2A10 10 0 0 0 14 36z" fill="#9fcdf6" stroke="#0f1d3a" stroke-width="2"/>
<circle cx="22" cy="26" r="1.8" fill="#0f1d3a"/><circle cx="31" cy="26" r="1.8" fill="#0f1d3a"/>
<path d="M23 30.5q3.5 3 7 0" fill="none" stroke="#0f1d3a" stroke-width="1.8" stroke-linecap="round"/>
<circle cx="19" cy="30" r="1.6" fill="#f5b800" opacity=".8"/><circle cx="34" cy="30" r="1.6" fill="#f5b800" opacity=".8"/>
</svg>"""


def mascot_img(width: int = 54) -> str:
    """The mascot as a data-URI image: st.html's sanitiser strips inline <svg>, but keeps <img>."""
    import base64
    svg = MASCOT_SVG.replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    data = base64.b64encode(svg.encode()).decode()
    return f'<img src="data:image/svg+xml;base64,{data}" width="{width}" alt="" aria-hidden="true">'


def brand_html(tagline: str = "Weather with a little intelligence.") -> str:
    return (f'<div class="cz-brand">{mascot_img()}<div><h2>Cloudzy</h2><p>{escape(tagline)}</p></div></div>')


def summary_html(sentences: list[str], title: str = "Cloudzy says",
                 source: str = "Written by rules from the provider forecast above (not by Cloudzy Core).") -> str:
    items = "".join(f"<li>{escape(s)}</li>" for s in sentences)
    src = f'<p class="src">{escape(source)}</p>' if source else ""
    return (f'<section class="cz-say cz-fade" aria-label="{escape(title)}"><h3>{icon("auto_awesome")}'
            f"{escape(title)}</h3><ul>{items}</ul>{src}</section>")


def alerts_html(alerts) -> str:
    if not alerts:
        return ""
    cards = []
    for a in alerts:
        cards.append(
            f'<div class="cz-alert cz-fade" role="note" aria-label="{escape(a.title)}">'
            f"<h4>{icon('warning')}{escape(a.title)} · {escape(a.value)}</h4>"
            f"<p><b>When:</b> {escape(a.period)}</p><p><b>Why:</b> {escape(a.reason)}</p>"
            f"<p><b>Uncertainty:</b> {escape(a.uncertainty)}</p><p class=\"lbl\">{escape(a.label)}</p></div>")
    return "".join(cards)


def recommendations_html(recs) -> str:
    icons = {"umbrella": "umbrella", "clothing": "checkroom", "outdoors": "park", "uv": "wb_sunny"}
    cards = [f'<div class="cz-rec cz-fade" role="group" aria-label="{escape(r.kind)} suggestion">'
             f'<p>{icon(icons.get(r.kind, "lightbulb"))}<span>{escape(r.text)}'
             f'<span class="why">Why: {escape(r.why)}</span></span></p></div>' for r in recs]
    return f'<div class="cz-grid" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))">{"".join(cards)}</div>'


# --- Hero -----------------------------------------------------------------------

def _stat(label: str, icon_name: str, value: str) -> str:
    return (f'<li class="stat"><p class="k">{icon(icon_name)}{escape(label)}</p>'
            f'<p class="v">{value}</p></li>')


def hero_html(place_label: str, current: dict, today: pd.Series | None, units: U.Units, tz_label: str) -> str:
    """Current conditions: place and local time, temperature, condition, feels-like, today's range and
    four supporting measurements. Every value comes from the provider; missing ones show a dash."""
    is_day = current.get("is_day")
    mode = "night" if is_day is not None and not pd.isna(is_day) and int(is_day) == 0 else "day"
    mode += hero_mood(current.get("weather_code"))
    t = pd.Timestamp(current["time"])
    hi = temp_text(today["temperature_2m_max"], units) if today is not None else DASH
    lo = temp_text(today["temperature_2m_min"], units) if today is not None else DASH
    sun = ""
    if today is not None and not pd.isna(today.get("sunrise")) and not pd.isna(today.get("sunset")):
        sun = (f'<p class="sub"><span class="sun">{icon("wb_twilight")}</span> Sunrise {today["sunrise"]:%H:%M}'
               f' · Sunset {today["sunset"]:%H:%M}</p>')
    feels = temp_text(current.get("apparent_temperature"), units)
    wind = U.wind(current.get("wind_speed_10m"), units.wind)
    chance = today.get("precipitation_probability_max") if today is not None else None
    stats = "".join([
        _stat("Humidity", "humidity_percentage", _with_unit(current.get("relative_humidity_2m"), "%", sep="")),
        _stat("Wind", "air", _with_unit(wind, units.wind) + (f" {escape(compass(current.get('wind_direction_10m')))}"
                                                               if wind is not None else "")),
        _stat("Rain chance today", "water_drop", _with_unit(chance, "%", sep="")),
        _stat("UV index", "wb_sunny", escape(_num(current.get("uv_index"), 0))),
    ])
    return f"""
<section class="cz-hero {mode} cz-fade" aria-label="Current weather in {escape(place_label)}">
  <div class="top">
    <div>
      <p class="place">{icon("location_on")}<span>{escape(place_label)}</span></p>
      <p class="sub">{t:%A %H:%M} local time ({escape(tz_label)})</p>
    </div>
    {sun}
  </div>
  <div class="now">
    <span class="icon">{_icon(current.get("weather_code"), is_day)}</span>
    <div class="temp">{temp_text(current.get("temperature_2m"), units)}</div>
    <div>
      <p class="cond">{escape(current.get("description") or "Unknown")}</p>
      <p class="hl">Feels like {feels} · H {hi} · L {lo}</p>
    </div>
  </div>
  <ul class="stats" aria-label="Current measurements">{stats}</ul>
</section>"""


# --- Hourly strip ----------------------------------------------------------------

def hourly_strip_html(hours: pd.DataFrame, units: U.Units) -> str:
    """One card per hour: time, icon, temperature, chance of rain. Keyboard-scrollable."""
    cards = []
    for i, (t, row) in enumerate(hours.iterrows()):
        label = "Now" if i == 0 else f"{t:%H:%M}"
        prob = row.get("precipitation_probability")
        rain = DASH if prob is None or pd.isna(prob) else f"{prob:.0f}%"
        amount = U.precipitation(row.get("precipitation"), units.precipitation)
        amount_txt = (f" · {amount:.{U.precipitation_digits(units.precipitation)}f} {units.precipitation}"
                      if amount is not None and amount > 0 else "")
        wind = U.wind(row.get("wind_speed_10m"), units.wind)
        wind_txt = DASH if wind is None else f"{wind:.0f} {units.wind}"
        cards.append(
            f'<li class="cz-hour{" first" if i == 0 else ""}" aria-label="{escape(label)}: '
            f'{escape(temp_text(row["temperature_2m"], units))}, {escape(describe_weather(None if pd.isna(row["weather_code"]) else row["weather_code"]))}, '
            f'chance of rain {escape(rain)}{escape(amount_txt)}, wind {escape(wind_txt)} from {escape(compass(row.get("wind_direction_10m")))}">'
            f'<p class="t">{escape(label)}</p><p class="i">{_icon(row["weather_code"], row.get("is_day"))}</p>'
            f'<p class="v">{temp_text(row["temperature_2m"], units)}</p>'
            f'<p class="r">{icon("water_drop")}{escape(rain)}{escape(amount_txt)}</p>'
            f'<p class="w">{icon("air")}{escape(wind_txt)}</p></li>')
    return (f'<ul class="cz-strip" role="list" tabindex="0" aria-label="Hourly forecast for the next '
            f'{len(hours)} hours. Scroll sideways for more." style="list-style:none;margin:0">'
            + "".join(cards) + "</ul>")


# --- 7-day list --------------------------------------------------------------------

def daily_list_html(daily: pd.DataFrame, units: U.Units, today) -> str:
    """Rows with day, icon, rain chance, low, a range bar on the week's scale, and high."""
    lows = [U.temperature(v, units.temperature) for v in daily["temperature_2m_min"]]
    highs = [U.temperature(v, units.temperature) for v in daily["temperature_2m_max"]]
    known = [v for v in lows + highs if v is not None]
    wk_lo, wk_hi = (min(known), max(known)) if known else (0.0, 1.0)
    span = max(wk_hi - wk_lo, 1e-9)
    rows = []
    for (day, row), lo, hi in zip(daily.iterrows(), lows, highs):
        name = "Today" if day == today else pd.Timestamp(day).strftime("%a")
        date = pd.Timestamp(day).strftime("%d %b")
        prob = row.get("precipitation_probability_max")
        rain = "" if prob is None or pd.isna(prob) or prob < 10 else f'{icon("water_drop")}{prob:.0f}%'
        if lo is None or hi is None:
            bar = '<div class="cz-bar" aria-hidden="true"></div>'
        else:
            left, width = (lo - wk_lo) / span * 100, max((hi - lo) / span * 100, 2)
            bar = f'<div class="cz-bar" aria-hidden="true"><span style="left:{left:.1f}%;width:{width:.1f}%"></span></div>'
        desc = describe_weather(None if pd.isna(row["weather_code"]) else row["weather_code"])
        rows.append(
            f'<div class="cz-day" role="listitem" aria-label="{escape(name)} {escape(date)}: {escape(desc)}, '
            f'low {escape(temp_text(row["temperature_2m_min"], units))}, high {escape(temp_text(row["temperature_2m_max"], units))}'
            f'{", chance of rain " + f"{prob:.0f}%" if rain else ""}">'
            f'<span class="d" title="{escape(date)}">{escape(name)}</span><span class="i">{_icon(row["weather_code"])}</span>'
            f'<span class="r">{rain}</span><span class="lo">{temp_text(row["temperature_2m_min"], units)}</span>'
            f'{bar}<span class="hi">{temp_text(row["temperature_2m_max"], units)}</span></div>')
    return f'<div class="cz-days" role="list" aria-label="7-day forecast">{"".join(rows)}</div>'


# --- Detail cards ------------------------------------------------------------------

CARD_ICONS = {"UV index": "wb_sunny", "Humidity": "humidity_percentage", "Wind": "air", "Feels like": "thermostat",
              "Precipitation": "water_drop", "Cloud cover": "cloud", "Pressure": "compress", "Visibility": "visibility",
              "Sunrise · Sunset": "wb_twilight"}


def _card(title: str, value: str, note: str = "") -> str:
    ico = icon(CARD_ICONS[title]) if title in CARD_ICONS else ""
    return (f'<div class="cz-card" role="group" aria-label="{escape(title)}"><p class="k">{ico}{escape(title)}</p>'
            f'<p class="v">{value}</p><p class="n">{note}</p></div>')


def detail_cards_html(current: dict, today: pd.Series | None, units: U.Units) -> str:
    c = current
    cards = []

    uv = c.get("uv_index")
    cards.append(_card("UV index", escape(_num(uv, 0)), escape(uv_category(uv))))

    rh, dew = c.get("relative_humidity_2m"), c.get("dew_point_2m")
    cards.append(_card("Humidity", _with_unit(rh, "%", sep=""),
                       f"Dew point {escape(temp_text(dew, units))}"))

    w, g = U.wind(c.get("wind_speed_10m"), units.wind), U.wind(c.get("wind_gusts_10m"), units.wind)
    cards.append(_card("Wind", _with_unit(w, units.wind),
                       f"From {escape(compass(c.get('wind_direction_10m')))} · gusts {_with_unit(g, units.wind)}"))

    t, f = c.get("temperature_2m"), c.get("apparent_temperature")
    if _missing(t) or _missing(f):
        feel_note = "Not available"
    elif f - t > 1.5:
        feel_note = "Feels warmer than the actual temperature"
    elif t - f > 1.5:
        feel_note = "Feels cooler than the actual temperature"
    else:
        feel_note = "Close to the actual temperature"
    cards.append(_card("Feels like", escape(temp_text(f, units)), escape(feel_note)))

    pdig = U.precipitation_digits(units.precipitation)
    p_now = U.precipitation(c.get("precipitation"), units.precipitation)
    p_day = U.precipitation(today.get("precipitation_sum"), units.precipitation) if today is not None else None
    chance = today.get("precipitation_probability_max") if today is not None else None
    cards.append(_card("Precipitation", _with_unit(p_day, units.precipitation, pdig),
                       f"Expected today · chance {_with_unit(chance, '%', sep='')} · "
                       f"now {_with_unit(p_now, units.precipitation, pdig)}"))

    cards.append(_card("Cloud cover", _with_unit(c.get("cloud_cover"), "%", sep="")))

    pr = c.get("pressure_msl")
    cards.append(_card("Pressure", _with_unit(pr, "hPa"), "At sea level"))

    vis = U.distance(c.get("visibility"), units.distance)
    cards.append(_card("Visibility", _with_unit(vis, units.distance, 1)))

    if today is not None and not pd.isna(today.get("sunrise")) and not pd.isna(today.get("sunset")):
        dl = today.get("daylight_duration")
        daylight = DASH if _missing(dl) else f"{int(dl // 3600)} h {int(dl % 3600 // 60)} min of daylight"
        cards.append(_card("Sunrise · Sunset", f'{today["sunrise"]:%H:%M} · {today["sunset"]:%H:%M}', escape(daylight)))
    else:
        cards.append(_card("Sunrise · Sunset", DASH, "Not available"))
    return f'<div class="cz-grid">{"".join(cards)}</div>'


def briefing_html(b, units: U.Units) -> str:
    """Compact daily briefing card from insights.briefing()."""
    if b is None:
        return ""
    changes = "".join(f"<li>{escape(c)}</li>" for c in b.changes) or "<li>No significant changes expected.</li>"
    n_alerts = len(b.alerts)
    alert_line = (f"{n_alerts} Cloudzy alert{'s' if n_alerts != 1 else ''} (see Alerts)" if n_alerts
                  else "No Cloudzy alerts with your thresholds")
    return (f'<section class="cz-say cz-fade" aria-label="Daily briefing for {escape(b.date)}">'
            f"<h3>{icon('event_note')}Daily briefing · {escape(b.date)}</h3>"
            f"<ul><li><b>Temperature:</b> {escape(b.temperature_range)}</li>"
            f"<li><b>Conditions:</b> {escape(b.conditions)}</li>"
            f"<li><b>Precipitation:</b> {escape(b.precipitation)}</li>"
            f"<li><b>Alerts:</b> {escape(alert_line)}</li></ul>"
            f'<p style="margin:8px 0 2px"><b>Changes through the day</b></p><ul>{changes}</ul></section>')


def daily_table(daily: pd.DataFrame, units: U.Units) -> pd.DataFrame:
    """Every daily variable in display units, as an accessible table."""
    pdig = U.precipitation_digits(units.precipitation)

    def t(v):
        x = U.temperature(v, units.temperature)
        return None if x is None else round(x, 1)

    def daylight(sec):
        return DASH if _missing(sec) else f"{int(sec // 3600)} h {int(sec % 3600 // 60)} min"

    return pd.DataFrame({
        "Day": [pd.Timestamp(d).strftime("%a %d %b") for d in daily.index],
        "Condition (most significant)": [describe_weather(None if _missing(c) else c) for c in daily["weather_code"]],
        f"Low {units.temperature}": [t(v) for v in daily["temperature_2m_min"]],
        f"High {units.temperature}": [t(v) for v in daily["temperature_2m_max"]],
        "Rain chance %": daily["precipitation_probability_max"].to_numpy(),
        f"Rain {units.precipitation}": [None if (x := U.precipitation(v, units.precipitation)) is None else round(x, pdig)
                                        for v in daily["precipitation_sum"]],
        f"Max wind {units.wind}": [None if (x := U.wind(v, units.wind)) is None else round(x)
                                   for v in daily["wind_speed_10m_max"]],
        "Wind from": [compass(v) for v in daily.get("wind_direction_10m_dominant", pd.Series([None] * len(daily)))],
        "UV max": daily["uv_index_max"].round(1).to_numpy(),
        "Sunrise": [DASH if _missing(v) else f"{v:%H:%M}" for v in daily["sunrise"]],
        "Sunset": [DASH if _missing(v) else f"{v:%H:%M}" for v in daily["sunset"]],
        "Daylight": [daylight(v) for v in daily.get("daylight_duration", pd.Series([None] * len(daily)))],
    })
