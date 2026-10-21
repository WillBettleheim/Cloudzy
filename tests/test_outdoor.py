"""Outdoor Intelligence rule engine: hazards, activity rules, missing data, windows, explanations and plans."""

import pandas as pd
import pytest

from src import air_alerts, aqi
from src import outdoor as O
from src import units as U
from src.air_quality import parse_air_quality
from src.preferences import AlertThresholds
from src.provider import Place, parse_forecast
from tests.aq_fixtures import aq_payload
from tests.test_provider import payload

DELHI = Place("New Delhi", 28.6358, 77.2244, "Asia/Kolkata", "India", "Delhi", 317797, "IN")
NOW = pd.Timestamp("2026-10-19T06:40Z")     # 12:10 IST
RUN, WALK, BIKE, CASUAL = (O.ACTIVITIES[k] for k in ("running", "walking", "cycling", "casual"))
T0 = pd.Timestamp("2026-10-19T07:00", tz="Asia/Kolkata")

PLEASANT = {"aqi": 30.0, "pm2_5": 8.0, "temperature_2m": 18.0, "relative_humidity_2m": 50.0,
            "apparent_temperature": 18.0, "weather_code": 1, "precipitation_probability": 5.0, "precipitation": 0.0,
            "wind_speed_10m": 10.0, "wind_gusts_10m": 18.0, "uv_index": 2.0, "is_day": 1}


def hour(**kw):
    row = dict(PLEASANT) | kw
    row["heat_index"] = O.heat_index_c(row["temperature_2m"], row["relative_humidity_2m"])
    row["wind_chill"] = O.wind_chill_c(row["temperature_2m"], row["wind_speed_10m"])
    return pd.Series(row, name=T0)


def check(activity=RUN, standard=aqi.US_EPA, sensitivity="standard", units=U.METRIC, **kw):
    return O.assess_hour(hour(**kw), activity, standard, sensitivity, units=units)


def kinds(h, level=None):
    return {f.kind for f in h.factors if level is None or f.level == level}


# --- Physical indices ------------------------------------------------------------------------------

@pytest.mark.parametrize("temp_f, rh, expected_f", [(90, 50, 95), (100, 40, 109), (86, 90, 105), (80, 40, 80)])
def test_heat_index_matches_nws_table(temp_f, rh, expected_f):
    hi_c = O.heat_index_c((temp_f - 32) * 5 / 9, rh)
    assert hi_c * 9 / 5 + 32 == pytest.approx(expected_f, abs=1.5)


def test_heat_categories():
    assert [O.heat_category(c) for c in (20, 27, 33, 40, 52)] == [0, 1, 2, 3, 4]
    assert O.heat_category(None) == 0 and O.heat_index_c(30, None) is None


def test_wind_chill_matches_eccc_table_and_is_undefined_when_warm_or_calm():
    assert O.wind_chill_c(-20, 30) == pytest.approx(-33, abs=0.6)
    assert O.wind_chill_c(15, 30) is None and O.wind_chill_c(-5, 3) is None


# --- Hazards override everything else --------------------------------------------------------------

def test_pleasant_hour_is_suitable_with_reasons():
    h = check()
    assert h.level == O.OK and "air" in kinds(h, O.OK)


@pytest.mark.parametrize("code", [95, 96, 99])
def test_thunderstorm_is_never_outweighed_by_good_conditions(code):
    for act in O.ACTIVITIES.values():
        h = check(activity=act, weather_code=code)
        assert h.level == O.AVOID and "storm" in kinds(h, O.AVOID)


def test_freezing_rain_and_dangerous_heat_are_critical():
    assert check(weather_code=66).level == O.AVOID
    hot = check(temperature_2m=40.0, relative_humidity_2m=45.0)            # heat index ~48 °C: NWS Danger
    assert hot.level == O.AVOID and "heat" in kinds(hot, O.AVOID)
    assert check(activity=CASUAL, temperature_2m=40.0, relative_humidity_2m=45.0).level == O.AVOID


def test_extreme_cold_is_critical():
    h = check(temperature_2m=-22.0, wind_speed_10m=30.0, apparent_temperature=-30.0)
    assert h.level == O.AVOID and "cold" in kinds(h, O.AVOID)
    assert check(temperature_2m=-12.0, wind_speed_10m=5.0).level == O.CAUTION


# --- Air quality follows the standard's guidance ---------------------------------------------------

def test_us_unhealthy_air_cautions_runners_but_not_walkers():
    assert check(activity=RUN, aqi=170).level == O.CAUTION
    assert check(activity=WALK, aqi=170).level == O.OK
    assert check(activity=RUN, aqi=250).level == O.AVOID              # Very Unhealthy
    assert check(activity=WALK, aqi=350).level == O.AVOID             # Hazardous


def test_conservative_setting_uses_sensitive_group_guidance():
    assert check(aqi=120).level == O.OK                               # USG: fine for everyone else
    h = check(aqi=120, sensitivity="conservative")
    assert h.level == O.CAUTION and "sensitive groups" in next(f for f in h.factors if f.kind == "air").rule


def test_conservative_setting_starts_heat_cautions_a_category_earlier():
    warm = dict(temperature_2m=29.0, relative_humidity_2m=40.0)       # heat index ~28 °C: NWS Caution
    assert check(activity=WALK, **warm).level == O.OK
    assert check(activity=WALK, sensitivity="conservative", **warm).level == O.CAUTION


def test_india_naqi_guidance_and_open_ended_severe():
    assert check(standard=aqi.IN_NAQI, aqi=150).level == O.CAUTION   # Moderately polluted, heavy exertion
    h = check(standard=aqi.IN_NAQI, aqi=401, naqi_open=True)
    assert h.level == O.AVOID and "401+" in next(f.text for f in h.factors if f.kind == "air")


def test_air_factor_names_the_standard():
    for s in aqi.STANDARDS.values():
        assert next(f for f in check(standard=s).factors if f.kind == "air").text.startswith(s.short)


# --- Activity-specific rules -----------------------------------------------------------------------

def test_cycling_is_more_wind_sensitive():
    windy = dict(wind_speed_10m=42.0, wind_gusts_10m=55.0)
    assert check(activity=BIKE, **windy).level == O.CAUTION
    assert check(activity=RUN, **windy).level == O.OK
    assert check(activity=BIKE, wind_gusts_10m=65.0).level == O.AVOID
    assert check(activity=RUN, wind_gusts_10m=65.0).level == O.CAUTION
    assert "wind" in kinds(check(activity=BIKE), O.OK)                # light wind supports cycling


def test_heat_caution_depends_on_exertion():
    warm = dict(temperature_2m=29.0, relative_humidity_2m=40.0)
    assert check(activity=RUN, **warm).level == O.CAUTION
    assert check(activity=WALK, **warm).level == O.OK


def test_heavy_snow_stops_cycling_but_cautions_walkers():
    assert check(activity=BIKE, weather_code=75).level == O.AVOID
    assert check(activity=WALK, weather_code=75).level == O.CAUTION


def test_rain_and_uv_are_cautions_not_bans():
    assert check(precipitation_probability=70.0).level == O.CAUTION
    assert check(precipitation=9.0, weather_code=65).level == O.CAUTION
    h = check(uv_index=9.0)
    assert h.level == O.CAUTION and "uv" in kinds(h, O.CAUTION)
    assert check(uv_index=5.0).level == O.OK and "uv" in kinds(check(uv_index=5.0), "info")


# --- Missing data ----------------------------------------------------------------------------------

@pytest.mark.parametrize("missing", ["aqi", "temperature_2m", "weather_code"])
def test_missing_essential_measurement_is_unavailable_not_guessed(missing):
    h = check(**{missing: float("nan")})
    assert h.level == O.UNAVAILABLE and kinds(h) == {"data"}


def test_missing_naqi_explains_cpcb_rule():
    h = check(standard=aqi.IN_NAQI, aqi=float("nan"), naqi_reason="PM2.5 and PM10 are both unavailable")
    assert h.level == O.UNAVAILABLE and "PM2.5 and PM10" in h.factors[0].text


def test_missing_optional_measurements_are_noted_but_do_not_block():
    h = check(activity=BIKE, wind_speed_10m=float("nan"), wind_gusts_10m=float("nan"), uv_index=float("nan"))
    assert h.level == O.OK and any("Wind data are missing" in f.text for f in h.factors)
    hot = check(temperature_2m=33.0, relative_humidity_2m=float("nan"))
    assert any("heat index can't be calculated" in f.text for f in hot.factors)


def test_text_follows_display_units():
    h = check(units=U.IMPERIAL, temperature_2m=40.0, relative_humidity_2m=45.0)
    heat = next(f.text for f in h.factors if f.kind == "heat")
    assert "°F" in heat and "°C" not in heat


# --- Windows on real-shaped forecasts --------------------------------------------------------------

def table(aq_over=None, wx=None, standard=aqi.US_EPA):
    wx_payload = payload()
    for k, v in (wx or {}).items():
        wx_payload["hourly"][k] = v if isinstance(v, list) else [v] * len(wx_payload["hourly"]["time"])
    f = parse_forecast(wx_payload, DELHI, "2026-10-19T06:30:00+00:00")
    aq = parse_air_quality(aq_payload(overrides=aq_over), DELHI, "2026-10-19T06:30:00+00:00")
    return O.combined_table(aq, f, standard), aq, f


def test_combined_table_aligns_instants_and_marks_hours_past_air_quality_coverage():
    t, aq, f = table()
    assert t.index.equals(f.hourly.index)
    assert t.loc[aq.coverage_end, "aqi"] == 68.0
    assert t.loc[t.index > aq.coverage_end, "aqi"].isna().all()


def test_windows_respect_duration_and_preferred_hours():
    t, _, _ = table()
    hs = O.assess(t, RUN, aqi.US_EPA)
    ws = O.windows(hs, 90, 6, 10, aqi.US_EPA, NOW)
    assert ws and all(len(w.hours) == 2 for w in ws)
    assert all(6 <= w.start.hour and w.end.hour <= 10 for w in ws)
    assert all(w.start >= NOW.tz_convert("Asia/Kolkata").floor("h") for w in ws)


def test_window_takes_its_worst_hour():
    codes = [0] * 168
    codes[24 + 8] = 95                                     # a storm at 08:00 on day 2
    t, _, _ = table(wx={"weather_code": codes})
    hs = O.assess(t, WALK, aqi.US_EPA)
    w = next(w for w in O.windows(hs, 120, 7, 10, aqi.US_EPA, NOW) if w.start.hour == 7 and w.start.day == 20)
    assert w.level == O.AVOID and w.limiting[0].kind == "storm"


def test_ranking_prefers_cleaner_air_among_equally_suitable_windows():
    pm = [20.0] * 216
    for i in range(48 + 24 + 6, 48 + 24 + 9):              # tomorrow 06:00-09:00 is cleaner
        pm[i] = 5.0
    t, _, _ = table(aq_over={"pm2_5": pm, "us_aqi": 40.0, "us_aqi_pm2_5": 40.0})
    hs = O.assess(t, RUN, aqi.US_EPA)
    best = O.best_windows(O.windows(hs, 60, 6, 21, aqi.US_EPA, NOW), RUN, aqi.US_EPA)
    tomorrow = next(w for w in best["by_day"] if w.start.day == 20)
    assert tomorrow.start.hour in (6, 7, 8) and tomorrow.level == O.OK


def test_hours_beyond_air_quality_coverage_are_never_recommended():
    t, aq, _ = table()
    hs = O.assess(t, RUN, aqi.US_EPA)
    best = O.best_windows(O.windows(hs, 60, 0, 24, aqi.US_EPA, NOW), RUN, aqi.US_EPA)
    assert all(w.end <= aq.coverage_end + pd.Timedelta(hours=1) for w in best["by_day"])
    assert all(h.level == O.UNAVAILABLE for h in hs if h.time > aq.coverage_end)


def test_coverage_note_explains_impossible_requests():
    t, _, _ = table()
    assert "shorter than the activity" in O.coverage_note(t, 120, NOW, 6, 7)
    assert O.coverage_note(t, 60, NOW, 6, 21) is None
    late = pd.Timestamp("2026-10-30T00:00Z")
    assert "no overlapping hours" in O.coverage_note(t, 60, late, 6, 21)


def test_long_lead_windows_say_they_are_less_certain():
    t, _, _ = table()
    hs = O.assess(t, WALK, aqi.US_EPA)
    late = [w for w in O.windows(hs, 60, 6, 21, aqi.US_EPA, NOW) if (w.start - NOW).total_seconds() > 80 * 3600]
    assert late and all(any("More than 3 days ahead" in n for n in w.notes) for w in late)


def test_explanations_are_grounded_and_avoid_causal_claims():
    us = [40.0] * 62 + [160.0] * 154
    t, _, _ = table(aq_over={"us_aqi": us, "us_aqi_pm2_5": us},
                    wx={"temperature_2m": [20.0 + i % 24 / 2 for i in range(168)]})
    hs = [h for h in O.assess(t, RUN, aqi.US_EPA) if h.time >= NOW.tz_convert("Asia/Kolkata").floor("h")]
    best = O.best_windows(O.windows(hs, 60, 6, 21, aqi.US_EPA, NOW), RUN, aqi.US_EPA)
    out = O.explain(t, hs, best["next_24h"], RUN, aqi.US_EPA, "standard", NOW)
    text = " ".join(i.text for i in out)
    assert "forecast to deteriorate" in text and "US AQI 40 (Good) now to 160 (Unhealthy)" in text
    assert not any(w in text.lower() for w in ("because", "due to", "disperse", "wash out", "safe"))
    assert all(i.measurements and i.basis in ("forecast", "now (model estimate)") for i in out)


def test_explanation_flags_sensitive_guidance_and_falls_back_without_data():
    t, _, _ = table(aq_over={"us_aqi": 120.0, "us_aqi_pm2_5": 120.0})
    hs = O.assess(t, RUN, aqi.US_EPA)
    out = O.explain(t, hs, None, RUN, aqi.US_EPA, "standard", NOW)
    assert any("sensitive groups" in i.text for i in out)
    empty = O.explain(t, hs, None, RUN, aqi.US_EPA, "standard", pd.Timestamp("2026-10-30T00:00Z"))
    assert len(empty) == 1 and "isn't enough" in empty[0].text


# --- Saved plans and forecast changes --------------------------------------------------------------

def test_plan_becoming_worse_triggers_one_alert_with_source_and_reason():
    t, aq, f = table()
    hs = O.assess(t, RUN, aqi.US_EPA)
    w = next(w for w in O.windows(hs, 60, 7, 9, aqi.US_EPA, NOW) if w.start.day == 20)
    assert w.level == O.OK
    plan = O.plan_record(DELHI, RUN, w, aqi.US_EPA, "standard", "2026-10-19T06:40:00+00:00")

    # Same forecast: no alert.
    alerts, results = air_alerts.plan_alerts([plan], aq, f, NOW, place=DELHI)
    assert not alerts and results[0]["status"] == "unchanged_or_better"

    # New forecast: unhealthy air in that hour.
    us = [68.0] * 216
    i = list(aq.hourly.index).index(w.start)
    us[i] = 180.0
    _, aq2, f2 = table(aq_over={"us_aqi": us})
    alerts, results = air_alerts.plan_alerts([plan], aq2, f2, NOW, place=DELHI)
    assert results[0]["status"] == "worse" and results[0]["level"] == O.CAUTION
    a = alerts[0]
    assert a.kind == "plan" and "CAMS" in a.reason and "US AQI 180" in a.reason
    again, _ = air_alerts.plan_alerts([plan], aq2, f2, NOW, place=DELHI)
    assert again[0].key == a.key                                         # same change: same key, notified once


def test_plans_for_other_places_and_past_plans_are_skipped():
    t, aq, f = table()
    hs = O.assess(t, RUN, aqi.US_EPA)
    w = O.windows(hs, 60, 7, 9, aqi.US_EPA, NOW)[0]
    plan = O.plan_record(DELHI, RUN, w, aqi.US_EPA, "standard", "x")
    paris = Place("Paris", 48.85, 2.35, "Europe/Paris")
    assert air_alerts.plan_alerts([plan], aq, f, NOW, place=paris) == ([], [])
    later = w.end + pd.Timedelta(hours=1)
    assert O.recheck_plan(plan, hs, aqi.US_EPA, later)["status"] == "past"


# --- Air-quality alerts --------------------------------------------------------------------------

def test_air_alert_uses_the_category_threshold_of_the_selected_standard():
    us = [40.0] * 62 + [160.0] * 154
    aq = parse_air_quality(aq_payload(overrides={"us_aqi": us}), DELHI, "2026-10-19T06:30:00+00:00")
    found = air_alerts.air_alerts(aq, NOW, aqi.US_EPA, AlertThresholds())
    a = next(a for a in found if a.kind == "air")
    assert a.title == "Air quality: Unhealthy" and "US AQI (EPA)" in a.reason and "retrieved" in a.reason
    assert "up to 160 US AQI" == a.value
    assert not [a for a in air_alerts.air_alerts(aq, NOW, aqi.US_EPA, AlertThresholds(aqi_category=4))
                if a.kind == "air"]


def test_particle_alert_uses_24_hour_mean_and_states_units():
    aq = parse_air_quality(aq_payload(overrides={"pm2_5": 50.0}), DELHI, "now")
    a = next(a for a in air_alerts.air_alerts(aq, NOW, aqi.US_EPA) if a.kind == "particles")
    assert "μg/m³" in a.value and "24-hour mean" in a.value
    calm = parse_air_quality(aq_payload(overrides={"pm2_5": 10.0}), DELHI, "now")
    assert not [a for a in air_alerts.air_alerts(calm, NOW, aqi.US_EPA) if a.kind == "particles"]


def test_disabled_categories_produce_no_alerts():
    aq = parse_air_quality(aq_payload(overrides={"pm2_5": 50.0, "us_aqi": 200.0}), DELHI, "now")
    assert air_alerts.air_alerts(aq, NOW, aqi.US_EPA, enabled={"rain"}) == []
