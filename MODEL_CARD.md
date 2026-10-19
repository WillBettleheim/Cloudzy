# Model card: Cloudzy Core

Cloudzy Core is the machine-learning engine behind the Cloudzy weather app. It
is an **experimental student project**, not an operational weather forecast.

## Model details

| | |
|---|---|
| Task | Predict the 2 m air temperature at New Delhi, 1, 2, 3, 6, 12 and 24 hours ahead |
| Deployed model | Six `HistGradientBoostingRegressor` models (one per horizon), each learning the *change* from the current temperature (residual target) |
| Inputs | 27 features built from the last 25 hours of ECMWF IFS model output for one grid cell: temperature, its lags (1–24 h), changes and rolling statistics, humidity, surface pressure and its 3 h change, wind speed, recent precipitation, and hour-of-day / day-of-year encodings |
| Output | One temperature value per horizon, in °C. A point estimate, not a probability |
| Target | ERA5-Land reanalysis 2 m temperature, via Open-Meteo `era5_seamless` |
| File | `models/cloudzy_core_delhi_serving_compact.joblib` (about 2 MB) |
| Deployed training data | 2023-01-02 to 2026-08-31 UTC (September 2026 held out as the refit acceptance check) |
| Code | `src/compact.py` (selection), `src/refit.py` (monthly refit), `src/service.py` (inference) |

## Intended use

- Learning and demonstration: an end-to-end, honestly evaluated forecasting pipeline.
- Showing how a simple statistical model corrects one weather model toward a
  reanalysis, next to the provider's own forecast.

**Out of scope:**
- Any decision about safety, health, agriculture, aviation or money.
- Any place more than 15 km from the Delhi training point. The app refuses to
  show Cloudzy there.
- Severe-weather warnings. Use national services, such as the India
  Meteorological Department.

## Data

| Source | Use | Licence |
|---|---|---|
| Open-Meteo Historical Weather API, `era5_seamless` (ERA5-Land for temperature and humidity, ERA5 for the rest) | Training target and Phase A inputs | CC BY 4.0, Copernicus |
| Open-Meteo Historical Forecast API, `ecmwf_ifs` | Archived live inputs for training and evaluation | CC BY 4.0 |
| Open-Meteo Forecast API, `ecmwf_ifs` | Live inputs at prediction time | CC BY 4.0 |

All three are model-derived, gridded data (about 9–25 km cells), not station
observations. Validation rejects responses with unexpected units, time zones or
empty variables, and never imputes missing values.

## Evaluation

The protocol is strictly chronological:
- Models are tuned on 2024 after fitting on 2023.
- They are refitted on 2023–2024 and scored **once** on 2025.
- No choice was made using 2025.

Intervals are 95% week-block bootstrap intervals for the difference in mean
absolute error (MAE).

**Deployed models (live IFS inputs → reanalysis), 2025 test MAE in °C:**

| Horizon | Cloudzy (deployed) | Research random forest | IFS value at t+h (optimistic) | Persistence |
|---|---|---|---|---|
| 1 h | 1.119 | 1.099 | 1.419 | 1.796 |
| 2 h | 1.124 | 1.120 | 1.419 | 2.437 |
| 3 h | 1.127 | 1.154 | 1.419 | 3.139 |
| 6 h | 1.232 | 1.214 | 1.419 | 5.014 |
| 12 h | 1.283 | 1.287 | 1.419 | 6.332 |
| 24 h | 1.343 | 1.370 | 1.420 | 1.690 |

- **Compact vs. random forest:** the compact models are about 2 MB instead of
  1.8 GB. They are within ±0.03 °C of the forest at every horizon, and no
  difference is significant.
- **Against IFS's own values:** they are closer to the reanalysis up to 12 h
  (intervals exclude zero). At 24 h the difference is not significant. This
  shows a learned bias correction toward the reanalysis. It doesn't show better
  weather forecasting than ECMWF.
- **Research setting:** with reanalysis inputs instead (Phase A), the 1-hour
  random forest reaches 0.242 °C. Persistence scores 0.924 °C and a
  climatological diurnal baseline 0.337 °C. The gap between the two settings
  comes from disagreement between the input and target sources (an IFS vs.
  reanalysis MAE of 1.13 °C), not from the model.

## Limitations and known issues

- **The target is a reanalysis.** Errors against a real thermometer would be
  larger, and city effects such as the urban heat island are smoothed out.
- **The live source drifts.** The mean IFS − reanalysis temperature bias went
  from −0.23 °C (2023) to +0.10 °C (2024) to +0.99 °C (2025). Monthly refitting
  helped on both 2024 and 2025, and `python -m src.refit` automates it.
- **The replay is a best case.** Archived IFS values come from the newest model
  run covering each hour. A real-time system sees older runs for the latest
  hours, so live errors are likely somewhat larger. The app logs live snapshots
  so this gap can be measured.
- **Artifacts in the target.** On some days the reanalysis jumps 3–4 °C between
  06 and 07 UTC and then goes flat. These jumps are among the largest misses.
- **One location, one test year.** The results don't carry over to other places
  or periods.
- **No uncertainty per prediction.** The "average error" shown in the app is a
  2025 MAE, not a confidence interval.

## Ethical considerations

- **No personal data** is collected or used. Location searches go to
  Open-Meteo's geocoding service.
- **Misinterpretation is the main risk.** The app puts Cloudzy in its own
  labelled panel, states its prediction point, shows the provider forecast first,
  labels the rain indicator as unofficial, and hides Cloudzy outside its
  training area.

## Maintenance

- **Monthly:** run `python -m src.refit`. It refits on data up to the last
  complete month, checks the new models on that month, and keeps a dated backup.
  Commit the updated bundle to redeploy.
- **Weekly:** CI runs live checks against the Open-Meteo APIs to catch upstream
  schema changes.
