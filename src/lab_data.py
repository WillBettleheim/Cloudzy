"""Build the data behind Cloudzy Lab (Phase E).

The Lab must show *honest* test predictions: models fitted on 2023-2024 and
scored on 2025, exactly as in the experiments. The saved serving models are
deployment fits that include 2025, so they cannot be used. This module refits
the selected configurations, read from the experiment reports (no re-tuning),
predicts the 2025 test year, and checks that the MAEs reproduce the reports
before writing anything.

Outputs in reports/lab/ (small enough to commit, so the Lab runs without the
raw data or model files):
- phase_a_test_2025.csv.gz   1-hour task, reanalysis → reanalysis, every model
- serving_test_2025.csv.gz   live IFS inputs → reanalysis, horizons 1-24 h (long format)
- shift_bias_by_hour.csv     mean IFS − reanalysis temperature by year and local hour
- manifest.json              provenance and the reproduction checks

Run:  python -m src.lab_data      (about 1 minute)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pandas as pd
from sklearn.base import clone

from src import config
from src.data_validation import load_processed, processed_path
from src.evaluate import mae
from src.features import TARGET, TEMP, FeatureConfig, build_features, feature_columns
from src.models import DiurnalChangeBaseline, PersistenceModel, candidates
from src.multistep import HORIZONS, eval_origins, target_for
from src.splits import DEFAULT_SPLITS, SplitSpec, chronological_split

logger = logging.getLogger(__name__)

LAB_DIR = config.REPORTS_DIR / "lab"
EVAL_REPORT = config.REPORTS_DIR / f"evaluation_{config.DELHI.slug}_{config.REANALYSIS.model}_h1.json"
MULTISTEP_REPORT = config.REPORTS_DIR / f"multistep_{config.DELHI.slug}.json"
TOLERANCE_C = 0.002  # parallel tree averaging differs by ~1e-14; anything larger is a real mismatch
DECIMALS = 2


class ReproductionError(RuntimeError):
    """Refitted models did not reproduce the reported test MAE."""


def single_threaded(model):
    """Make a fitted forest predict with one thread. Parallel predictions add the trees
    in a varying order, which changes results by ~1e-14 and can flip a rounded digit."""
    inner = getattr(model, "estimator_", model)
    if hasattr(inner, "n_jobs"):
        inner.n_jobs = 1
    return model


def estimator_for(family: str, params: dict):
    for p, est in candidates()[family]:
        if p == params:
            return clone(est)
    raise KeyError(f"No {family} candidate with params {params}")


def check(name: str, got: float, expected: float) -> dict:
    ok = abs(got - expected) <= TOLERANCE_C
    if not ok:
        raise ReproductionError(f"{name}: MAE {got:.4f} does not reproduce the report's {expected:.4f}")
    return {"name": name, "mae_c": round(got, 4), "report_mae_c": expected}


def phase_a(ref: pd.DataFrame, report: dict, importance: bool = True) -> tuple[pd.DataFrame, list[dict], pd.DataFrame | None]:
    cols = feature_columns()
    table, _ = build_features(ref)
    s = chronological_split(table, DEFAULT_SPLITS)
    trval = pd.concat([s["train"], s["validation"]])
    test = s["test"]
    out = pd.DataFrame({"observed": test[TARGET]}, index=test.index)
    # Conditions *at issue time* (known when predicting), for error breakdowns.
    out["precip_at_issue_mm"] = test["precipitation"].to_numpy()
    out["temp_at_issue_c"] = test[TEMP].to_numpy()
    checks, fitted = [], {}
    for family, best in report["best_per_family"].items():
        if family == "persistence":
            model = PersistenceModel()
        elif family == "diurnal_baseline":
            model = DiurnalChangeBaseline()
        else:
            model = estimator_for(family, best["params"])
        single_threaded(model.fit(trval[cols], trval[TARGET]))
        fitted[family] = model
        out[family] = model.predict(test[cols])
        checks.append(check(f"phase_a/{family}", mae(out["observed"], out[family]),
                            report["test_metrics"][family]["mae_c"]))
    imp = None
    if importance:
        rf = fitted[SELECTED]
        full = permutation_importance_table(rf, test[cols], test[TARGET]).assign(view="full prediction")
        # The residual model predicts "current temperature + learned change". Scoring the
        # inner model on the change itself shows what the machine-learning part learned.
        change = permutation_importance_table(rf.estimator_, test[cols], test[TARGET] - test[TEMP]).assign(
            view="learned change")
        imp = pd.concat([full, change], ignore_index=True)
    return out, checks, imp


SELECTED = "random_forest"  # Phase A's selected model


def permutation_importance_table(model, X: pd.DataFrame, y: pd.Series, repeats: int = 5) -> pd.DataFrame:
    """How much the 2025 test MAE rises when one feature's values are shuffled.

    Measured on held-out data, in °C. Correlated features (the lags, rolling
    means and current temperature all carry similar information) share
    credit: shuffling one leaves the others to fill in. So a low score means
    "replaceable here", not "irrelevant to the weather".
    """
    from sklearn.inspection import permutation_importance
    r = permutation_importance(model, X, y, scoring="neg_mean_absolute_error", n_repeats=repeats,
                               random_state=42, n_jobs=1)
    return (pd.DataFrame({"feature": X.columns, "mae_increase_c": r.importances_mean,
                          "std_c": r.importances_std})
            .sort_values("mae_increase_c", ascending=False).round(4).reset_index(drop=True))


def serving(ref: pd.DataFrame, live: pd.DataFrame, report: dict) -> tuple[pd.DataFrame, list[dict]]:
    origins = eval_origins(live)
    part2 = report["part2_serving_live_inputs_to_reanalysis"]
    frames, checks = [], []
    for h in HORIZONS:
        cfg = FeatureConfig(horizon_hours=h)
        cols = feature_columns(cfg)
        table, _ = build_features(live, cfg)
        table[TARGET] = target_for(table.index, ref[TEMP], h)
        table = table.dropna(subset=[TARGET])
        fit = chronological_split(table, (SplitSpec("fit", "2023-01-01", "2025-01-01"),), horizon_hours=h)["fit"]
        model = single_threaded(estimator_for("random_forest", part2[str(h)]["direct_params"]).fit(fit[cols], fit[TARGET]))
        X = table.loc[origins, cols]
        f = pd.DataFrame({
            "horizon_h": h,
            "observed": table.loc[origins, TARGET].to_numpy(),
            "cloudzy": model.predict(X),
            "provider_optimistic": target_for(origins, live[TEMP], h),
            "persistence": X[TEMP].to_numpy(),
        }, index=origins)
        checks.append(check(f"serving/h{h}/cloudzy", mae(f["observed"], f["cloudzy"]),
                            part2[str(h)]["direct"]["mae_c"]))
        checks.append(check(f"serving/h{h}/provider", mae(f["observed"], f["provider_optimistic"]),
                            part2[str(h)]["provider_optimistic"]["mae_c"]))
        frames.append(f)
        logger.info("serving h=%d reproduced", h)
    return pd.concat(frames), checks


def shift_bias(ref: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    both = ref.index.intersection(live.index)
    diff = (live.loc[both, TEMP] - ref.loc[both, TEMP]).dropna()
    local = diff.index.tz_convert(config.DELHI.local_timezone)
    out = diff.groupby([diff.index.year, local.hour]).agg(["mean", "count"])
    out.index.names = ["year", "local_hour"]
    return out.rename(columns={"mean": "mean_ifs_minus_reanalysis_c", "count": "hours"}).reset_index().round(3)


def write_predictions(df: pd.DataFrame, path) -> None:
    out = df.copy()
    num = out.select_dtypes("number").columns.drop("horizon_h", errors="ignore")
    out[num] = out[num].round(DECIMALS)
    out.index = out.index.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.index.name = "issue_time_utc"
    out.to_csv(path, compression={"method": "gzip", "mtime": 0})  # byte-identical on rebuild


def load_predictions(path) -> pd.DataFrame:
    df = pd.read_csv(path, compression="gzip")
    df["issue_time_utc"] = pd.to_datetime(df["issue_time_utc"], format="%Y-%m-%dT%H:%M:%SZ", utc=True)
    return df.set_index("issue_time_utc")


def run() -> dict:
    eval_report = json.loads(EVAL_REPORT.read_text())
    ms_report = json.loads(MULTISTEP_REPORT.read_text())
    ref = load_processed(processed_path(source=config.REANALYSIS))
    live = load_processed(processed_path(source=config.LIVE_ARCHIVE))

    a, checks_a, importance = phase_a(ref, eval_report)
    b, checks_b = serving(ref, live, ms_report)
    bias = shift_bias(ref, live)

    LAB_DIR.mkdir(parents=True, exist_ok=True)
    write_predictions(a, LAB_DIR / "phase_a_test_2025.csv.gz")
    write_predictions(b, LAB_DIR / "serving_test_2025.csv.gz")
    bias.to_csv(LAB_DIR / "shift_bias_by_hour.csv", index=False)
    importance.to_csv(LAB_DIR / "feature_importance.csv", index=False)
    manifest = {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "location": config.DELHI.name,
        "reference_source": config.REANALYSIS.model,
        "live_source": config.LIVE_ARCHIVE.model,
        "protocol": "Selected configurations refitted on 2023-2024 and scored on 2025; no re-tuning.",
        "rounding_decimals": DECIMALS,
        "reproduction_checks": checks_a + checks_b,
        "rows": {"phase_a": len(a), "serving": len(b)},
        "feature_importance": {"model": f"Phase A {SELECTED}", "method": "permutation importance on the 2025 test "
                               "set (scoring: MAE, 5 repeats, seed 42)"},
    }
    (LAB_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    logger.info("Lab data written to %s (%d checks passed)", LAB_DIR, len(checks_a) + len(checks_b))
    return manifest


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    m = run()
    for c in m["reproduction_checks"]:
        print(f"  ✓ {c['name']:<32} {c['mae_c']:.4f}  (report {c['report_mae_c']:.4f})")


if __name__ == "__main__":
    main()
