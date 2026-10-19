# Cloudzy ☁️ — Weather with a little intelligence

Cloudzy is a weather app backed by **Cloudzy Core**, a machine-learning forecasting
engine evaluated against honest baselines. This repository is built engine-first:
the UI comes only after the ML pipeline works and has been measured.

> **Status:** Phase A (ML engine) is complete for one task: predicting Delhi's
> 2 m temperature one hour ahead. On the untouched 2025 test year, the selected
> random forest has an MAE of **0.242 °C**. Persistence scores 0.924 °C and a
> climatological diurnal baseline scores 0.337 °C. These are errors against
> **reanalysis data, not a thermometer** (see the caveats below).

## Quick start (macOS, Apple Silicon)

```bash
cd cloudzy-weather-ai
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pytest                 # 72 offline tests, no network needed
python -m src.data_validation    # download (first run only), validate, write processed CSV
python -m src.features           # build the feature table
python -m src.train              # tune on 2024, test on 2025, save model and report (~1 min)
```

The data step downloads three yearly files on its first run (about 1 s each).
Later runs log `Cached: ... (skipping download)` and make no API calls.

To reproduce the ERA5 comparison run, prefix any command with
`CLOUDZY_WEATHER_MODEL=era5`. Every output file name includes the data source,
so the two runs never overwrite each other.

## Stage 1: data pipeline

| Layer | Location | Contents |
|---|---|---|
| Raw | `data/raw/<city>_<model>_<start>_<end>_<hash>.json` | Untouched API response, request parameters, retrieval timestamp |
| Processed | `data/processed/delhi_hourly_<model>_2023-01-01_2025-12-31.csv` | Validated hourly table, UTC timestamps, `is_gap_fill` flag |
| Report | `reports/data_quality_delhi_<model>.json` | Every check result, counts of every fix, summary stats, source metadata |

### Data decisions (and why)

- **Source: Open-Meteo Historical Weather API, `models=era5_seamless`.** This
  takes temperature and humidity from **ERA5-Land** (~9 km grid) and pressure,
  wind and precipitation from **ERA5** (~25 km). The combination is the same
  for the whole period. Both are ECMWF reanalyses: they blend observations with
  a physics model.
- **Why not plain ERA5 (the first choice)?** At this location, ERA5's hourly
  temperature moves in uneven steps at fixed UTC hours. For example, the mean
  hour-to-hour change is 3.2 °C into 05 UTC but 0.5 °C into 07 UTC, and a real
  thermometer doesn't behave like that. Measured as the mean absolute second
  difference over 2023–2025, ERA5 is 1.00 °C and ERA5-Land is 0.52 °C.
- **Why not plain ERA5-Land?** Through Open-Meteo, it returns *no values at all*
  for pressure, wind and precipitation. Validation now treats an all-missing
  variable as fatal. The first version only counted the missing values, which
  was a gap.
- **How the switch was decided.** The switch was based on the smoothness check
  above, not on model scores. ERA5 results are kept and reported below as a
  comparison.
- **What "observed" means here.** It means the reanalysis value for the grid
  cell at 28.60 N, 77.20 E (elevation 214 m), which the API snapped from the
  requested 28.61 N, 77.21 E. Results describe how well we predict that
  reanalysis, not a specific weather station.
- **Period: 2023-01-01 to 2025-12-31, UTC.** This is three full calendar years
  (26,304 hours), with fixed dates. Each year is requested and cached separately.
- **No imputation in the data layer.** Missing hours become NaN rows marked with
  `is_gap_fill`. Implausible values become NaN and are counted in the report.

### What validation checks

| Category | Checks |
|---|---|
| Fatal (pipeline stops) | Missing columns, unexpected units, non-UTC offset, unparseable or off-the-hour timestamps, duplicate timestamps whose values disagree, data from different grid cells, empty data, a variable with no values at all |
| Fixed and counted | Exact duplicate rows, unsorted rows, missing hours, non-numeric values, physically implausible values |
| Flagged but kept | Hour-to-hour temperature changes larger than 8 °C; any variable more than 5% missing |

**Result (`era5_seamless`):** 26,304 rows. There are no missing hours,
duplicates, missing values, out-of-range values or flagged jumps. Temperature
ranges from 2.7 to 45.6 °C, with a mean of 24.4 °C. The ERA5 run also came out
clean. It had one flagged jump, −9.9 °C at 14→15 UTC on 2025-05-21, when wind
rose from 12 to 30 km/h at the same time, which fits a thunderstorm outflow.

## Stage 2: features and target

For each hour *t* (the time a prediction is issued), the target is the 2 m
temperature at *t + 1 h*. Every feature uses only values at times ≤ *t*.

| Group | Features |
|---|---|
| Current temperature | `temperature_2m` at *t* (also the persistence forecast) |
| Lags | temperature at *t − 1, 2, 3, 6, 12, 24 h* |
| Changes | 1 h and 24 h temperature change |
| Rolling stats | mean and std over 3, 6 and 24 h; min and max over 24 h (windows end at *t*) |
| Other variables at *t* | humidity, surface pressure, wind speed, precipitation |
| Derived | 3 h pressure change, 3 h precipitation total |
| Calendar | hour of day (IST) and day of year, each as sin/cos |

That is 27 features in total. `feature_columns()` in `src/features.py` defines
their exact order, and every saved model stores it.

**How leakage is prevented and tested.**
- Lags and windows use `shift` and right-aligned `rolling` on a complete hourly
  grid. `build_features` refuses any index that is not a sorted, unique, hourly
  UTC grid, because on an irregular index a shift would silently misalign.
- A perturbation test replaces every value after *t* with random numbers,
  rebuilds the features, and requires everything at or before *t* to be
  unchanged.
- Synthetic ramp data (temperature = hour number) checks that each lag is
  exactly *k* hours back, and that the target is exactly one hour ahead.
- Rows touching a gap are dropped, never imputed. A test checks that exactly
  the affected rows disappear.
- The split purges boundary rows whose target lies in the next period (see
  Stage 3). Scaling sits inside the scikit-learn pipeline, so it is fitted on
  training rows only.

The table has 26,279 rows: 26,304 minus 24 warm-up hours, minus the final hour,
which has no target.

## Stage 3: models and evaluation

### Protocol

| Split | Issue times (UTC) | Rows | Used for |
|---|---|---|---|
| Train | 2023-01-02 00:00 → 2023-12-31 22:00 | 8,735 | Fitting candidates |
| Validation | 2024-01-01 00:00 → 2024-12-31 22:00 | 8,783 | Choosing hyperparameters and the final model |
| Test | 2025-01-01 00:00 → 2025-12-31 22:00 | 8,759 | One final score per model, no decisions |

- **Each split is a full year,** so all three cover every season. With a split
  such as "validate on autumn, test on winter", you couldn't tell season effects
  from model effects.
- **Boundary purge:** the last hour of each split is dropped, because its target
  is the first hour of the next split.
- **Selection is by validation MAE only.** Each model family's best
  configuration is then refitted on train + validation (17,518 rows) and scored
  once on 2025.
- **Models:**
  - Persistence.
  - A **climatological diurnal baseline**: the current temperature plus the
    average 1-hour change at that local hour and time of year, learned from
    training data.
  - Linear regression.
  - Random forest.
  - Gradient boosting (`HistGradientBoostingRegressor`). Its built-in early
    stopping is disabled because it holds out a *random* subset of rows.
- **Residual target:** tree models can learn *y − temperature(t)* instead of *y*.
  This is a tuned hyperparameter, and it won for both tree families.
- **Fixed seed:** 42 everywhere.

### Results on the untouched 2025 test year (`era5_seamless`)

| Model | MAE °C | RMSE °C | Bias °C | 90th pct abs error °C | Max abs error °C |
|---|---|---|---|---|---|
| Persistence | 0.924 | 1.263 | 0.000 | 2.10 | 6.40 |
| Diurnal baseline | 0.337 | 0.523 | −0.001 | 0.77 | 5.79 |
| Linear regression | 0.381 | 0.568 | +0.001 | 0.85 | 6.05 |
| **Random forest (selected)** | **0.242** | 0.410 | +0.005 | 0.54 | 6.03 |
| Gradient boosting | 0.248 | 0.408 | +0.004 | 0.56 | 5.95 |

Selected configuration: random forest, residual target, `max_depth=12`,
`min_samples_leaf=5`, 200 trees, `max_features=0.5`. Its validation MAE was
0.251 °C.

**Does it beat the baselines? Yes.** The intervals below are 95% bootstrap
intervals that resample whole weeks, because hourly errors are autocorrelated
and resampling single hours would give falsely narrow intervals.
- **Against persistence:** MAE is 0.682 °C lower, interval [0.598, 0.760]. That
  is 74% lower.
- **Against the diurnal baseline:** MAE is 0.095 °C lower, interval
  [0.079, 0.114]. That is 28% lower.

The second comparison is the honest one. Most of the gain over persistence comes
from simply knowing the typical daily heating and cooling. The model's
additional skill is real but much smaller.

**Where errors occur:**
- **Persistence** fails during morning heating (MAE above 2 °C at 07 and 08 IST)
  and evening cooling (about 1.5 °C at 17–18 IST).
- **The random forest** stays between 0.15 and 0.52 °C at every hour.
- **By season,** the model's MAE is 0.22 °C in winter, 0.28 °C pre-monsoon,
  0.23 °C during the monsoon and 0.24 °C post-monsoon. It is highest in May
  (0.32 °C), when pre-monsoon storms are common.
- **The largest errors (up to about 6 °C)** happen in sudden events, such as
  storm outflows, that no model of this kind can anticipate.

**What didn't work:**
- **Linear regression is worse than the diurnal baseline.** It can only add one
  sin/cos curve to the current temperature, but the typical hourly change
  depends on hour and season *together*. Trees can model that interaction.
- **Deeper gradient boosting overfit:** 800 iterations scored worse than 300 on
  validation.

Figures are in `reports/figures/delhi_era5_seamless/`:

![MAE by hour](reports/figures/delhi_era5_seamless/test_mae_by_hour.png)

### Comparison run on plain ERA5

| Model | Test MAE °C |
|---|---|
| Persistence | 0.900 |
| Diurnal baseline | 0.410 |
| Linear regression | 0.639 |
| **Random forest (selected)** | **0.346** |
| Gradient boosting | 0.355 |

On ERA5, the selected random forest beats the diurnal baseline by 0.064 °C, with
a 95% interval of [0.052, 0.078]. I suspected ERA5's uneven hourly steps might
*inflate* the model's apparent skill. The comparison shows the opposite: the
steps make ERA5 *harder* to predict. So the reason for switching sources is
realism, not better scores.

### Limitations

- **Reanalysis targets, not station observations.** Errors against a real
  thermometer would be larger. A single grid cell also smooths out city-scale
  effects such as the urban heat island.
- **"Available at time t" is idealised.** ERA5 data is published about five days
  late. A live service needs a real-time source of current conditions, and
  switching sources is a distribution shift that must be measured (Phase B/C).
- **One location, one horizon, one test year.** The results don't carry over to
  other cities or to longer lead times without new experiments.
- **Point predictions only.** There are no probabilities or confidence
  intervals. The bootstrap intervals above describe uncertainty in the *MAE
  comparison*, not in individual forecasts.

**Nowcasting vs. forecasting.** Predicting one hour ahead from recent values is
mostly about persistence plus the daily cycle. Forecasting many hours or days
ahead is a different problem. Uncertainty compounds over time, and recent local
values carry less information. Operational forecasts handle long horizons with
physical models of the whole atmosphere. Cloudzy's 1-hour model doesn't replace
a meteorological forecast. Multi-step prediction will need its own strategy and
its own evaluation.

**Observed vs. forecast-provider features.** All 27 features are
observation-type. A provider's forecast for *t + 1* would be a different kind of
input, so it is reserved for a separate, clearly labelled experiment.

## Using a trained model

```python
from src.predict import load_bundle, predict_next_hour
from src.data_validation import load_processed, processed_path

bundle = load_bundle("models/cloudzy_core_delhi_era5_seamless_h1.joblib")
history = load_processed(processed_path()).loc[:"2025-06-15T08:00"]  # ≥ 25 complete hours
predict_next_hour(bundle, history)
# {'issue_time_utc': '2025-06-15T08:00:00+00:00', 'valid_time_utc': '2025-06-15T09:00:00+00:00',
#  'predicted_temperature_2m_c': 37.45..., 'model_family': 'random_forest'}   (actual: 36.6 °C)
```

The bundle stores:
- the fitted model and its hyperparameters
- the ordered feature list and feature configuration
- the training date range
- validation and test metrics
- package versions, Python version, platform, seed and data source

Model files (about 24 MB) are generated by `python -m src.train` and are not
committed.

## Project layout

```
src/config.py            location, period, data source, variables, units, plausible ranges
src/data_collection.py   API client: retries, response checks, caching
src/data_validation.py   validation, cleaning, quality report, processed CSV
src/features.py          features, target, leakage-safe construction
src/splits.py            chronological splits with boundary purge
src/models.py            baselines, residual wrapper, model grids
src/evaluate.py          metrics, breakdowns, week-block bootstrap, figures
src/train.py             tune → select → refit → test → save bundle and report
src/predict.py           load bundle, validate inputs, predict
tests/                   72 offline tests
reports/                 data-quality, feature and evaluation reports (JSON) and figures
data/, models/           generated by the pipeline and ignored by git
```

Three modules go beyond the suggested layout, and each has a reason:
- `config.py` keeps every dataset-defining choice in one place.
- `splits.py` and `models.py` keep the evaluation protocol and the model
  definitions testable on their own, separate from the training script.

## Roadmap

- **A — ML engine** ✅ data → features and leakage tests → baselines and models → chronological evaluation
- **B — Forecast service:** a reusable prediction interface built on `predict.py`; a real-time input source; multi-step strategy and evaluation
- **C — Real weather:** the Open-Meteo forecast API, kept clearly separate from Cloudzy's own predictions
- **D — Web app:** Streamlit and Plotly
- **E — Cloudzy Lab:** a model-evaluation dashboard
- **F — Quality and deployment**

## Attribution and terms

- Weather data by [Open-Meteo.com](https://open-meteo.com/), licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The free API is for
  non-commercial use, within Open-Meteo's published rate limits.
- ERA5: Hersbach, H. et al. (2023), *ERA5 hourly data on single levels from 1940
  to present*. Copernicus Climate Change Service (C3S) Climate Data Store.
  DOI: 10.24381/cds.adbb2d47.
- ERA5-Land: Muñoz Sabater, J. (2019), *ERA5-Land hourly data from 1950 to
  present*. C3S Climate Data Store. DOI: 10.24381/cds.e2161bac.
- Both datasets contain modified Copernicus Climate Change Service information.
