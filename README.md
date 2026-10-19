# Cloudzy ☁️ — Weather with a little intelligence

Cloudzy is a weather app backed by **Cloudzy Core**, a machine-learning forecasting
engine evaluated against honest baselines. This repository is built engine-first:
the UI comes only after the ML pipeline works and has been measured.

> **Status:** Phase A, Stage 2 (feature engineering and leakage tests) is complete.
> No model has been trained yet, so this README reports no accuracy figures.

## Quick start (macOS, Apple Silicon)

```bash
cd cloudzy-weather-ai
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pytest                 # offline tests, no network needed
python -m src.data_validation    # download (first run only), validate, write processed CSV
python -m src.features           # build the feature table from the processed CSV
```

The second command downloads three yearly files on its first run (about 1 s each).
Later runs log `Cached: ... (skipping download)` and make no API calls. Add
`--force` to `python -m src.data_collection` to re-download.

## Stage 1: data pipeline

| Layer | Location | Contents |
|---|---|---|
| Raw | `data/raw/<city>_<model>_<start>_<end>_<hash>.json` | Untouched API response, request parameters, retrieval timestamp |
| Processed | `data/processed/delhi_hourly_era5_2023-01-01_2025-12-31.csv` | Validated hourly table, UTC timestamps, `is_gap_fill` flag |
| Report | `reports/data_quality_delhi.json` | Every check result, counts of every fix, summary stats, source metadata |

### Data decisions (and why)

- **Source: Open-Meteo Historical Weather API, `models=era5`.** Without a model
  parameter, Open-Meteo returns a "best match" blend of several models. Asking for
  ERA5 alone keeps the source the same across the whole period.
- **ERA5 is a reanalysis, not a weather station.** It blends observations with a
  physics model on a grid of about 25 km. The API snapped our Delhi coordinates
  (28.61 N, 77.21 E) to the grid cell at 28.50 N, 77.25 E (elevation 214 m).
  In this project, "observed" means ERA5 reanalysis values, and results describe
  how well we predict ERA5, not a specific thermometer.
- **Period: 2023-01-01 to 2025-12-31, UTC.** This is three full calendar years
  (26,304 hours). The dates are fixed so the dataset is identical on every run.
  Each year is a separate request: each one stays small and gets its own cache file.
- **Timestamps are stored in UTC.** Local time (Asia/Kolkata, UTC+5:30) is applied
  only when building features such as hour of day.
- **No imputation in the data layer.** Missing hours become NaN rows marked with
  `is_gap_fill`. Implausible values become NaN and are counted in the report.
  Feature engineering must then handle gaps explicitly; nothing hides them.

### What validation checks

Fatal (the pipeline stops): missing columns, unexpected units, non-UTC offset,
unparseable or off-the-hour timestamps, duplicate timestamps whose values
disagree, data from different grid cells, empty data.

Fixed and counted: exact duplicate rows, unsorted rows, missing hours,
non-numeric values, physically implausible values.

Flagged but kept: hour-to-hour temperature changes larger than 8 °C.

### Actual result of the first run (2026-10-19)

- 26,304 rows from 2023-01-01 00:00 to 2025-12-31 23:00 UTC. No missing hours,
  duplicates, missing values or out-of-range values.
- Temperature ranged from 3.7 to 46.5 °C, with a mean of 24.9 °C.
- One temperature jump was flagged: −9.9 °C between 14:00 and 15:00 UTC on
  2025-05-21. In the same hours wind rose from 12 to 30 km/h, which fits a
  thunderstorm outflow, so the value was kept.
- Sanity checks: the warmest local hour is about 14:00 IST and the coolest about
  04:00 IST. Monthly mean temperature ranges from 12 °C in January to 33 °C in
  June. Annual precipitation was 730–900 mm.

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
their exact order, which every saved model will store.

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

**Result on the real data:** 26,279 rows (26,304 minus 24 warm-up hours, minus
the final hour, which has no target), from 2023-01-02 00:00 to 2025-12-31
22:00 UTC, with no NaNs. The current temperature correlates 0.987 with the
target, so persistence is a strong baseline.

**An honest caveat on "available at time t".** ERA5 itself is published about
five days late. In this experiment, "known at t" means "a value for time t that
is in the dataset". A live service (Phase B/C) would need a real-time source
for current conditions, and this switch is a possible distribution shift that
will need checking.

**Observed vs. forecast-provider features.** All 27 features are
observation-type. A provider's forecast for *t + 1* would be a different kind of
input, so it is excluded here and reserved for a separate experiment.

**Nowcasting vs. forecasting.** Predicting one hour ahead from recent values is
mostly about persistence: the temperature an hour from now is usually close to
the temperature now. Forecasting many hours or days ahead is a different
problem. Uncertainty compounds over time, and recent local values carry less
information. Operational forecasts handle long horizons with physical models of
the whole atmosphere. Cloudzy's 1-hour model is a learning exercise with a
narrow scope. It is not a replacement for a meteorological forecast.

## Project layout

```
src/config.py            location, period, variables, units, plausible ranges
src/data_collection.py   API client: retries, response checks, caching
src/data_validation.py   validation, cleaning, quality report, processed CSV
src/features.py          features, target, leakage-safe construction
tests/                   offline tests for both modules
data/raw, data/processed generated by the pipeline and ignored by git
models/, reports/, notebooks/
```

`train.py`, `evaluate.py` and `predict.py` will be added in the
stages that implement them. Empty placeholder files are deliberately left out.
`config.py` is an addition to the suggested layout: it keeps every
dataset-defining choice in one place.

## Roadmap

- **A — ML engine:** data ✅ → features and leakage tests ✅ → persistence baseline,
  linear regression, random forest and gradient boosting → chronological
  train/validation/test evaluation
- **B — Forecast service:** a reusable prediction interface
- **C — Real weather:** the Open-Meteo forecast API, kept clearly separate from Cloudzy's own predictions
- **D — Web app:** Streamlit and Plotly
- **E — Cloudzy Lab:** a model-evaluation dashboard
- **F — Quality and deployment**

## Attribution and terms

- Weather data by [Open-Meteo.com](https://open-meteo.com/), licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The free API is for
  non-commercial use, within Open-Meteo's published rate limits.
- ERA5 reanalysis: Hersbach, H. et al. (2023), *ERA5 hourly data on single levels
  from 1940 to present*. Copernicus Climate Change Service (C3S) Climate Data Store.
  DOI: 10.24381/cds.adbb2d47. The data contains modified Copernicus Climate
  Change Service information.
