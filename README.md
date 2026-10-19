# Cloudzy ☁️ — Weather with a little intelligence

Cloudzy is a weather app backed by **Cloudzy Core**, a machine-learning forecasting
engine evaluated against honest baselines. This repository is built engine-first:
the UI comes only after the ML pipeline works and has been measured.

> **Status:** Phases A–D are complete: ML engine, forecast service, provider forecasts and the web app.
> Cloudzy's own predictions cover Delhi only; provider forecasts work for any place.
> - **Research setting** (reanalysis in, reanalysis out): MAE is **0.242 °C** one
>   hour ahead on the untouched 2025 test year. Persistence scores 0.924 °C and a
>   climatological diurnal baseline 0.337 °C.
> - **Live setting** (real-time ECMWF IFS inputs): MAE is **1.10 °C** one hour
>   ahead and **1.37 °C** 24 hours ahead. The bottleneck is the disagreement
>   between the two data sources, not the model.
>
> All errors are measured against **reanalysis data, not a thermometer**.

## Quick start (macOS, Apple Silicon)

```bash
cd cloudzy-weather-ai
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pytest                 # 156 offline tests, no network needed
python -m src.data_validation    # download (first run only), validate, write processed CSV
python -m src.features           # build the feature table
python -m src.train              # tune on 2024, test on 2025, save model and report (~1 min)

# Phase B
python -m src.data_validation --source ecmwf_ifs   # archived live-model inputs, 2023-2025
python -m src.shift              # distribution shift study (~15 s)
python -m src.multistep          # direct vs recursive, horizons 1-24 h; saves the serving bundle (~1 min)
python -m src.service            # live forecast for Delhi right now (needs internet)
python -m src.refit              # monthly: refit serving models on data to the latest complete month

# Phase C
python -m src.provider "Reykjavik"   # Open-Meteo forecast for any place
python -m src.briefing "New Delhi"   # provider forecast + rain outlook + Cloudzy (Delhi only)

# Phase D
streamlit run streamlit_app.py       # the web app at http://localhost:8501
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

## Phase B: forecast service, distribution shift and multi-step

### Where live inputs come from

At run time Cloudzy can't get reanalysis data, which is published about five
days late. It uses **ECMWF IFS** output from the Open-Meteo Forecast API
(`models=ecmwf_ifs`). The default `best_match` uses the same grid cell for Delhi,
but it is pinned here because `best_match` can change its underlying model
without notice. The 15-minute "current" value is model-derived as well.

**Replaying live inputs for 2023–2025.** Open-Meteo's Historical Forecast API
archives what the Forecast API serves. I checked this directly: for 144 hours
(2026-10-14 to 2026-10-19), the live past hours and the archive were identical
for all five variables.

**This replay is a best case.** The archive keeps, for each hour, the value from
the newest model run covering it. A system running at time *t* only has runs
already published, which arrive hours late, so its newest inputs are
longer-lead forecasts. The service saves every live response to
`data/live_snapshots/` so this gap can be measured over time. For the same
reason, the archived IFS value *for t+h* is never used as a feature: it may come
from a run started after *t*.

### Distribution shift: IFS vs. reanalysis (`python -m src.shift`)

Hour-by-hour differences between the two sources (IFS − reanalysis), 2023–2025:

| Variable | Bias, all years | MAE | Correlation | Bias, 2023 / 2024 / 2025 |
|---|---|---|---|---|
| Temperature (°C) | +0.29 | 1.13 | 0.982 | −0.23 / +0.10 / **+0.99** |
| Relative humidity (%) | −2.2 | 6.1 | 0.943 | +0.2 / −2.1 / −4.7 |
| Surface pressure (hPa) | +0.12 | 0.37 | 0.998 | stable |
| Wind speed (km/h) | −0.36 | 2.53 | 0.713 | +0.9 / +0.3 / −2.3 |
| Precipitation (mm) | +0.01 | 0.14 | 0.242 | rain timing rarely agrees hour by hour |

Three things stand out:
- **The sources disagree by 1.13 °C** hour by hour. That is more than
  persistence's entire 1-hour error on reanalysis.
- **The IFS source drifts.** The temperature bias jumped in 2025, and IFS's
  hourly temperature became smoother over time: its mean absolute second
  difference fell from 0.80 to 0.69 to 0.55. This is consistent with model
  upgrades, but the data alone can't confirm the cause.
- **The bias depends on the time of day,** and its shape changes from year to
  year. In 2025, IFS ran about 1.5 °C warm at night. In 2023, it ran about
  1.4 °C cold in the early evening (`reports/figures/shift/`).

![IFS minus reanalysis by hour](reports/figures/shift/ifs_minus_reanalysis_by_hour.png)

**Effect on the 1-hour model.** All setups use the Phase A configuration. The
serving setup was chosen on 2024; the 2025 numbers are the test.

| Setup | Trained on (inputs → target) | Evaluated on | 2025 MAE °C | Persistence | Diurnal baseline |
|---|---|---|---|---|---|
| A | reanalysis → reanalysis | reanalysis → reanalysis | 0.243 | 0.924 | 0.337 |
| B | reanalysis → reanalysis | **IFS → reanalysis** | 1.417 | 1.794 | 1.445 |
| C | reanalysis → reanalysis | IFS → IFS | 0.378 | 0.897 | 0.448 |
| D | IFS → IFS | IFS → IFS | 0.354 | 0.897 | 0.403 |
| **E** | **IFS → reanalysis** | **IFS → reanalysis** | **1.133** | 1.794 | 1.462 |
| E + monthly refit | same, refitted at each month start | IFS → reanalysis | **1.053** | | |

What the setups show:
- **Shift penalty (B):** feeding live inputs to the Phase A model raises its
  error almost sixfold.
- **Cross-source training (E):** training directly on live inputs → reanalysis
  target, a form of statistical post-processing, recovers part of the loss. It
  was chosen over B on 2024 (0.843 vs 0.948 °C).
- **Monthly refit:** this was also decided on 2024, where it improved MAE by
  0.047 °C (95% interval 0.022–0.079). On 2025 it improved MAE by 0.080 °C
  (interval 0.053–0.107).
- **C and D:** predicting IFS's *own* next hour is easy (0.35–0.38 °C). But the
  provider already publishes its own forecast for that hour, so this is not a
  useful product.

### Multi-step strategy (`python -m src.multistep`)

There are two strategies. **Direct** trains one model per horizon, each mapping
features at *t* to temperature at *t + h*. **Recursive** applies the 1-hour
model repeatedly and feeds its own predictions back in. Future humidity,
pressure, wind and rain are unknown, so the recursive strategy holds them at
their last known values. Direct models were tuned per horizon on 2024 among the
residual random-forest configurations, then tested once on 2025 (8,736 shared
issue times).

**Part 1, reanalysis → reanalysis (2025 test MAE, °C):**

| Horizon | Direct | Recursive | Persistence | Diurnal baseline | Direct − recursive (95% CI) |
|---|---|---|---|---|---|
| 1 h | 0.243 | 0.242 | 0.924 | 0.337 | +0.001 [−0.000, +0.001] |
| 2 h | 0.421 | 0.428 | 1.791 | 0.596 | −0.007 [−0.011, −0.002] |
| 3 h | 0.572 | 0.590 | 2.614 | 0.809 | −0.018 [−0.028, −0.007] |
| 6 h | 0.811 | 0.987 | 4.738 | 1.263 | −0.176 [−0.221, −0.133] |
| 12 h | 0.999 | 1.565 | 6.605 | 1.675 | −0.567 [−0.658, −0.473] |
| 24 h | 1.098 | 2.009 | 1.148 | 1.145 | −0.911 [−1.080, −0.737] |

![MAE by horizon](reports/figures/multistep/mae_by_horizon_reanalysis.png)

**Direct wins at every horizon of 2 h or more, and the gap widens with lead
time.** Recursive errors compound, and the frozen weather inputs grow stale. At
24 h the recursive strategy is worse than plain persistence, which at 24 h means
"same time tomorrow". At 24 h even the direct model barely beats that baseline
(by 0.048 °C): local recent history carries little information a day ahead.
That is where physical weather models take over.

**Part 2, serving setup (IFS inputs → reanalysis, 2025 test MAE, °C):**

| Horizon | Cloudzy direct | IFS value at *t+h* (optimistic) | Persistence | Diurnal baseline |
|---|---|---|---|---|
| 1 h | 1.099 | 1.419 | 1.796 | 1.464 |
| 3 h | 1.154 | 1.419 | 3.139 | 1.567 |
| 6 h | 1.214 | 1.419 | 5.014 | 1.767 |
| 12 h | 1.287 | 1.419 | 6.332 | 1.971 |
| 24 h | 1.370 | 1.420 | 1.690 | 1.704 |

How to read this:
- **Closer to the reanalysis than IFS itself:** up to 12 h, Cloudzy's
  predictions are closer to the reanalysis than IFS's own values are (95%
  intervals exclude zero). At 24 h the difference is not significant.
- **What that means:** this is a learned **bias correction toward the
  reanalysis**. It doesn't show that Cloudzy forecasts the weather better than
  ECMWF, because the reanalysis is the target Cloudzy was trained to reproduce,
  not the ground truth.
- **What the service ships:** these configurations, refitted on all of
  2023–2025. The 2025 test errors above are their error estimates.

### The service (`python -m src.service`)

```
☁️  Cloudzy — New Delhi, India   issued 2026-10-19T11:30:00+05:30
   input temperature now (IFS model value): 27.6 °C

    ahead  local time         Cloudzy  2025 MAE*     IFS
      1 h  2026-10-19T12:30     28.0°      1.10°    28.5
      ...
     24 h  2026-10-20T11:30     27.4°      1.37°    27.3
```

That is real output from 2026-10-19; it will differ when you run it. In Python:

```python
from src.service import forecast_now
result = forecast_now()   # dict: issue time, inputs, cloudzy.predictions, provider.forecasts, limitations
```

**Input requirements:** 25 consecutive complete hours ending at the issue time
(the latest whole UTC hour), with the five training variables. Live data passes
the same validation as the training data. Any missing hour stops the forecast
with a `ServiceError`, and nothing is imputed.

**What the output contains:**
- Cloudzy's predictions and the provider's forecast in separate, labelled
  blocks. They are never mixed.
- For each horizon, Cloudzy's 2025-replay MAE. It is labelled as an average
  error, not a confidence interval.

**Known limitations:**
- One location.
- Accuracy is measured on a replay that is a best case for real-time inputs.
- The IFS source drifts against the reanalysis, so the serving models go stale.
  `python -m src.refit` (run it once a month) refits them on 2023-01-01 → the
  end of the latest month that ERA5 fully covers (about 6 days behind real
  time). It uses the hyperparameters already in the bundle, with no
  re-tuning. That most recent month is held out: the refitted models and the
  current bundle are both scored on it, and the bundle is replaced only if the
  refitted models' mean MAE is not worse (`--allow-worse` overrides). The
  shipped models never see the held-out month, so they lag real time by one
  to two months. The new bundle records its data end date, row counts, raw
  files and package versions. The previous bundle is kept as
  `….backup-YYYY-MM-DD.joblib` (1.5–2 GB each; delete old ones by hand).
  The results are written to `reports/refit_delhi.json`. `typical_error_c` in
  the service output is still the 2025-replay MAE.
  First run (2026-10-19): data to 2026-09-30, refit on rows before September
  (~32,100 per horizon), held out September 2026 (~700 rows). The refitted
  models scored a mean MAE of 0.927 °C and the previous bundle 0.938 °C. They
  were better at 1, 2, 3 and 12 h, tied at 6 h, and worse at 24 h (1.196 vs
  1.178). One month is a small sample, so treat these per-horizon gaps as
  noisy.

## Phase C: real provider forecasts

Phase C adds Open-Meteo's operational forecasts next to Cloudzy's predictions.
The two are kept strictly apart in code and in output:
- `src/provider.py` returns only provider data.
- `src/service.py` returns only Cloudzy predictions.
- `src/briefing.py` places them side by side under separate labels.

```bash
python -m src.provider "Reykjavik"     # provider forecast for any place
python -m src.briefing "New Delhi"     # provider forecast + rain outlook + Cloudzy (Delhi only)
python -m src.briefing "Mumbai" --json # the JSON a front end will consume
```

**Provider forecast.** This comes from the Open-Meteo Forecast API with
`models=best_match`, Open-Meteo's default blend of the best models for each
location. For Delhi it gives the same values as ECMWF IFS, and it also fills
fields IFS lacks (UV index is all-null under `ecmwf_ifs`). The forecast
contains:
- **Current conditions:** temperature, feels-like temperature, humidity, wind,
  gusts, cloud cover and weather. These are 15-minute model values, not station
  observations.
- **Hourly forecast:** 7 days, including precipitation probability.
- **Daily outlook:** 7 days of min/max temperature, rain total and probability,
  maximum wind, sunrise and sunset, and UV.

Weather codes are translated with the WMO table Open-Meteo documents. Unknown
codes are shown as "Unknown (WMO n)" rather than guessed.

**Validation.** Responses are checked before use:
- **Fatal:** units, section and array lengths, a consecutive hourly sequence, and
  daily dates being unique.
- **Removed and reported in `warnings`:** values outside physical bounds that
  hold anywhere on Earth.
- **Counted, never filled:** missing values.

**Time zones.** Requests use `timeformat=unixtime`, so every hourly timestamp is
an unambiguous instant, shown in the place's own time zone. One API quirk
matters here: Open-Meteo builds *daily* timestamps with one fixed UTC offset,
so after a daylight-saving change, "2 Nov" arrives as "1 Nov 23:00 −05:00".
Daily dates are therefore decoded with that same fixed offset. A New York test
across the 2026-11-01 clock change covers this.

**Location search.** This uses the Open-Meteo Geocoding API (GeoNames). Queries
need at least 2 characters, and no match returns an empty list. The top result
is not always the place the user means. For example, "Gurgaon" returns a
village in Maharashtra, not the city near Delhi (now officially Gurugram). So
the app must let the user choose from the list.

**Rain indicator.** An hour in the next 24 is flagged when precipitation
probability is ≥ 50% *or* forecast rain is ≥ 0.5 mm. It is labelled "Derived
from the provider forecast; not an official weather warning". Open-Meteo does
not provide official alerts, and Cloudzy doesn't invent them.

**When Cloudzy is shown.** Cloudzy always predicts for one point: the Delhi grid
cell it was trained on. It is shown only for places within **15 km** of that
point, and the output names the point and the distance. That covers the Delhi
geocode (4.8 km) but not Noida (19.8 km) or Gurugram (24.7 km). Further away,
showing it would put Delhi's weather under another city's name. If live inputs
fail validation or the network fails, the Cloudzy block gives the reason, and
the provider forecast is still shown.

**Rate limits.** A Delhi briefing makes three API calls: geocoding, provider
forecast, and live IFS inputs. During testing Open-Meteo returned
`HTTP 503 service overloaded` three times in a row, and the existing
retry-with-backoff recovered on the fourth attempt. The Phase D app must cache
responses (for example, 10 minutes per place).

## Phase D: the Cloudzy web app

```bash
streamlit run streamlit_app.py
```

**What it shows, top to bottom:**
- **Search:** a place search with a "choose the right match" picker. Picking from
  the list is required, because the top geocoding result can be the wrong town
  (see Phase C).
- **Current conditions:** temperature, feels-like, humidity, cloud cover, wind
  with compass direction, gusts and precipitation. It also shows today's range
  and sunrise/sunset.
- **Rain indicator:** a coloured banner ("Rain likely from …" or "No rain
  expected"). It always shows its rule and states that it is not an official
  warning.
- **Next 48 hours:** a temperature and feels-like chart, a chance-of-rain chart
  (amounts appear on hover), and an hour-by-hour table with humidity and wind.
- **7-day outlook:** day cards (icon, high/low, rain chance and amount, wind, UV)
  and a low-to-high range chart.
- **Cloudzy Core 🧪:** for places within 15 km of the Delhi training point, it
  shows Cloudzy's predictions beside the provider's values for the same times,
  with the prediction point, issue time and each horizon's 2025 average error.
  Elsewhere it explains why Cloudzy isn't available.

**States.** Every state is covered by headless `AppTest` tests, with the network
replaced by fakes:

| State | What the user sees |
|---|---|
| Loading | A spinner for each slow step ("Checking the sky…", "Asking Cloudzy Core…") |
| Query too short | A hint to type at least two letters |
| No matching place | "No places match …", with a suggestion |
| Search or forecast outage | An error with the reason and a **Try again** button, which clears the cache |
| Cloudzy unavailable | A warning with the reason; the provider forecast is unaffected |
| Missing or implausible provider values | Shown as "—", never zero, and listed under "Data notes" |
| No rain in 48 h | A one-line note instead of an empty chart |

**Themes.** `.streamlit/config.toml` defines separate light (pale sky and navy
text) and dark (midnight navy) palettes. Streamlit follows the viewer's system
setting, and viewers can switch in ⋮ → Settings. Charts read the active theme
(`st.context.theme`) and use matching colours. Provider data is **blue** and
Cloudzy is **orange** in every chart, in both themes. Both colour pairs passed a
palette check against their background: colour-blind separation ΔE > 24, and
contrast ≥ 3:1. I checked both themes visually in a browser, including at a
narrow width, where metrics and day cards wrap instead of stacking.

**Caching.** Search, provider forecasts and Cloudzy results are cached for 10
minutes per place, because one Delhi page view otherwise makes three API calls.
Live IFS snapshots are saved to `data/live_snapshots/`; set
`CLOUDZY_SNAPSHOTS=0` to turn that off.

**Running Cloudzy Core in the app** needs the trained serving bundle
(`python -m src.multistep`, or a monthly `python -m src.refit`). Model files
aren't committed. Without one, the Cloudzy tab says so, and everything else
works.

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
src/shift.py             reanalysis vs live-source comparison and shift experiments
src/multistep.py         direct vs recursive multi-step evaluation; serving bundle
src/service.py           live inputs → validated → labelled Cloudzy + provider forecasts
src/refit.py             monthly refit of the serving models, held-out check, dated backup
src/provider.py          Open-Meteo provider forecasts and location search (provider data only)
src/briefing.py          provider forecast, rain outlook and Cloudzy side by side, labelled
src/ui.py                chart, icon and table helpers for the app (no Streamlit calls; tested)
streamlit_app.py         the Cloudzy web app
.streamlit/config.toml   brand theme: light and dark palettes
tests/                   156 offline tests
reports/                 data-quality, feature and evaluation reports (JSON) and figures
data/, models/           generated by the pipeline and ignored by git
```

Three modules go beyond the suggested layout, and each has a reason:
- `config.py` keeps every dataset-defining choice in one place.
- `splits.py` and `models.py` keep the evaluation protocol and the model
  definitions testable on their own, separate from the training script.

## Roadmap

- **A — ML engine** ✅ data → features and leakage tests → baselines and models → chronological evaluation
- **B — Forecast service** ✅ live IFS inputs, shift study, direct multi-horizon models, service.
  Monthly refit is now a command (`python -m src.refit`); scheduling it is up to the host (e.g. cron).
  Open follow-up: measuring the real-time vs. archive input gap from snapshots.
- **C — Real weather** ✅ provider current/hourly/7-day forecasts, location search, rain indicator, labelled briefing
- **D — Web app** ✅ Streamlit + Plotly: search, current, 48-hour and 7-day views, rain indicator, Cloudzy panel, both themes
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
