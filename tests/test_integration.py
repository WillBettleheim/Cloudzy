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
