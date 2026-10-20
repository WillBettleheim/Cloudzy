"""Regional weather summaries from the Weather Explorer's GFS frames (a genuine spatial aggregate).

Method: the 1° grid cells whose centres lie inside the region (src.geo.region_mask, limited to the region's
country) are averaged with cos(latitude) weights, because a 1° cell's area shrinks towards the poles. Cells with
no value are left out. With fewer than src.geo.MIN_CELLS cells the region is smaller than the model can describe
and no regional value is given; the caller offers a point forecast instead.

Values are the GFS model forecast for one step (valid time), never a single city's forecast standing in for a
region, and never mixed with the Open-Meteo point forecast or Cloudzy Core.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from src import explorer_data as E
from src import geo


def nearest_step(manifest: dict, fhr: int | None = None, now: datetime | None = None) -> dict | None:
    """The requested ready step, or the ready step closest to now."""
    frames = [f for f in manifest.get("frames", []) if f.get("ready")]
    if not frames:
        return None
    if fhr is not None:
        match = next((f for f in frames if f["fhr"] == fhr), None)
        if match:
            return match
    now = now or datetime.now(UTC)
    return min(frames, key=lambda f: abs((datetime.fromisoformat(f["valid_utc"].replace("Z", "+00:00")) - now)
                                         .total_seconds()))


def _field(manifest: dict, fhr: int, var: str, directory: Path) -> np.ndarray | None:
    path = E.frame_path(directory, manifest["run_id"], fhr, var)
    enc = manifest["encoding"].get(var)
    if enc is None or not path.exists():
        return None
    return E.decode_stored(path.read_bytes(), enc["offset"], enc["scale"])


def _stats(values: np.ndarray, weights: np.ndarray) -> dict[str, float] | None:
    ok = np.isfinite(values) & (weights > 0)
    if not ok.any():
        return None
    w, v = weights[ok], values[ok]
    return {"mean": float((w * v).sum() / w.sum()), "min": float(v.min()), "max": float(v.max()), "cells": int(ok.sum())}


@lru_cache(maxsize=64)
def _summary(run_id: str, fhr: int, kind: str, a3: str, unit_id: str, directory: str, updated: str) -> dict[str, Any]:
    manifest = E.read_manifest(Path(directory))
    if manifest is None or manifest["run_id"] != run_id:
        return {"error": "The forecast run changed; select the region again."}
    found = geo.region_mask(kind, a3, unit_id or None)
    if found is None:
        return {"error": "Unknown region."}
    mask, n = found
    out: dict[str, Any] = {"cells": n, "fhr": fhr, "run_utc": manifest["run_utc"],
                           "valid_utc": next(f["valid_utc"] for f in manifest["frames"] if f["fhr"] == fhr),
                           "min_cells": geo.MIN_CELLS}
    if n < geo.MIN_CELLS:
        return out
    w = geo.area_weights(mask)
    d = Path(directory)
    t2m, prate, u10, v10, tcc = (_field(manifest, fhr, v, d) for v in ("t2m", "prate", "u10", "v10", "tcc"))
    out["temperature"] = _stats(t2m, w) if t2m is not None else None
    if prate is not None:
        wet = np.where(np.isfinite(prate), prate >= 0.1, np.nan)
        cover = _stats(wet.astype(float), w)
        out["precipitation"] = {"cover": cover["mean"] if cover else None, "max": float(np.nanmax(np.where(mask, prate, np.nan)))
                                if np.isfinite(np.where(mask, prate, np.nan)).any() else None}
    else:
        out["precipitation"] = None
    out["wind"] = _stats(np.hypot(u10, v10), w) if u10 is not None and v10 is not None else None
    out["clouds"] = _stats(tcc, w) if tcc is not None else None
    return out


def summary(kind: str, a3: str, unit_id: str | None, fhr: int | None = None,
            directory: Path | None = None) -> dict[str, Any]:
    """Regional GFS summary for a country or first-level division at one step.

    Returns {"cells", "fhr", "valid_utc", "run_utc", "temperature", "precipitation", "wind", "clouds"} (statistics
    are None when a field is missing), only {"cells", …} when the region is too small for the grid, or
    {"error": …} when there's no prepared run."""
    directory = Path(directory or E.out_dir())
    manifest = E.read_manifest(directory)
    if manifest is None:
        return {"error": "Global forecast data isn't ready yet."}
    step = nearest_step(manifest, fhr)
    if step is None:
        return {"error": "No forecast step is ready yet."}
    return _summary(manifest["run_id"], step["fhr"], kind, a3, unit_id or "", str(directory),
                    manifest.get("updated_utc", ""))


@lru_cache(maxsize=1)
def _places() -> list[list]:
    import json
    return json.loads((geo.GEO_DIR / "places.json").read_text())["places"]


def places_in(kind: str, a3: str, unit_id: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
    """The most populous Natural Earth places inside a country or division (tested against its outline)."""
    rows = [p for p in _places() if p[4] == a3]
    if kind == "admin1" and unit_id:
        u = geo.division(a3, unit_id)
        if u is None:
            return []
        lon = np.array([p[2] for p in rows])
        lat = np.array([p[1] for p in rows])
        inside = geo.points_in_polys(lon, lat, u["polys"]) if rows else np.zeros(0, bool)
        rows = [p for p, ok in zip(rows, inside) if ok]
    rows.sort(key=lambda p: -p[6])
    return [{"name": p[0], "lat": p[1], "lon": p[2], "pop": p[6], "capital": p[7]} for p in rows[:limit]]


def cell_area_km2(lat: float) -> float:
    """Approximate area of one 1° cell at a latitude (for explaining the grid size)."""
    return 111.32 ** 2 * math.cos(math.radians(lat))
