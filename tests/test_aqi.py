"""AQI standards: category mapping, India NAQI calculation (CPCB 2014) and standard selection."""

import math

import numpy as np
import pandas as pd
import pytest

from src import aqi


# --- Categories ------------------------------------------------------------------------------------

@pytest.mark.parametrize("standard, value, label", [
    (aqi.US_EPA, 0, "Good"), (aqi.US_EPA, 50, "Good"), (aqi.US_EPA, 51, "Moderate"),
    (aqi.US_EPA, 101, "Unhealthy for Sensitive Groups"), (aqi.US_EPA, 150, "Unhealthy for Sensitive Groups"),
    (aqi.US_EPA, 151, "Unhealthy"), (aqi.US_EPA, 201, "Very Unhealthy"), (aqi.US_EPA, 301, "Hazardous"),
    (aqi.US_EPA, 650, "Hazardous"),
    (aqi.IN_NAQI, 50, "Good"), (aqi.IN_NAQI, 51, "Satisfactory"), (aqi.IN_NAQI, 101, "Moderately polluted"),
    (aqi.IN_NAQI, 200, "Moderately polluted"), (aqi.IN_NAQI, 201, "Poor"), (aqi.IN_NAQI, 301, "Very poor"),
    (aqi.IN_NAQI, 401, "Severe"),
    (aqi.EU_EAQI, 20, "Good"), (aqi.EU_EAQI, 20.5, "Fair"), (aqi.EU_EAQI, 59, "Moderate"), (aqi.EU_EAQI, 79, "Poor"),
    (aqi.EU_EAQI, 99, "Very poor"), (aqi.EU_EAQI, 130, "Extremely poor"),
])
def test_category_boundaries(standard, value, label):
    assert standard.category(value).label == label


@pytest.mark.parametrize("value", [None, float("nan"), np.nan])
def test_missing_value_has_no_category(value):
    for s in aqi.STANDARDS.values():
        assert s.category(value) is None


def test_every_standard_has_six_ordered_categories_with_text_labels():
    for s in aqi.STANDARDS.values():
        assert [c.index for c in s.categories] == list(range(6))
        assert all(c.label and c.general and c.sensitive for c in s.categories)
        uppers = [c.upper for c in s.categories[:-1]]
        assert uppers == sorted(uppers) and s.categories[-1].upper is None


def test_guidance_never_gets_more_permissive_as_air_worsens():
    rank = {aqi.OK: 0, aqi.CAUTION: 1, aqi.AVOID: 2}
    for s in aqi.STANDARDS.values():
        for heavy in (True, False):
            for sensitive in (True, False):
                levels = [rank[c.guidance(heavy, sensitive)] for c in s.categories]
                assert levels == sorted(levels), (s.code, heavy, sensitive)
                # Sensitive guidance is at least as cautious as general, and heavy exertion as light.
                for c in s.categories:
                    assert rank[c.guidance(heavy, True)] >= rank[c.guidance(heavy, False)]
                    assert rank[c.guidance(True, sensitive)] >= rank[c.guidance(False, sensitive)]


def test_standards_have_distinct_names_and_columns():
    names = {s.name for s in aqi.STANDARDS.values()}
    cols = {s.column for s in aqi.STANDARDS.values()}
    assert len(names) == len(cols) == 3
    assert aqi.IN_NAQI.computed_by == "Cloudzy" and aqi.US_EPA.computed_by == "Open-Meteo"


# --- India NAQI ------------------------------------------------------------------------------------

@pytest.mark.parametrize("pollutant, conc, expected", [
    ("pm2_5", 0, 0), ("pm2_5", 30, 50), ("pm2_5", 45, 49 / 29 * 14 + 51), ("pm2_5", 60, 100),
    ("pm2_5", 90, 200), ("pm2_5", 250, 400),
    ("pm10", 300, 250), ("pm10", 100, 100), ("pm10", 430, 400),
    ("nitrogen_dioxide", 180, 200), ("ozone", 168, 200), ("sulphur_dioxide", 40, 50),
    ("carbon_monoxide", 2.0, 100), ("carbon_monoxide", 17, 300),
])
def test_naqi_sub_index_matches_cpcb_breakpoints(pollutant, conc, expected):
    assert aqi.naqi_sub_index(pollutant, conc) == pytest.approx(expected, abs=0.01)


def test_naqi_values_between_published_bands_stay_in_band():
    # CPCB lists 0-30 and 31-60: 30.5 belongs to neither, and must not produce an index below 51 or above 100.
    assert aqi.naqi_sub_index("pm2_5", 30.5) == 51


def test_naqi_severe_has_no_invented_upper_number():
    assert aqi.naqi_sub_index("pm2_5", 251) == aqi.SEVERE_FLOOR
    assert aqi.naqi_sub_index("pm2_5", 900) == aqi.SEVERE_FLOOR
    assert aqi.index_text(aqi.IN_NAQI, 401, open_ended=True) == "401+"
    assert aqi.naqi_sub_index("pm2_5", None) is None and aqi.naqi_sub_index("pm2_5", -1) is None


def _hours(n, **cols):
    idx = pd.date_range("2026-10-20", periods=n, freq="h", tz="Asia/Kolkata")
    return pd.DataFrame({k: (v if isinstance(v, list) else [v] * n) for k, v in cols.items()}, index=idx)


FULL = {"pm2_5": 45.0, "pm10": 50.0, "nitrogen_dioxide": 30.0, "ozone": 40.0, "sulphur_dioxide": 10.0,
        "carbon_monoxide": 800.0}


def test_naqi_needs_16_hours_for_a_24_hour_average():
    df = aqi.naqi_frame(_hours(24, **FULL))
    assert df["naqi"].iloc[:15].isna().all()          # PM needs 16 hours; ozone/CO alone aren't enough
    assert df["naqi"].iloc[15] == round(49 / 29 * 14 + 51)
    assert df["naqi_dominant"].iloc[20] == "pm2_5"


def test_naqi_converts_co_from_micrograms_to_milligrams():
    df = aqi.naqi_frame(_hours(24, **(FULL | {"carbon_monoxide": 17000.0, "pm2_5": 10.0, "pm10": 10.0})))
    assert df["naqi_carbon_monoxide"].iloc[-1] == pytest.approx(300)    # 17 mg/m³ is the top of "Poor"
    assert df["naqi_dominant"].iloc[-1] == "carbon_monoxide"


def test_naqi_requires_three_pollutants_including_pm():
    no_pm = {k: v for k, v in FULL.items() if k not in ("pm2_5", "pm10")}
    df = aqi.naqi_frame(_hours(24, **no_pm, pm2_5=None, pm10=None))
    assert df["naqi"].isna().all() and "PM2.5 and PM10" in df["naqi_reason"].iloc[-1]
    two = aqi.naqi_frame(_hours(24, pm2_5=45.0, nitrogen_dioxide=30.0))
    assert two["naqi"].isna().all() and "only 2 pollutant" in two["naqi_reason"].iloc[-1]
    three = aqi.naqi_frame(_hours(24, pm2_5=45.0, nitrogen_dioxide=30.0, sulphur_dioxide=10.0))
    assert three["naqi"].iloc[-1] == round(49 / 29 * 14 + 51)


def test_naqi_severe_is_flagged_open_ended():
    df = aqi.naqi_frame(_hours(24, **(FULL | {"pm2_5": 300.0})))
    assert df["naqi"].iloc[-1] == aqi.SEVERE_FLOOR and bool(df["naqi_open"].iloc[-1])
    assert aqi.IN_NAQI.category(df["naqi"].iloc[-1]).label == "Severe"


def test_naqi_uses_rolling_averages_not_hourly_values():
    pm = [30.0] * 23 + [300.0]                     # one spike: the 24-hour mean is 41.25, not 300
    df = aqi.naqi_frame(_hours(24, **(FULL | {"pm2_5": pm, "pm10": 20.0})))
    assert df["naqi_pm2_5"].iloc[-1] == pytest.approx(49 / 29 * (41.25 - 31) + 51)


def test_naqi_ignores_missing_hours_within_the_minimum():
    pm = [45.0] * 24
    for i in range(0, 8):
        pm[i] = math.nan                          # 16 of 24 present: still allowed
    df = aqi.naqi_frame(_hours(24, **(FULL | {"pm2_5": pm})))
    assert df["naqi_pm2_5"].iloc[-1] == pytest.approx(49 / 29 * 14 + 51)


# --- Regions and labels ------------------------------------------------------------------------------

@pytest.mark.parametrize("country, code", [("IN", "in_naqi"), ("in", "in_naqi"), ("US", "us_epa"), ("PR", "us_epa"),
                                           ("FR", "eu_eaqi"), ("NO", "eu_eaqi"), ("GB", "eu_eaqi"),
                                           ("BR", "us_epa"), (None, "us_epa")])
def test_default_standard_by_country(country, code):
    standard, note = aqi.default_standard(country)
    assert standard.code == code and note


def test_uk_and_unsupported_countries_explain_the_choice():
    assert "DAQI" in aqi.default_standard("GB")[1]
    assert "reference scale" in aqi.default_standard("BR")[1]


def test_saved_choice_overrides_region_and_auto_falls_back():
    assert aqi.resolve_standard("eu_eaqi", "IN")[0] is aqi.EU_EAQI
    assert aqi.resolve_standard("auto", "IN")[0] is aqi.IN_NAQI
    assert aqi.resolve_standard("bogus", "US")[0] is aqi.US_EPA


def test_dominant_pollutant_only_when_sub_indices_explain_the_total():
    row = pd.Series({"us_aqi": 120, "us_aqi_pm2_5": 120, "us_aqi_pm10": 60, "us_aqi_ozone": 30})
    assert aqi.dominant_pollutant(aqi.US_EPA, row) == "pm2_5"
    assert aqi.dominant_pollutant(aqi.US_EPA, pd.concat([row.drop("us_aqi"), pd.Series({"us_aqi": 150})])) is None
    assert aqi.dominant_pollutant(aqi.US_EPA, pd.Series({"us_aqi": np.nan})) is None


def test_categories_table_lists_guidance_for_both_audiences():
    t = aqi.categories_table(aqi.US_EPA)
    assert list(t["Category"])[2] == "Unhealthy for Sensitive Groups" and list(t["Index"])[:2] == ["0–50", "51–100"]
    assert list(aqi.categories_table(aqi.EU_EAQI)["Index"])[:2] == ["0–20", "20–40"]
