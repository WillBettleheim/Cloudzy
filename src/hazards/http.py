"""One HTTP helper for the hazard providers: JSON, text or raw bytes, with the same retry rules as
src.data_collection.fetch_json (timeouts, exponential backoff on network errors and 5xx, HTTP 429 with
Retry-After, immediate failure on other 4xx) and a User-Agent that identifies Cloudzy, as NWS, USGS and
Nominatim ask."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from src.data_collection import _error_reason, _retry_after_s

logger = logging.getLogger(__name__)

USER_AGENT = "Cloudzy/2.0 (student weather app; https://github.com/WillBettleheim/Cloudzy)"
TIMEOUT_S = 15
RETRIES = 2
MAX_WAIT_S = 5
MAX_RESPONSE_BYTES = 64_000_000   # the largest feed (24 h of global fire detections) is a few MB


class FeedError(RuntimeError):
    """A provider couldn't be reached or returned something unusable."""


def get(url: str, params: dict | None = None, *, kind: str = "json", session: requests.Session | None = None,
        timeout: float = TIMEOUT_S, retries: int = RETRIES, headers: dict | None = None) -> Any:
    """GET `url`. kind: "json" (parsed), "text" or "bytes"."""
    http = session or requests.Session()
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    last = "no attempt made"
    for attempt in range(1, retries + 1):
        wait = 2.0 ** attempt
        try:
            resp = http.get(url, params=params, timeout=timeout, headers=hdrs)
        except requests.Timeout:
            last = f"no response within {timeout:.0f} s"
        except requests.ConnectionError as exc:
            last = f"couldn't connect ({exc.__class__.__name__})"
        else:
            if resp.status_code == 204:          # "no content": the query matched nothing
                return None
            if resp.status_code == 200:
                declared = (getattr(resp, "headers", None) or {}).get("Content-Length")
                if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                    raise FeedError(f"response too large ({int(declared):,} bytes)")
                if kind == "bytes":
                    return resp.content
                if kind == "text":
                    return resp.text
                try:
                    return resp.json()
                except ValueError as exc:
                    raise FeedError(f"response is not valid JSON: {resp.text[:120]!r}") from exc
            reason = _error_reason(resp)
            if resp.status_code == 429:
                retry_after = _retry_after_s(resp)
                last = "rate-limited (HTTP 429)"
                if retry_after is not None:
                    if retry_after > MAX_WAIT_S:
                        raise FeedError(f"rate-limited (HTTP 429); the provider asks to wait {retry_after:.0f} s")
                    wait = retry_after
            elif resp.status_code >= 500:
                last = f"temporarily unavailable (HTTP {resp.status_code})"
            else:
                raise FeedError(f"HTTP {resp.status_code}: {str(reason)[:160]}")
        logger.warning("%s attempt %d/%d failed: %s", url, attempt, retries, last)
        if attempt < retries:
            time.sleep(min(wait, MAX_WAIT_S))
    raise FeedError(f"gave up after {retries} attempts: {last}")
