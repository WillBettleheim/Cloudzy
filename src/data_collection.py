"""Download and cache hourly historical weather from the Open-Meteo Archive API.

Design:
- Each calendar year is requested separately and cached as one raw JSON file in
  data/raw/. The file keeps the exact request parameters and the untouched API
  response, so the raw layer is a faithful record of what the API returned.
- The cache file name includes a hash of the request parameters. Changing the
  location, variables or model therefore produces a new file rather than
  silently reusing stale data.
- Re-running is cheap: cached years are not downloaded again unless --force.

Run:  python -m src.data_collection [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from src import config

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 60
MAX_RETRIES = 4
BACKOFF_BASE_S = 2.0


class DataCollectionError(RuntimeError):
    """Raised when the API cannot provide a usable response."""


def build_params(
    location: config.Location,
    start: date,
    end: date,
    variables: list[str],
    model: str,
) -> dict[str, Any]:
    return {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(variables),
        "models": model,
        "timezone": "GMT",
    }


def params_hash(params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:8]


def cache_path(location: config.Location, params: dict[str, Any], raw_dir: Path) -> Path:
    name = (
        f"{location.slug}_{params['models']}_{params['start_date']}_"
        f"{params['end_date']}_{params_hash(params)}.json"
    )
    return raw_dir / name


def year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into calendar-year chunks (inclusive bounds)."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(date(cursor.year, 12, 31), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def check_response(payload: Any, variables: list[str]) -> None:
    """Raise DataCollectionError if the payload is not a usable hourly response."""
    if not isinstance(payload, dict):
        raise DataCollectionError(f"Expected a JSON object, got {type(payload).__name__}")
    if payload.get("error"):
        raise DataCollectionError(f"API returned an error: {payload.get('reason', 'no reason given')}")
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise DataCollectionError("Response has no 'hourly' object")
    times = hourly.get("time")
    if not isinstance(times, list) or len(times) == 0:
        raise DataCollectionError("Response contains no hourly timestamps (empty dataset)")
    missing = [v for v in variables if v not in hourly]
    if missing:
        raise DataCollectionError(f"Response is missing requested variables: {missing}")
    for v in variables:
        if not isinstance(hourly[v], list) or len(hourly[v]) != len(times):
            raise DataCollectionError(
                f"Variable '{v}' has {len(hourly[v]) if isinstance(hourly[v], list) else 'no'} "
                f"values but there are {len(times)} timestamps"
            )


def fetch_json(url: str, params: dict[str, Any], session: requests.Session | None = None,
               timeout: float = REQUEST_TIMEOUT_S, retries: int = MAX_RETRIES, max_wait_s: float = 60.0) -> dict:
    """GET with retries and exponential backoff on network errors, 429 and 5xx.

    4xx errors other than 429 mean the request itself is wrong, so they fail
    immediately with the API's explanation. On 429 the server's Retry-After is
    respected. If it asks for longer than `max_wait_s`, we stop and say so
    instead of making a user wait. Batch downloads use the patient defaults; the
    app passes INTERACTIVE settings (short timeout, few retries).
    """
    http = session or requests.Session()
    last_error: str = "no attempt made"
    for attempt in range(1, retries + 1):
        wait = BACKOFF_BASE_S ** attempt
        try:
            resp = http.get(url, params=params, timeout=timeout)
        except requests.Timeout:
            last_error = f"the weather service didn't respond within {timeout:.0f} s"
            logger.warning("Attempt %d/%d timed out", attempt, retries)
        except requests.ConnectionError as exc:
            last_error = f"couldn't connect to the weather service ({exc.__class__.__name__}); check the internet connection"
            logger.warning("Attempt %d/%d failed: %s", attempt, retries, exc)
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise DataCollectionError(f"Response is not valid JSON: {resp.text[:200]!r}") from exc
            reason = _error_reason(resp)
            if resp.status_code == 429:
                retry_after = _retry_after_s(resp)
                last_error = "the weather service is rate-limiting requests (HTTP 429)" + (
                    f"; it asks to wait {retry_after:.0f} s" if retry_after else "")
                if retry_after is not None:
                    if retry_after > max_wait_s:
                        raise DataCollectionError(f"Rate limited: {last_error}. Please try again later.")
                    wait = retry_after
                logger.warning("Attempt %d/%d rate-limited", attempt, retries)
            elif resp.status_code >= 500:
                last_error = f"the weather service is temporarily unavailable (HTTP {resp.status_code}: {reason})"
                logger.warning("Attempt %d/%d got HTTP %d: %s", attempt, retries, resp.status_code, reason)
            else:
                raise DataCollectionError(f"HTTP {resp.status_code}: {reason}")
        if attempt < retries:
            time.sleep(min(wait, max_wait_s))
    raise DataCollectionError(f"Giving up after {retries} attempts: {last_error}")


# Settings for requests a person is waiting on: fail within ~25 s, not minutes.
INTERACTIVE = {"timeout": 10, "retries": 2, "max_wait_s": 5}


def _retry_after_s(resp: requests.Response) -> float | None:
    value = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


def _error_reason(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("reason", resp.text[:200]))
    except ValueError:
        return resp.text[:200]


def download_chunk(
    location: config.Location,
    start: date,
    end: date,
    variables: list[str],
    model: str,
    raw_dir: Path,
    force: bool = False,
    session: requests.Session | None = None,
    url: str = config.ARCHIVE_API_URL,
) -> Path:
    """Download one date range and cache it. Returns the cache file path."""
    params = build_params(location, start, end, variables, model)
    path = cache_path(location, params, raw_dir)
    if path.exists() and not force:
        logger.info("Cached: %s (skipping download)", path.name)
        return path

    logger.info("Downloading %s %s → %s", location.slug, start, end)
    payload = fetch_json(url, params, session=session)
    check_response(payload, variables)

    raw_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "source": "Open-Meteo Historical Weather API (https://open-meteo.com/)",
        "url": url,
        "request_params": params,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "response": payload,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record))
    tmp.replace(path)  # atomic: never leave a half-written cache file
    logger.info("Saved %d hourly rows to %s", len(payload["hourly"]["time"]), path.name)
    return path


def collect(
    location: config.Location = config.DELHI,
    start: date = config.START_DATE,
    end: date = config.END_DATE,
    variables: list[str] | None = None,
    source: config.DataSource = config.REANALYSIS,
    raw_dir: Path = config.RAW_DIR,
    force: bool = False,
) -> list[Path]:
    variables = variables or list(config.HOURLY_VARIABLES)
    latest_available = date.today() - timedelta(days=source.lag_days)
    if end > latest_available:
        raise ValueError(
            f"end date {end} is too recent; {source.model} data is complete only up to about {latest_available}"
        )
    session = requests.Session()
    return [
        download_chunk(location, s, e, variables, source.model, raw_dir, force=force,
                       session=session, url=source.url)
        for s, e in year_chunks(start, end)
    ]


def load_raw_record(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def record_to_frame(record: dict) -> pd.DataFrame:
    """Convert one cached record to a DataFrame without altering any values."""
    hourly = record["response"]["hourly"]
    return pd.DataFrame(hourly)


def load_raw_frames(paths: list[Path]) -> tuple[pd.DataFrame, list[dict]]:
    """Concatenate cached chunks. Returns (frame, list of response metadata)."""
    frames, metadata = [], []
    for p in paths:
        record = load_raw_record(p)
        frames.append(record_to_frame(record))
        meta = {k: v for k, v in record["response"].items() if k != "hourly"}
        meta["file"] = Path(p).name
        meta["retrieved_at_utc"] = record["retrieved_at_utc"]
        metadata.append(meta)
    if not frames:
        raise DataCollectionError("No raw files to load")
    return pd.concat(frames, ignore_index=True), metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-download even if cached")
    parser.add_argument("--source", choices=sorted(config.SOURCES), default=config.REANALYSIS.model)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    paths = collect(source=config.SOURCES[args.source], force=args.force)
    logger.info("Raw files ready: %s", [p.name for p in paths])


if __name__ == "__main__":
    main()
