"""Deployable serving models under a size constraint (Phase F).

The research serving bundle (random forests, src.multistep) is 1.8 GB, or
408 MB compressed. That is too big for GitHub and for free hosting with
about 1 GB of memory. Size is therefore treated as a deployment constraint and
the same honest protocol is applied to models that satisfy it:

- Candidates: the residual HistGradientBoosting configurations from
  src.models. Each is a few hundred small trees, so megabytes, not gigabytes.
- Per horizon, the best configuration is chosen on 2024 (fitted on 2023),
  refitted on 2023-2024, and scored once on 2025.
- The 2025 errors are compared with the research random forests (their 2025
  predictions are in reports/lab/), with week-block bootstrap intervals, so
  the cost of compactness is reported, not assumed.
- The deployed bundle is then refitted on all 2023-2025 rows, as in
  src.multistep, and saved compressed.

Run:  python -m src.compact      (about 2 minutes)
"""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime

import joblib
import pandas as pd
from sklearn.base import clone

from src import config
from src.data_validation import load_processed, processed_path
from src.evaluate import block_bootstrap_mae_diff, mae, metrics
from src.features import TARGET, TEMP, FeatureConfig, build_features, feature_columns
from src.lab_data import LAB_DIR, load_predictions
from src.models import candidates
from src.multistep import HORIZONS, eval_origins, target_for
from src.splits import SplitSpec, chronological_split

logger = logging.getLogger(__name__)

COMPACT_BUNDLE = config.MODELS_DIR / f"cloudzy_core_{config.DELHI.slug}_serving_compact.joblib"
REPORT = config.REPORTS_DIR / f"compact_{config.DELHI.slug}.json"
MAX_BUNDLE_MB = 50.0  # comfortably inside GitHub's file limit and free-hosting memory


def compact_grid():
    return [(p, est) for p, est in candidates()["gradient_boosting"] if p["residual"]]


def size_mb(obj) -> float:
    buf = io.BytesIO()
    joblib.dump(obj, buf, compress=3)
    return buf.tell() / 1e6


def serving_table(ref: pd.DataFrame, live: pd.DataFrame, h: int) -> pd.DataFrame:
    table, _ = build_features(live, FeatureConfig(horizon_hours=h))
    table[TARGET] = target_for(table.index, ref[TEMP], h)
    return table.dropna(subset=[TARGET])


def select(table: pd.DataFrame, cols: list[str], h: int):
    sel = chronological_split(table, (SplitSpec("fit", "2023-01-01", "2024-01-01"),
                                      SplitSpec("val", "2024-01-01", "2025-01-01")), horizon_hours=h)
    scored = []
    for params, est in compact_grid():
        m = clone(est).fit(sel["fit"][cols], sel["fit"][TARGET])
        scored.append((mae(sel["val"][TARGET], m.predict(sel["val"][cols])), params, est))
    return min(scored, key=lambda s: s[0])


def run() -> dict:
    ref = load_processed(processed_path(source=config.REANALYSIS))
    live = load_processed(processed_path(source=config.LIVE_ARCHIVE))
    research = load_predictions(LAB_DIR / "serving_test_2025.csv.gz")
    cols = feature_columns()
    origins = eval_origins(live)
    results, deployed = {}, {}
    for h in HORIZONS:
        table = serving_table(ref, live, h)
        val_mae, params, est = select(table, cols, h)
        fit = chronological_split(table, (SplitSpec("fit", "2023-01-01", "2025-01-01"),), horizon_hours=h)["fit"]
        model = clone(est).fit(fit[cols], fit[TARGET])
        y = table.loc[origins, TARGET].to_numpy()
        pred = model.predict(table.loc[origins, cols])
        rf = research[research["horizon_h"] == h].reindex(origins)
        if rf["observed"].isna().any() or (abs(rf["observed"].to_numpy() - y) > 0.006).any():
            raise RuntimeError(f"h={h}: research predictions don't line up with this test set")
        deploy = clone(est).fit(table[cols], table[TARGET])
        results[str(h)] = {
            "params": params, "validation_mae_c": round(val_mae, 4),
            "compact_test": metrics(y, pred),
            "research_rf_test_mae_c": round(mae(y, rf["cloudzy"].to_numpy()), 4),
            "provider_optimistic_test_mae_c": round(mae(y, rf["provider_optimistic"].to_numpy()), 4),
            "compact_vs_research_rf": block_bootstrap_mae_diff(origins, y, pred, rf["cloudzy"].to_numpy()),
            "compact_vs_provider": block_bootstrap_mae_diff(origins, y, pred, rf["provider_optimistic"].to_numpy()),
            "model_size_mb": round(size_mb(deploy), 2),
        }
        deployed[h] = {"model": deploy, "params": params, "validation_mae_c": round(val_mae, 4),
                       "evaluated_model_trained_on": [fit.index.min().isoformat(), fit.index.max().isoformat(), len(fit)],
                       "deployed_model_trained_on": [table.index.min().isoformat(), table.index.max().isoformat(),
                                                     len(table)]}
        r = results[str(h)]
        logger.info("h=%2d  compact %.3f  research RF %.3f  provider %.3f  size %.2f MB  %s", h,
                    r["compact_test"]["mae_c"], r["research_rf_test_mae_c"],
                    r["provider_optimistic_test_mae_c"], r["model_size_mb"], params)

    bundle = {
        "kind": "direct_multi_horizon",
        "family": "gradient_boosting (size-constrained)",
        "inputs_source": config.LIVE_ARCHIVE.model,
        "target_source": config.REANALYSIS.model,
        "feature_columns": cols,
        "feature_config": FeatureConfig().to_dict(),
        "horizons": deployed,
        "test_2025_mae_c": {h: results[str(h)]["compact_test"]["mae_c"] for h in HORIZONS},
        "test_2025_provider_optimistic_mae_c": {h: results[str(h)]["provider_optimistic_test_mae_c"] for h in HORIZONS},
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    total = size_mb(bundle)
    if total > MAX_BUNDLE_MB:
        raise RuntimeError(f"Compact bundle is {total:.1f} MB, above the {MAX_BUNDLE_MB} MB limit")
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, COMPACT_BUNDLE, compress=3)
    report = {
        "constraint": f"compressed bundle <= {MAX_BUNDLE_MB} MB",
        "bundle_file": COMPACT_BUNDLE.name, "bundle_size_mb": round(COMPACT_BUNDLE.stat().st_size / 1e6, 2),
        "research_bundle_size_note": "random-forest serving bundle: 1.8 GB raw, 408 MB compressed",
        "protocol": "Tuned on 2024 (fit 2023), tested once on 2025 (fit 2023-2024); deployed fit 2023-2025.",
        "horizons": results,
    }
    REPORT.write_text(json.dumps(report, indent=2, default=str))
    logger.info("Saved %s (%.1f MB) and %s", COMPACT_BUNDLE.name, report["bundle_size_mb"], REPORT.name)
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    r = run()
    print(f"bundle {r['bundle_file']}: {r['bundle_size_mb']} MB")
    for h, v in r["horizons"].items():
        d, p = v["compact_vs_research_rf"], v["compact_vs_provider"]
        print(f"  h={h:>2}  compact {v['compact_test']['mae_c']:.3f}  RF {v['research_rf_test_mae_c']:.3f}  "
              f"compact−RF {d['mae_diff_c']:+.3f} [{d['ci95_low_c']:+.3f},{d['ci95_high_c']:+.3f}]  "
              f"compact−provider {p['mae_diff_c']:+.3f} [{p['ci95_low_c']:+.3f},{p['ci95_high_c']:+.3f}]  "
              f"{v['model_size_mb']} MB")


if __name__ == "__main__":
    main()
