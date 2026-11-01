"""Model artifacts: safe loading, schema checks and metadata sidecars.

Every Cloudzy Core model is a joblib file with a JSON "sidecar" beside it
(`<model>.json`). The sidecar records:
- the artifact version and a SHA-256 of the exact model file;
- the schema version;
- the model type and configuration;
- feature names, order and units, and the target definition;
- data provenance, split date ranges and metrics.

Trust: joblib files are pickles, and loading one can run code. Cloudzy loads
only model files it produced itself, from this repository's `models/` folder,
and never files from users or the network.

Integrity is checked *before* unpickling: when a sidecar exists, the file's
SHA-256 must match it, or nothing is loaded (`verify_checksum`; offline tools
use `load_verified`). A file without a sidecar (a refit backup, say) is one you
produced locally and is loaded as is. The checksum catches a swapped or
corrupted file, not someone who can rewrite both files: that is what control
over the repository is for (SECURITY.md).

Loading (`load_trusted`) fails with a clear ArtifactError instead of crashing
or quietly falling back to something else:
- missing file;
- unreadable or corrupted file (or one written by an incompatible library);
- wrong structure, missing keys;
- a feature schema the current code doesn't produce;
- a sidecar whose checksum doesn't match the file.

Loaded models are cached until the file changes, so the app doesn't re-read
the model on every request.

Run:  python -m src.artifacts models/<file>.joblib   (writes or refreshes the sidecar)
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import joblib

from src.features import FeatureConfig, feature_columns

SCHEMA_VERSION = 1

FEATURE_UNITS = {
    "temperature_2m": "°C", "temp_change_1h": "°C", "temp_change_24h": "°C",
    "relative_humidity_2m": "%", "surface_pressure": "hPa", "pressure_change_3h": "hPa",
    "wind_speed_10m": "km/h", "precipitation": "mm", "precip_sum_3h": "mm",
    "hour_sin": "unitless", "hour_cos": "unitless", "doy_sin": "unitless", "doy_cos": "unitless",
    "ifs_prev2_target": "°C", "ifs_prev2_minus_now": "°C",          # lagged ECMWF forecast (src.mos)
}


def unit_of(feature: str) -> str:
    if feature in FEATURE_UNITS:
        return FEATURE_UNITS[feature]
    if feature.startswith(("temp_lag_", "temp_roll_")):
        return "°C"
    raise KeyError(f"No unit recorded for feature {feature!r}")


class ArtifactError(ValueError):
    """A model file can't be used: missing pieces, corrupted, or incompatible."""


def sidecar_path(model_path: Path) -> Path:
    return Path(model_path).with_suffix(".json")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def feature_config_of(bundle: dict) -> FeatureConfig:
    cfg = bundle.get("feature_config") or {}
    try:
        return FeatureConfig(**{k: tuple(v) if isinstance(v, list) else v for k, v in cfg.items()})
    except TypeError as exc:
        raise ArtifactError(f"Model has an unrecognised feature configuration: {exc}") from exc


def target_definition(horizons: list[int]) -> str:
    hs = ", ".join(f"+{h} h" for h in horizons)
    return (f"temperature_2m (°C) at issue time t {hs}, from the reanalysis target source; inputs use only "
            "values at or before t")


def metadata_for(bundle: dict, model_path: Path, extra: dict | None = None) -> dict:
    """Describe a saved bundle. Everything comes from the bundle, the file itself, or `extra`."""
    cols = list(bundle["feature_columns"])
    horizons = sorted(int(h) for h in bundle.get("horizons", {})) or [int(bundle.get("feature_config", {}).get("horizon_hours", 1))]
    sha = file_sha256(model_path)
    if "horizons" in bundle:
        models = {str(h): {"type": type(e["model"]).__name__ + "(" + type(getattr(e["model"], "estimator_", e["model"])).__name__ + ")",
                           "params": e.get("params"),
                           "trained_on": e.get("deployed_model_trained_on") or e.get("trained_on")}
                  for h, e in bundle["horizons"].items()}
    else:
        m = bundle["model"]
        models = {str(horizons[0]): {"type": type(m).__name__ + "(" + type(getattr(m, "estimator_", m)).__name__ + ")",
                                     "family": bundle.get("model_family"), "params": bundle.get("params"),
                                     "trained_on": bundle.get("trained_on")}}
    meta = {
        "schema_version": SCHEMA_VERSION,
        "artifact_file": Path(model_path).name,
        "artifact_sha256": sha,
        "artifact_version": f"{Path(model_path).stem}@{sha[:12]}",
        "described_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "kind": bundle.get("kind", "single_horizon"),
        "horizons_h": horizons,
        "models": models,
        "features": [{"name": c, "unit": unit_of(c)} for c in cols],
        "feature_config": bundle.get("feature_config"),
        "target": target_definition(horizons) + (
            "; plus the ECMWF forecast for t+h from the run 48 h before it (published at least 24 h before t)"
            if bundle.get("lagged_forecast") else ""),
        "lagged_forecast": bundle.get("lagged_forecast"),
        "inputs_source": bundle.get("inputs_source", bundle.get("provenance", {}).get("data", {}).get("model")),
        "target_source": bundle.get("target_source", bundle.get("provenance", {}).get("data", {}).get("model")),
        "metrics": {k: bundle[k] for k in ("validation_mae_c", "test_metrics", "test_2025_mae_c",
                                            "test_2025_provider_optimistic_mae_c", "typical_error_c",
                                            "typical_error_period", "test_provider_lagged_mae_c", "holdout_mae_c")
                    if k in bundle},
        "provenance": bundle.get("provenance"),
        "library_versions": {p: version(p) for p in ("scikit-learn", "joblib", "numpy", "pandas")},
        "python": sys.version.split()[0],
    }
    meta.update(extra or {})
    return json.loads(json.dumps(meta, default=str))


def write_metadata(model_path: Path, bundle: dict, extra: dict | None = None) -> Path:
    path = sidecar_path(model_path)
    path.write_text(json.dumps(metadata_for(bundle, model_path, extra), indent=2, ensure_ascii=False))
    return path


def read_metadata(model_path: Path) -> dict | None:
    try:
        return json.loads(sidecar_path(model_path).read_text())
    except (FileNotFoundError, ValueError):
        return None


_cache: dict[tuple, dict] = {}
_lock = threading.Lock()


def verify_checksum(path: Path) -> None:
    """Refuse a model file whose SHA-256 differs from its sidecar. Runs before the (pickle) file is loaded."""
    meta = read_metadata(path)
    if meta is not None and meta.get("artifact_sha256") not in (None, file_sha256(path)):
        raise ArtifactError(f"{path.name} doesn't match its metadata file (checksum differs); refresh it with "
                            f"`python -m src.artifacts {path}` only if you produced this model yourself.")


def load_verified(path: Path):
    """Load a model file for offline tools (refit, interval study, evaluation), checksum-verified first."""
    path = Path(path)
    verify_checksum(path)
    return joblib.load(path)


def load_trusted(path: Path, required_keys: set[str]) -> dict:
    """Load a Cloudzy model bundle, verifying it before anyone uses it. Cached until the file changes."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if key in _cache:
            return _cache[key]
    verify_checksum(path)                   # before unpickling: a swapped file never runs
    try:
        bundle = joblib.load(path)
    except Exception as exc:  # truncated file, pickle error, incompatible library version, …
        raise ArtifactError(f"Couldn't read {path.name}: the file may be corrupted or written by an incompatible "
                            f"library version ({type(exc).__name__}). Regenerate it (see the README).") from exc
    if not isinstance(bundle, dict):
        raise ArtifactError(f"{path.name} isn't a Cloudzy model bundle")
    missing = required_keys - set(bundle)
    if missing:
        raise ArtifactError(f"Model bundle {path.name} is missing keys: {sorted(missing)}")
    expected = feature_columns(feature_config_of(bundle)) + list((bundle.get("lagged_forecast") or {}).get("features", []))
    if list(bundle["feature_columns"]) != expected:
        raise ArtifactError(f"{path.name} was trained with a different feature schema than this code produces; "
                            "retrain it with the current pipeline.")
    with _lock:
        _cache[key] = bundle
    return bundle


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    path = Path(sys.argv[1])
    bundle = joblib.load(path)  # a file you produced yourself (see "Trust" above)
    out = write_metadata(path, bundle)
    print(f"Wrote {out} ({read_metadata(path)['artifact_version']})")


if __name__ == "__main__":
    main()
