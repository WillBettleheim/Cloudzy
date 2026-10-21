"""Live integration tests against the real Open-Meteo APIs (Phase F).

Skipped by default. Run with:  python -m pytest -m integration

They catch upstream changes that offline tests cannot: renamed fields, new
units, missing variables, or a model no longer being served. Each test makes
one or two small requests, well inside Open-Meteo's free limits.
"""

from datetime import date, timedelta

import pandas as pd
import pytest
import requests

from src import config
from src.data_collection import build_params, check_response, fetch_json
from src.data_validation import validate_and_clean, validate_metadata
from src.provider import fetch_forecast, search_locations
from src.service import fetch_live

pytestmark = pytest.mark.integration
VARS = list(config.HOURLY_VARIABLES)


@pytest.fixture(scope="module")
def session():
    with requests.Session() as s:
        yield s


@pytest.mark.parametrize("source", [config.REANALYSIS, config.LIVE_ARCHIVE], ids=lambda s: s.model)
def test_archive_sources_return_valid_hourly_data(source, session):
    day = date.today() - timedelta(days=10)
    params = build_params(config.DELHI, day, day, VARS, source.model)
    payload = fetch_json(source.url, params, session=session)
    check_response(payload, VARS)
    meta = {k: v for k, v in payload.items() if k != "hourly"} | {"file": source.model}
    validate_metadata([meta], config.HOURLY_VARIABLES)
    df, report = validate_and_clean(pd.DataFrame(payload["hourly"]), VARS, config.PLAUSIBLE_RANGES)
    assert len(df) == 24
    assert all(report.missing_values[v] == 0 for v in VARS), report.missing_values


def test_live_forecast_inputs_validate(session, tmp_path):
    df, _ = fetch_live(session=session, snapshot_dir=tmp_path)
    now = pd.Timestamp.now(tz="UTC").floor("h")
    assert now in df.index
    assert df.loc[:now].iloc[-25:][VARS].notna().all().all()
    assert list(tmp_path.glob("*.json"))


def test_provider_forecast_and_geocoding(session):
    places = search_locations("New Delhi", session=session)
    assert places and places[0].country == "India"
    f = fetch_forecast(places[0], session=session)
    assert len(f.daily) == 7 and len(f.hourly) == 168
    assert f.hourly["temperature_2m"].notna().all()
    assert str(f.hourly.index.tz) == "Asia/Kolkata"


def test_geocoding_no_match_is_empty(session):
    assert search_locations("Qzxwvplace", session=session) == []


def test_air_quality_api_returns_valid_cams_data(session):
    """Units, the US AQI/European AQI fields, a multi-day forecast and enough history for India's NAQI."""
    from src import aqi
    from src.air_quality import fetch_air_quality, reading
    from src.provider import Place
    place = Place("New Delhi", 28.6139, 77.2090, "Asia/Kolkata", "India", None, None, "IN")
    data = fetch_air_quality(place, session=session)
    now = pd.Timestamp.now(tz="UTC")
    assert data.coverage_end > now + pd.Timedelta(hours=72)            # CAMS forecasts run about 4-5 days
    assert data.hourly["pm2_5"].notna().sum() > 96
    for standard in aqi.STANDARDS.values():
        assert reading(data, standard, now).value is not None, standard.code


@pytest.mark.parametrize("name", ["usgs", "gdacs", "nhc", "nws", "usgs_volcano", "tsunami"])
def test_hazard_feeds_answer_with_parseable_data(name):
    """Each connected disaster feed still answers in the format its parser expects (FIRMS's 11 MB file and the
    18 MB drought map are left out to stay light)."""
    from src.hazards import gdacs, http, nhc, nws, tsunami, usgs, volcanoes
    fetch = {"usgs": lambda: usgs.fetch("day"), "gdacs": lambda: gdacs._fetch_type("TC", 30, pd.Timestamp.now(tz="UTC"), None),
             "nhc": lambda: nhc.parse_current(http.get(nhc.CURRENT_URL)),
             "nws": nws.fetch, "usgs_volcano": volcanoes.fetch_usgs, "tsunami": tsunami.fetch}[name]
    result = fetch()
    assert result.warnings == [] or all(isinstance(w, str) for w in result.warnings)
    for e in result.events:
        assert e.id and e.source and e.kind and (e.lat is None or -90 <= e.lat <= 90)
