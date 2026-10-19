"""One weather briefing per place, keeping provider data and Cloudzy apart.

The briefing has three clearly separated blocks:
- `provider`: Open-Meteo's forecast (current conditions, hourly data and a 7-day
  outlook). This is the operational forecast and always comes first.
- `rain_outlook`: a simple indicator derived from the provider forecast. It is
  not an official warning.
- `cloudzy`: Cloudzy Core's experimental ML prediction. It is offered only where
  the model is valid: within CLOUDZY_RADIUS_KM of the grid cell it was trained
  on (Delhi). Everywhere else, and whenever live inputs fail validation, it
  says why it is unavailable instead of guessing.

A Cloudzy failure never hides the provider forecast, and provider values are
never relabelled as Cloudzy's.

Run:  python -m src.briefing "New Delhi"
"""

from __future__ import annotations

import argparse
import json
import logging
import math

import pandas as pd
import requests

from src import config
from src.artifacts import ArtifactError
from src.provider import Place, ProviderError, fetch_forecast, rain_outlook, search_locations, upcoming

logger = logging.getLogger(__name__)

# Cloudzy always predicts for one point: the Delhi grid cell it was trained on
# (ERA5-Land cells are ~9 km apart). It is shown only for places close enough
# that this point is a fair stand-in, and the output always names that point.
# Further away, showing it would mislabel Delhi's weather as somewhere else's.
CLOUDZY_RADIUS_KM = 15.0


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cloudzy_block(place: Place, session: requests.Session | None = None, snapshot: bool = True) -> dict:
    d = distance_km(place.latitude, place.longitude, config.DELHI.latitude, config.DELHI.longitude)
    if d > CLOUDZY_RADIUS_KM:
        return {"available": False,
                "reason": f"Cloudzy Core is trained only for {config.DELHI.name}; "
                          f"{place.label} is {d:,.0f} km away."}
    try:
        from src.service import forecast_now  # imported lazily: needs the trained serving bundle
        result = forecast_now(snapshot=snapshot, session=session)
    except FileNotFoundError as exc:
        return {"available": False, "reason": f"No trained serving model: {exc}"}
    except ArtifactError as exc:  # corrupted, incomplete or incompatible model file: say so plainly
        logger.warning("Cloudzy Core model unusable: %s", exc)
        return {"available": False, "reason": f"The Cloudzy Core model file can't be used: {exc}"}
    except Exception as exc:  # validation or network failure: report it, keep the provider forecast
        logger.warning("Cloudzy prediction unavailable: %s", exc)
        _monitor("failure", f"{type(exc).__name__}: {exc}")
        return {"available": False, "reason": f"Live inputs could not be used: {exc}"}
    _monitor("prediction", result)
    return {
        "available": True,
        "prediction_point": f"{config.DELHI.name} training grid cell "
                            f"({config.DELHI.latitude:.2f}° N, {config.DELHI.longitude:.2f}° E)",
        "distance_km_from_place": round(d, 1),
        **result,
    }


def _monitor(kind: str, payload) -> None:
    """Record live predictions and refusals for src.monitoring. Never breaks a forecast."""
    from src import monitoring
    if not monitoring.enabled():
        return
    try:
        monitoring.log_prediction(payload) if kind == "prediction" else monitoring.log_failure(payload)
    except Exception as exc:  # disk full, read-only filesystem, …
        logger.warning("Could not write monitoring log: %s", exc)


def build_briefing(place: Place, hours: int = 24, session: requests.Session | None = None,
                   include_cloudzy: bool = True, now: pd.Timestamp | None = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC")
    forecast = fetch_forecast(place, session=session)
    provider = forecast.to_dict()
    next_hours = upcoming(forecast, now, hours)
    provider["hourly"] = [r for r in provider["hourly"] if pd.Timestamp(r["time"]) >= next_hours.index[0]][:hours]
    return {
        "place": provider["place"],
        "provider": provider,
        "rain_outlook": rain_outlook(forecast, now),
        "cloudzy": cloudzy_block(place, session=session) if include_cloudzy
        else {"available": False, "reason": "Not requested"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("place", nargs="?", default="New Delhi")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    places = search_locations(args.place)
    if not places:
        print(f"No place found for {args.place!r}.")
        return
    try:
        b = build_briefing(places[0])
    except ProviderError as exc:
        print(f"Provider forecast unavailable: {exc}")
        return
    if args.json:
        print(json.dumps(b, indent=2, default=str))
        return
    p, c = b["provider"], b["cloudzy"]
    cur = p["current"]
    print(f"\n☁️  {b['place']['label']}")
    print(f"\n[{p['source']}]")
    print(f"  now: {cur['temperature_2m']} °C ({cur['description']}), feels {cur['apparent_temperature']} °C")
    print(f"  today: {p['daily'][0]['temperature_2m_min']} – {p['daily'][0]['temperature_2m_max']} °C")
    r = b["rain_outlook"]
    print(f"  rain next 24 h: {'expected from ' + r['first_rain_hour_local'][11:16] if r['rain_expected'] else 'not expected'}"
          f" — {r['label']}")
    print("\n[Cloudzy Core — experimental ML]")
    if not c["available"]:
        print(f"  unavailable: {c['reason']}")
    else:
        print(f"  for the {c['prediction_point']}, {c['distance_km_from_place']} km from the searched place")
        for pred in c["cloudzy"]["predictions"]:
            print(f"  +{pred['horizon_h']:>2} h  {pred['valid_time_local'][11:16]}  {pred['temperature_2m_c']:5.1f} °C"
                  f"   (avg error {pred['typical_error_c']:.2f} °C)")
    print(f"\n{p['attribution']}")


if __name__ == "__main__":
    main()
