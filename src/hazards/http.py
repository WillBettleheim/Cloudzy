"""One HTTP helper for the hazard providers: JSON, text or raw bytes, with the same retry rules as
src.data_collection.fetch_json (timeouts, exponential backoff on network errors and 5xx, HTTP 429 with
Retry-After, immediate failure on other 4xx) and a User-Agent that identifies Cloudzy, as NWS, USGS and
Nominatim ask."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from src.data_collection import _error_reason, _retry_after_s

logger = logging.getLogger(__name__)

USER_AGENT = "Cloudzy/2.0 (student weather app; https://github.com/WillBettleheim/Cloudzy)"
TIMEOUT_S = 15
RETRIES = 2
MAX_WAIT_S = 5
MAX_RESPONSE_BYTES = 64_000_000   # the largest feed (24 h of global fire detections) is a few MB

# The only hosts this helper may contact. Some feeds hand Cloudzy further URLs (NHC storm files, GDACS geometry);
# those, and every redirect, must also be HTTPS to one of these hosts, so a tampered feed can't make Cloudzy
# request an arbitrary or internal address (server-side request forgery).
ALLOWED_HOSTS = frozenset({
    "earthquake.usgs.gov", "volcanoes.usgs.gov",              # USGS earthquakes, seismicity, volcano alert levels
    "www.gdacs.org",                                          # GDACS events and geometry
    "www.nhc.noaa.gov",                                       # NHC storms, cones and tracks
    "api.weather.gov",                                        # NWS alerts
    "www.tsunami.gov",                                        # NOAA tsunami warning centres
    "firms.modaps.eosdis.nasa.gov",                           # NASA FIRMS fire detections
    "droughtmonitor.unl.edu",                                 # U.S. Drought Monitor
    "webservices.volcano.si.edu",                             # Smithsonian GVP catalogue
    "mesonet.agron.iastate.edu",                              # Iowa Environmental Mesonet (station readings)
})


def check_url(url: str) -> str:
    """`url` if it is HTTPS to an allowed host; FeedError otherwise."""
    parts = urlsplit(str(url))
    if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS or parts.username or parts.port:
        raise FeedError(f"refused to fetch {parts.scheme or '?'}://{parts.hostname or '?'}: not an allowed data host")
    return url


class _SafeSession(requests.Session):
    """A session that checks every redirect against ALLOWED_HOSTS before following it."""

    def get_redirect_target(self, resp):
        target = super().get_redirect_target(resp)
        if target:
            check_url(urljoin(resp.url, target))
        return target


class FeedError(RuntimeError):
    """A provider couldn't be reached or returned something unusable."""


def get(url: str, params: dict | None = None, *, kind: str = "json", session: requests.Session | None = None,
        timeout: float = TIMEOUT_S, retries: int = RETRIES, headers: dict | None = None) -> Any:
    """GET `url` (HTTPS to an allowed host only). kind: "json" (parsed), "text" or "bytes"."""
    check_url(url)
    http = session or _SafeSession()
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
