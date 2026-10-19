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
from src.ui import compass, weather_icon

DASH = "—"

CSS = """
<style>
.cz-hero{border-radius:28px;padding:22px 26px;margin:4px 0 6px;display:flex;flex-wrap:wrap;
  align-items:center;justify-content:space-between;gap:12px 28px}
.cz-hero.day{background:linear-gradient(160deg,#d9edff 0%,#9fcdf6 100%);color:#0f1d3a}
.cz-hero.night{background:linear-gradient(160deg,#22356a 0%,#0b1630 100%);color:#f2f6ff}
.cz-hero.wet{background:linear-gradient(160deg,#c9d6e6 0%,#8fa6c2 100%);color:#0f1d3a}
.cz-hero.wet.night{background:linear-gradient(160deg,#2b3a55 0%,#121c33 100%);color:#f2f6ff}
.cz-hero.grey{background:linear-gradient(160deg,#e3e8ef 0%,#b9c4d2 100%);color:#0f1d3a}
.cz-hero.grey.night{background:linear-gradient(160deg,#323d52 0%,#161f33 100%);color:#f2f6ff}
.cz-hero.snow{background:linear-gradient(160deg,#f4f8fd 0%,#cfdcec 100%);color:#0f1d3a}
.cz-hero.snow.night{background:linear-gradient(160deg,#3a4a6b 0%,#1b2747 100%);color:#f2f6ff}
.cz-fade{animation:czfade .45s ease-out both}
@keyframes czfade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.cz-fade{animation:none}}
.cz-brand{display:flex;align-items:center;gap:12px;margin:0}
.cz-brand svg{flex:0 0 auto}
.cz-brand h2{margin:0;font-size:2rem;line-height:1.1}
.cz-brand p{margin:2px 0 0;opacity:.85;font-size:.95rem}
.cz-say{border-radius:20px;padding:14px 18px;background:rgba(57,135,229,.10);border:1px solid rgba(57,135,229,.35)}
.cz-say h3{margin:0 0 6px;font-size:1rem}
.cz-say ul{margin:0;padding-left:1.1rem}
.cz-say li{margin:3px 0}
.cz-alert{border-radius:16px;padding:12px 16px;margin:0 0 8px;border:1px solid rgba(245,184,0,.6);
  background:rgba(245,184,0,.12)}
.cz-alert h4{margin:0 0 4px;font-size:1rem}
.cz-alert p{margin:2px 0;font-size:.92rem}
.cz-alert .lbl{font-size:.78rem;opacity:.85}
.cz-rec{border-radius:16px;padding:10px 14px;background:rgba(128,140,160,.10);border:1px solid rgba(128,140,160,.30)}
.cz-rec p{margin:0}
.cz-rec .why{font-size:.86rem;opacity:.9;margin-top:3px}
.cz-hero .place{font-size:1.05rem;font-weight:600;margin:0}
.cz-hero .sub{font-size:.9rem;opacity:.9;margin:2px 0 0}
.cz-hero .now{display:flex;align-items:center;gap:14px}
.cz-hero .icon{font-size:clamp(3rem,10vw,4.4rem);line-height:1}
.cz-hero .temp{font-size:clamp(3.4rem,13vw,5.4rem);font-weight:700;line-height:1;letter-spacing:-.02em}
.cz-hero .cond{font-size:1.25rem;font-weight:600;margin:0}
.cz-hero .hl{font-size:1rem;margin:4px 0 0}
.cz-hero .sun{color:#f5b800}
.cz-hero.day .sun{color:#8a5a00}
.cz-strip{display:flex;gap:8px;overflow-x:auto;padding:4px 2px 10px;scroll-snap-type:x proximity}
.cz-strip:focus-visible{outline:3px solid #3987e5;outline-offset:2px;border-radius:12px}
.cz-hour{flex:0 0 76px;scroll-snap-align:start;text-align:center;border-radius:18px;padding:10px 4px;
  background:rgba(128,140,160,.10);border:1px solid rgba(128,140,160,.30)}
.cz-hour.first{border:2px solid #3987e5}
.cz-hour .t{font-size:.85rem;font-weight:600;margin:0}
.cz-hour .i{font-size:1.6rem;margin:4px 0}
.cz-hour .v{font-size:1.15rem;font-weight:700;margin:0}
.cz-hour .r{font-size:.8rem;margin:2px 0 0}
.cz-hour .w{font-size:.75rem;margin:2px 0 0;opacity:.9}
.cz-days{border-radius:20px;padding:6px 14px;background:rgba(128,140,160,.10);border:1px solid rgba(128,140,160,.30)}
.cz-day{display:grid;grid-template-columns:minmax(3.6rem,5rem) 2rem 3.4rem 2.8rem 1fr 2.8rem;align-items:center;
  gap:8px;padding:9px 0;border-bottom:1px solid rgba(128,140,160,.22)}
.cz-day:last-child{border-bottom:none}
.cz-day .d{font-weight:600}
.cz-day .i{font-size:1.4rem;text-align:center}
.cz-day .r{font-size:.85rem}
.cz-day .lo{text-align:right;opacity:.85}
.cz-day .hi{font-weight:700}
.cz-bar{position:relative;height:8px;border-radius:4px;background:rgba(128,140,160,.28)}
.cz-bar span{position:absolute;top:0;bottom:0;border-radius:4px;background:linear-gradient(90deg,#3987e5,#f5b800)}
.cz-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:10px}
.cz-card{border-radius:18px;padding:12px 14px;background:rgba(128,140,160,.10);border:1px solid rgba(128,140,160,.30)}
.cz-card .k{font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;margin:0;opacity:.85}
.cz-card .v{font-size:1.55rem;font-weight:700;margin:4px 0 2px}
.cz-card .n{font-size:.88rem;margin:0}
@media (max-width:520px){.cz-day{grid-template-columns:3.2rem 1.8rem 2.8rem 2.4rem 1fr 2.4rem;gap:6px}
  .cz-hero{padding:18px}}
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
    label = describe_weather(None if code is None or pd.isna(code) else code)
    return f'<span role="img" aria-label="{escape(label)}">{weather_icon(code, is_day)}</span>'


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


def summary_html(sentences: list[str], title: str = "Cloudzy says") -> str:
    items = "".join(f"<li>{escape(s)}</li>" for s in sentences)
    return (f'<section class="cz-say cz-fade" aria-label="{escape(title)}"><h3>☁️ {escape(title)}</h3>'
            f"<ul>{items}</ul></section>")


def alerts_html(alerts) -> str:
    if not alerts:
        return ""
    cards = []
    for a in alerts:
        cards.append(
            f'<div class="cz-alert cz-fade" role="note" aria-label="{escape(a.title)}">'
            f"<h4>⚠️ {escape(a.title)} · {escape(a.value)}</h4>"
            f"<p><b>When:</b> {escape(a.period)}</p><p><b>Why:</b> {escape(a.reason)}</p>"
            f"<p><b>Uncertainty:</b> {escape(a.uncertainty)}</p><p class=\"lbl\">{escape(a.label)}</p></div>")
    return "".join(cards)


def recommendations_html(recs) -> str:
    icons = {"umbrella": "☂️", "clothing": "🧥", "outdoors": "🌳", "uv": "🧴"}
    cards = [f'<div class="cz-rec cz-fade" role="group" aria-label="{escape(r.kind)} suggestion">'
             f'<p><span aria-hidden="true">{icons.get(r.kind, "💡")}</span> {escape(r.text)}</p>'
             f'<p class="why">Why: {escape(r.why)}</p></div>' for r in recs]
    return f'<div class="cz-grid" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))">{"".join(cards)}</div>'


# --- Hero -----------------------------------------------------------------------

def hero_html(place_label: str, current: dict, today: pd.Series | None, units: U.Units, tz_label: str) -> str:
    is_day = current.get("is_day")
    mode = "night" if is_day is not None and not pd.isna(is_day) and int(is_day) == 0 else "day"
    mode += hero_mood(current.get("weather_code"))
    t = pd.Timestamp(current["time"])
    hi = temp_text(today["temperature_2m_max"], units) if today is not None else DASH
    lo = temp_text(today["temperature_2m_min"], units) if today is not None else DASH
    sun = ""
    if today is not None and not pd.isna(today.get("sunrise")) and not pd.isna(today.get("sunset")):
        sun = (f'<p class="sub"><span class="sun" aria-hidden="true">☀</span> Sunrise {today["sunrise"]:%H:%M}'
               f' · Sunset {today["sunset"]:%H:%M}</p>')
    feels = temp_text(current.get("apparent_temperature"), units)
    return f"""
<section class="cz-hero {mode}" aria-label="Current weather in {escape(place_label)}">
  <div>
    <p class="place">📍 {escape(place_label)}</p>
    <p class="sub">{t:%A %H:%M} local time ({escape(tz_label)})</p>
    {sun}
  </div>
  <div class="now">
    <span class="icon">{_icon(current.get("weather_code"), is_day)}</span>
    <div>
      <div class="temp">{temp_text(current.get("temperature_2m"), units)}</div>
      <p class="cond">{escape(current.get("description") or "Unknown")}</p>
      <p class="hl">Feels like {feels} · H {hi} · L {lo}</p>
    </div>
  </div>
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
            f'<p class="r"><span aria-hidden="true">💧</span>{escape(rain)}{escape(amount_txt)}</p>'
            f'<p class="w"><span aria-hidden="true">💨</span>{escape(wind_txt)}</p></li>')
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
        rain = "" if prob is None or pd.isna(prob) or prob < 10 else f'<span aria-hidden="true">💧</span>{prob:.0f}%'
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

def _card(title: str, value: str, note: str = "") -> str:
    return (f'<div class="cz-card" role="group" aria-label="{escape(title)}"><p class="k">{escape(title)}</p>'
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
    alert_line = (f"{n_alerts} Cloudzy alert{'s' if n_alerts != 1 else ''} (see above)" if n_alerts
                  else "No Cloudzy alerts with your thresholds")
    return (f'<section class="cz-say cz-fade" aria-label="Daily briefing for {escape(b.date)}">'
            f"<h3>📋 Daily briefing · {escape(b.date)}</h3>"
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
