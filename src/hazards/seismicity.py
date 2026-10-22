"""Historical seismicity around a point: what the instrumental catalogue shows, never a prediction.

Earthquakes can't be predicted (time, place and size). What can be said defensibly is how often earthquakes of
each size have been recorded nearby, and how their numbers fall off with magnitude (Gutenberg-Richter). This
module computes exactly that from the USGS ComCat catalogue, and labels it "historical analysis". Long-term
hazard (ground-shaking probabilities) is a different product, published by official probabilistic seismic hazard
models (USGS national maps, the GEM global hazard map); the page links to those instead of inventing one.

Method
- Catalogue: USGS FDSN event service, text format, M ≥ 4.0 within RADIUS_KM since START (1976, when routine global
  moment-tensor solutions began), at most LIMIT events.
- Counts and historical rates for M ≥ 5, 6 and 7, plus the largest event. A rate is a count divided by the
  catalogue's span: a description of the past, not a forecast probability.
- Magnitude of completeness Mc: maximum-curvature method on 0.1-magnitude bins, plus the usual +0.2 correction
  (Woessner and Wiemer 2005). Events below Mc are not used for the b-value.
- b-value: Aki (1965) maximum-likelihood estimate with Utsu's bin correction,
  b = log10(e) / (mean(M) - (Mc - Δ/2)), with a 90% bootstrap interval (500 resamples, fixed seed). Not computed
  with fewer than MIN_EVENTS events above Mc.
- Caveat stated with every result: the catalogue mixes magnitude types (mostly mb, which saturates for large
  events), so the b-value is indicative.
"""

from __future__ import annotations

import io
import math

import numpy as np
import pandas as pd
import requests

from src.hazards import http

FDSN_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
START = "1976-01-01"
RADIUS_KM = 300
MIN_MAG = 4.0
LIMIT = 20000
MIN_EVENTS = 50
BIN = 0.1
HAZARD_LINKS = [("GEM Global Seismic Hazard Map", "https://maps.openquake.org/map/global-seismic-hazard-map/")]
NOTE = ("Historical analysis of the instrumental catalogue, not a prediction. Earthquake timing, location and size "
        "can't be predicted; for long-term hazard, use the official hazard maps linked here.")


def parse_catalogue(text: str) -> pd.DataFrame:
    """FDSN text output → time (UTC), lat, lon, depth_km, mag, mag_type. Bad rows are dropped."""
    if not text or not text.lstrip().startswith("#"):
        if not text.strip():
            return pd.DataFrame(columns=["time", "lat", "lon", "depth_km", "mag", "mag_type"])
        raise http.FeedError("USGS catalogue response isn't FDSN text")
    df = pd.read_csv(io.StringIO(text.lstrip("#")), sep="|")
    df.columns = [c.strip() for c in df.columns]
    out = pd.DataFrame({
        "time": pd.to_datetime(df["Time"], utc=True, errors="coerce", format="ISO8601"),
        "lat": pd.to_numeric(df["Latitude"], errors="coerce"), "lon": pd.to_numeric(df["Longitude"], errors="coerce"),
        "depth_km": pd.to_numeric(df["Depth/km"], errors="coerce"), "mag": pd.to_numeric(df["Magnitude"], errors="coerce"),
        "mag_type": df["MagType"].astype(str).str.lower()})
    return out.dropna(subset=["time", "lat", "lon", "mag"]).sort_values("time").reset_index(drop=True)


def completeness(mags: np.ndarray, correction: float = 0.2) -> float | None:
    """Maximum curvature Mc (+ correction) on 0.1 bins; None for an empty catalogue."""
    m = np.round(np.asarray(mags, float) / BIN) * BIN
    if len(m) == 0:
        return None
    values, counts = np.unique(np.round(m, 1), return_counts=True)
    return round(float(values[np.argmax(counts)]) + correction, 1)


def b_value(mags: np.ndarray, mc: float, rng_seed: int = 42, n_boot: int = 500) -> dict | None:
    """Aki-Utsu b-value above Mc with a 90% bootstrap interval, or None with fewer than MIN_EVENTS events."""
    m = np.asarray(mags, float)
    m = m[m >= mc - 1e-9]
    if len(m) < MIN_EVENTS:
        return None

    def est(x):
        d = x.mean() - (mc - BIN / 2)
        return math.log10(math.e) / d if d > 0 else float("nan")
    rng = np.random.default_rng(rng_seed)
    boots = np.array([est(rng.choice(m, size=len(m), replace=True)) for _ in range(n_boot)])
    lo, hi = np.nanpercentile(boots, [5, 95])
    return {"b": round(est(m), 2), "ci90": [round(float(lo), 2), round(float(hi), 2)], "n": len(m), "mc": mc}


def summarise(cat: pd.DataFrame, now: pd.Timestamp, start: str = START) -> dict:
    """Counts, historical rates, largest event, completeness and b-value for a catalogue."""
    span_years = max((now - pd.Timestamp(start, tz="UTC")).days / 365.25, 1e-9)
    out: dict = {"n": len(cat), "span_years": round(span_years, 1), "start": start, "note": NOTE,
                 "hazard_links": HAZARD_LINKS, "thresholds": []}
    if cat.empty:
        return out
    for m in (5.0, 6.0, 7.0):
        n = int((cat["mag"] >= m).sum())
        out["thresholds"].append({"min_mag": m, "count": n, "per_decade": round(10 * n / span_years, 2)})
    big = cat.loc[cat["mag"].idxmax()]
    out["largest"] = {"mag": float(big["mag"]), "mag_type": big["mag_type"], "time": big["time"].isoformat()}
    types = cat["mag_type"].value_counts(normalize=True)
    out["mag_types"] = {str(k): round(float(v), 3) for k, v in types.head(4).items()}
    mc = completeness(cat["mag"].to_numpy())
    out["mc"] = mc
    out["b_value"] = b_value(cat["mag"].to_numpy(), mc) if mc is not None else None
    out["last_5_years"] = int((cat["time"] >= now - pd.Timedelta(days=5 * 365)).sum())
    return out


def fetch(lat: float, lon: float, session: requests.Session | None = None, radius_km: int = RADIUS_KM) -> pd.DataFrame:
    text = http.get(FDSN_URL, {"format": "text", "latitude": round(lat, 3), "longitude": round(lon, 3),
                               "maxradiuskm": radius_km, "starttime": START, "minmagnitude": MIN_MAG,
                               "orderby": "time-asc", "limit": LIMIT}, kind="text", session=session, timeout=40)
    return parse_catalogue(text or "")
