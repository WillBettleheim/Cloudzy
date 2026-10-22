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
/* ===== Weather components ("Atmospheric Intelligence"). Tokens (--cz-*) come from src.theme. ===== */

/* --- Hero: an immersive sky that follows the real condition and local time ------------------------ */
.cz-hero{position:relative;overflow:hidden;isolation:isolate;border-radius:var(--cz-r-xl);color:#f4f7fd;
  padding:clamp(20px,3vw,34px) clamp(20px,3.4vw,40px) clamp(18px,2.6vw,28px);margin:var(--cz-s-1) 0 var(--cz-s-2);
  box-shadow:var(--cz-sh-2);min-height:360px;display:flex;flex-direction:column;justify-content:space-between;
  gap:var(--cz-s-5);background:#0d1d44;transition:background var(--cz-m-slow) var(--cz-m-ease)}
.cz-hero::after{content:"";position:absolute;inset:0;z-index:-1;pointer-events:none;
  background:linear-gradient(0deg,rgba(4,9,20,.62) 0%,rgba(4,9,20,.18) 46%,rgba(4,9,20,0) 70%)}
.cz-hero .fx{position:absolute;inset:0;z-index:-2;pointer-events:none;overflow:hidden}
.cz-hero .fx span{position:absolute;display:none}
/* Sky palettes */
.cz-hero.atm-clear-day{background:linear-gradient(170deg,#0f3a86 0%,#2467c8 42%,#5aa0ea 78%,#9cc9f3 100%)}
.cz-hero.atm-partly-day{background:linear-gradient(170deg,#14376f 0%,#2c63ad 45%,#6a9bd3 85%,#a9c6e6 100%)}
.cz-hero.atm-clear-night{background:linear-gradient(175deg,#02060f 0%,#08142f 45%,#132755 80%,#1e3466 100%)}
.cz-hero.atm-partly-night{background:linear-gradient(175deg,#03070f 0%,#0b1834 50%,#1b2c52 100%)}
.cz-hero.atm-dawn{background:linear-gradient(175deg,#131b45 0%,#4a3a78 38%,#b85d7d 70%,#f2a64e 100%)}
.cz-hero.atm-dusk{background:linear-gradient(175deg,#0c1236 0%,#3b2d6b 40%,#a84f72 72%,#ee8f43 100%)}
.cz-hero.atm-cloudy{background:linear-gradient(175deg,#1f2b40 0%,#3a4a63 50%,#66778f 100%)}
.cz-hero.atm-fog{background:linear-gradient(180deg,#2a3346 0%,#566176 55%,#8c96a8 100%)}
.cz-hero.atm-rain{background:linear-gradient(175deg,#0f1828 0%,#22324a 50%,#40536d 100%)}
.cz-hero.atm-storm{background:linear-gradient(175deg,#05080f 0%,#141c2e 50%,#2a3448 100%)}
.cz-hero.atm-snow{background:linear-gradient(175deg,#24365a 0%,#5b7097 50%,#a9bbd6 100%)}
/* After dark, overcast, fog, rain and snow skies deepen (the hero's "night" class comes from is_day). */
.cz-hero.night.atm-cloudy{background:linear-gradient(175deg,#060b16 0%,#141d2f 55%,#28344a 100%)}
.cz-hero.night.atm-fog{background:linear-gradient(180deg,#0d1320 0%,#252d3d 55%,#3d4658 100%)}
.cz-hero.night.atm-rain{background:linear-gradient(175deg,#04070f 0%,#101a2b 55%,#1f2c42 100%)}
.cz-hero.night.atm-snow{background:linear-gradient(175deg,#0b1428 0%,#25365a 55%,#4a5f85 100%)}
/* Sunlight bloom (day, dawn, dusk), stars and moon glow (clear night) */
.atm-clear-day .fx .sunglow,.atm-partly-day .fx .sunglow,.atm-dawn .fx .sunglow,.atm-dusk .fx .sunglow{display:block;
  width:520px;height:520px;right:-140px;top:-200px;border-radius:50%;
  background:radial-gradient(circle,rgba(255,226,150,.55) 0%,rgba(255,196,90,.22) 30%,rgba(255,180,80,0) 66%)}
.atm-dawn .fx .sunglow,.atm-dusk .fx .sunglow{top:auto;bottom:-300px;right:10%;
  background:radial-gradient(circle,rgba(255,190,110,.55) 0%,rgba(255,140,90,.22) 35%,rgba(255,120,80,0) 68%)}
.atm-clear-night .fx .stars,.atm-partly-night .fx .stars{display:block;inset:0;opacity:.85;
  background-image:radial-gradient(1px 1px at 8% 18%,#fff 60%,transparent),radial-gradient(1px 1px at 22% 42%,#dfe9ff 60%,transparent),
  radial-gradient(1.4px 1.4px at 35% 12%,#fff 60%,transparent),radial-gradient(1px 1px at 48% 30%,#cfe0ff 60%,transparent),
  radial-gradient(1px 1px at 61% 8%,#fff 60%,transparent),radial-gradient(1.2px 1.2px at 72% 26%,#fff 60%,transparent),
  radial-gradient(1px 1px at 84% 14%,#dfe9ff 60%,transparent),radial-gradient(1px 1px at 92% 38%,#fff 60%,transparent),
  radial-gradient(1px 1px at 15% 60%,#cfe0ff 60%,transparent),radial-gradient(1px 1px at 56% 52%,#fff 60%,transparent),
  radial-gradient(1.3px 1.3px at 78% 48%,#fff 60%,transparent),radial-gradient(1px 1px at 30% 70%,#dfe9ff 60%,transparent)}
.atm-clear-night .fx .sunglow{display:block;width:260px;height:260px;right:6%;top:-70px;border-radius:50%;
  background:radial-gradient(circle,rgba(220,232,255,.30) 0%,rgba(190,210,255,.08) 40%,rgba(190,210,255,0) 70%)}
/* Clouds: soft layered shapes, drifting very slowly */
.cz-hero .fx .cloud{width:60%;height:52%;border-radius:50%;filter:blur(28px);opacity:.0}
.atm-partly-day .fx .cloud,.atm-partly-night .fx .cloud,.atm-cloudy .fx .cloud,.atm-rain .fx .cloud,
.atm-storm .fx .cloud,.atm-snow .fx .cloud,.atm-fog .fx .cloud{display:block;animation:czdrift 70s linear infinite alternate}
.cz-hero .fx .c1{left:-8%;top:4%;background:rgba(235,242,252,.30)}
.cz-hero .fx .c2{right:-12%;top:22%;background:rgba(220,230,245,.24);animation-duration:95s !important}
.cz-hero .fx .c3{left:22%;bottom:-18%;background:rgba(200,214,235,.20);animation-duration:120s !important}
.atm-partly-day .fx .cloud{opacity:.75}.atm-partly-night .fx .cloud{opacity:.35}
.atm-cloudy .fx .cloud,.atm-snow .fx .cloud{opacity:.9}.atm-rain .fx .cloud{opacity:.55}
.atm-storm .fx .cloud{opacity:.45;background:rgba(150,165,190,.25)}.atm-fog .fx .cloud{opacity:.8;height:30%;filter:blur(36px)}
@keyframes czdrift{from{transform:translateX(-4%)}to{transform:translateX(6%)}}
/* Rain and storm: fine diagonal streaks moved by transform (composited, cheap) */
.atm-rain .fx .rainfx,.atm-storm .fx .rainfx{display:block;left:-20%;top:-100%;width:140%;height:200%;opacity:.38;
  background-image:repeating-linear-gradient(105deg,rgba(200,225,255,0) 0 11px,rgba(200,225,255,.55) 11px 12px,
  rgba(200,225,255,0) 12px 23px);background-size:46px 90px;animation:czrain .9s linear infinite}
@keyframes czrain{from{transform:translate3d(0,0,0)}to{transform:translate3d(-18px,90px,0)}}
.atm-storm .fx .flash{display:block;inset:0;background:rgba(210,225,255,.0);animation:czflash 11s ease-out infinite}
@keyframes czflash{0%,91%,95.5%,100%{background:rgba(210,225,255,0)}92%{background:rgba(210,225,255,.20)}
  93.5%{background:rgba(210,225,255,.05)}94.5%{background:rgba(210,225,255,.16)}}
/* Snow: two layers of soft flakes */
.atm-snow .fx .snowfx{display:block;left:0;top:-100%;width:100%;height:200%;opacity:.8;
  background-image:radial-gradient(2px 2px at 10% 20%,#fff 50%,transparent),radial-gradient(1.5px 1.5px at 30% 60%,#fff 50%,transparent),
  radial-gradient(2.5px 2.5px at 55% 35%,#fff 50%,transparent),radial-gradient(1.5px 1.5px at 75% 75%,#fff 50%,transparent),
  radial-gradient(2px 2px at 90% 45%,#fff 50%,transparent);background-size:220px 180px;animation:czsnow 14s linear infinite}
@keyframes czsnow{from{transform:translate3d(0,0,0)}to{transform:translate3d(20px,180px,0)}}
.atm-fog .fx .fogfx{display:block;inset:0;background:repeating-linear-gradient(180deg,rgba(230,236,245,0) 0 26px,
  rgba(230,236,245,.10) 26px 46px)}
@media (prefers-reduced-motion:reduce){.cz-hero .fx span{animation:none !important}}
/* Hero content */
.cz-hero .top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-start;gap:var(--cz-s-2) var(--cz-s-5)}
.cz-hero .place{font-size:var(--cz-t-lg);font-weight:600;margin:0;display:flex;gap:6px;align-items:baseline;
  text-shadow:0 1px 12px rgba(0,0,0,.25)}
.cz-hero .sub{font-size:var(--cz-t-sm);opacity:.85;margin:3px 0 0}
.cz-hero .sun{color:var(--cz-c-amber)}
.cz-hero .now{display:flex;flex-wrap:wrap;align-items:flex-end;gap:var(--cz-s-2) clamp(16px,3vw,40px)}
.cz-hero .temp{font-family:var(--cz-f-display);font-size:var(--cz-t-display);font-weight:200;line-height:.82;
  letter-spacing:-.05em;font-variant-numeric:tabular-nums;text-shadow:0 4px 40px rgba(0,0,0,.25);margin-left:-.04em}
.cz-hero .cond-block{padding-bottom:.55rem}
.cz-hero .cond{font-size:var(--cz-t-xl);font-weight:600;margin:0;display:flex;align-items:center;gap:10px}
.cz-hero .cond .cz-ico{font-size:2rem}
.cz-hero .hl{font-size:var(--cz-t-md);margin:6px 0 0;opacity:.9;font-variant-numeric:tabular-nums}
.cz-hero .icon{display:none}
.cz-hero .stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:0;margin:0;padding:0;
  border-top:1px solid rgba(255,255,255,.18)}
.cz-hero .stat{list-style:none;padding:var(--cz-s-3) var(--cz-s-3) 0;border-left:1px solid rgba(255,255,255,.12)}
.cz-hero .stat:first-child{border-left:none;padding-left:0}
.cz-hero .stat .k{font-size:.68rem;text-transform:uppercase;letter-spacing:.1em;font-weight:600;opacity:.75;margin:0;
  display:flex;gap:4px;align-items:center}
.cz-hero .stat .k .cz-ico{font-size:.95rem}
.cz-hero .stat .v{font-family:var(--cz-f-display);font-size:var(--cz-t-lg);font-weight:500;margin:4px 0 0;
  font-variant-numeric:tabular-nums;white-space:nowrap}
.cz-hero .deco{position:absolute;right:16px;bottom:8px;font-size:.66rem;opacity:.0}
.cz-wx.sunny{color:var(--cz-c-amber)}

/* --- Summary panel: editorial text with an accent rule, not a card ------------------------------- */
.cz-say{padding:var(--cz-s-1) 0 var(--cz-s-1) var(--cz-s-5);border-left:2px solid var(--cz-c-sky);margin:var(--cz-s-2) 0}
.cz-say h3{margin:0 0 var(--cz-s-3);font-size:.72rem;text-transform:uppercase;letter-spacing:.14em;font-weight:600;
  color:var(--cz-c-sky);display:flex;gap:6px;align-items:center}
.cz-say h3 .cz-ico{font-size:1rem}
.cz-say ul{margin:0;padding:0;list-style:none}
.cz-say li{margin:0 0 var(--cz-s-3);line-height:1.5;font-size:1.02rem}
.cz-say li:first-child{font-family:var(--cz-f-display);font-size:var(--cz-t-xl);line-height:1.3;font-weight:400}
.cz-say .src{font-size:var(--cz-t-xs);opacity:.6;margin:var(--cz-s-1) 0 0}

/* --- Alerts: a restrained severity strip ----------------------------------------------------------- */
.cz-alert{border-radius:var(--cz-r-md);padding:var(--cz-s-3) var(--cz-s-4);margin:0 0 var(--cz-s-2);
  border:1px solid rgba(227,154,28,.35);border-left:4px solid var(--cz-c-amber);
  background:linear-gradient(90deg,rgba(242,169,59,.12),rgba(242,169,59,.03))}
.cz-alert h4{margin:0 0 4px;font-size:var(--cz-t-md);font-weight:600;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.cz-alert h4 .cz-ico{color:var(--cz-c-amber)}
.cz-alert p{margin:2px 0;font-size:var(--cz-t-sm)}
.cz-alert .lbl{font-size:var(--cz-t-xs);opacity:.7}

/* --- Suggestions ----------------------------------------------------------------------------------- */
.cz-rec{padding:var(--cz-s-3) 0;border-top:1px solid var(--cz-n-border);height:100%}
.cz-rec p{margin:0;display:flex;gap:10px;align-items:flex-start}
.cz-rec p .cz-ico{color:var(--cz-c-amber);font-size:1.3rem}
.cz-rec .why{font-size:var(--cz-t-sm);opacity:.7;margin-top:3px;display:block}

/* --- Hourly: one continuous timeline, not 24 boxes -------------------------------------------------- */
.cz-strip{display:flex;gap:0;overflow-x:auto;padding:4px 2px 12px;scroll-snap-type:x proximity;
  border-top:1px solid var(--cz-n-border);border-bottom:1px solid var(--cz-n-border)}
.cz-strip:focus-visible{outline:3px solid var(--cz-c-sky);outline-offset:2px;border-radius:var(--cz-r-md)}
.cz-hour{flex:0 0 76px;scroll-snap-align:start;text-align:center;padding:12px 4px 4px;position:relative;
  border-left:1px solid var(--cz-n-border);transition:background var(--cz-m-fast) var(--cz-m-ease)}
.cz-hour:first-child{border-left:none}
.cz-hour:hover{background:var(--cz-n-surface)}
.cz-hour.first{background:linear-gradient(180deg,rgba(74,163,255,.16),rgba(74,163,255,0))}
.cz-hour.first::before{content:"";position:absolute;left:14px;right:14px;top:0;height:2px;border-radius:2px;background:var(--cz-c-sky)}
.cz-hour .t{font-size:var(--cz-t-xs);font-weight:600;margin:0;opacity:.75;letter-spacing:.04em}
.cz-hour.first .t{color:var(--cz-c-sky);opacity:1}
.cz-hour .i{margin:8px 0 6px}
.cz-hour .i .cz-ico{font-size:1.65rem;color:var(--cz-c-sky-soft)}
.cz-hour .i .cz-wx.sunny{color:var(--cz-c-amber)}
.cz-hour .v{font-family:var(--cz-f-display);font-size:1.3rem;font-weight:400;margin:0;font-variant-numeric:tabular-nums}
.cz-hour .r,.cz-hour .w{font-size:.7rem;margin:4px 0 0;opacity:.7;display:flex;gap:2px;justify-content:center;
  align-items:center;font-variant-numeric:tabular-nums}
.cz-hour .r{color:var(--cz-c-rain);opacity:.9}
.cz-hour .r .cz-ico,.cz-hour .w .cz-ico{font-size:.85rem}

/* --- Daily: an editorial list with a temperature-range bar ------------------------------------------ */
.cz-days{padding:0}
.cz-day{display:grid;grid-template-columns:minmax(3.6rem,5rem) 2rem 3.6rem 2.8rem 1fr 2.8rem;align-items:center;
  gap:var(--cz-s-3);padding:12px 4px;border-bottom:1px solid var(--cz-n-border);
  transition:background var(--cz-m-fast) var(--cz-m-ease)}
.cz-day:hover{background:var(--cz-n-surface)}
.cz-day .d{font-weight:600}
.cz-day .i{text-align:center}
.cz-day .i .cz-ico{font-size:1.5rem;color:var(--cz-c-sky-soft)}
.cz-day .i .cz-wx.sunny{color:var(--cz-c-amber)}
.cz-day .r{font-size:var(--cz-t-sm);display:flex;align-items:center;gap:2px;color:var(--cz-c-rain);font-variant-numeric:tabular-nums}
.cz-day .r .cz-ico{font-size:.9rem}
.cz-day .lo,.cz-day .hi{font-family:var(--cz-f-display);font-size:1.1rem;font-variant-numeric:tabular-nums}
.cz-day .lo{text-align:right;opacity:.6}
.cz-day .hi{font-weight:500}
.cz-bar{position:relative;height:6px;border-radius:3px;background:var(--cz-n-surface-2)}
.cz-bar span{position:absolute;top:0;bottom:0;border-radius:3px;
  background:linear-gradient(90deg,#5cc4ff,#8bd3a8 45%,var(--cz-c-amber) 80%,#f07a4a)}

/* --- Current measurements: an instrument panel with hairlines ------------------------------------- */
.cz-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:0 var(--cz-s-5)}
.cz-card{padding:var(--cz-s-3) 0;border-top:1px solid var(--cz-n-border)}
.cz-card .k{font-size:.68rem;text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin:0;opacity:.65;
  display:flex;gap:5px;align-items:center}
.cz-card .k .cz-ico{font-size:.95rem;color:var(--cz-c-sky)}
.cz-card .v{font-family:var(--cz-f-display);font-size:1.6rem;font-weight:400;margin:6px 0 2px;font-variant-numeric:tabular-nums}
.cz-card .n{font-size:var(--cz-t-sm);margin:0;opacity:.7}

/* --- Saved-place cards: a small sky driven by that place's actual current conditions -------------- */
.cz-hero.cz-mini{min-height:168px;padding:16px 18px 14px;border-radius:var(--cz-r-lg);gap:var(--cz-s-2);margin:0}
.cz-hero.cz-mini .place{font-size:var(--cz-t-md)}
.cz-hero.cz-mini .temp{font-size:3.4rem;font-weight:200}
.cz-hero.cz-mini .cond{font-size:var(--cz-t-md)}
.cz-hero.cz-mini .cond .cz-ico{font-size:1.4rem}
.cz-hero.cz-mini .cond-block{padding-bottom:.2rem}
.cz-hero.cz-mini .hl{font-size:var(--cz-t-sm)}
.cz-hero.cz-mini .badge{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:999px;font-size:.7rem;
  font-weight:600;background:rgba(242,169,59,.22);border:1px solid rgba(242,169,59,.6);color:#ffe2b0}
.cz-hero.cz-mini .badge .cz-ico{font-size:.9rem}

/* The hero responds to its own width (the sidebar can make it narrow on a wide screen), not only the viewport. */
.cz-hero{container-type:inline-size}
.cz-hero .stat .v{overflow-wrap:anywhere}
@container (max-width:640px){.cz-hero .stats{grid-template-columns:repeat(3,minmax(0,1fr))}
  .cz-hero .stat:nth-child(4){border-left:none;padding-left:0}}
@container (max-width:380px){.cz-hero .stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .cz-hero .stat{border-left:none;padding-left:0}
  .cz-hero .stat:nth-child(even){border-left:1px solid rgba(255,255,255,.12);padding-left:var(--cz-s-3)}}
@media (max-width:900px){.cz-hero .stats{grid-template-columns:repeat(3,minmax(0,1fr))}
  .cz-hero .stat:nth-child(4){border-left:none;padding-left:0}}
@media (max-width:640px){
  .cz-hero{min-height:0;padding:20px 18px 16px;border-radius:var(--cz-r-lg)}
  .cz-hero .stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .cz-hero .stat{border-left:none;padding-left:0}
  .cz-hero .stat:nth-child(even){border-left:1px solid rgba(255,255,255,.12);padding-left:var(--cz-s-3)}
  .cz-day{grid-template-columns:3.2rem 1.8rem 3rem 2.4rem 1fr 2.4rem;gap:6px}
  .cz-say{padding-left:var(--cz-s-4)}
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


TWILIGHT = pd.Timedelta(minutes=50)


def atmosphere(code, is_day, now=None, sunrise=None, sunset=None) -> str:
    """The hero's sky treatment, from the actual WMO weather code and local sun times.

    Precipitation and fog win over time of day; clear or partly cloudy skies within 50 minutes of
    sunrise or sunset get the dawn/dusk palette. Unknown codes fall back to a neutral day/night sky.
    """
    night = is_day is not None and not pd.isna(is_day) and int(is_day) == 0
    c = None if code is None or pd.isna(code) else int(code)
    if c in {95, 96, 99}:
        return "storm"
    if c in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if c in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain"
    if c in {45, 48}:
        return "fog"
    if c == 3:
        return "cloudy"
    if c in {0, 1, 2} and now is not None:
        t = pd.Timestamp(now)
        for edge, name in ((sunrise, "dawn"), (sunset, "dusk")):
            if edge is not None and not pd.isna(edge) and abs(t - pd.Timestamp(edge)) <= TWILIGHT:
                return name
    if c in {1, 2}:
        return "partly-night" if night else "partly-day"
    return "clear-night" if night else "clear-day"


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
    """Current conditions over an atmospheric sky that follows the actual condition and local time.

    Every value comes from the provider; missing ones show a dash. The sky effects are decorative
    (aria-hidden) and never stand for imagery or measured precipitation.
    """
    is_day = current.get("is_day")
    mode = "night" if is_day is not None and not pd.isna(is_day) and int(is_day) == 0 else "day"
    mode += hero_mood(current.get("weather_code"))
    t = pd.Timestamp(current["time"])
    sunrise = today.get("sunrise") if today is not None else None
    sunset = today.get("sunset") if today is not None else None
    atm = atmosphere(current.get("weather_code"), is_day, t, sunrise, sunset)
    hi = temp_text(today["temperature_2m_max"], units) if today is not None else DASH
    lo = temp_text(today["temperature_2m_min"], units) if today is not None else DASH
    sun = ""
    if today is not None and not pd.isna(today.get("sunrise")) and not pd.isna(today.get("sunset")):
        sun = (f'<p class="sub"><span class="sun">{icon("wb_twilight")}</span> Sunrise {today["sunrise"]:%H:%M}'
               f' · Sunset {today["sunset"]:%H:%M}</p>')
    feels = temp_text(current.get("apparent_temperature"), units)
    wind = U.wind(current.get("wind_speed_10m"), units.wind)
    chance = today.get("precipitation_probability_max") if today is not None else None
    vis = U.distance(current.get("visibility"), units.distance)
    stats = "".join([
        _stat("Humidity", "humidity_percentage", _with_unit(current.get("relative_humidity_2m"), "%", sep="")),
        _stat("Wind", "air", _with_unit(wind, units.wind) + (f" {escape(compass(current.get('wind_direction_10m')))}"
                                                               if wind is not None else "")),
        _stat("Pressure", "compress", _with_unit(current.get("pressure_msl"), "hPa")),
        _stat("UV index", "wb_sunny", escape(_num(current.get("uv_index"), 0))),
        _stat("Rain today", "water_drop", _with_unit(chance, "%", sep="")),
        _stat("Visibility", "visibility", _with_unit(vis, units.distance, 1)),
    ])
    fx = "".join(f'<span class="{c}"></span>' for c in
                 ("sunglow", "stars", "cloud c1", "cloud c2", "cloud c3", "rainfx", "snowfx", "flash", "fogfx"))
    return f"""
<section class="cz-hero {mode} atm-{atm} cz-fade" aria-label="Current weather in {escape(place_label)}" data-atmosphere="{atm}">
  <div class="fx" aria-hidden="true">{fx}</div>
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
    <div class="cond-block">
      <p class="cond">{_icon(current.get("weather_code"), is_day)}{escape(current.get("description") or "Unknown")}</p>
      <p class="hl">Feels like {feels} · H {hi} · L {lo}</p>
    </div>
  </div>
  <ul class="stats" aria-label="Current measurements">{stats}</ul>
</section>"""


def place_card_html(place, current: dict | None, today: pd.Series | None, units: U.Units, is_home: bool) -> str:
    """A saved place as a small sky card. With no current data, a neutral card says so (no invented values)."""
    region = ", ".join(x for x in (place.admin1, place.country) if x)
    badge = f'<span class="badge">{icon("home")}Home</span>' if is_home else ""
    head = (f'<div class="top"><div><p class="place">{icon("location_on")}<span>{escape(place.name)}</span></p>'
            f'<p class="sub">{escape(region)}</p></div>{badge}</div>')
    if not current:
        return (f'<section class="cz-hero cz-mini atm-cloudy" aria-label="{escape(place.label)}">{head}'
                f'<p class="sub">Current weather isn\'t available right now.</p></section>')
    is_day = current.get("is_day")
    t = pd.Timestamp(current["time"])
    atm = atmosphere(current.get("weather_code"), is_day, t,
                     today.get("sunrise") if today is not None else None, today.get("sunset") if today is not None else None)
    fx = "".join(f'<span class="{c}"></span>' for c in ("sunglow", "stars", "cloud c1", "cloud c2", "rainfx", "snowfx", "fogfx"))
    hi = temp_text(today["temperature_2m_max"], units) if today is not None else DASH
    lo = temp_text(today["temperature_2m_min"], units) if today is not None else DASH
    return (f'<section class="cz-hero cz-mini atm-{atm}" aria-label="{escape(place.label)}: '
            f'{escape(temp_text(current.get("temperature_2m"), units))}, {escape(current.get("description") or "")}">'
            f'<div class="fx" aria-hidden="true">{fx}</div>{head}'
            f'<div class="now"><div class="temp">{temp_text(current.get("temperature_2m"), units)}</div>'
            f'<div class="cond-block"><p class="cond">{_icon(current.get("weather_code"), is_day)}'
            f'{escape(current.get("description") or "Unknown")}</p>'
            f'<p class="hl">H {hi} · L {lo} · {t:%H:%M} local</p></div></div></section>')


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
