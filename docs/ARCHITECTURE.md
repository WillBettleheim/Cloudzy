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
   src/preferences.py ── local JSON   alerts, suggestions, briefing    │      → models/…compact.joblib (2 MB)
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
   src/monitoring.py ── logs every prediction/refusal ─► later scored against the archive
                                                         (python -m src.monitoring)

   app_pages/lab.py ◄── reads committed reports/lab/ + reports/*.json, local monitoring summary,
                        src/history.py (Archive API on demand)
```

## Layers

| Layer | Modules | Responsibility |
|---|---|---|
| HTTP | `data_collection.fetch_json` | Every network call goes through this one function, which handles timeouts, retries with backoff, HTTP 429 `Retry-After`, 4xx/5xx handling and user-readable errors. `INTERACTIVE` settings fail within about 25 s. Batch downloads are patient. |
| Data services | `provider`, `history`, `service`, `data_validation`, `geolocation` | Validate units, time zones, array lengths and physical bounds. Missing values stay missing; optional variables a model lacks are reported as unavailable; wrong units are fatal. |
| Domain logic | `insights`, `units`, `preferences`, `monitoring` | Pure functions, testable without Streamlit or the network. |
| Presentation | `weather_view`, `ui`, `lab_ui` | Build HTML and Plotly figures from data and units. They never fetch anything. |
| Pages | `app_pages/weather.py`, `app_pages/lab.py`, `streamlit_app.py` | Lay out the page, cache results, and handle loading, empty and error states. |
| ML (offline) | `features`, `splits`, `models`, `train`, `multistep`, `shift`, `compact`, `refit`, `lab_data` | Chronological experiments, the deployed bundle and reproducible Lab data. |

## Data types are never mixed

| Type | Source | Shown as |
|---|---|---|
| Provider forecast | Forecast API (`best_match`) | The main weather app: hero, hourly, 7-day, details |
| Cloudzy Core prediction | `service.py` + compact bundle, Delhi only | A separate "🧪 Cloudzy Core · experimental" section |
| Historical reanalysis | Archive API | Lab → History explorer, labelled "historical reanalysis" |
| Evaluation results | `reports/`, `reports/lab/` (committed) | Lab scoreboard, breakdowns, explainability |
| Live monitoring | `data/monitoring/` (local only) | Lab → Live monitoring |

## Caching and request budget

- **Cache:** search, forecasts, Cloudzy results and history are cached by
  `st.cache_data`. Forecasts and Cloudzy results live for 10 minutes, history
  for 1 hour.
- **Freshness:** the page shows when its data was retrieved, so a cached
  forecast is never presented as freshly fetched.
- **Request budget:** one place costs at most three requests (geocoding,
  forecast, and Cloudzy's inputs near Delhi). That is far inside Open-Meteo's
  free limits: 600 per minute, 5,000 per hour, 10,000 per day.

## Local state (never committed, never sent anywhere)

| Path | Contents | Override |
|---|---|---|
| `data/user/preferences.json` | Unit override, favourites (coordinates), alert thresholds | `CLOUDZY_PREFS_PATH` |
| `data/monitoring/` | Prediction and refusal logs, latest monitoring summary | `CLOUDZY_MONITORING_DIR`, `CLOUDZY_MONITORING=0` |
| `data/live_snapshots/` | Raw live-input responses | `CLOUDZY_SNAPSHOTS=0` |

## Tests

- **Offline suite:** `python -m pytest` runs 385 tests and needs no network.
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
