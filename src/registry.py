"""Cloudzy's model registry: every forecasting system in the app, what it predicts and how it was checked.

Two kinds of entries, never mixed:
- **ML models** (Cloudzy Core), read from each artifact's JSON sidecar (src.artifacts), which records the
  artifact's SHA-256, algorithm and parameters, target, inputs and target sources, feature schema, training
  ranges, splits, metrics and library versions. The registry checks the artifact file against that checksum:
  - "verified": the file is here and matches, so predictions can be reproduced from this checkout;
  - "metadata only": the file isn't in this checkout (research artifacts aren't committed), so the metrics are
    documented but can't be re-run from here;
  - "checksum mismatch": the file was changed after it was described; it must not be trusted.
- **Other systems**: provider forecasts, baselines and rule engines. They are listed so the app never implies
  that a provider value or a rule is a Cloudzy ML prediction.

Nothing here is computed from live traffic except the optional tracking summary, which comes from evaluated
records only (src.monitoring).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src import config
from src.artifacts import file_sha256

DEPLOYED = "cloudzy_core_delhi_serving_compact.joblib"
STATUS_LABELS = {"deployed": "Deployed (serves the app)", "research": "Research (offline only)"}


@dataclass
class ModelEntry:
    id: str
    version: str
    status: str                    # deployed | research
    artifact: str                  # verified | metadata only | checksum mismatch
    algorithm: str
    parameters: dict
    target: str
    unit: str
    location: str
    horizons_h: list[int]
    inputs_source: str
    target_source: str
    trained: dict                  # horizon → {first, last, rows}
    validation: str                # how it was validated, in words
    metrics: list[dict]            # each: name, value, unit, n, period, method
    feature_count: int
    features: list[str]
    library_versions: dict
    described_at_utc: str
    limitations: list[str] = field(default_factory=list)
    previous_version: dict | None = None   # what this artifact replaced, and how both scored on the same month
    reproduced: str = "not checked"         # from reports/reproducibility.json, for this exact version


@dataclass(frozen=True)
class OtherArtifact:
    """A file in models/ with no sidecar: listed so nothing is hidden, but nothing about it is claimed."""
    file: str
    size_mb: float
    role: str                      # backup | undocumented


@dataclass(frozen=True)
class SystemEntry:
    name: str
    kind: str          # provider forecast | baseline | rule engine | derived index | official data
    predicts: str
    source: str
    evaluated: str     # how (or whether) Cloudzy evaluates it
    ml: bool = False


SYSTEMS = (
    SystemEntry("Open-Meteo forecast (best_match)", "provider forecast",
                "Hourly and daily weather for any place, 7 days", "Open-Meteo Forecast API (national weather models)",
                "Not evaluated by Cloudzy; shown as the provider's forecast"),
    SystemEntry("ECMWF IFS value at t+h", "baseline", "Delhi 2 m temperature at the same horizons as Cloudzy Core",
                "Open-Meteo Forecast / Historical Forecast API", "Scored on the 2025 test year and on tracked predictions"),
    SystemEntry("ECMWF IFS forecast from 48 h earlier", "baseline",
                "Delhi 2 m temperature at t+h, from the run issued about 48 h before t+h (Previous Runs API)",
                "Open-Meteo Previous Runs API", "Scored on the 2026-01..08 test period (src.mos); also a Cloudzy "
                "Core input"),
    SystemEntry("Persistence", "baseline", "Temperature at t+h = temperature at t", "Computed",
                "Scored on the 2025 test year"),
    SystemEntry("Diurnal climatology", "baseline", "Temperature at t plus the usual change at that hour",
                "Computed from training years", "Scored on the 2025 test year (Phase A)"),
    SystemEntry("Linear regression", "baseline", "Delhi temperature +1 h from the same features", "Computed",
                "Scored on the 2025 test year (Phase A)", ml=True),
    SystemEntry("CAMS air-quality forecast", "provider forecast", "Pollutants and AQI, ~4-5 days",
                "Open-Meteo Air Quality API", "Not evaluated by Cloudzy (no station observations connected)"),
    SystemEntry("India NAQI", "derived index", "CPCB index from CAMS concentrations", "Calculated by Cloudzy (CPCB rules)",
                "Checked against CPCB breakpoints in tests; not an ML model"),
    SystemEntry("Outdoor Intelligence", "rule engine", "Suitable / caution / avoid per hour",
                "Published guidance (EPA, EEA, CPCB, NWS, WHO, ECCC)", "Rules unit-tested; not a statistical model"),
    SystemEntry("Earth Intelligence feeds", "official data", "Disaster events, warnings, detections",
                "USGS, GDACS, NOAA, NASA FIRMS, USDM, Smithsonian", "Parsed and validated; Cloudzy makes no hazard "
                "predictions"),
)


def _location(sidecar: dict, name: str) -> str:
    for slug, label in (("delhi", "New Delhi grid cell"), ("mumbai", "Mumbai grid cell"),
                        ("reykjavik", "Reykjavík grid cell")):
        if slug in name:
            return label
    return "unknown"


def _trained(sidecar: dict) -> dict:
    out = {}
    for h, m in sidecar.get("models", {}).items():
        t = m.get("trained_on")
        if isinstance(t, list) and len(t) == 3:
            out[int(h)] = {"first": t[0], "last": t[1], "rows": t[2]}
        elif isinstance(t, dict):
            out[int(h)] = {"first": t.get("first_issue_time_utc"), "last": t.get("last_issue_time_utc"),
                           "rows": t.get("rows")}
    return out


def _metrics(sidecar: dict, deployed: bool, report_dir: Path) -> list[dict]:
    m = sidecar.get("metrics", {})
    rows: list[dict] = []
    if deployed:
        prov = sidecar.get("provenance") or {}
        month = (prov.get("holdout_month") or ["2026-09"])[0][:7]
        held_n = prov.get("holdout_rows") or sidecar.get("holdout_rows") or {}
        for h, v in (m.get("holdout_mae_c") or {}).items():
            rows.append({"name": f"MAE +{h} h", "value": v, "unit": "°C", "period": f"{month} (held-out month)",
                         "n": held_n.get(str(h)), "method": "deployed models on a month they never saw"})
        if m.get("typical_error_c"):
            exp = report_dir / str(prov.get("experiment_report", ""))
            test_n = ({h: r["test"]["candidate"]["n"] for h, r in json.loads(exp.read_text())["horizons"].items()}
                      if prov.get("experiment_report") and exp.is_file() else {})
            for h, v in m["typical_error_c"].items():
                rows.append({"name": f"MAE +{h} h", "value": v, "unit": "°C", "period": m.get("typical_error_period"),
                             "n": test_n.get(str(h)), "method": "same configuration trained before the test period, tuned on "
                                                  "2025-07..12, scored once (src.mos)"})
        for h, v in (m.get("test_2025_mae_c") or {}).items():
            rows.append({"name": f"MAE +{h} h", "value": v, "unit": "°C", "period": "2025 (test year)", "n": None,
                         "method": "same configuration trained on 2023-2024, scored once on 2025"})
        refit = report_dir / f"refit_{config.DELHI.slug}.json"
        if refit.exists() and prov.get("command") == "python -m src.refit":   # that report scored this artifact
            hold = json.loads(refit.read_text()).get("holdout_mae_c", {})
            for r in rows:
                h = r["name"].split("+")[1].split(" ")[0]
                if r["period"].startswith("2026-09") and h in hold:
                    r["n"] = hold[h].get("holdout_rows")
        return rows
    t = m.get("test_metrics") or {}
    test = (sidecar.get("splits") or {}).get("test") or {}
    period = (f"test {test.get('first_issue_time_utc', '')[:10]} → {test.get('last_issue_time_utc', '')[:10]}"
              if test else "test split")
    if t:
        for key, label in (("mae_c", "MAE"), ("rmse_c", "RMSE"), ("bias_c", "Bias")):
            if key in t:
                rows.append({"name": f"{label} +1 h", "value": t[key], "unit": "°C", "n": t.get("n"),
                             "period": period, "method": "chronological train / validation / test split"})
    if "validation_mae_c" in m:
        rows.append({"name": "Validation MAE +1 h", "value": m["validation_mae_c"], "unit": "°C", "n": None,
                     "period": "validation split", "method": "used for model selection only"})
    return rows


def _reproduced(report_dir: Path) -> dict:
    try:
        models = json.loads((report_dir / "reproducibility.json").read_text())["models"]
    except (OSError, ValueError, KeyError):
        return {}
    return {m.get("artifact_version"): m.get("status", "not checked") for m in models.values()}


def model_entries(models_dir: Path = config.MODELS_DIR, report_dir: Path = config.REPORTS_DIR) -> list[ModelEntry]:
    out = []
    reproduced = _reproduced(report_dir)
    for side in sorted(models_dir.glob("*.json")):
        try:
            s = json.loads(side.read_text())
        except ValueError:
            continue
        if "artifact_file" not in s or "artifact_sha256" not in s:
            continue
        art = models_dir / s["artifact_file"]
        if not art.exists():
            artifact = "metadata only"
        else:
            artifact = "verified" if file_sha256(art) == s["artifact_sha256"] else "checksum mismatch"
        deployed = s["artifact_file"] == DEPLOYED
        first: dict = next(iter(s.get("models", {}).values()), {})
        splits = s.get("splits") or {}
        if deployed and s.get("lagged_forecast"):
            prov = s.get("provenance", {})
            validation = ("Adds the ECMWF forecast issued 48 h before each target hour. Adopted by a rule fixed "
                          "before results were seen (src.mos): tuned on 2025-07..12, compared with the previous "
                          "configuration on 2026-01..08 (lower MAE at every horizon, block-bootstrap CI). "
                          f"Deployed model trained up to {prov.get('refit_data_end_exclusive', '?')} and accepted "
                          f"only after scoring the held-out month {prov.get('holdout_month', ['?'])[0][:7]}.")
        elif deployed:
            prov = s.get("provenance", {})
            validation = (f"Hyperparameters chosen on 2024 after fitting on 2023; configuration scored once on 2025. "
                          f"Deployed refit trained up to {prov.get('refit_data_end_exclusive', '?')} and accepted "
                          f"only after scoring the held-out month {prov.get('holdout_month', ['?'])[0][:7]}.")
        else:
            validation = (f"Chronological split by {s.get('split_rule', 'time')}: "
                          + ", ".join(f"{k} {v.get('first_issue_time_utc', '')[:10]} → "
                                      f"{v.get('last_issue_time_utc', '')[:10]}" for k, v in splits.items()))
        limitations = ["Target is a reanalysis grid value, not a station reading",
                       "Valid only at its own grid cell; not tested elsewhere"]
        if deployed:
            limitations.append("Live inputs can arrive later than in the replay used for testing")
            if s.get("lagged_forecast"):
                limitations.append("Needs the Open-Meteo Previous Runs API live; without it Cloudzy refuses to "
                                   "forecast rather than switch models")
        else:
            limitations.append("Research setting (reanalysis inputs); not available live")
        out.append(ModelEntry(
            id=s["artifact_file"].removesuffix(".joblib"), version=s.get("artifact_version", "?"),
            status="deployed" if deployed else "research", artifact=artifact,
            algorithm=first.get("type", "?"), parameters=first.get("params", {}),
            target="2 m air temperature", unit="°C", location=_location(s, s["artifact_file"]),
            horizons_h=[int(h) for h in s.get("horizons_h", [])], inputs_source=s.get("inputs_source", "?"),
            target_source=s.get("target_source", "?"), trained=_trained(s), validation=validation,
            metrics=_metrics(s, deployed, report_dir), feature_count=len(s.get("features", [])),
            features=[f["name"] for f in s.get("features", [])], library_versions=s.get("library_versions", {}),
            described_at_utc=s.get("described_at_utc", "?"), limitations=limitations,
            previous_version=_previous(s) if deployed else None,
            reproduced=reproduced.get(s.get("artifact_version"), "not checked")))
    out.sort(key=lambda e: (e.status != "deployed", e.id))
    return out


def _previous(sidecar: dict) -> dict | None:
    prev = sidecar.get("previous_version")
    if not prev:
        return None
    old = ((sidecar.get("provenance") or {}).get("previous_bundle") or {}).get("holdout_mae_c") or {}
    new = (sidecar.get("metrics") or {}).get("holdout_mae_c") or {}
    month = ((sidecar.get("provenance") or {}).get("holdout_month") or ["?"])[0][:7]
    rows = [{"horizon_h": int(h), "previous_mae_c": old[h], "current_mae_c": new.get(h)} for h in old if h in new]
    return {**prev, "holdout_month": month, "comparison": sorted(rows, key=lambda r: r["horizon_h"])}


def other_artifacts(models_dir: Path = config.MODELS_DIR) -> list[OtherArtifact]:
    """Model files without a sidecar (backups and old research files). Never loaded here."""
    described = {json.loads(p.read_text()).get("artifact_file") for p in models_dir.glob("*.json")
                 if p.read_text().strip().startswith("{")}
    out = []
    for f in sorted(models_dir.glob("*.joblib")):
        if f.name in described:
            continue
        out.append(OtherArtifact(f.name, round(f.stat().st_size / 1e6, 1),
                                 "backup" if ".backup-" in f.name else "undocumented"))
    return out


def deployed_entry(models_dir: Path = config.MODELS_DIR) -> ModelEntry | None:
    return next((e for e in model_entries(models_dir) if e.status == "deployed"), None)
