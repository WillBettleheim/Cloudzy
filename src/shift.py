"""Distribution shift between training data (reanalysis) and live inputs (ECMWF IFS).

Cloudzy Core was trained on reanalysis (era5_seamless). A live service can only
get NWP model output (ECMWF IFS via the Open-Meteo Forecast API). This module:

1. Compares the two sources hour by hour for 2023-2025 (bias, MAE, correlation,
   spread, hourly roughness), per variable and per year.
2. Measures what the shift does to the 1-hour model, using these setups:

   | setup | trained on (inputs → target)      | evaluated on (inputs → target) |
   |-------|-----------------------------------|--------------------------------|
   | A     | reanalysis → reanalysis           | reanalysis → reanalysis        |
   | B     | reanalysis → reanalysis           | IFS → reanalysis               |
   | C     | reanalysis → reanalysis           | IFS → IFS                      |
   | D     | IFS → IFS                         | IFS → IFS                      |
   | E     | IFS → reanalysis (cross-source)   | IFS → reanalysis               |

   B and E answer the service question: given live inputs, how well do we
   predict the reanalysis "truth proxy" an hour later? C and D answer: how well
   do we predict the live source's own next hour?

Protocol: every setup uses the configuration selected in Phase A (no re-tuning).
Models are fitted on 2023 and compared on 2024 to *choose* the serving setup.
They are then refitted on 2023-2024 and scored once on 2025.

Important caveat: the IFS archive keeps, for each hour, the value from the most
recent model run covering it. A real-time system at time t only has runs that
were already published, so its newest inputs come from somewhat longer lead
times. This replay is therefore a best case for live inputs. The forecast
service logs live snapshots so the real gap can be measured over time. For the
same reason, the IFS value *for t+1* is never used as a feature here: in the
archive it may come from a run started after t, which would be leakage.

Run:  python -m src.shift
"""

from __future__ import annotations

import json
import logging

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.base import clone

from src import config
from src.data_validation import load_processed, processed_path
from src.evaluate import INK, INK_MUTED, _save, _style, block_bootstrap_mae_diff, metrics
from src.features import TARGET, TEMP, FeatureConfig, build_features, feature_columns
from src.models import DiurnalChangeBaseline, PersistenceModel
from src.predict import load_bundle
from src.splits import SplitSpec, chronological_split

logger = logging.getLogger(__name__)

PROVIDER = "provider_next_hour_optimistic"

PHASE_A_MODEL = config.MODELS_DIR / f"cloudzy_core_{config.DELHI.slug}_{config.REANALYSIS.model}_h1.joblib"
VARIABLES = list(config.HOURLY_VARIABLES)
YEAR_COLORS = ("#2a78d6", "#eb6834", "#1baf7a")  # validated categorical slots 1-3

SELECT_SPECS = (SplitSpec("fit", "2023-01-01", "2024-01-01"), SplitSpec("eval", "2024-01-01", "2025-01-01"))
TEST_SPECS = (SplitSpec("fit", "2023-01-01", "2025-01-01"), SplitSpec("eval", "2025-01-01", "2026-01-01"))


def compare_sources(ref: pd.DataFrame, live: pd.DataFrame) -> dict:
    """Paired statistics of live - reference, overall and per year."""
    out = {}
    both = ref.index.intersection(live.index)
    years = both.year
    for var in VARIABLES:
        r, lv = ref.loc[both, var], live.loc[both, var]
        ok = r.notna() & lv.notna()
        per = {}
        for label, mask in [("all", ok), *[(str(y), ok & (years == y)) for y in sorted(set(years))]]:
            rr, ll = r[mask], lv[mask]
            d = ll - rr
            per[label] = {
                "n": int(mask.sum()),
                "bias": round(float(d.mean()), 3),
                "mae": round(float(d.abs().mean()), 3),
                "corr": round(float(np.corrcoef(rr, ll)[0, 1]), 4),
                "std_ratio": round(float(ll.std() / rr.std()), 3),
                "ref_mean": round(float(rr.mean()), 3),
                "live_mean": round(float(ll.mean()), 3),
            }
        out[var] = per
    rough = {}
    for name, df in (("reanalysis", ref), ("ifs", live)):
        t = df[TEMP]
        rough[name] = {
            str(y): {
                "mean_abs_change": round(float(t[t.index.year == y].diff().abs().mean()), 3),
                "mean_abs_second_diff": round(float(t[t.index.year == y].diff().diff().abs().mean()), 3),
            }
            for y in sorted(set(t.index.year))
        }
    out["temperature_roughness"] = rough
    return out


def tables(ref: pd.DataFrame, live: pd.DataFrame, cfg: FeatureConfig) -> dict[str, pd.DataFrame]:
    """Feature tables for each (input source, target source) pairing."""
    ref_tab, _ = build_features(ref, cfg)
    live_tab, _ = build_features(live, cfg)
    ref_target = ref[TEMP].shift(-cfg.horizon_hours)
    cross = live_tab.copy()
    cross[TARGET] = ref_target.reindex(cross.index)
    cross = cross.dropna(subset=[TARGET])
    # Provider baseline (evaluation only, never a feature): the IFS value for t+1
    # used directly as the prediction. Optimistic, because in the archive that
    # value may come from a run started after t.
    cross[PROVIDER] = live[TEMP].shift(-cfg.horizon_hours).reindex(cross.index)
    return {"ref_ref": ref_tab, "live_live": live_tab, "live_ref": cross}


SETUPS = {  # name: (training table, evaluation table)
    "A": ("ref_ref", "ref_ref"),
    "B": ("ref_ref", "live_ref"),
    "C": ("ref_ref", "live_live"),
    "D": ("live_live", "live_live"),
    "E": ("live_ref", "live_ref"),
}


def run_setups(tabs: dict[str, pd.DataFrame], template, specs, cols) -> dict:
    results, preds = {}, {}
    split = {k: chronological_split(t, specs) for k, t in tabs.items()}
    for name, (train_key, eval_key) in SETUPS.items():
        tr, ev = split[train_key]["fit"], split[eval_key]["eval"]
        model = clone(template).fit(tr[cols], tr[TARGET])
        diurnal = DiurnalChangeBaseline().fit(tr[cols], tr[TARGET])
        p = {
            "model": model.predict(ev[cols]),
            "persistence": PersistenceModel().fit(ev[cols]).predict(ev[cols]),
            "diurnal_baseline": diurnal.predict(ev[cols]),
        }
        if PROVIDER in ev:
            p[PROVIDER] = ev[PROVIDER].to_numpy()
        y = ev[TARGET].to_numpy()
        results[name] = {
            "train": train_key, "eval": eval_key,
            "eval_rows": len(ev),
            "eval_range_utc": [ev.index.min().isoformat(), ev.index.max().isoformat()],
            **{k: metrics(y, v) for k, v in p.items()},
            "model_vs_diurnal": block_bootstrap_mae_diff(ev.index, y, p["model"], p["diurnal_baseline"]),
        }
        preds[name] = (ev.index, y, p)
        logger.info("Setup %s  model MAE %.3f  persistence %.3f  diurnal %.3f", name,
                    results[name]["model"]["mae_c"], results[name]["persistence"]["mae_c"],
                    results[name]["diurnal_baseline"]["mae_c"])
    return results, preds


def rolling_monthly(table: pd.DataFrame, template, cols, year: int = 2025, horizon_hours: int = 1) -> dict:
    """Refit at the start of every month on all earlier rows, then predict that month.

    This tests whether frequent recalibration can follow a drifting live source.
    Training rows whose target lies at or after the month start are excluded.
    """
    preds, idx, ys = [], [], []
    horizon = pd.Timedelta(hours=horizon_hours)
    for month in range(1, 13):
        start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
        end = start + pd.offsets.MonthBegin(1)
        tr = table[table.index + horizon < start]
        ev = table[(table.index >= start) & (table.index < end) & (table.index + horizon < pd.Timestamp(year + 1, 1, 1, tz="UTC"))]
        model = clone(template).fit(tr[cols], tr[TARGET])
        preds.append(model.predict(ev[cols]))
        idx.append(ev.index)
        ys.append(ev[TARGET].to_numpy())
    index = idx[0].append(idx[1:])
    y, p = np.concatenate(ys), np.concatenate(preds)
    return {"index": index, "y": y, "pred": p, "metrics": metrics(y, p)}


def bias_figure(ref: pd.DataFrame, live: pd.DataFrame, path) -> None:
    both = ref.index.intersection(live.index)
    d = (live.loc[both, TEMP] - ref.loc[both, TEMP])
    local = both.tz_convert(config.DELHI.local_timezone)
    fig, ax = plt.subplots(figsize=(7, 4))
    for color, year in zip(YEAR_COLORS, sorted(set(both.year))):
        m = both.year == year
        g = d[m].groupby(local.hour[m]).mean()
        ax.plot(g.index, g.values, color=color, linewidth=2, marker="o", markersize=4, label=str(year))
    ax.axhline(0, color=INK_MUTED, linewidth=1)
    ax.legend(frameon=False, labelcolor=INK)
    _style(ax, "Live input bias: ECMWF IFS − reanalysis temperature", "Local hour (IST)", "Mean difference (°C)")
    _save(fig, path)


def run() -> dict:
    cfg = FeatureConfig()
    cols = feature_columns(cfg)
    ref = load_processed(processed_path(source=config.REANALYSIS))
    live = load_processed(processed_path(source=config.LIVE_ARCHIVE))
    template = clone(load_bundle(PHASE_A_MODEL)["model"])

    comparison = compare_sources(ref, live)
    t = comparison[TEMP]["all"]
    logger.info("Temperature IFS - reanalysis: bias %+.2f °C, MAE %.2f °C, r=%.3f", t["bias"], t["mae"], t["corr"])

    tabs = tables(ref, live, cfg)
    selection, selection_preds = run_setups(tabs, template, SELECT_SPECS, cols)
    serving_candidates = ["B", "E"]  # setups whose inputs are live and whose target is the reanalysis
    chosen = min(serving_candidates, key=lambda k: selection[k]["model"]["mae_c"])
    logger.info("Chosen serving setup on 2024 validation: %s", chosen)

    # Should the serving model be refitted monthly? Decided on 2024, not on the test year.
    roll_val = rolling_monthly(tabs[SETUPS[chosen][0]], template, cols, year=2024)
    roll_val_diff = block_bootstrap_mae_diff(roll_val["index"], roll_val["y"], roll_val["pred"],
                                             selection_preds[chosen][2]["model"])
    monthly_refit = roll_val_diff["ci95_high_c"] < 0
    logger.info("Monthly refit on 2024: MAE %.3f vs static %.3f → %s", roll_val["metrics"]["mae_c"],
                selection[chosen]["model"]["mae_c"], "adopt" if monthly_refit else "reject")
    test, test_preds = run_setups(tabs, template, TEST_SPECS, cols)

    rolling = rolling_monthly(tabs[SETUPS[chosen][0]], template, cols, year=2025)
    _, _, static = test_preds[chosen]
    rolling_vs_static = block_bootstrap_mae_diff(rolling["index"], rolling["y"], rolling["pred"], static["model"])
    logger.info("%s with monthly refits, 2025: MAE %.3f (static %.3f)", chosen, rolling["metrics"]["mae_c"],
                test[chosen]["model"]["mae_c"])

    fig_dir = config.REPORTS_DIR / "figures" / "shift"
    fig_dir.mkdir(parents=True, exist_ok=True)
    bias_figure(ref, live, fig_dir / "ifs_minus_reanalysis_by_hour.png")

    report = {
        "reference_source": config.REANALYSIS.model,
        "live_source": config.LIVE_ARCHIVE.model,
        "source_comparison": comparison,
        "setups": {k: {"train": f"{a} (inputs_target)", "eval": b} for k, (a, b) in SETUPS.items()},
        "model_config": "Phase A selected random forest (residual, max_depth=12, min_samples_leaf=5)",
        "selection_2024": selection,
        "chosen_serving_setup": chosen,
        "selection_2024_monthly_refit": {"metrics": roll_val["metrics"], "vs_static": roll_val_diff,
                                         "adopted": monthly_refit},
        "test_2025": test,
        "test_2025_monthly_refit": {"metrics": rolling["metrics"], "vs_static_E": rolling_vs_static},
        "caveat": "IFS archive replay is a best case for live inputs (latest run per hour).",
    }
    out = config.REPORTS_DIR / f"shift_{config.DELHI.slug}_{config.REANALYSIS.model}_vs_{config.LIVE_ARCHIVE.model}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    logger.info("Shift report: %s", out.name)
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    r = run()
    for var, per in r["source_comparison"].items():
        if var != "temperature_roughness":
            print(f"{var:22s}", {k: (v['bias'], v['mae'], v['corr'], v['std_ratio']) for k, v in per.items()})
    print("roughness", r["source_comparison"]["temperature_roughness"])
    for phase in ("selection_2024", "test_2025"):
        print(f"\n{phase}")
        for k, v in r[phase].items():
            b = v["model_vs_diurnal"]
            print(f"  {k} {v['train']:>9s} → {v['eval']:<9s} model {v['model']['mae_c']:.3f}  "
                  f"persistence {v['persistence']['mae_c']:.3f}  diurnal {v['diurnal_baseline']['mae_c']:.3f}  "
                  f"model−diurnal {b['mae_diff_c']:+.3f} [{b['ci95_low_c']:+.3f}, {b['ci95_high_c']:+.3f}]")
    print("chosen:", r["chosen_serving_setup"])
    for k in ("B", "E"):
        print(k, "provider t+1 (optimistic) MAE", r["test_2025"][k][PROVIDER]["mae_c"])
    print("monthly refit adopted on 2024:", r["selection_2024_monthly_refit"]["adopted"])
    m = r["test_2025_monthly_refit"]
    print("monthly refit 2025:", m["metrics"]["mae_c"], "vs static diff", m["vs_static_E"])


if __name__ == "__main__":
    main()
