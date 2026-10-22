"""The hazard data store: one cache per source, refreshed in the background, merged into one snapshot.

Behaviour
- **Fresh enough:** a source is re-requested only after its `ttl` (src.hazards.sources), whatever the page does.
  Camera moves, filters and playback never cause a request.
- **De-duplicated:** while a source is being refreshed, other page runs reuse that same request.
- **Stale-while-refreshing:** a page gets the last good data immediately and the refresh runs in the
  background. Only the very first load of a source waits (up to `wait_s`).
- **Failures:** a failed refresh keeps the last good data, marked "Delayed data" with the error, and is retried
  after `RETRY_AFTER` at the earliest. A source that has never loaded is "Temporarily unavailable"; the other
  sources and the globe are unaffected.
- **Merging:** the same storm or earthquake reported by an official source and by GDACS is shown once, as the
  official record, with GDACS's automatic alert attached and linked (see `merge`).
- **Static files:** large overlays (fire detections, drought polygons, the volcano catalogue) and the event list
  are written to `static/earth/` with content-hashed names, so the browser downloads each version once.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src import config
from src.hazards import firms, gdacs, http, nhc, nws, situations, tsunami, usdm, usgs, volcanoes
from src.hazards.geometry import haversine_km
from src.hazards.schema import FeedResult, FeedStatus, HazardEvent, freshness
from src.hazards.sources import SOURCES

logger = logging.getLogger(__name__)

STATIC_URL = "app/static/earth"
RETRY_AFTER = timedelta(seconds=60)
KEEP_FILES = 3                    # versions kept per layer (older ones are deleted)
EQ_MATCH_S, EQ_MATCH_KM = 120, 100
TC_MATCH_KM = 400


def static_dir() -> Path:
    return Path(os.environ.get("CLOUDZY_EARTH_DIR", config.PROJECT_ROOT / "static" / "earth"))


@dataclass(frozen=True)
class Query:
    """What the page asked for. Only USGS and GDACS depend on it; other feeds are current-only."""
    window: str = "week"                     # day, week, month or custom
    min_mag: float = 2.5
    start: pd.Timestamp | None = None        # custom range (UTC), for earthquakes and GDACS events
    end: pd.Timestamp | None = None

    def days(self) -> int:
        if self.window == "custom" and self.start is not None and self.end is not None:
            return max(1, int((self.end - self.start) / pd.Timedelta(days=1)) + 1)
        return {"day": 1, "week": 7, "month": 30}.get(self.window, 7)


def default_fetchers() -> dict[str, Callable[[Query], FeedResult]]:
    def eq(q: Query) -> FeedResult:
        if q.window == "custom":
            return usgs.fetch(min_mag=q.min_mag, start=q.start, end=q.end)
        return usgs.fetch(q.window, q.min_mag)

    def gd(q: Query) -> FeedResult:
        res = gdacs.fetch(max(q.days(), 7), now=q.end if q.window == "custom" and q.end is not None else None)
        if q.window == "custom" and q.start is not None and q.end is not None:
            res.events = [e for e in res.events if e.time is not None and q.start <= e.time <= q.end]
        return res
    return {"usgs": eq, "gdacs": gd, "nhc": lambda q: nhc.fetch(), "nws": lambda q: nws.fetch(),
            "firms": lambda q: firms.fetch(), "usdm": lambda q: usdm.fetch(),
            "usgs_volcano": lambda q: volcanoes.fetch_usgs(), "gvp": lambda q: volcanoes.fetch_gvp(),
            "tsunami": lambda q: tsunami.fetch()}


# Sources whose request depends on the query (they get one cache entry per distinct query).
QUERY_SOURCES = {"usgs", "gdacs"}


@dataclass
class _Entry:
    result: FeedResult | None = None
    fetched_at: pd.Timestamp | None = None
    attempted_at: pd.Timestamp | None = None
    error: str | None = None
    future: Future | None = None
    layer_urls: dict[str, str] = field(default_factory=dict)   # written once per successful fetch


@dataclass
class Snapshot:
    events: list[HazardEvent]
    statuses: dict[str, FeedStatus]
    layers: dict[str, str] = field(default_factory=dict)       # layer name → static URL
    warnings: list[str] = field(default_factory=list)
    generated: pd.Timestamp | None = None


class HazardStore:
    def __init__(self, fetchers: dict[str, Callable[[Query], FeedResult]] | None = None,
                 clock: Callable[[], pd.Timestamp] | None = None, workers: int = 6):
        self.fetchers = fetchers or default_fetchers()
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self._entries: dict[tuple, _Entry] = {}
        self._lock = threading.Lock()
        self._memo: tuple[tuple, list[HazardEvent], str] | None = None
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hazards")

    # --- Refreshing -------------------------------------------------------------------------------

    def _key(self, source: str, q: Query) -> tuple:
        return (source, q) if source in QUERY_SOURCES else (source,)

    def _run(self, source: str, q: Query, entry: _Entry) -> None:
        try:
            result = self.fetchers[source](q)
        except (http.FeedError, ValueError, KeyError, TypeError, OSError) as exc:
            logger.warning("Hazard source %s failed: %s", source, exc)
            with self._lock:
                entry.error, entry.attempted_at, entry.future = str(exc), self.clock(), None
            return
        urls = {name: write_static(name, data) for name, data in result.layers.items()}
        with self._lock:
            entry.result, entry.fetched_at, entry.attempted_at = result, self.clock(), self.clock()
            entry.error, entry.future, entry.layer_urls = None, None, urls

    def _ensure(self, source: str, q: Query) -> _Entry:
        """Start a refresh if the entry is due; never two at once for the same entry."""
        now = self.clock()
        with self._lock:
            entry = self._entries.setdefault(self._key(source, q), _Entry())
            if entry.future is not None:
                return entry
            fresh = entry.fetched_at is not None and now - entry.fetched_at < SOURCES[source].ttl
            backoff = entry.error is not None and entry.attempted_at is not None and \
                now - entry.attempted_at < RETRY_AFTER
            if not fresh and not backoff:
                entry.future = self._pool.submit(self._run, source, q, entry)
            return entry

    def refresh(self, source: str, q: Query = Query()) -> None:
        """Force the next read to re-request a source (the page's "Refresh" button)."""
        with self._lock:
            entry = self._entries.get(self._key(source, q))
            if entry is not None and entry.future is None:
                entry.fetched_at = None
                entry.error = None

    def snapshot(self, q: Query = Query(), sources: list[str] | None = None, wait_s: float = 25.0) -> Snapshot:
        names = sources or list(self.fetchers)
        entries = {s: self._ensure(s, q) for s in names}
        for e in entries.values():              # only a source with no data yet makes the page wait
            fut = e.future
            if e.result is None and fut is not None:
                with contextlib.suppress(FutureTimeout):
                    fut.result(timeout=wait_s)
        now = self.clock()
        statuses, events, layers, warnings = {}, [], {}, []
        for s, e in entries.items():
            src = SOURCES[s]
            res = e.result
            statuses[s] = FeedStatus(
                source=s, ok=e.error is None and res is not None, fetched_at=e.fetched_at,
                data_time=res.data_time if res else None, count=len(res.events) if res else 0,
                error=e.error if e.error else (None if res else "still loading" if e.future else "no data"),
                note=res.note if res else None, using_previous=res is not None and e.error is not None)
            statuses[s].label = freshness(statuses[s].ok, res is not None, statuses[s].data_time, now, src.cadence,
                                          historical=(s == "gvp") or (s in QUERY_SOURCES and q.window == "custom"),
                                          using_previous=statuses[s].using_previous,
                                          event_driven=src.event_driven, fetched_at=e.fetched_at)
            if res is None:
                continue
            events += res.events
            warnings += [f"{src.name}: {w}" for w in res.warnings[:5]]
            layers.update(e.layer_urls)
        version = tuple((s, e.fetched_at) for s, e in entries.items())
        memo = self._memo
        if memo is None or memo[0] != version:                   # merge and write only when data changed
            merged = merge(events)
            memo = (version, merged, write_static("events", [map_record(e) for e in merged]))
            self._memo = memo
        layers["events"] = memo[2]
        return Snapshot(memo[1], statuses, layers, warnings, now)


# --- Merging the same event from several sources -----------------------------------------------------

def merge(events: list[HazardEvent]) -> list[HazardEvent]:
    """De-duplicate by id, then fold GDACS duplicates of official records into the official record."""
    by_id: dict[str, HazardEvent] = {}
    # Shallow copies with their own dicts and lists, so merging never alters the cached source records.
    events = [dataclasses.replace(e, measurements=dict(e.measurements), related=list(e.related),
                                  extra=dict(e.extra)) for e in events]
    for e in events:
        old = by_id.get(e.id)
        if old is None or (e.updated or e.time or pd.Timestamp.min.tz_localize("UTC")) > \
                (old.updated or old.time or pd.Timestamp.min.tz_localize("UTC")):
            by_id[e.id] = e
    out = list(by_id.values())
    official_eq = [e for e in out if e.source == "usgs"]
    official_tc = [e for e in out if e.source == "nhc"]
    hidden: set[str] = set()
    for g in (e for e in out if e.source == "gdacs"):
        match = None
        if g.category == "earthquake" and g.time is not None:
            match = next((u for u in official_eq if u.time is not None
                          and abs((u.time - g.time).total_seconds()) <= EQ_MATCH_S
                          and float(haversine_km(u.lat, u.lon, g.lat, g.lon)) <= EQ_MATCH_KM), None)
        elif g.category == "cyclone" and g.current:
            name = (g.title.rsplit(" ", 1)[-1].split("-")[0]).lower()
            match = next((n for n in official_tc if (n.extra.get("name") or "").lower() == name
                          or float(haversine_km(n.lat, n.lon, g.lat, g.lon)) <= TC_MATCH_KM), None)
        if match is not None:
            hidden.add(g.id)
            match.related.append(g.id)
            match.measurements["GDACS alert (automatic estimate)"] = g.severity_label
            match.extra.setdefault("related_links", []).append({"label": "GDACS report", "url": g.url})
    return [e for e in out if e.id not in hidden]


def map_record(e: HazardEvent) -> dict:
    """What the globe needs to draw and list an event. Full details are rendered by the page on selection."""
    d = {"id": e.id, "s": e.source, "c": e.category, "k": e.kind, "t": e.title, "lat": e.lat, "lon": e.lon,
         "time": e.to_json()["time"], "end": e.to_json()["ends"], "r": e.severity_rank, "sev": e.severity_label,
         "o": e.official, "cur": e.current, "area": (e.area or e.country or "")[:120],
         "sit": situations.situation_for(e), "lk": situations.layer_for(e)}
    if situations.product(e):
        d["prod"] = situations.product(e)
    if e.category == "cyclone":
        d["basin"] = situations.basin(e)
    if e.category == "flood":
        d["fl"] = situations.flood_type(e)
    if e.category == "earthquake":
        d["mag"], d["depth"] = e.extra.get("mag"), e.extra.get("depth_km")
    if e.geometry is not None:
        d["g"] = e.geometry
    if e.extra.get("track"):
        d["track"] = [[p["lon"], p["lat"], p["time"], p["status"]] for p in e.extra["track"]]
    return d


# --- Static files --------------------------------------------------------------------------------------

def write_static(name: str, data: Any) -> str:
    """Write `data` as JSON with a content hash in the name (written once, then reused); return its URL."""
    body = json.dumps(data, separators=(",", ":"), default=str).encode()
    digest = hashlib.sha1(body).hexdigest()[:12]
    folder = static_dir()
    path = folder / f"{name}-{digest}.json"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        # A unique temporary name per writer: several visitors' page runs may write the same version at once.
        tmp = folder / f".{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_bytes(body)
        tmp.replace(path)                       # atomic; identical content, so the last writer wins harmlessly
        olds = sorted(folder.glob(f"{name}-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in olds[KEEP_FILES:]:
            old.unlink(missing_ok=True)
    return f"{STATIC_URL}/{path.name}"


_STORE: HazardStore | None = None
_STORE_LOCK = threading.Lock()


def store() -> HazardStore:
    """The process-wide store, shared by every visitor (the data are public)."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = HazardStore()
        return _STORE
