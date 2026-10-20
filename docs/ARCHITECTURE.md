# Cloudzy architecture

Cloudzy is a Python project. It has a Streamlit front end and two separate
halves: one serves the live weather app, and one runs the offline machine-learning
experiments behind it. The app never trains a model and never fits anything per
request.

```
                          ┌───────────────────────── Open-Meteo (free, CC BY 4.0) ─────────────────────────┐
                          │ Geocoding API     Forecast API        Historical Forecast API     Archive API   │
                          └──────┬──────────────┬──────────────────────┬───────────────────────────┬───────┘
                                 │              │                      │                           │
   LIVE APP (per request, cached 10 min)        │                      │  OFFLINE (batch commands) │
   ─────────────────────────────────────        │                      │  ─────────────────────────│────────
   src/data_collection.fetch_json  ◄── one HTTP client: timeouts, retries, Retry-After, clear errors ──►
          │                                                            │                           │
   src/provider.py ── search_locations, fetch_forecast                 │      src/data_validation.py
          │   (validated, metric, time-zone aware)                     │      src/features.py (27 features)
          ▼                                                            │      src/train.py, multistep.py,
   src/units.py ── display units      src/insights.py ── summary,      │      shift.py, compact.py
   src/preferences.py ── per browser  alerts, suggestions, briefing    │      → models/…compact.joblib (2 MB)
          │                                   │                        │      → reports/*.json
          ▼                                   ▼                        │      src/lab_data.py → reports/lab/
   app_pages/weather.py ◄── src/weather_view.py (HTML), src/ui.py (charts)   (refit, 17 reproduction checks)
          │                                                            │      src/refit.py (monthly)
          │  Delhi only (≤ 15 km)                                      │
          ▼                                                            │
   Browser ── navigator.geolocation (one-time, with permission) ── src/geolocation.py
          │   validate → exact coords to Forecast API; rounded coords to Nominatim (place name)
          ▼
   src/briefing.cloudzy_block ──► src/service.py ── live IFS inputs ───┘
          │                        (validates 25 h, refuses if incomplete)
          ▼
   src/monitoring.py ── records each prediction once (SQLite) ─► evaluated against the archive
                        at the exact target hour (Predictions page button, or python -m src.monitoring)

   app_pages/lab.py ◄── reads committed reports/lab/ + reports/*.json, model sidecar, tracking aggregates
   app_pages/predictions.py ◄── src/monitoring.py (query, aggregate, evaluate)
   app_pages/history.py ◄── src/history.py (Archive API on demand: hourly, and daily for comparisons)
   app_pages/settings.py ◄── src/preferences.py
```

## Layers

| Layer | Modules | Responsibility |
|---|---|---|
| HTTP | `data_collection.fetch_json` | Every network call goes through this one function, which handles timeouts, retries with backoff, HTTP 429 `Retry-After`, 4xx/5xx handling and user-readable errors. `INTERACTIVE` settings fail within about 25 s. Batch downloads are patient. |
| Data services | `provider`, `history`, `service`, `data_validation`, `geolocation` | Validate units, time zones, array lengths and physical bounds. Missing values stay missing; optional variables a model lacks are reported as unavailable; wrong units are fatal. |
| Domain logic | `insights`, `units`, `preferences`, `monitoring` | Pure functions, testable without Streamlit or the network. |
| Presentation | `weather_view`, `ui`, `lab_ui` | Build HTML and Plotly figures from data and units. They never fetch anything. |
| Pages | `app_pages/{weather,history,predictions,lab,settings}.py`, `streamlit_app.py` | Lay out the page, cache results, and handle loading, empty and error states. |
| ML (offline) | `features`, `splits`, `models`, `train`, `multistep`, `shift`, `compact`, `refit`, `lab_data` | Chronological experiments, the deployed bundle and reproducible Lab data. |

## Data types are never mixed

| Type | Source | Shown as |
|---|---|---|
| Provider forecast | Forecast API (`best_match`) | The main weather app: hero, hourly, 7-day, details |
| Cloudzy Core prediction | `service.py` + compact bundle, Delhi only | A separate "🧪 Cloudzy Core · experimental" section |
| Historical reanalysis | Archive API | History page, labelled "historical reanalysis"; also the reference for tracked predictions |
| Evaluation results | `reports/`, `reports/lab/` (committed) | Lab scoreboard, breakdowns, explainability |
| Tracked predictions | `data/monitoring/cloudzy_predictions.sqlite` (local only) | Predictions page; summary in Lab → Live tracking |

## Caching and request budget

- **Cache:** search, forecasts, Cloudzy results and history are cached by
  `st.cache_data`. Forecasts and Cloudzy results live for 10 minutes, history
  and comparison years for 6 hours.
- **Freshness:** the page shows when its data was retrieved, so a cached
  forecast is never presented as freshly fetched.
- **Request budget:** one place costs at most three requests (geocoding,
  forecast, and Cloudzy's inputs near Delhi). That is far inside Open-Meteo's
  free limits: 600 per minute, 5,000 per hour, 10,000 per day.

## Per-visitor state (in each visitor's browser)

| Where | Contents |
|---|---|
| `localStorage["cloudzy-preferences-v1"]` (`src/browser_prefs.py`) | Unit override, favourites and home city (city coordinates), alert thresholds, categories and dismissals, summary style, auto-locate and notification choices (yes/no). Never device coordinates |
| `localStorage["cloudzy-notified-alerts"]` (`src/notify.py`) | Keys of alerts already sent as browser notifications |

`streamlit_app.py` reads the settings once per session, before any page draws,
and pages save through `src.preferences`. Stored settings are treated as
untrusted input and validated field by field. If the browser blocks storage,
settings last for the session only. Setting `CLOUDZY_PREFS_STORE=file` (or
`CLOUDZY_PREFS_PATH`) switches back to one JSON file for a single local user;
the tests use that mode.

## Server state (never committed, shared by every visitor)

| Path | Contents | Override |
|---|---|---|
| `data/monitoring/` | `cloudzy_predictions.sqlite` (predictions, refusals); the old `predictions.jsonl` is imported once and kept | `CLOUDZY_MONITORING_DIR`, `CLOUDZY_MONITORING=0` |
| `data/live_snapshots/` | Raw live-input responses, deleted after 30 days | `CLOUDZY_SNAPSHOTS=0` |
| Memory | Place-name lookups (at most 1,000, oldest dropped first); Streamlit's data caches (public weather data only) | |

## Tests

- **Offline suite:** `python -m pytest` runs 459 tests and needs no network.
  `tests/conftest.py` points preferences, monitoring and snapshots at temporary
  folders, so tests never touch real user data.
- **Live checks:** `python -m pytest -m integration` runs 5 tests against the
  real APIs.
- **App states:** headless `streamlit.testing.v1.AppTest` covers every page
  state, with the network replaced by fakes.

## Device location

`src/geolocation.py` holds a components-v2 script that runs in the page (no iframe, so no iframe
permission policy is involved). It answers each numbered request exactly once: coordinates, or a
reason code. Python validates the result and applies it only if the request number matches the one
pending. The coordinates live in `st.session_state` only. Exact coordinates go to the Open-Meteo
Forecast API; coordinates rounded to 2 decimals go to Nominatim, cached and throttled to 1 request
per second.

## Cloudzy Core pipeline and artifacts

`python -m src.pipeline {fetch,train,evaluate,all} [--city … --country … | --lat --lon --name --timezone] [--start --end]`
wires `data_collection` → `data_validation` → `features` → `splits` → `train` → `artifacts` for
any location and period. Every model file has a `.json` sidecar describing it (version, SHA-256,
features with units, target, provenance, splits, metrics). `artifacts.load_trusted` verifies the
structure, feature schema and checksum before the app or the evaluator uses a model.

## Prediction tracking

- **Record:** `monitoring.record_prediction` stores one row per (model version,
  location, issue time, horizon, variable). Times are converted to UTC first and
  must carry a time zone, so the same prediction written with another offset is
  still one record. The row ID is a SHA-256 of those fields, so re-renders and
  retries can't duplicate a record (`INSERT OR IGNORE`). Non-finite values, or
  target times that aren't issue + horizon, are rejected.
- **Evaluate:** `monitoring.evaluate` only requests reference data for pending
  records whose target date is more than the reanalysis lag (6 days, by the UTC
  date) in the past; newer records cost no request. It matches the exact UTC
  hour and checks the reference is in UTC and °C. Updates apply only while a
  record is still pending, so repeated or overlapping runs can't double-count.
  - Reference value missing for that hour: the record stays pending with a
    note, and is retried on every later check.
  - Still missing 30 days after the target hour: `unavailable`, with the reason.
  - Request failed: no record changes; the failure is stored.
  Every run, successful or not, is stored as `meta.last_check` (time, counts,
  how far the reference data reached, error).
- **Status breakdown:** `monitoring.classify` explains each record: evaluated;
  waiting for the target hour; waiting for publication; due on the next run;
  checked with the value still missing; unavailable. Each record also gets the
  first date it's checkable.
- **Aggregate:** only `evaluated` rows count, one model version at a time.
  Results carry sample counts, issue times and the date range, and are flagged
  as not conclusive below 30 issue times. Reference values are reanalysis (a
  model reconstruction), not station readings; the most recent ones are
  preliminary (ERA5T) and may be revised slightly later.
- **Scheduling:** `python -m src.monitoring` exits with status 1 when the
  reference request fails, so cron or launchd can run it daily (see README).

## Alerts and notifications

- **What they are:** threshold rules in `insights.alerts`, documented on the
  Weather page and in the Settings page. They are never official warnings.
- **Identity:** each alert's key is its kind plus its time window, which gives
  one alert per kind and window. Dismissing stores that key for 24 hours, so a
  new or changed window shows again.
- **Browser notifications** (`src/notify.py`): optional, off by default.
  - Permission is requested only from a click on the component's own button.
  - Each key notifies once (tracked in the browser's localStorage).
  - Notifications appear only while a tab is open: there's no service worker or
    push.
  - If notifications are blocked or unsupported, alerts stay on the page.
