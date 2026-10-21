"""Place search for Earth Intelligence: where to point the globe.

Tiers, in order (all results are offered; ambiguous names show several):
1. Coordinates typed as "lat, lon".
2. Countries and states/provinces from Natural Earth (src.geo), with their outline and bounding box.
3. Cities and towns from Open-Meteo geocoding (GeoNames data, CC BY 4.0), the same as the Weather page.
4. Neighbourhoods, streets, landmarks and addresses from OpenStreetMap Nominatim, on submit only (never as you
   type), at most one request per second, cached, with Cloudzy's User-Agent, as the Nominatim usage policy asks.

Each target says how far to zoom: a country or state fits its bounding box; Nominatim results fit the box OSM
gives (a single building's box zooms to street level). Zooming in shows mapped detail, not information about
a particular building.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict

import requests

from src import geo
from src.data_collection import DataCollectionError, fetch_json
from src.geolocation import USER_AGENT
from src.provider import ProviderError

logger = logging.getLogger(__name__)

NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_ATTRIBUTION = "Search © OpenStreetMap contributors (Nominatim, ODbL)"
CACHE_SIZE = 500
_cache: OrderedDict[str, list[dict]] = OrderedDict()
_last_call = [0.0]


def _throttle() -> None:
    wait = 1.0 - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def region_geometry(kind: str, a3: str, unit_id: str | None = None) -> dict | None:
    """A country's or division's outline as GeoJSON, from Natural Earth's flat [lon, lat, …] rings."""
    feature = geo.country(a3) if kind == "country" else geo.division(a3, unit_id or "") if kind == "admin1" else None
    if not feature:
        return None
    polys = [[[[ring[i], ring[i + 1]] for i in range(0, len(ring) - 1, 2)]] for ring in feature["polys"] if len(ring) >= 6]
    return {"type": "MultiPolygon", "coordinates": polys} if polys else None


def _bbox(kind: str, a3: str, unit_id: str | None) -> list[list[float]] | None:
    feature = geo.country(a3) if kind == "country" else geo.division(a3, unit_id or "")
    if not feature or not feature.get("bbox"):
        return None
    w, s, e, n = feature["bbox"]
    return [[w, s], [e, n]]


def parse_nominatim(payload) -> list[dict]:
    out = []
    for r in payload if isinstance(payload, list) else []:
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
            s, n, w, e = (float(x) for x in r["boundingbox"])
        except (KeyError, TypeError, ValueError):
            continue
        kind = r.get("addresstype") or r.get("type") or "place"
        out.append({"kind": "osm", "name": r.get("name") or r.get("display_name", "").split(",")[0],
                    "label": f"{kind.replace('_', ' ').capitalize()} · {r.get('display_name', '')}",
                    "lat": lat, "lon": lon, "bbox": [[w, s], [e, n]], "maxzoom": 18,
                    "source": NOMINATIM_ATTRIBUTION})
    return out


def nominatim(query: str, session: requests.Session | None = None) -> list[dict]:
    key = query.strip().lower()
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    http = session or requests.Session()
    http.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
    _throttle()
    payload = fetch_json(NOMINATIM_SEARCH, {"q": query, "format": "jsonv2", "limit": 5}, session=http, timeout=8,
                         retries=1, max_wait_s=2)
    result = parse_nominatim(payload)
    _cache[key] = result
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return result


def search(query: str, cities=None, addresses: bool = True) -> tuple[list[dict], list[str]]:
    """Targets for a query, and any tier that failed (the others still answer). `cities(query)` → Places."""
    q = (query or "").strip()
    errors: list[str] = []
    if len(q) < 2:
        return [], errors
    coords = geo.parse_coordinates(q)
    if coords:
        lat, lon = coords
        return [{"kind": "point", "name": f"{lat:.4f}, {lon:.4f}", "label": f"Coordinates · {lat:.4f}, {lon:.4f}",
                 "lat": lat, "lon": lon, "zoom": 10}], errors
    out: list[dict] = []
    for e in geo.search(q):
        where = e["cont"] if e["kind"] == "country" else f"{e.get('type') or 'division'}, {e['country']}"
        out.append({"kind": e["kind"], "name": e["name"], "a3": e["a3"], "id": e["id"], "lat": e["lat"],
                    "lon": e["lon"], "bbox": _bbox(e["kind"], e["a3"], e["id"]), "maxzoom": 8,
                    "label": f"{'Country' if e['kind'] == 'country' else 'State/province'} · {e['name']} ({where})",
                    "source": "Natural Earth (public domain)"})
    if cities is not None:
        try:
            for p in cities(q):
                out.append({"kind": "city", "name": p.name, "lat": p.latitude, "lon": p.longitude, "zoom": 10.5,
                            "label": f"City · {p.label}" + (f" · pop. {p.population:,}" if p.population else ""),
                            "source": "Open-Meteo geocoding (GeoNames, CC BY 4.0)"})
        except (ProviderError, DataCollectionError) as exc:
            errors.append(f"city search: {exc}")
    if addresses:
        try:
            seen = {(round(t["lat"], 2), round(t["lon"], 2)) for t in out}
            out += [t for t in nominatim(q) if (round(t["lat"], 2), round(t["lon"], 2)) not in seen]
        except DataCollectionError as exc:
            errors.append(f"address search: {exc}")
    return out, errors
