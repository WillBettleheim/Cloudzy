"""Device location: one-time browser geolocation and reverse geocoding.

How it works
1. The page mounts a tiny Streamlit (components v2) script that runs in the
   app page itself, not in a sandboxed iframe. When asked, it calls
   `navigator.geolocation.getCurrentPosition` once. The browser shows its own
   permission prompt; nothing here can or tries to bypass it.
2. The script reports exactly one result per request: coordinates, or a
   reason (permission denied, position unavailable, timeout, unsupported
   browser, insecure context, or no answer to the permission prompt).
3. Python validates the coordinates and fetches the weather for those exact
   coordinates through the existing Open-Meteo client. A readable place name
   comes from OpenStreetMap's Nominatim reverse geocoder (Open-Meteo has no
   reverse lookup), or a neutral "Your current location" if that fails.

Privacy
- **One-time lookups only:** there is no background tracking, and a new lookup
  happens only when the user asks.
- **Nominatim:** receives coordinates rounded to 2 decimals (about 1 km), enough
  for a city name. Results are cached and calls are throttled to Nominatim's
  1 request per second.
- **Weather request:** receives the exact coordinates, as any weather lookup
  for a place does.
- **Never stored or exposed:** coordinates stay in the browser session only.
  They are never written to disk, logs or the URL.

Request handling (`start`, `handle`, `cancel`, `use_manual`):
- **Numbered requests:** every request gets a number (nonce). A result is
  accepted only if it answers the request currently pending.
- **No duplicates:** a second click while a request is pending reuses it.
- **No stale results:** a late answer after the user chose a city manually, or
  after a newer request, is ignored.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import OrderedDict
from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

from src.data_collection import DataCollectionError, fetch_json
from src.provider import Place

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_POLICY = "https://operations.osmfoundation.org/policies/nominatim/"
OSM_ATTRIBUTION = "Place names © OpenStreetMap contributors (ODbL), via Nominatim"
USER_AGENT = "Cloudzy/2.0 (student weather app; https://github.com/WillBettleheim/Cloudzy)"
NEUTRAL_LABEL = "Your current location"
REVERSE_DECIMALS = 2          # ~1.1 km: enough for a city name, less revealing than GPS precision
MIN_INTERVAL_S = 1.1          # Nominatim: at most 1 request per second
STATE_KEY = "cz_geo"


@dataclass(frozen=True)
class GeoOptions:
    timeout_ms: int = int(os.environ.get("CLOUDZY_GEO_TIMEOUT_MS", 15000))
    high_accuracy: bool = os.environ.get("CLOUDZY_GEO_HIGH_ACCURACY", "1") == "1"
    maximum_age_ms: int = 60_000       # accept a fix up to a minute old: faster, less battery
    permission_wait_ms: int = 60_000   # how long to wait for the user to answer the browser prompt

    def as_data(self, nonce: int) -> dict:
        return {"nonce": nonce, "timeoutMs": self.timeout_ms, "highAccuracy": self.high_accuracy,
                "maximumAgeMs": self.maximum_age_ms, "permissionWaitMs": self.permission_wait_ms}


@dataclass(frozen=True)
class DeviceFix:
    latitude: float
    longitude: float
    accuracy_m: float | None
    detected_at_utc: str


@dataclass(frozen=True)
class Outcome:
    ok: bool
    code: str            # "located" or an error code
    title: str = ""
    detail: str = ""
    retry: bool = True
    fix: DeviceFix | None = None


# --- Validation and interpretation -----------------------------------------------------

def validate_coordinates(lat: Any, lon: Any) -> tuple[float, float]:
    """Return (lat, lon) as floats, or raise ValueError. Run before any API request."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        raise ValueError("Coordinates are not numbers") from None
    if not (math.isfinite(la) and math.isfinite(lo)):
        raise ValueError("Coordinates are not finite numbers")
    if not -90 <= la <= 90:
        raise ValueError(f"Latitude {la} is outside -90…90")
    if not -180 <= lo <= 180:
        raise ValueError(f"Longitude {lo} is outside -180…180")
    return la, lo


MESSAGES = {
    "permission_denied": ("Location permission was denied.",
                          "Cloudzy can't see your location unless you allow it. To change this, open your browser's "
                          "site settings for this page (usually the icon left of the address bar), set Location to "
                          "Allow, and try again. Or search for a city below."),
    "position_unavailable": ("Your device couldn't determine its position.",
                             "Location services may be off, or there's no GPS, Wi-Fi or network signal to locate "
                             "from. Check your device's location settings and try again, or search for a city."),
    "timeout": ("Finding your location took too long.",
                "Your device didn't report a position in time. Try again (it's often faster the second time), "
                "or search for a city."),
    "no_response": ("No answer to the location request.",
                    "Your browser may be waiting for you to allow or block location access. Look for a prompt "
                    "near the address bar, then try again. Or search for a city."),
    "unsupported": ("This browser doesn't support location detection.",
                    "Search for your city instead. Everything else works the same."),
    "insecure": ("Location needs a secure connection.",
                 "Browsers only share location with pages served over HTTPS or from this computer (localhost). "
                 "Open Cloudzy at http://localhost:8501, or search for a city instead."),
    "invalid_coordinates": ("The browser returned an invalid position.",
                            "Cloudzy didn't use it. Try again, or search for a city."),
    "unknown": ("Location detection failed.", "Try again, or search for a city."),
}


def interpret(payload: Any) -> Outcome:
    """Turn the browser's report into an Outcome. Never trusts unvalidated coordinates."""
    if not isinstance(payload, dict):
        return _error("unknown")
    status = payload.get("status")
    if status == "ok":
        try:
            lat, lon = validate_coordinates(payload.get("latitude"), payload.get("longitude"))
        except ValueError as exc:
            logger.warning("Rejected browser coordinates: %s", exc)  # the reason, never the values
            return _error("invalid_coordinates")
        acc = payload.get("accuracy")
        acc = float(acc) if isinstance(acc, (int, float)) and math.isfinite(acc) and acc >= 0 else None
        return Outcome(True, "located", fix=DeviceFix(lat, lon, acc, datetime.now(UTC).isoformat(timespec="seconds")))
    if status in ("unsupported", "insecure"):
        return _error(status, retry=False)
    if status == "error":
        return _error(payload.get("code") if payload.get("code") in MESSAGES else "unknown")
    return _error("unknown")


def _error(code: str, retry: bool = True) -> Outcome:
    title, detail = MESSAGES[code]
    return Outcome(False, code, title, detail, retry)


# --- Request handling (stored in st.session_state; any mutable mapping works) -----------

def state(store: MutableMapping) -> dict:
    if STATE_KEY not in store:
        store[STATE_KEY] = {"mode": "manual", "nonce": 0, "pending": None, "fix": None, "error": None,
                            "auto_tried": False}
    return store[STATE_KEY]


def start(geo: dict) -> int:
    """Begin a location request, or reuse the one already pending (no duplicates)."""
    if geo["pending"] is None:
        geo["nonce"] += 1
        geo["pending"] = geo["nonce"]
        geo["error"] = None
    return geo["pending"]


def cancel(geo: dict) -> None:
    geo["pending"] = None


def use_manual(geo: dict) -> None:
    """The user chose to search: respect it, and ignore any late browser answer."""
    geo.update(mode="manual", pending=None, error=None)


def handle(geo: dict, payload: Any) -> Outcome | None:
    """Apply a browser report. Returns None if there was none, or if it was stale."""
    if payload is None:
        return None
    if not isinstance(payload, dict) or geo["pending"] is None or payload.get("nonce") != geo["pending"]:
        return None  # stale: from an older or cancelled request
    geo["pending"] = None
    outcome = interpret(payload)
    if outcome.ok:
        geo.update(mode="device", fix=outcome.fix, error=None)
    else:
        geo["error"] = outcome
    return outcome


# --- Browser component -----------------------------------------------------------------------

JS = r"""
export default function (component) {
  const { data, setTriggerValue } = component;
  const nonce = data && data.nonce;
  if (!nonce || window.__cloudzyGeoNonce === nonce) return;   // one lookup per request, never on re-render
  window.__cloudzyGeoNonce = nonce;
  const send = (payload) => setTriggerValue("location", Object.assign({ nonce: nonce }, payload));
  if (!window.isSecureContext) { send({ status: "insecure" }); return; }
  if (!("geolocation" in navigator)) { send({ status: "unsupported" }); return; }
  let done = false;
  const finish = (payload) => { if (done) return; done = true; clearTimeout(guard); send(payload); };
  // getCurrentPosition's own timeout only starts after permission is granted; this guard makes sure an
  // unanswered permission prompt can never leave Cloudzy loading forever.
  const guard = setTimeout(() => finish({ status: "error", code: "no_response" }),
                           (data.timeoutMs || 15000) + (data.permissionWaitMs || 60000));
  const codes = { 1: "permission_denied", 2: "position_unavailable", 3: "timeout" };
  navigator.geolocation.getCurrentPosition(
    (pos) => finish({ status: "ok", latitude: pos.coords.latitude, longitude: pos.coords.longitude,
                      accuracy: pos.coords.accuracy, timestamp: pos.timestamp }),
    (err) => finish({ status: "error", code: codes[err.code] || "unknown" }),
    { enableHighAccuracy: !!data.highAccuracy, timeout: data.timeoutMs || 15000,
      maximumAge: data.maximumAgeMs || 0 }
  );
}
"""

_component = None


def browser_location(nonce: int, options: GeoOptions = GeoOptions()) -> dict | None:
    """Mount the (invisible) geolocation script and return its report for this run, if any.

    Tests replace this function with a fake. The real one needs a browser.
    """
    global _component
    import streamlit as st
    if _component is None:
        _component = st.components.v2.component("cloudzy_geolocation", js=JS)
    result = _component(key="cloudzy_geolocation", data=options.as_data(nonce), on_location_change=lambda: None)
    return getattr(result, "location", None)


# --- Reverse geocoding (Nominatim) -------------------------------------------------------------

# Shared by every visitor of this server, so it is bounded: the oldest entries are dropped first.
CACHE_MAX = 1000
_cache: OrderedDict[tuple[float, float], dict | None] = OrderedDict()
_cache_lock = threading.Lock()
_lock = threading.Lock()
_last_call = [0.0]


def _cache_get(key):
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return True, _cache[key]
        return False, None


def _cache_put(key, value) -> None:
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def _throttle() -> None:
    with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def parse_reverse(payload: Any) -> dict | None:
    """City / region / country from a Nominatim jsonv2 response, or None if there is no usable name."""
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    a = payload.get("address") or {}
    name = next((a[k] for k in ("city", "town", "village", "municipality", "hamlet", "suburb", "county")
                 if a.get(k)), None)
    if not name:
        return None
    code = a.get("country_code")
    return {"name": name, "admin1": a.get("state") or a.get("region"), "country": a.get("country"),
            "country_code": code.upper() if isinstance(code, str) else None}


def reverse_geocode(lat: float, lon: float, session: requests.Session | None = None) -> dict | None:
    """Readable place for coordinates, or None on any failure (the caller then uses NEUTRAL_LABEL)."""
    lat, lon = validate_coordinates(lat, lon)
    key = (round(lat, REVERSE_DECIMALS), round(lon, REVERSE_DECIMALS))
    hit, cached = _cache_get(key)
    if hit:
        return cached
    http = session or requests.Session()
    http.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
    _throttle()
    try:
        payload = fetch_json(NOMINATIM_URL, {"lat": key[0], "lon": key[1], "format": "jsonv2", "zoom": 10,
                                             "addressdetails": 1}, session=http, timeout=8, retries=1, max_wait_s=2)
        result = parse_reverse(payload)
    except DataCollectionError as exc:
        logger.warning("Reverse geocoding failed: %s", exc)
        return None  # not cached: a later try may succeed
    _cache_put(key, result)
    return result


def device_place(fix: DeviceFix, rev: dict | None) -> Place:
    """A Place for the device's exact coordinates. The forecast API resolves the time zone
    (timezone=auto); "UTC" is only a placeholder that the forecast response overrides."""
    if rev:
        return Place(rev["name"], fix.latitude, fix.longitude, "UTC", rev.get("country"), rev.get("admin1"),
                     None, rev.get("country_code"))
    return Place(NEUTRAL_LABEL, fix.latitude, fix.longitude, "UTC")
