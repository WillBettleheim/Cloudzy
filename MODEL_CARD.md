# Model card: Cloudzy Core

Cloudzy Core is the machine-learning engine behind the Cloudzy weather app. It
is an **experimental student project**, not an operational weather forecast.

## Model details

| | |
|---|---|
| Task | Predict the 2 m air temperature at New Delhi, 1, 2, 3, 6, 12 and 24 hours ahead |
| Deployed model | Six `HistGradientBoostingRegressor` models (one per horizon), each learning the *change* from the current temperature (residual target). Version `cloudzy_core_delhi_serving_compact@e67c4fa9ccd8`, deployed 2026-10-22 (see *Version history*) |
| Inputs | 29 features. 27 are built from the last 25 hours of ECMWF IFS model output for one grid cell: temperature, its lags (1–24 h), changes and rolling statistics, humidity, surface pressure and its 3 h change, wind speed, recent precipitation, and hour-of-day / day-of-year encodings. 2 come from the **lagged ECMWF forecast**: what the IFS 0.25° run started about 48 h before the target hour predicted for that hour, and its difference from the current temperature |
| Output | One temperature value per horizon, in °C, with a validated 80% prediction range (95% also calibrated; see *Uncertainty*) |
| Target | ERA5-Land reanalysis 2 m temperature, via Open-Meteo `era5_seamless` |
| File | `models/cloudzy_core_delhi_serving_compact.joblib` (about 2 MB) |
| Deployed training data | 2024-02-05 (when the lagged-forecast archive starts) to 2026-08-31 UTC; September 2026 held out as the acceptance check |
| Code | `src/mos.py` (experiment and deployment), `src/compact.py` (original selection), `src/refit.py` (monthly refit), `src/service.py` (inference), `src/reproduce.py` (reproducibility check) |

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
| Open-Meteo Previous Runs API, `ecmwf_ifs025`, `temperature_2m_previous_day2` | The lagged-forecast input, archived (from 2024-02-05) and live | CC BY 4.0 |
| Iowa Environmental Mesonet METAR archive: Palam (VIDP) and Safdarjung (VIDD) | Independent check against real thermometer readings (not training) | Public METAR data; credit IEM, Iowa State University |

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

**Original configuration (live IFS inputs → reanalysis; deployed until 2026-10-22), 2025 test MAE in °C.** The
current model's evaluation is under *Version history* below.

| Horizon | Cloudzy (27 features) | Research random forest | IFS value at t+h (optimistic) | Persistence |
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

## Version history: the October 2026 upgrade

**What changed.** Two inputs were added: the ECMWF IFS forecast for the target hour from the run issued about
48 hours before it (`temperature_2m_previous_day2`), and that value minus the current temperature. The model
type, target and horizons are unchanged; hyperparameters were re-tuned per horizon on the new inputs.

**Why it is leakage-safe.** The run that `previous_day2` refers to starts 48 h before the valid hour. For every
horizon up to 24 h that is at least 24 h before the issue time t, so the value was published before t. The
`previous_day1` series was rejected: at +24 h its run starts at t itself. Alignment was checked: the lagged
forecast's error against the reanalysis is smallest at a time offset of 0 h (1.22 °C), against 1.43 to 2.91 °C at
±1 to ±3 h.

**How it was decided.** `python -m src.mos` → `reports/mos_delhi.json`. The rule was written in the module before
any result was seen. Adopt only if the candidate has a lower MAE at every horizon, its block-bootstrap 95%
interval excludes zero at four or more horizons, and no horizon is worse by more than 0.05 °C in September 2026.
- **Fit:** 2024-02-05 to 2025-06. **Validation (tuning only):** 2025-07 to 2025-12.
- **Refit** on 2024-02-05 to 2025-12, then **test once** on 2026-01 to 2026-08: 5,832 hours per horizon.
- **Control:** the previous configuration, given the same fit, refit and test rows.

| Horizon | Previous config MAE °C | New config MAE °C | Difference [95% CI] | Lagged ECMWF alone | Ridge, same inputs | Persistence |
|---|---|---|---|---|---|---|
| 1 h | 0.966 | 0.928 | −0.038 [−0.079, −0.001] | 1.446 | 1.023 | 1.869 |
| 2 h | 1.000 | 0.946 | −0.054 [−0.100, −0.001] | 1.446 | 1.062 | 2.475 |
| 3 h | 1.029 | 0.939 | −0.090 [−0.139, −0.035] | 1.446 | 1.075 | 3.145 |
| 6 h | 1.110 | 0.976 | −0.134 [−0.197, −0.074] | 1.446 | 1.125 | 4.983 |
| 12 h | 1.220 | 1.016 | −0.205 [−0.308, −0.115] | 1.447 | 1.114 | 6.248 |
| 24 h | 1.440 | 1.067 | −0.373 [−0.484, −0.270] | 1.445 | 1.084 | 1.868 |

The gain is significant at all six horizons and grows with lead time. The decision was **adopt**.
- **September 2026 (a further month):** the new configuration was again lower everywhere: 0.852, 0.866, 0.882,
  0.930, 0.981 and 1.012 °C, against 0.868 to 1.183 °C.
- **Ridge regression:** with the same inputs it was competitive in September but clearly worse over the
  8-month test.

**Deployment.** `python -m src.mos --deploy` trained the chosen configuration on 2024-02-05 to 2026-08-31. Before
installing it, it compared the new model with the bundle then deployed on September 2026, which neither had
seen. The previous bundle is kept as a dated backup, and its scores are recorded in the new sidecar.

| Horizon | Previous deployed, Sept 2026 MAE °C | New deployed, Sept 2026 MAE °C |
|---|---|---|
| 1 h | 0.829 | 0.816 |
| 2 h | 0.860 | 0.829 |
| 3 h | 0.876 | 0.860 |
| 6 h | 0.917 | 0.903 |
| 12 h | 0.984 | 0.963 |
| 24 h | 1.168 | 0.994 |

The app's "average error" for each prediction is now the 2026-01 to 2026-08 test MAE of this configuration
(`typical_error_c`, labelled with its period); the 2025 figures above describe the previous configuration.

**New dependency.** Live predictions need the Previous Runs API. If it can't be reached, or the value for a
target hour is missing, Cloudzy refuses to predict and says why. It never falls back silently to another model.
The refusal is logged and recorded by the monitoring store.

**Reproducibility.** `python -m src.reproduce` reloads every described artifact and recomputes its recorded
metric from cached data, without retraining. On 2026-10-22, all four matched exactly
(`reports/reproducibility.json`):
- the deployed bundle's September 2026 MAE at all six horizons;
- the three Phase A research models' 2025 test MAE (Delhi, Mumbai, Reykjavík).

## Against real station observations

Everything above scores Cloudzy against the reanalysis, its training target. `python -m src.station_eval` →
`reports/station_eval_delhi.json` compares it with real thermometer readings: routine METARs from Palam airport
(VIDP), 10.4 km from the grid point. The questions were written before the results were seen.

**Setup.**
- **Rows:** the same 2026-01 to 2026-08 test rows as the upgrade experiment, about 5,780 hours per horizon,
  identical for every model.
- **Models:** rebuilt exactly as in that experiment (their reanalysis scores are reproduced first).
- **Why Palam:** Safdarjung, 3.4 km away, has almost no reports before July 2026. Palam was chosen for coverage
  alone, before any scoring.
- **Data checks:** the station data match NOAA ISD's copy of the same reports at 99.9% of hours. METAR
  temperatures are whole degrees, and the station's SYNOP reports differ from its METARs by 0.6 °C on average, so
  the observations carry noise too.

| Horizon | Cloudzy (current) | Cloudzy (previous) | ECMWF IFS value | Reanalysis itself | Cloudzy − IFS [95% CI] |
|---|---|---|---|---|---|
| 1 h | 1.405 | 1.407 | 1.206 | 1.613 | +0.198 [+0.073, +0.317] |
| 2 h | 1.439 | 1.489 | 1.206 | 1.614 | +0.233 [+0.099, +0.358] |
| 3 h | 1.474 | 1.552 | 1.207 | 1.615 | +0.267 [+0.131, +0.397] |
| 6 h | 1.563 | 1.732 | 1.208 | 1.614 | +0.355 [+0.205, +0.496] |
| 12 h | 1.631 | 1.955 | 1.208 | 1.614 | +0.423 [+0.280, +0.560] |
| 24 h | 1.633 | 1.985 | 1.209 | 1.611 | +0.424 [+0.272, +0.566] |

**What this shows.**
- **Q1, against the thermometer:** the raw ECMWF value is closer than Cloudzy at every horizon, significantly.
- **Q2, the upgrade:** the current version is closer to the station than the previous one from +3 h on
  (significant), with no difference at +1 and +2 h.
- **The cause is the target.** The reanalysis is 1.5–1.8 °C colder than the station at night, the urban heat
  island a ~9 km cell doesn't resolve, and slightly warm around midday. Cloudzy inherits about 1 °C of the night
  bias. ECMWF is nearly unbiased at night and about 0.7 °C too warm around midday.
- **A closer station agrees.** Safdarjung, available only from July 2026, gives the same ordering: ECMWF
  1.54 °C, the reanalysis 1.82 °C, Cloudzy 1.83–2.04 °C.
- **What it means.** Cloudzy's improvements are improvements at reproducing the reanalysis. As a guide to what
  a thermometer in Delhi will read, the provider's forecast is better.

**What would address it** was tested next.

### Experiment: training against the station readings

`python -m src.station_model` → `reports/station_model_delhi.json`. The rules were written in the module before
any model was trained or scored.
- **Target:** Palam's reading at t + h.
- **Candidate A:** the current 29 inputs.
- **Candidate B:** also four station features at or before t (the reading, its offset from ECMWF, its 3-hour
  change, and the offset a day earlier). Live, these come from METARs published within minutes.
- **Splits and rows:** the same as above.
- **Rules (per candidate):** beat raw ECMWF at every horizon, significantly at four or more; also beat ECMWF
  plus a local-hour bias correction at four or more; no horizon worse than ECMWF by over 0.05 °C in September.

| Horizon | A | B | ECMWF value | ECMWF + hourly correction | Station persistence | B − ECMWF [95% CI] |
|---|---|---|---|---|---|---|
| 1 h | 1.181 | **0.589** | 1.206 | 1.225 | 0.874 | −0.618 [−0.703, −0.540] |
| 2 h | 1.218 | **0.805** | 1.206 | 1.225 | 1.656 | −0.401 [−0.484, −0.323] |
| 3 h | 1.310 | **0.979** | 1.207 | 1.225 | 2.384 | −0.228 [−0.321, −0.142] |
| 6 h | 1.410 | 1.250 | 1.208 | 1.226 | 4.176 | +0.042 [−0.055, +0.140] |
| 12 h | 1.436 | 1.399 | 1.208 | 1.227 | 5.656 | +0.191 [+0.093, +0.287] |
| 24 h | 1.472 | 1.390 | 1.209 | 1.227 | 1.902 | +0.180 [+0.067, +0.301] |

**Verdict under the rules: neither candidate beats ECMWF.**
- **B up to 3 hours:** it roughly halves ECMWF's error at +1 h, beats persistence, and is significantly better
  than ECMWF and the hourly correction up to +3 h. This also holds at Safdarjung (0.82 against 1.53 °C at +1 h).
- **B from 6 hours:** level at +6 h and significantly worse at +12 and +24 h, so it fails the "every horizon"
  rule.
- **A:** no better than ECMWF anywhere.
- **The hourly correction:** it didn't improve ECMWF, so its midday warm bias isn't stable enough to subtract.

**Context, stated in advance:** the ECMWF baseline is optimistic. The archive holds the newest run for each
hour, which a model at time t couldn't have seen, and this matters most at long horizons. A baseline matched to
what was available at t would need its own experiment.

**What it means.** With live station readings, Cloudzy becomes a good 1–3 hour nowcast for the city's real
temperature. Beyond that, ECMWF's own forecast is the better guide. Nothing was deployed: a station-target model
changes what Cloudzy predicts and how live predictions are evaluated, so adopting one would be a separate step
with its own rules.

## Uncertainty

`python -m src.intervals` computes split-conformal ranges per horizon: asymmetric empirical quantiles of the
model's own out-of-sample errors, with a finite-sample correction. Report: `reports/intervals_delhi.json`.

- **Method check:** fitted on the data before 2025 (from 2024-02-05 for the lagged-forecast model), calibrated on
  its errors in Jan–Jun 2025, coverage measured on Jul–Dec 2025 (4,416 hours per horizon).
- **Deployed ranges:** calibrated on the errors of a fit on the data before 2026 during Jan–Aug 2026, then checked
  on the deployed models' predictions for September 2026, a month they never saw (about 700 hours per horizon).
- Ranges are only served for the exact bundle they were calibrated for (SHA-256 match). After a refit, no range
  is shown until the study is re-run.

| Horizon | 80% range (°C around prediction) | 80% coverage 2025 H2 | 80% coverage 2026-09 | 95% coverage 2026-09 |
|---|---|---|---|---|
| 1 h | −1.72 to +1.15 | 81.8% | 84.4% | 97.5% |
| 2 h | −1.74 to +1.17 | 84.2% | 84.5% | 97.8% |
| 3 h | −1.72 to +1.18 | 84.5% | 81.0% | 97.6% |
| 6 h | −1.77 to +1.23 | 85.7% | 81.1% | 96.9% |
| 12 h | −1.80 to +1.36 | 87.1% | 80.1% | 97.0% |
| 24 h | −1.91 to +1.44 | 87.5% | 81.5% | 97.8% |

These are the ranges for the October 2026 model, recalibrated after it was deployed.
- **Width:** the 80% ranges are 2.9 to 3.3 °C wide on September 2026, down from 3.0 to 4.4 °C for the previous
  model.
- **Coverage:** still at or just above the nominal level. 80% ranges covered 80.1–84.5% of September; 95% ranges
  covered 96.9–97.8%.
One month of hourly, correlated values is evidence, not proof. Coverage on live predictions is tracked once they
are evaluated.

## Live tracking

Every live prediction is stored (with its range) in a local SQLite file and evaluated against the reanalysis
once it's published, about 6 days after the target hour (`src/monitoring.py`).

- **Scheduling:** checks run automatically in the app's server at most every 6 hours when a record is due, or
  with `python -m src.monitoring` (cron/launchd).
- **States:** awaiting target time; pending observation (not published yet, or checked and still missing);
  ready for evaluation; evaluated; invalid observation (outside physical bounds, never scored); observation
  unavailable (none within 30 days).
- **Summaries:** MAE, RMSE and bias by horizon, MAE skill against the IFS value on the same predictions, and
  range coverage. They are flagged as inconclusive below 30 issue times.

## Model registry

The Model Lab's *Registry* tab lists every model from its sidecar: algorithm, parameters, target, sources,
training ranges, validation method, metrics with their periods and sample counts, and whether the artifact in
this checkout matches its recorded checksum and reproduces its recorded metric.
- **Upgrade:** the tab shows the previous → current version comparison and the upgrade experiment.
- **Undescribed files:** model files in `models/` without a sidecar (backups, the 1.8 GB research random
  forest, an early plain-ERA5 model) are listed without claims. Providers, baselines and rule engines are listed separately, so they
are never presented as Cloudzy predictions.

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
- **Uncertainty covers model error only.** The 80% range describes how far the
  model has missed the reanalysis on data it wasn't fitted on. It doesn't cover
  live inputs arriving later than in the replay, or errors in the reanalysis
  itself. The "average error" shown in the app is a 2025 MAE, not an interval.

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
