"""One command for Cloudzy Core's 1-hour temperature model: fetch → train → evaluate.

Examples
  python -m src.pipeline all                                   # the configured Delhi model, 2023-2025
  python -m src.pipeline all --city Mumbai --country IN        # any place found by Open-Meteo geocoding
  python -m src.pipeline all --city Reykjavik --start 2022-01-01 --end 2025-12-31
  python -m src.pipeline evaluate                              # re-check a saved model, no retraining
  python -m src.pipeline fetch --lat 51.5 --lon -0.12 --name London --timezone Europe/London

Steps
- fetch:    download (cached) hourly reanalysis from the Open-Meteo archive API, then validate it
            and write the processed CSV (src.data_validation).
- train:    build leakage-safe features, split chronologically (one calendar year each for the
            default 2023-2025 period, otherwise 70/15/15 by time), tune every candidate on
            validation, refit, score once on test, save the selected model, its metadata and a
            report (src.train).
- evaluate: reload the saved model through the verified loader, re-score the held-out test
            period, check that it matches the recorded test MAE, and make one example
            prediction from real inputs.

Data provenance: ERA5/ERA5-Land reanalysis via Open-Meteo, a model-based reconstruction of
past weather. These are not station observations.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date

import pandas as pd

from src import config

logger = logging.getLogger(__name__)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "place"


def resolve_location(city: str | None = None, country: str | None = None, lat: float | None = None,
                     lon: float | None = None, name: str | None = None, timezone: str | None = None,
                     search=None) -> config.Location:
    """The configured Delhi location by default; otherwise a geocoded city or explicit coordinates."""
    if city:
        from src.provider import search_locations
        matches = (search or search_locations)(city, count=10)
        if country:
            matches = [p for p in matches if (p.country_code or "").upper() == country.upper()]
        if not matches:
            raise SystemExit(f"No place found for {city!r}" + (f" in {country}" if country else ""))
        p = matches[0]
        logger.info("Using %s (%.4f, %.4f, %s)", p.label, p.latitude, p.longitude, p.timezone)
        return config.Location(slugify(p.name if not country else f"{p.name}-{country}"), p.label,
                               p.latitude, p.longitude, p.timezone)
    if lat is not None or lon is not None:
        from src.geolocation import validate_coordinates
        la, lo = validate_coordinates(lat, lon)
        if not (name and timezone):
            raise SystemExit("--lat/--lon also need --name and --timezone (an IANA name such as Europe/London)")
        return config.Location(slugify(name), name, la, lo, timezone)
    return config.DELHI


def fetch(location: config.Location, start: date, end: date, force: bool = False) -> dict:
    from src.data_validation import run_pipeline
    _, report = run_pipeline(force_download=force, location=location, start=start, end=end)
    return {"rows": report.rows_clean, "missing_timestamps": report.missing_timestamps_inserted,
            "missing_values": report.missing_values, "start": report.start_utc, "end": report.end_utc}


def train(location: config.Location, start: date, end: date) -> dict:
    from src.train import run
    return run(location, start, end)


def evaluate(location: config.Location, start: date, end: date) -> dict:
    from src.artifacts import feature_config_of, read_metadata
    from src.data_validation import load_processed, processed_path
    from src.evaluate import metrics
    from src.features import TARGET, TEMP, build_features, feature_columns
    from src.predict import load_bundle, predict, predict_next_hour
    from src.splits import chronological_split
    from src.train import model_path, splits_for, tag_for

    path = model_path(location, config.REANALYSIS, start, end)
    bundle = load_bundle(path)  # verified: schema, keys, checksum
    cfg = feature_config_of(bundle)
    processed = load_processed(processed_path(location, config.REANALYSIS, start, end))
    table, _ = build_features(processed, cfg)
    test = chronological_split(table, splits_for(start, end), cfg.horizon_hours)["test"]
    cols = feature_columns(cfg)
    y = test[TARGET].to_numpy()
    m = metrics(y, predict(bundle, test[cols]))
    p = metrics(y, test[TEMP].to_numpy())
    recorded = bundle["test_metrics"]["mae_c"]
    if abs(m["mae_c"] - recorded) > 0.002:
        raise SystemExit(f"Reloaded model scores MAE {m['mae_c']:.4f} °C, but {recorded:.4f} °C was recorded: "
                         "the artifact doesn't match its evaluation.")
    issue = processed.index[-2]  # last hour whose next hour is also known, so we can compare
    example = predict_next_hour(bundle, processed.loc[:issue])
    observed = float(processed.loc[issue + pd.Timedelta(hours=1), TEMP])
    report = json.loads((config.REPORTS_DIR / f"evaluation_{tag_for(location, config.REANALYSIS, start, end)}_h1.json").read_text())
    return {
        "model": (read_metadata(path) or {}).get("artifact_version", path.name),
        "selected": report["selected"], "split_rule": report["split_rule"] if "split_rule" in report else "calendar years",
        "splits": report["splits"],
        "test_metrics_all": {k: (v["mae_c"], v["rmse_c"]) for k, v in report["test_metrics"].items()},
        "reloaded_test": {"n": m["n"], "mae_c": m["mae_c"], "rmse_c": m["rmse_c"], "matches_recorded": True},
        "persistence_test": {"mae_c": p["mae_c"], "rmse_c": p["rmse_c"]},
        "verdict": report["verdict"],
        "example_prediction": {**example, "observed_next_hour_c": observed},
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["fetch", "train", "evaluate", "all"])
    ap.add_argument("--city"), ap.add_argument("--country", help="ISO country code to pick among same-name places")
    ap.add_argument("--lat", type=float), ap.add_argument("--lon", type=float)
    ap.add_argument("--name"), ap.add_argument("--timezone")
    ap.add_argument("--start", type=date.fromisoformat, default=config.START_DATE)
    ap.add_argument("--end", type=date.fromisoformat, default=config.END_DATE)
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.start >= args.end:
        raise SystemExit("--start must be before --end")
    loc = resolve_location(args.city, args.country, args.lat, args.lon, args.name, args.timezone)
    out = {}
    if args.step in ("fetch", "all"):
        out["fetch"] = fetch(loc, args.start, args.end, args.force)
    if args.step in ("train", "all"):
        r = train(loc, args.start, args.end)
        out["train"] = {"selected": r["selected"], "verdict": r["verdict"], "model_file": r["model_file"]}
    if args.step in ("evaluate", "all"):
        out["evaluate"] = evaluate(loc, args.start, args.end)
    json.dump(out, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
