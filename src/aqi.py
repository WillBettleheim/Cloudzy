"""Air-quality index standards: categories, health guidance and India's NAQI calculation.

Three indices are supported. They are never interchangeable: every number Cloudzy shows carries
the name of the index it belongs to.

- **US AQI (EPA).** Taken from Open-Meteo, which applies the EPA breakpoints to 24-hour rolling
  means for PM2.5 and PM10, 8-hour means for CO, the higher of 1-hour and 8-hour ozone, and
  1-hour NO₂. Cloudzy checked on 2026-10-22 that its PM2.5 sub-index follows the PM2.5
  breakpoints EPA revised in 2024 (Good ends at 9.0 µg/m³), not the older ones (12.0 µg/m³).
  AirNow reports current hours with the NowCast method, so its values can differ.
- **European AQI (EEA).** Taken from Open-Meteo, which uses the 2024 EEA bands on hourly
  concentrations and interpolates within each band (0–20 good … above 100 extremely poor).
- **India National AQI (CPCB, 2014).** Open-Meteo doesn't provide it, so Cloudzy calculates it
  here from the modelled concentrations with CPCB's published breakpoints and rules: 24-hour
  averages (8-hour for O₃ and CO), at least 16 hours of data for each 24-hour average, at least
  three pollutants including PM2.5 or PM10, and the worst sub-index as the overall AQI. CPCB
  gives no upper breakpoint for "Severe", so values there are reported as "401+", never as an
  invented number. Because the inputs are CAMS model estimates, the result is an estimate made
  with the NAQI method, not an official CPCB or station value.

Activity guidance per category is expressed for two exertion levels (heavy: running, cycling,
workouts; light: walking, casual time) and two audiences (everyone; people who want the more
conservative "sensitive groups" guidance). Each level is ok, caution or avoid:
- US: AirNow's Air Quality Guide for activities, mapped "reduce" → caution, "avoid" → avoid.
- EU: EEA health recommendations, mapped the same way. "Reduce physical activities outdoors"
  (all activity, not only intense) is mapped to avoid for heavy exertion.
- India: CPCB publishes health impacts, not activity advice. Cloudzy's mapping is documented in
  the table below and in docs/AIR_QUALITY.md; it follows the US guidance for similar PM2.5 levels.

Pure functions only (pandas in, pandas out): no Streamlit, no network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

OK, CAUTION, AVOID = "ok", "caution", "avoid"


@dataclass(frozen=True)
class Category:
    index: int              # 0 = best; the same 0-5 scale in every standard, used for alert thresholds
    label: str
    upper: float | None     # inclusive upper bound of the index value; None = open-ended
    color: str              # the standard's own category colour, always shown with the label
    general: str            # what the standard says for everyone
    sensitive: str          # what it says for sensitive groups
    general_heavy: str = OK
    general_light: str = OK
    sensitive_heavy: str = OK
    sensitive_light: str = OK

    def guidance(self, heavy: bool, sensitive: bool) -> str:
        if sensitive:
            return self.sensitive_heavy if heavy else self.sensitive_light
        return self.general_heavy if heavy else self.general_light


@dataclass(frozen=True)
class Standard:
    code: str
    name: str                    # "US AQI (EPA)"
    short: str                   # "US AQI"
    column: str                  # column holding the overall index in the hourly table
    sub_columns: dict            # pollutant -> sub-index column
    categories: tuple[Category, ...]
    computed_by: str             # "Open-Meteo" or "Cloudzy"
    method: str
    source: str
    source_url: str
    scale_max: float             # where the gauge ends (values above it pin to the end)

    def category(self, value) -> Category | None:
        if value is None or (isinstance(value, float) and math.isnan(value)) or pd.isna(value):
            return None
        for c in self.categories:
            if c.upper is None or value <= c.upper:
                return c
        return self.categories[-1]


US_EPA = Standard(
    code="us_epa", name="US AQI (EPA)", short="US AQI", column="us_aqi",
    sub_columns={"pm2_5": "us_aqi_pm2_5", "pm10": "us_aqi_pm10", "nitrogen_dioxide": "us_aqi_nitrogen_dioxide",
                 "ozone": "us_aqi_ozone", "sulphur_dioxide": "us_aqi_sulphur_dioxide",
                 "carbon_monoxide": "us_aqi_carbon_monoxide"},
    categories=(
        Category(0, "Good", 50, "#00e400", "Air quality is satisfactory and poses little or no risk.",
                 "Air quality is satisfactory and poses little or no risk."),
        Category(1, "Moderate", 100, "#ffff00", "Air quality is acceptable for most people.",
                 "Unusually sensitive people: consider making outdoor activities shorter and less intense.",
                 sensitive_heavy=CAUTION),
        Category(2, "Unhealthy for Sensitive Groups", 150, "#ff7e00",
                 "Most people are unlikely to be affected; it's OK to be active outside.",
                 "Sensitive groups: reduce prolonged or heavy exertion and take more breaks.",
                 sensitive_heavy=CAUTION),
        Category(3, "Unhealthy", 200, "#ff0000", "Everyone: reduce prolonged or heavy exertion and take more breaks.",
                 "Sensitive groups: avoid prolonged or heavy exertion.",
                 general_heavy=CAUTION, sensitive_heavy=AVOID, sensitive_light=CAUTION),
        Category(4, "Very Unhealthy", 300, "#8f3f97",
                 "Everyone: avoid prolonged or heavy exertion; consider moving activities indoors.",
                 "Sensitive groups: avoid all physical activity outdoors.",
                 general_heavy=AVOID, general_light=CAUTION, sensitive_heavy=AVOID, sensitive_light=AVOID),
        Category(5, "Hazardous", None, "#7e0023", "Everyone: avoid all physical activity outdoors.",
                 "Everyone: avoid all physical activity outdoors.",
                 general_heavy=AVOID, general_light=AVOID, sensitive_heavy=AVOID, sensitive_light=AVOID),
    ),
    computed_by="Open-Meteo",
    method="EPA breakpoints (PM2.5 breakpoints revised in 2024); 24-hour rolling means for PM2.5 and PM10, 8-hour "
           "for CO, the higher of 1-hour and 8-hour ozone, 1-hour NO₂. Not EPA's NowCast, so it can differ from AirNow.",
    source="US EPA Air Quality Index; AirNow Air Quality Guide for activities",
    source_url="https://www.airnow.gov/aqi/aqi-basics/", scale_max=500,
)

EU_EAQI = Standard(
    code="eu_eaqi", name="European AQI (EEA)", short="European AQI", column="european_aqi",
    sub_columns={"pm2_5": "european_aqi_pm2_5", "pm10": "european_aqi_pm10",
                 "nitrogen_dioxide": "european_aqi_nitrogen_dioxide", "ozone": "european_aqi_ozone",
                 "sulphur_dioxide": "european_aqi_sulphur_dioxide"},
    categories=(
        Category(0, "Good", 20, "#50f0e6", "The air quality is good. Enjoy your usual outdoor activities.",
                 "The air quality is good. Enjoy your usual outdoor activities."),
        Category(1, "Fair", 40, "#50ccaa", "Enjoy your usual outdoor activities.",
                 "Enjoy your usual outdoor activities."),
        Category(2, "Moderate", 60, "#f0e641", "Enjoy your usual outdoor activities.",
                 "Consider reducing intense outdoor activities if you experience symptoms.",
                 sensitive_heavy=CAUTION),
        Category(3, "Poor", 80, "#ff5050",
                 "Consider reducing intense activities outdoors if you experience symptoms such as sore eyes, "
                 "a cough or sore throat.",
                 "Consider reducing physical activities, particularly outdoors, especially if you experience symptoms.",
                 general_heavy=CAUTION, sensitive_heavy=CAUTION, sensitive_light=CAUTION),
        Category(4, "Very poor", 100, "#960032", "Consider reducing physical activities outdoors.",
                 "Avoid physical activities outdoors.",
                 general_heavy=AVOID, general_light=CAUTION, sensitive_heavy=AVOID, sensitive_light=AVOID),
        Category(5, "Extremely poor", None, "#7d2181", "Reduce physical activities outdoors.",
                 "Avoid physical activities outdoors.",
                 general_heavy=AVOID, general_light=CAUTION, sensitive_heavy=AVOID, sensitive_light=AVOID),
    ),
    computed_by="Open-Meteo",
    method="EEA bands (2024 revision) on hourly concentrations of PM2.5, PM10, NO₂, O₃ and SO₂, interpolated within "
           "each band; the overall index is the highest sub-index and can exceed 100.",
    source="European Environment Agency, European Air Quality Index",
    source_url="https://airindex.eea.europa.eu/", scale_max=120,
)

IN_NAQI = Standard(
    code="in_naqi", name="India National AQI (CPCB)", short="India NAQI", column="naqi",
    sub_columns={"pm2_5": "naqi_pm2_5", "pm10": "naqi_pm10", "nitrogen_dioxide": "naqi_nitrogen_dioxide",
                 "ozone": "naqi_ozone", "sulphur_dioxide": "naqi_sulphur_dioxide",
                 "carbon_monoxide": "naqi_carbon_monoxide"},
    categories=(
        Category(0, "Good", 50, "#009933", "Minimal impact.", "Minimal impact."),
        Category(1, "Satisfactory", 100, "#58ff09", "Minimal impact for most people.",
                 "May cause minor breathing discomfort to sensitive people.", sensitive_heavy=CAUTION),
        Category(2, "Moderately polluted", 200, "#ffff00",
                 "May cause discomfort to children and older adults.",
                 "May cause breathing discomfort to people with lung disease such as asthma, and discomfort to "
                 "people with heart disease.",
                 general_heavy=CAUTION, sensitive_heavy=AVOID, sensitive_light=CAUTION),
        Category(3, "Poor", 300, "#ffa500", "May cause breathing discomfort to most people on prolonged exposure.",
                 "May cause breathing discomfort on prolonged exposure, and discomfort to people with heart disease.",
                 general_heavy=AVOID, general_light=CAUTION, sensitive_heavy=AVOID, sensitive_light=AVOID),
        Category(4, "Very poor", 400, "#ff0000", "May cause respiratory illness on prolonged exposure.",
                 "Effects are more pronounced in people with lung and heart diseases.",
                 general_heavy=AVOID, general_light=AVOID, sensitive_heavy=AVOID, sensitive_light=AVOID),
        Category(5, "Severe", None, "#990000",
                 "May affect healthy people, possibly even during light physical activity.",
                 "Serious impacts on people with lung or heart disease.",
                 general_heavy=AVOID, general_light=AVOID, sensitive_heavy=AVOID, sensitive_light=AVOID),
    ),
    computed_by="Cloudzy",
    method="Calculated by Cloudzy with CPCB's 2014 NAQI breakpoints from CAMS modelled concentrations: 24-hour "
           "averages (8-hour for O₃ and CO), at least 16 hours of data per 24-hour average, at least three "
           "pollutants including PM2.5 or PM10; the worst sub-index is the AQI. An estimate made with the NAQI "
           "method, not an official CPCB value.",
    source="Central Pollution Control Board (India), National Air Quality Index (2014)",
    source_url="https://cpcb.nic.in/national-air-quality-index/", scale_max=500,
)

STANDARDS = {s.code: s for s in (US_EPA, EU_EAQI, IN_NAQI)}

# EEA member countries (the EEA's index is the reference there) plus the UK, which is inside the CAMS
# European domain. National indices (such as the UK's DAQI) can differ and aren't calculated.
EEA_COUNTRIES = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
                 "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "CH", "TR",
                 "GB"}
US_COUNTRIES = {"US", "PR", "GU", "VI", "AS", "MP", "UM"}


def default_standard(country_code: str | None) -> tuple[Standard, str]:
    """The index to show first for a place, and a note explaining the choice."""
    code = (country_code or "").upper()
    if code == "IN":
        return IN_NAQI, "India's National AQI is the official index here."
    if code in US_COUNTRIES:
        return US_EPA, "The US AQI is the official index here."
    if code == "GB":
        return EU_EAQI, ("The UK's official index is the Daily Air Quality Index (DAQI), which Cloudzy doesn't "
                         "calculate; the European AQI is shown instead.")
    if code in EEA_COUNTRIES:
        return EU_EAQI, "The European Environment Agency's index; national indices may differ."
    return US_EPA, ("Cloudzy doesn't support this country's own index (if it has one), so the US AQI is shown as a "
                    "reference scale. Local authorities may use a different index.")


def resolve_standard(choice: str | None, country_code: str | None) -> tuple[Standard, str]:
    """The viewer's saved choice ("auto" or a code), else the regional default."""
    if choice in STANDARDS:
        return STANDARDS[choice], "Your chosen index (change it on this page or in Settings)."
    return default_standard(country_code)


# --- Pollutants ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Pollutant:
    key: str
    name: str
    symbol: str
    who_guideline: float | None    # WHO 2021 air quality guideline level, µg/m³
    who_period: str                # its averaging period ("24-hour", "8-hour peak")
    who_hours: int                 # hours averaged to compare with it


POLLUTANTS = {
    "pm2_5": Pollutant("pm2_5", "Fine particles", "PM2.5", 15, "24-hour mean", 24),
    "pm10": Pollutant("pm10", "Coarse particles", "PM10", 45, "24-hour mean", 24),
    "nitrogen_dioxide": Pollutant("nitrogen_dioxide", "Nitrogen dioxide", "NO₂", 25, "24-hour mean", 24),
    "ozone": Pollutant("ozone", "Ozone", "O₃", 100, "8-hour mean", 8),
    "sulphur_dioxide": Pollutant("sulphur_dioxide", "Sulphur dioxide", "SO₂", 40, "24-hour mean", 24),
    "carbon_monoxide": Pollutant("carbon_monoxide", "Carbon monoxide", "CO", 4000, "24-hour mean", 24),
}
WHO_SOURCE = "WHO global air quality guidelines (2021)"


# --- India NAQI (CPCB 2014) ----------------------------------------------------------------------

_INDEX_BANDS = ((0, 50), (51, 100), (101, 200), (201, 300), (301, 400))
SEVERE_FLOOR = 401
# Concentration bands (low, high) per pollutant, one per index band above. µg/m³, except CO in mg/m³.
NAQI_BREAKPOINTS = {
    "pm10": ((0, 50), (51, 100), (101, 250), (251, 350), (351, 430)),
    "pm2_5": ((0, 30), (31, 60), (61, 90), (91, 120), (121, 250)),
    "nitrogen_dioxide": ((0, 40), (41, 80), (81, 180), (181, 280), (281, 400)),
    "ozone": ((0, 50), (51, 100), (101, 168), (169, 208), (209, 748)),
    "carbon_monoxide": ((0, 1.0), (1.1, 2.0), (2.1, 10), (10, 17), (17, 34)),
    "sulphur_dioxide": ((0, 40), (41, 80), (81, 380), (381, 800), (801, 1600)),
}
NAQI_AVERAGING_H = {"pm10": 24, "pm2_5": 24, "nitrogen_dioxide": 24, "sulphur_dioxide": 24, "ozone": 8,
                    "carbon_monoxide": 8}
# CPCB: at least 16 hours for a 24-hour average. For 8-hour averages CPCB states no figure here; Cloudzy
# requires 6 of 8 hours (the same three-quarters share, rounded up), documented in docs/AIR_QUALITY.md.
NAQI_MIN_HOURS = {24: 16, 8: 6}
NAQI_MIN_POLLUTANTS = 3


def naqi_sub_index(pollutant: str, concentration) -> float | None:
    """CPCB sub-index for one averaged concentration (CO in mg/m³). 401 means "401 or more" (Severe)."""
    if concentration is None or pd.isna(concentration) or concentration < 0:
        return None
    for (b_lo, b_hi), (i_lo, i_hi) in zip(NAQI_BREAKPOINTS[pollutant], _INDEX_BANDS):
        if concentration <= b_hi:
            value = (i_hi - i_lo) / (b_hi - b_lo) * (concentration - b_lo) + i_lo
            return float(min(max(value, i_lo), i_hi))   # concentrations between published bands stay in band
    return float(SEVERE_FLOOR)


def naqi_frame(hourly: pd.DataFrame) -> pd.DataFrame:
    """NAQI per hour from hourly concentrations (µg/m³, consecutive hourly, time-zone-aware index).

    Columns: naqi (rounded, NaN when not enough data), naqi_open (True when Severe, i.e. "401+"),
    naqi_dominant (pollutant key), naqi_reason (why it's missing), and naqi_<pollutant> sub-indices.
    """
    out = pd.DataFrame(index=hourly.index)
    for pol, hours in NAQI_AVERAGING_H.items():
        if pol not in hourly:
            out[f"naqi_{pol}"] = float("nan")
            continue
        conc = pd.to_numeric(hourly[pol], errors="coerce")
        if pol == "carbon_monoxide":
            conc = conc / 1000.0                      # µg/m³ → mg/m³, as CPCB's CO breakpoints are
        mean = conc.rolling(f"{hours}h", min_periods=NAQI_MIN_HOURS[hours]).mean()
        out[f"naqi_{pol}"] = [naqi_sub_index(pol, c) if pd.notna(c) else float("nan") for c in mean]
    subs = out[[f"naqi_{p}" for p in NAQI_AVERAGING_H]].astype(float)
    count = subs.notna().sum(axis=1)
    has_pm = subs[["naqi_pm2_5", "naqi_pm10"]].notna().any(axis=1)
    enough = (count >= NAQI_MIN_POLLUTANTS) & has_pm
    worst = subs.max(axis=1)
    out["naqi"] = worst.where(enough).round()
    out["naqi_open"] = enough & (worst >= SEVERE_FLOOR)
    out["naqi_dominant"] = [str(subs.loc[t].idxmax())[5:] if ok else None for t, ok in zip(subs.index, enough)]
    out["naqi_reason"] = [None if ok else (
        "PM2.5 and PM10 are both unavailable" if not pm else
        f"only {n} pollutant(s) have enough hours for an average (CPCB needs {NAQI_MIN_POLLUTANTS})")
        for ok, pm, n in zip(enough, has_pm, count)]
    return out


# --- Reading an index from the hourly table -------------------------------------------------------

def index_text(standard: Standard, value, open_ended: bool = False) -> str:
    """'153', '401+' (NAQI Severe) or '—'."""
    if value is None or pd.isna(value):
        return "—"
    if open_ended:
        return f"{SEVERE_FLOOR}+"
    return f"{value:.0f}"


def dominant_pollutant(standard: Standard, row: pd.Series) -> str | None:
    """The pollutant whose sub-index sets the overall index, when the sub-indices support it."""
    if standard is IN_NAQI:
        return row.get("naqi_dominant") or None
    overall = row.get(standard.column)
    subs = {p: row.get(c) for p, c in standard.sub_columns.items()}
    subs = {p: v for p, v in subs.items() if v is not None and pd.notna(v)}
    if not subs or overall is None or pd.isna(overall):
        return None
    pol, top = max(subs.items(), key=lambda kv: kv[1])
    return pol if abs(top - overall) <= 1 else None   # sub-indices disagree with the total: don't guess


def categories_table(standard: Standard) -> pd.DataFrame:
    """The standard's categories with their guidance, for the methodology section."""
    rows = []
    lower = 0.0
    for c in standard.categories:
        rng = f"{lower:.0f}+" if c.upper is None else f"{lower:.0f}–{c.upper:.0f}"
        rows.append({"Category": c.label, "Index": rng, "Everyone": c.general, "Sensitive groups": c.sensitive,
                     "Running, cycling, workouts": f"{c.general_heavy} / {c.sensitive_heavy}",
                     "Walking, casual time": f"{c.general_light} / {c.sensitive_light}"})
        lower = (c.upper or 0) + (1 if standard is not EU_EAQI else 0)
    return pd.DataFrame(rows)
