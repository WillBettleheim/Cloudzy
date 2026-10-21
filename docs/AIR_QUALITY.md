# Air Quality and Outdoor Intelligence

How Cloudzy gets air-quality data, which index standards it shows, how the outdoor planner decides, and
what it can't do. Code: `src/air_quality.py`, `src/aqi.py`, `src/outdoor.py`, `src/air_alerts.py`,
`src/air_view.py`, `app_pages/air_quality.py`, `app_pages/outdoor.py`.

## Data provider

**Open-Meteo Air Quality API** (`https://air-quality-api.open-meteo.com/v1/air-quality`), which serves
Copernicus Atmosphere Monitoring Service (CAMS) forecasts. It was chosen because:

- it is free for non-commercial use, needs no API key and is the same provider (and HTTP client, cache and
  error handling) as the rest of Cloudzy;
- it covers the whole globe, which matches Cloudzy's worldwide place search;
- it returns pollutant concentrations *and* documented US AQI and European AQI calculations.

| | CAMS European | CAMS Global |
|---|---|---|
| Where | Europe (chosen automatically by Open-Meteo) | Everywhere else |
| Grid | 0.1° (about 11 km) | 0.4° (about 45 km) |
| Time step | Hourly | 3-hourly, interpolated to hours |
| Forecast | 4 days, updated every 24 h | 5 days, updated every 12 h |

The response doesn't say which domain was used, so the app names both. Cloudzy asks for 7 forecast days
(the API maximum) and 2 past days; hours past the model's end come back empty, are trimmed, and the real
end of the forecast is shown (for example "Forecast to Mon 26 Oct 06:00 · about 101 h ahead").

**Attribution (required, shown on both pages and on the Weather page footer):** "Air quality: Copernicus
Atmosphere Monitoring Service (CAMS) forecasts, via Open-Meteo.com (CC BY 4.0). Contains modified Copernicus
Atmosphere Monitoring Service information."

**Limits:** Open-Meteo's free tier allows 600 requests per minute, 5,000 per hour, 10,000 per day and
300,000 per month, for non-commercial use only. Commercial use needs an Open-Meteo API key on the
`customer-` server; Cloudzy doesn't support that and embeds no key anywhere.

**Station observations:** none. Open-Meteo provides model values only, including for past hours. Sources
with real monitoring-station data (OpenAQ, CPCB, AirNow) all need an API key, so they weren't added.
Every air-quality value in Cloudzy is labelled as a model estimate, and the past hours on the chart are
labelled "model values, not observed".

### Variables

| Variable | Unit | Notes |
|---|---|---|
| PM2.5, PM10 | μg/m³ | Particulate matter |
| NO₂, O₃, SO₂, CO | μg/m³ | CO is converted to mg/m³ only inside the India NAQI calculation |
| Dust | μg/m³ | A separate CAMS field. It can exceed PM10, so it is never shown as a share of PM10 |
| US AQI and its six sub-indices | USAQI | From Open-Meteo |
| European AQI and its five sub-indices | EAQI | From Open-Meteo |
| India NAQI and six sub-indices | — | Calculated by Cloudzy |

Units are checked against the API's own labels (both Unicode spellings of μ are accepted). A wrong unit on a
variable with values is an error; it is never converted by guesswork. Values outside physical bounds are
removed and counted. A pollutant a location lacks is reported as unavailable, never filled in.

## Index standards

Indices use different formulas and categories, so their numbers can't be compared. Cloudzy prints every
index with the name of its standard and never relabels one as another.

| Standard | Computed by | Method | Categories |
|---|---|---|---|
| **US AQI (EPA)** | Open-Meteo | EPA breakpoints; 24-h rolling means for PM2.5 and PM10, 8-h for CO, max of 1-h and 8-h ozone, 1-h NO₂ | Good · Moderate · Unhealthy for Sensitive Groups · Unhealthy · Very Unhealthy · Hazardous |
| **European AQI (EEA)** | Open-Meteo | 2024 EEA bands on hourly concentrations, interpolated within bands; can exceed 100 | Good · Fair · Moderate · Poor · Very poor · Extremely poor |
| **India National AQI (CPCB, 2014)** | Cloudzy | See below | Good · Satisfactory · Moderately polluted · Poor · Very poor · Severe |

**Which PM2.5 breakpoints does Open-Meteo's US AQI use?** Open-Meteo's documentation doesn't say, so
Cloudzy checked on 2026-10-22. It recomputed `us_aqi_pm2_5` from Open-Meteo's own 24-hour PM2.5 means
for Paris, New Delhi and New York (49 hours each). The 2024 EPA breakpoints (Good ends at 9.0 μg/m³)
reproduced Open-Meteo's values within ±1 for 82%, 94% and 69% of hours. The pre-2024 breakpoints (12.0
μg/m³) matched 0%, 0% and 6%. The rest differ because the window alignment differs. So the US AQI shown
follows the current EPA breakpoints. It isn't EPA's NowCast, which AirNow uses for current hours, so the
two can differ.

**India NAQI, as calculated here** (`src/aqi.py`), follows CPCB's published rules:

- Sub-index per pollutant: `I = (I_hi − I_lo) / (B_hi − B_lo) × (C − B_lo) + I_lo` within CPCB's
  breakpoint band. A concentration falling between two published bands (for example PM2.5 30.5) stays
  inside the band it rounds into.
- Averages: 24-hour rolling for PM10, PM2.5, NO₂ and SO₂; 8-hour for O₃ and CO.
- Minimum data: 16 hours for a 24-hour average (CPCB). CPCB gives no figure for 8-hour averages, so Cloudzy
  requires 6 of 8 hours, the same three-quarters share.
- Overall AQI: the worst sub-index, and only when at least three pollutants have one, including PM2.5 or
  PM10. Otherwise the AQI is missing, with the reason.
- **Severe** has no upper breakpoint in CPCB's table, so values there are shown as **401+**, never as an
  invented number.
- Inputs are CAMS model concentrations, so the result is labelled "Calculated by Cloudzy with the CPCB
  method from modelled concentrations". It is not an official CPCB value.

**Which index a place starts with:**

| Place | Default | Note shown |
|---|---|---|
| India | India NAQI | Official index there |
| United States and territories | US AQI | Official index there |
| EEA member countries | European AQI | National indices may differ |
| United Kingdom | European AQI | The UK's DAQI isn't calculated |
| Anywhere else | US AQI | Shown as a reference scale; local indices may differ |

The viewer can choose another index on the Air Quality page. The choice is saved in their browser and also
used by Outdoor Intelligence and the alerts.

**WHO comparison.** Each pollutant readout compares a recent mean with the WHO 2021 air quality guideline
for the same averaging period: PM2.5 15, PM10 45, NO₂ 25 and SO₂ 40 (24-hour), O₃ 100 (8-hour) and CO
4 mg/m³ (24-hour), all in μg/m³ unless stated. The page notes that these are long-term health targets,
not safety limits.

## Forecasts and projections

- Hourly index and pollutant forecasts, up to the end of the CAMS forecast.
- Daily outlook: the lowest and highest hourly index per local day, the worst category and when it occurs,
  the main pollutant at that time and the mean PM2.5. A partial last day is labelled.
- Improving and deteriorating periods: every forecast change of category, with time and values (for
  example "Forecast to worsen from Moderate to Unhealthy around Thu 21:00, US AQI 98 → 156").
- Uncertainty: CAMS publishes no uncertainty range for this data, so none is shown or invented. Windows
  more than 3 days ahead are marked as more likely to change.

### Cloudzy Core and air quality

**Cloudzy Core does not predict air quality.** It is a temperature model for New Delhi and was not
repurposed. Before deciding whether to build an AQI model, Cloudzy audited the history it could use (on
2026-10-22):

- The Open-Meteo CAMS Global history for New Delhi runs from 4 Aug 2022: 36,936 hourly PM2.5 values. 18.5%
  of 2022 is missing (the series starts in August); 2023 to 2026 are complete.
- **Every value is model output.** There are no monitoring-station observations without an external API
  key.
- A model trained to predict CAMS values from past CAMS values would learn to reproduce CAMS. Without
  independent measurements there's nothing to evaluate it against, so any MAE or RMSE would describe
  agreement with the provider, not accuracy.

So no AQI model was built, and no AQI metrics are reported. A future model would need station observations
(for example CPCB data through OpenAQ) as the target. It would then need chronological splits, persistence
and CAMS baselines, and evaluation by horizon and location, as Cloudzy Core's temperature work already has.

## Outdoor Intelligence: how recommendations are made

A transparent rule engine (`src/outdoor.py`) runs on every forecast hour in the place's local time, for
one activity:

| Activity | Exertion | Notes |
|---|---|---|
| Running | heavy | |
| Walking | light | |
| Cycling | heavy | Wind-sensitive |
| Outdoor exercise | heavy | |
| Casual time outside | light | |

New activities are added to `ACTIVITIES` with their exertion level, wind sensitivity and an indoor
alternative.

### Rules

Each check gives **ok**, **caution** (acceptable with care), **avoid** (postpone or move indoors) or
**unavailable**.

| Check | Rule | Avoid when | Caution when | Source |
|---|---|---|---|---|
| Air quality | Category of the selected index → its guidance for this exertion level | The guidance says avoid | The guidance says reduce | AirNow activity guide (US), EEA health messages (EU), Cloudzy's mapping of CPCB health statements (India) |
| Thunderstorms | WMO codes 95, 96, 99 | Any | — | NWS lightning safety |
| Freezing rain | WMO codes 56, 57, 66, 67 | Any | — | WMO codes |
| Snow | WMO codes 71–77, 85, 86 | Heavy snow when cycling | Otherwise | WMO codes |
| Heat | NWS heat index (Rothfusz regression with adjustments) from temperature and humidity | Danger, 39.4 °C (103 °F) and above | Caution from 26.7 °C (heavy exertion); extreme caution from 32.2 °C (all) | NWS |
| Cold | Wind chill (T ≤ 10 °C, wind ≥ 4.8 km/h), else temperature | −28 °C and colder (skin can freeze in 10–30 min) | −10 to −27 °C | Environment and Climate Change Canada |
| UV | UV index, rounded to whole numbers | Never | 8 and above; protection noted from 3 | WHO |
| Rain | ≥ 50% chance or ≥ 0.5 mm in the hour; heavy at ≥ 7.6 mm/h | Never on its own | Likely or heavy rain | Cloudzy rain indicator; AMS heavy-rain rate |
| Wind (cycling) | Sustained wind and gusts | Gusts ≥ 62 km/h (Beaufort 8) | Wind ≥ 39 or gusts ≥ 50 km/h | Beaufort scale |
| Wind (others) | Gusts | Gusts ≥ 75 km/h (Beaufort 9) | Gusts ≥ 62 km/h | Beaufort scale |

**Activity mapping for India's NAQI** (CPCB publishes health impacts, not activity advice). The mapping
follows the US guidance for similar PM2.5 levels:

| NAQI category | Heavy exertion (everyone) | Heavy exertion (sensitive) | Light exertion (everyone) | Light exertion (sensitive) |
|---|---|---|---|---|
| Good | ok | ok | ok | ok |
| Satisfactory | ok | caution | ok | ok |
| Moderately polluted | caution | avoid | ok | caution |
| Poor | avoid | avoid | caution | avoid |
| Very poor | avoid | avoid | avoid | avoid |
| Severe | avoid | avoid | avoid | avoid |

For the European AQI, "reduce physical activities outdoors" (Very poor and Extremely poor, everyone) is
mapped to avoid for heavy exertion and caution for light. The full table for each standard is on the Air
Quality page (About this data).

### Combining checks

- **An hour** takes its worst check. A critical failure (storm, very poor air, dangerous heat) can't be
  outweighed by pleasant temperatures or sunshine.
- **Essential data:** the selected index, temperature and weather code. If any is missing, the hour is
  *unavailable* and isn't judged. Hours past the end of the CAMS forecast are therefore never recommended.
  Missing humidity (no heat index) or wind is noted but doesn't block the hour.
- **A window** is the activity's length, rounded up to whole hours, entirely inside the preferred local
  hours. It takes its worst hour.
- **Ranking:** windows are compared on these values in order, with **no weighted score**: result, number of
  caution factors, air-quality category, mean PM2.5, highest heat index, highest UV, highest rain chance,
  then the earlier start. Mean PM2.5 is a tie-breaker because the US AQI and the NAQI use 24-hour averages
  for particles, which change slowly; the hourly concentration shows shorter swings.
- **Output:** the best window in the next 24 hours, the next fully suitable window, and the best window per
  day. Each comes with its time, index and standard, PM2.5, temperature, heat index, UV, rain, wind,
  supporting and limiting factors, and notes. If every option is unfavourable, an indoor alternative is
  suggested (this can be switched off).

### Personalisation

All settings are optional and saved in the visitor's browser: activity, duration (30 min to 3 h),
preferred local hours, indoor suggestions, and **More conservative guidance**. That last option uses the
index's advice for sensitive groups and starts heat cautions one heat-index category earlier. Cloudzy
never asks for, infers or stores medical information. If the requested duration or hours can't fit in the
remaining forecast, the page says why.

### Explanations

Short sentences built by fixed rules (no language model), each listing the measurements it rests on and
whether it describes now (a model estimate) or the forecast. For example: "Air quality is forecast to
deteriorate, from US AQI 40 (Good) now to 160 (Unhealthy) around 21:00, while temperatures rise to 26 °C."
They describe values side by side and never claim causes; there is no "wind will clear the air". If data
are missing, a fallback sentence says so.

### Limits

- Heat index is not WBGT. Wet-bulb globe temperature, used for exercise in heat, also accounts for sun and
  wind. Cloudzy has no validated WBGT source and doesn't estimate one. The heat index assumes shade; full sun
  can add up to about 8 °C.
- Model grids (about 11–45 km) miss street-level differences such as traffic, fires and parks.
- Nothing here is a medical assessment or a promise that a time is safe. Personal susceptibility varies.

## Alerts

New alert categories on the Alerts page (on by default, each can be switched off):

| Kind | Rule | Threshold (default) |
|---|---|---|
| Air quality | The selected index reaches a category within 24 h | Third category (USG, Moderately polluted, Moderate); choose 2nd to 6th |
| Fine particles | 24-hour mean of modelled PM2.5 within 24 h | 35 μg/m³ (the level of the US EPA 24-hour standard); 10–250 |
| Thunderstorms | WMO codes 95, 96, 99 within 24 h | — |
| Saved outdoor plans | A saved window is now rated worse than when saved | — |

Heat and UV alerts already existed. Each alert names its source and data time, uses the existing dismissal
and one-notification-per-event logic, and is never an official warning. Saved windows are re-checked
whenever new forecasts load while Cloudzy is open. There is no background worker or push service, so
nothing is checked while no tab is open, and Cloudzy doesn't claim otherwise.

## Caching, requests and failure handling

- `app_data.air(place)`: one request per place, cached for 15 minutes (`AIR_CACHE_SECONDS`). CAMS runs
  update every 12–24 hours, and the current hour changes at most hourly. Weather forecasts keep their
  10-minute cache.
- One place now costs at most four requests (geocoding, forecast, air quality, and Cloudzy Core inputs
  near Delhi), far inside the free limits.
- Charts are client-side Plotly, so hovering and zooming make no requests.
- The shared HTTP client (`data_collection.fetch_json`) applies timeouts, retries with backoff and HTTP 429
  `Retry-After`, failing fast when the wait would be long.
- Streamlit stops a page run when the visitor changes something, so stale results aren't drawn.
- Failures are isolated. If air quality fails, the Weather page and its weather alerts still work, and the
  Air Quality page explains the error with a retry button. The outdoor planner needs both forecasts and
  says which one is missing.
- Nothing is stored on the server. Saved windows (at most 5, removed once past) and settings live in the
  visitor's browser and are validated field by field when read.

## Tests

`python -m pytest` (offline, network faked) covers the following:

- **Air-quality parsing:** units, both μ spellings, trimming of trailing nulls, gaps, implausible values,
  error payloads, the daylight-saving change in Paris, rate limits and timeouts.
- **Index standards:** category boundaries for all three, the NAQI breakpoints, the 16-hour and
  three-pollutant rules, 401+, never showing a US value as NAQI, and regional defaults.
- **Rule engine:** heat index and wind chill against published tables, hazard overrides, activity
  differences, the conservative setting, missing data, windows, ranking, coverage messages, explanations
  without causal claims, and saved plans that get worse.
- **Pages:** both new pages, their error states, saved preferences, the alert integration, and the Weather
  page when air quality fails.

`python -m pytest -m integration` adds a live check of the Air Quality API.
