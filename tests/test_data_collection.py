"""Tests for src.data_collection. No network access: HTTP is faked."""

from datetime import date

import pytest
import requests

from src import config, data_collection as dc

VARS = ["temperature_2m", "precipitation"]


def make_payload(n=3):
    return {
        "latitude": 28.5, "longitude": 77.25, "utc_offset_seconds": 0, "timezone": "GMT",
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C", "precipitation": "mm"},
        "hourly": {
            "time": [f"2024-01-01T{h:02d}:00" for h in range(n)],
            "temperature_2m": [10.0 + h for h in range(n)],
            "precipitation": [0.0] * n,
        },
    }


class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(dc.time, "sleep", lambda s: None)


def test_year_chunks_split_on_calendar_years():
    assert dc.year_chunks(date(2023, 6, 1), date(2025, 2, 1)) == [
        (date(2023, 6, 1), date(2023, 12, 31)),
        (date(2024, 1, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 2, 1)),
    ]


def test_year_chunks_rejects_reversed_range():
    with pytest.raises(ValueError):
        dc.year_chunks(date(2025, 1, 1), date(2024, 1, 1))


def test_params_hash_changes_with_variables():
    a = dc.build_params(config.DELHI, date(2024, 1, 1), date(2024, 1, 2), VARS, "era5")
    b = dc.build_params(config.DELHI, date(2024, 1, 1), date(2024, 1, 2), VARS[:1], "era5")
    assert dc.params_hash(a) != dc.params_hash(b)
    assert a["timezone"] == "GMT"


@pytest.mark.parametrize("payload, message", [
    ([], "JSON object"),
    ({"error": True, "reason": "bad var"}, "bad var"),
    ({"latitude": 1}, "no 'hourly'"),
    ({"hourly": {"time": []}}, "empty dataset"),
    ({"hourly": {"time": ["t"], "temperature_2m": [1]}}, "missing requested variables"),
    ({"hourly": {"time": ["t", "u"], "temperature_2m": [1], "precipitation": [0, 0]}}, "values but"),
])
def test_check_response_rejects_bad_payloads(payload, message):
    with pytest.raises(dc.DataCollectionError, match=message):
        dc.check_response(payload, VARS)


def test_fetch_retries_on_server_error_then_succeeds():
    session = FakeSession([FakeResponse(503, {"reason": "busy"}), requests.Timeout("slow"),
                           FakeResponse(200, make_payload())])
    assert dc.fetch_json("u", {}, session=session)["hourly"]["time"][0] == "2024-01-01T00:00"
    assert session.calls == 3


def test_fetch_fails_fast_on_client_error():
    session = FakeSession([FakeResponse(400, {"error": True, "reason": "Invalid value"})])
    with pytest.raises(dc.DataCollectionError, match="HTTP 400: Invalid value"):
        dc.fetch_json("u", {}, session=session)
    assert session.calls == 1


def test_fetch_gives_up_after_max_retries():
    session = FakeSession([requests.ConnectionError("down")] * dc.MAX_RETRIES)
    with pytest.raises(dc.DataCollectionError, match="Giving up"):
        dc.fetch_json("u", {}, session=session)


def test_fetch_rejects_invalid_json():
    session = FakeSession([FakeResponse(200, None, text="<html>")])
    with pytest.raises(dc.DataCollectionError, match="not valid JSON"):
        dc.fetch_json("u", {}, session=session)


def test_download_chunk_caches_and_reuses(tmp_path):
    session = FakeSession([FakeResponse(200, make_payload())])
    args = (config.DELHI, date(2024, 1, 1), date(2024, 1, 1), VARS, "era5", tmp_path)
    path = dc.download_chunk(*args, session=session)
    assert path.exists() and session.calls == 1

    again = dc.download_chunk(*args, session=session)  # would raise if it hit the session
    assert again == path and session.calls == 1

    frame, meta = dc.load_raw_frames([path])
    assert list(frame.columns) == ["time", *VARS] and len(frame) == 3
    assert meta[0]["utc_offset_seconds"] == 0 and "hourly" not in meta[0]


def test_collect_rejects_end_date_beyond_archive_lag(tmp_path):
    with pytest.raises(ValueError, match="too recent"):
        dc.collect(start=date(2024, 1, 1), end=date.today(), raw_dir=tmp_path)
