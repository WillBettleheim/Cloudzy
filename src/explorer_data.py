"""Global forecast grids for the Weather Explorer, from NOAA GFS (public domain).

Source
- NOAA Global Forecast System (GFS), 1.0° global grid (360 × 181 points), via the NOAA Open Data
  Dissemination programme on AWS: https://noaa-gfs-bdp-pds.s3.amazonaws.com (no key, no login).
- GFS runs four times a day (00, 06, 12, 18 UTC). Each run is published a few hours later.
- This is model *forecast* data (the 000 h step is the model analysis). It is not radar and not
  station observations.

How frames are prepared
1. Pick the newest run whose +72 h index file exists (so the whole timeline is available).
2. For each step 0, 3, …, 72 h, read the run's `.idx` index and download only the needed GRIB2
   messages with HTTP byte ranges (about 50–100 KB each instead of a 44 MB file).
3. Decode with ECMWF ecCodes (Apache-2.0), check the grid is the expected 1° global grid, convert
   units and store each field as little-endian uint16 (value = offset + q × scale; 65535 = missing,
   never zero) in `static/explorer/<run>/`, served by Streamlit's static file server.
4. `manifest.json` lists the run, valid times, encoding and which frames are ready. The browser
   reads it and fetches only the frames of the layer it is showing.

Only instantaneous fields are used (records labelled "N hour fcst" or "anl"), never time averages,
so every frame is that forecast hour's own data. Only the newest complete run is kept on disk.
Preparation runs in one background thread; pages never wait for it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import requests

from src import config

logger = logging.getLogger(__name__)

BUCKET = os.environ.get("CLOUDZY_GFS_BUCKET", "https://noaa-gfs-bdp-pds.s3.amazonaws.com")
SOURCE = "NOAA GFS 1.0° (NOAA Open Data Dissemination on AWS)"
ATTRIBUTION = "Forecast data: NOAA/NCEP GFS (public domain)"
FHOURS = tuple(range(0, 73, 3))           # 25 frames, 3-hourly, to +72 h
RUN_HOURS = (18, 12, 6, 0)
NLAT, NLON = 181, 360                     # 90 → -90 by 1°, 0 → 359 by 1°
MISSING = 65535
def out_dir() -> Path:
    """Read at call time, so tests can redirect it with CLOUDZY_EXPLORER_DIR."""
    return Path(os.environ.get("CLOUDZY_EXPLORER_DIR", config.PROJECT_ROOT / "static" / "explorer"))


def refresh_enabled() -> bool:
    """CLOUDZY_EXPLORER_REFRESH=0 turns off downloading (tests, offline use); stored frames still show."""
    return os.environ.get("CLOUDZY_EXPLORER_REFRESH", "1") == "1"
STATIC_URL = "app/static/explorer"        # where Streamlit serves out_dir() (enableStaticServing)
CHECK_EVERY_S = 15 * 60                   # look for a newer run at most this often
USER_AGENT = "Cloudzy/1.0 (student project; weather explorer)"


@dataclass(frozen=True)
class Field:
    grib: str          # GRIB short name in the .idx file
    level: str         # level text in the .idx file
    units: str         # stored units (after conversion)
    offset: float
    scale: float
    convert: str = ""  # "K->C", "Pa->hPa", "kg/m2/s->mm/h"


FIELDS: dict[str, Field] = {
    "u10": Field("UGRD", "10 m above ground", "m/s", -160.0, 0.005),
    "v10": Field("VGRD", "10 m above ground", "m/s", -160.0, 0.005),
    "t2m": Field("TMP", "2 m above ground", "°C", -120.0, 0.005, "K->C"),
    "prmsl": Field("PRMSL", "mean sea level", "hPa", 850.0, 0.005, "Pa->hPa"),
    "tcc": Field("TCDC", "entire atmosphere", "%", 0.0, 0.01),
    "prate": Field("PRATE", "surface", "mm/h", 0.0, 0.001, "kg/m2/s->mm/h"),
    "crain": Field("CRAIN", "surface", "flag", 0.0, 1.0),
    "csnow": Field("CSNOW", "surface", "flag", 0.0, 1.0),
    "cfrzr": Field("CFRZR", "surface", "flag", 0.0, 1.0),
    "cicep": Field("CICEP", "surface", "flag", 0.0, 1.0),
    "gust": Field("GUST", "surface", "m/s", 0.0, 0.005),
    "rh2m": Field("RH", "2 m above ground", "%", 0.0, 0.01),
    "cape": Field("CAPE", "surface", "J/kg", 0.0, 0.25),
}
# Stored per frame: the fields above except the four precipitation-type flags, which are combined
# into one derived field, `ptype` (0 none, 1 rain, 2 snow, 3 mixed/freezing).
DERIVED = {"ptype": ("code", 0.0, 1.0)}
LAYERS = {
    "wind": {"label": "Wind", "vars": ["u10", "v10"], "kind": "forecast"},
    "temperature": {"label": "Temperature", "vars": ["t2m"], "kind": "forecast"},
    "precipitation": {"label": "Precipitation", "vars": ["prate", "ptype"], "kind": "forecast"},
    "clouds": {"label": "Cloud cover", "vars": ["tcc"], "kind": "forecast"},
    "pressure": {"label": "Pressure", "vars": ["prmsl"], "kind": "forecast"},
    "gusts": {"label": "Wind gusts", "vars": ["gust"], "kind": "forecast"},
    "humidity": {"label": "Humidity", "vars": ["rh2m"], "kind": "forecast"},
    "cape": {"label": "CAPE", "vars": ["cape"], "kind": "forecast"},
}
STORED = [k for k in FIELDS if k not in ("crain", "csnow", "cfrzr", "cicep")] + ["ptype"]


class ExplorerDataError(RuntimeError):
    pass


# --- Run discovery and index parsing ----------------------------------------------------------------

def run_url(run: datetime, fhr: int) -> str:
    d, h = run.strftime("%Y%m%d"), run.strftime("%H")
    return f"{BUCKET}/gfs.{d}/{h}/atmos/gfs.t{h}z.pgrb2.1p00.f{fhr:03d}"


def run_id(run: datetime) -> str:
    return run.strftime("%Y%m%d_%H")


def latest_run(now: datetime, session: requests.Session, last_fhr: int = FHOURS[-1]) -> datetime:
    """Newest run (up to 2 days back) whose last needed step is published."""
    day = now.replace(minute=0, second=0, microsecond=0)
    for back in range(0, 3):
        date = (day - timedelta(days=back)).replace(hour=0)
        for h in RUN_HOURS:
            run = date.replace(hour=h)
            if run > now:
                continue
            r = session.head(run_url(run, last_fhr) + ".idx", timeout=15)
            if r.status_code == 200:
                return run
    raise ExplorerDataError("No complete GFS run found in the last two days.")


def parse_index(text: str, fhr: int) -> dict[str, tuple[int, int | None]]:
    """Byte ranges of the instantaneous records we use: {field: (start, end_inclusive or None)}.

    Lines look like `588:35317024:d=2026102000:UGRD:10 m above ground:24 hour fcst:`. Averages and
    accumulations ("18-24 hour ave fcst") are skipped, so a frame never mixes in a time mean.
    """
    rows = [ln.split(":") for ln in text.strip().splitlines() if ln.count(":") >= 6]
    step = "anl" if fhr == 0 else f"{fhr} hour fcst"
    out: dict[str, tuple[int, int | None]] = {}
    for i, r in enumerate(rows):
        for key, f in FIELDS.items():
            if key not in out and r[3] == f.grib and r[4] == f.level and r[5] == step:
                end = int(rows[i + 1][1]) - 1 if i + 1 < len(rows) else None
                out[key] = (int(r[1]), end)
    return out


# --- Decoding and encoding -----------------------------------------------------------------------

def decode(message: bytes) -> np.ndarray:
    """A GRIB2 message → float array (181, 360), north to south, 0°E eastward. NaN for missing points."""
    import eccodes
    h = eccodes.codes_new_from_message(message)
    try:
        ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
        lat0 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees")
        lon0 = eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees")
        inc = eccodes.codes_get(h, "iDirectionIncrementInDegrees")
        north_first = eccodes.codes_get(h, "jScansPositively") == 0
        if (ni, nj) != (NLON, NLAT) or abs(lat0 - 90) > 1e-6 or abs(lon0) > 1e-6 or abs(inc - 1) > 1e-6 \
                or not north_first:
            raise ExplorerDataError(f"Unexpected grid {nj}x{ni} from {lat0},{lon0} step {inc}")
        values = eccodes.codes_get_values(h).astype("float64")
        if eccodes.codes_get(h, "bitmapPresent"):
            values[values == eccodes.codes_get(h, "missingValue")] = np.nan
        return values.reshape(NLAT, NLON)
    finally:
        eccodes.codes_release(h)


def convert(values: np.ndarray, how: str) -> np.ndarray:
    if how == "K->C":
        return values - 273.15
    if how == "Pa->hPa":
        return values / 100.0
    if how == "kg/m2/s->mm/h":
        return values * 3600.0
    return values


def encode(values: np.ndarray, offset: float, scale: float) -> bytes:
    """uint16 little-endian; NaN (and anything out of range) → MISSING, never a fake value."""
    q = np.round((values - offset) / scale)
    bad = ~np.isfinite(q) | (q < 0) | (q > MISSING - 1)
    out = np.where(bad, MISSING, q).astype("<u2")
    return out.tobytes()


def decode_stored(raw: bytes, offset: float, scale: float) -> np.ndarray:
    q = np.frombuffer(raw, dtype="<u2").astype("float64").reshape(NLAT, NLON)
    return np.where(q == MISSING, np.nan, offset + q * scale)


def precip_type(flags: dict[str, np.ndarray]) -> np.ndarray:
    """0 none, 1 rain, 2 snow, 3 mixed or freezing (GFS categorical flags; NaN if all flags missing)."""
    rain, snow = flags.get("crain"), flags.get("csnow")
    frz = [flags[k] for k in ("cfrzr", "cicep") if k in flags]
    if rain is None and snow is None and not frz:
        return np.full((NLAT, NLON), np.nan)
    z = np.zeros((NLAT, NLON))
    r = np.nan_to_num(rain if rain is not None else z) > 0.5
    s = np.nan_to_num(snow if snow is not None else z) > 0.5
    m = np.zeros((NLAT, NLON), bool)
    for f in frz:
        m |= np.nan_to_num(f) > 0.5
    out = np.zeros((NLAT, NLON))
    out[r] = 1
    out[s] = 2
    out[m | (r & s)] = 3
    return out


# --- Frame preparation -----------------------------------------------------------------------------

def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def frame_path(directory: Path, rid: str, fhr: int, var: str) -> Path:
    return directory / rid / f"f{fhr:03d}_{var}.bin"


def prepare_frame(run: datetime, fhr: int, session: requests.Session, directory: Path) -> list[str]:
    """Download, decode and store one forecast step. Returns the fields that were not available."""
    rid = run_id(run)
    url = run_url(run, fhr)
    r = session.get(url + ".idx", timeout=20)
    if r.status_code != 200:
        raise ExplorerDataError(f"Index for +{fhr} h not available (HTTP {r.status_code}).")
    ranges = parse_index(r.text, fhr)

    def fetch(key: str) -> tuple[str, np.ndarray]:
        start, end = ranges[key]
        hdr = {"Range": f"bytes={start}-{'' if end is None else end}"}
        resp = session.get(url, headers=hdr, timeout=30)
        if resp.status_code not in (200, 206):
            raise ExplorerDataError(f"{key} +{fhr} h: HTTP {resp.status_code}")
        f = FIELDS[key]
        return key, convert(decode(resp.content), f.convert)

    with ThreadPoolExecutor(max_workers=4) as pool:
        decoded = dict(pool.map(fetch, list(ranges)))
    (directory / rid).mkdir(parents=True, exist_ok=True)
    missing = []
    for key in STORED:
        if key == "ptype":
            flags: dict[str, np.ndarray] = {k: decoded[k] for k in ("crain", "csnow", "cfrzr", "cicep") if k in decoded}
            if not flags:
                missing.append(key)
                continue
            values, off, sc = precip_type(flags), 0.0, 1.0
        elif key in decoded:
            f = FIELDS[key]
            values, off, sc = decoded[key], f.offset, f.scale
        else:
            missing.append(key)
            continue
        _write_atomic(frame_path(directory, rid, fhr, key), encode(values, off, sc))
    return missing


def manifest_for(run: datetime, frames: list[dict], status: str, error: str | None = None) -> dict:
    enc = {k: {"offset": f.offset, "scale": f.scale, "units": f.units} for k, f in FIELDS.items() if k in STORED}
    enc["ptype"] = {"offset": 0.0, "scale": 1.0, "units": "code (0 none, 1 rain, 2 snow, 3 mixed/freezing)"}
    return {
        "source": SOURCE, "attribution": ATTRIBUTION, "kind": "Numerical weather prediction (model forecast)",
        "run_utc": run.strftime("%Y-%m-%dT%H:00Z"), "run_id": run_id(run),
        "grid": {"nlat": NLAT, "nlon": NLON, "lat0": 90.0, "dlat": -1.0, "lon0": 0.0, "dlon": 1.0,
                 "resolution": "1.0°"},
        "missing": MISSING, "encoding": enc, "layers": LAYERS,
        "frames": frames, "status": status, "error": error,
        "updated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": f"{STATIC_URL}/{run_id(run)}",
    }


def read_manifest(directory: Path | None = None) -> dict | None:
    path = Path(directory or out_dir()) / "manifest.json"
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def write_manifest(m: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _write_atomic(directory / "manifest.json", json.dumps(m, separators=(",", ":")).encode())


def prune(directory: Path, keep: str) -> None:
    """Delete every run folder except `keep`, so the store never grows beyond one run."""
    for p in directory.iterdir() if directory.exists() else []:
        if p.is_dir() and p.name != keep:
            shutil.rmtree(p, ignore_errors=True)


def prepare_run(run: datetime, session: requests.Session, directory: Path | None = None,
                fhours: tuple[int, ...] = FHOURS) -> dict:
    """Prepare every frame of `run`, publishing progress in the manifest. Keeps the previous run's
    files (and manifest) until this run is complete, then removes them."""
    directory = Path(directory or out_dir())
    previous = read_manifest(directory)
    frames: list[dict[str, Any]] = [
        {"fhr": h, "valid_utc": (run + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00Z"), "ready": False, "missing": []}
        for h in fhours]
    staging = directory / "staging_manifest.json"
    for fr in frames:
        rid = run_id(run)
        if all(frame_path(directory, rid, fr["fhr"], v).exists() for v in STORED):
            fr["ready"] = True                      # resumed: already prepared earlier
            continue
        fr["missing"] = prepare_frame(run, fr["fhr"], session, directory)
        fr["ready"] = True
        # Show progress only if no complete run is being served; otherwise switch over at the end.
        if previous is None or previous.get("status") != "complete":
            write_manifest(manifest_for(run, frames, "preparing"), directory)
        else:
            staging.write_text(json.dumps(frames))
    m = manifest_for(run, frames, "complete")
    write_manifest(m, directory)
    staging.unlink(missing_ok=True)
    prune(directory, keep=run_id(run))
    return m


# --- Background refresh (one thread per server process) ---------------------------------------------

_lock = threading.Lock()
_state: dict = {"thread": None, "last_check": 0.0, "error": None}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _refresh(directory: Path) -> None:
    try:
        s = _session()
        run = latest_run(datetime.now(UTC), s)
        current = read_manifest(directory)
        if current and current.get("run_id") == run_id(run) and current.get("status") == "complete":
            return
        prepare_run(run, s, directory)
        _state["error"] = None
    except (requests.RequestException, ExplorerDataError, OSError, ValueError) as exc:
        logger.warning("Weather Explorer data refresh failed: %s", exc)
        _state["error"] = f"{type(exc).__name__}: {exc}"[:300]


def ensure_current(directory: Path | None = None, now: float | None = None) -> dict:
    """Start a background refresh if one is due, and return the current status (never blocks).

    Returns {"manifest": dict | None, "refreshing": bool, "error": str | None}.
    """
    directory = Path(directory or out_dir())
    now = time.monotonic() if now is None else now
    with _lock:
        t = _state["thread"]
        running = t is not None and t.is_alive()
        if refresh_enabled() and not running and now - _state["last_check"] >= CHECK_EVERY_S:
            _state["last_check"] = now
            t = threading.Thread(target=_refresh, args=(directory,), name="cloudzy-explorer-refresh", daemon=True)
            _state["thread"] = t
            t.start()
            running = True
    return {"manifest": read_manifest(directory), "refreshing": running, "error": _state["error"]}


def value_at(manifest: dict, fhr: int, var: str, lat: float, lon: float, directory: Path | None = None) -> float | None:
    """Bilinear value of a stored field at a point (for checks and tests), or None if missing."""
    enc = manifest["encoding"][var]
    raw = frame_path(Path(directory or out_dir()), manifest["run_id"], fhr, var).read_bytes()
    g = decode_stored(raw, enc["offset"], enc["scale"])
    y = (90.0 - lat)
    x = (lon % 360.0)
    y0, x0 = math.floor(y), math.floor(x) % NLON
    y1, x1 = min(y0 + 1, NLAT - 1), (x0 + 1) % NLON
    fy, fx = y - math.floor(y), x - math.floor(x)
    vals = [g[y0, x0], g[y0, x1], g[y1, x0], g[y1, x1]]
    if any(not np.isfinite(v) for v in vals):
        return None
    top = vals[0] * (1 - fx) + vals[1] * fx
    bot = vals[2] * (1 - fx) + vals[3] * fx
    return float(top * (1 - fy) + bot * fy)
