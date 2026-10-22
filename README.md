# Cloudzy

**Weather with a little intelligence.** A weather and hazard-awareness app built around one research question:

> *Can a small machine-learning model honestly improve on a global weather forecast for one city, and how do you
> prove it?*

![Python 3.13](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/app-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![Tests](https://img.shields.io/badge/tests-900%2B%20passing-34b37a)
![License: MIT](https://img.shields.io/badge/license-MIT-blue)

Cloudzy combines live forecasts, air quality, outdoor-activity guidance and a 3D disaster globe with **Cloudzy
Core**, an experimental temperature model for New Delhi. The model is tested the way research should be:
- data split strictly by time;
- simple baselines to beat;
- confidence intervals on every comparison;
- decision rules written down *before* results are seen;
- a check against real thermometer readings.

---

## Key findings

| What was tested | Result |
|---|---|
| **Upgrade** (Oct 2026): add the ECMWF forecast issued ~2 days before each target hour | Lower error against the reanalysis at **all 6 horizons**, all significant. +24 h: 1.44 → 1.07 °C |
| **Reality check** against Delhi's Palam airport station | The raw **ECMWF forecast was closer** than Cloudzy at every horizon (1.21 °C vs 1.40–1.63 °C). The cause: the reanalysis Cloudzy learns from runs ~1.5 °C colder than the city at night |
| **Retrained on the station's own readings**, with live station inputs | Roughly **halves ECMWF's error 1 hour ahead** (0.59 vs 1.21 °C) and wins up to 3 h, but is worse from 12 h. By the rules fixed beforehand, it doesn't beat ECMWF overall |
| **80% prediction ranges** (split conformal) | Covered 80–85% of hours in a month the model never saw; 2.9–3.3 °C wide |

**Takeaway:** Cloudzy learned a real bias correction, but toward the wrong target. Testing against real
observations exposed that. Fed live station readings, it becomes a useful 1–3 hour nowcast, not a better
forecast than ECMWF. The full story, including what didn't work, is in the [model card](MODEL_CARD.md).

---

## What's in the app

| Page | What it does |
|---|---|
| **Overview** | Today at a glance: conditions, alerts, air quality, the next good outdoor window, nearby hazards and Cloudzy Core, each labelled by the kind of data it is |
| **Weather** | Forecast for any place (Open-Meteo), plain-language summary, alerts, and, for Delhi, Cloudzy Core's prediction next to ECMWF's, with its 80% range |
| **Air Quality** | PM2.5, PM10, NO₂, O₃, SO₂, CO and the index the region uses (US AQI, European AQI or India's NAQI). These are CAMS model estimates, labelled as such |
| **Outdoor Intelligence** | Better times to run, walk or cycle, from a transparent rule engine (air quality, heat index, wind chill, UV, storms). It never claims a time is "safe" |
| **Earth Intelligence** | 3D globe of real disasters and warnings (USGS, GDACS, NOAA, NASA FIRMS and more): 18 situation types, presets, filters, timeline, search to street level |
| **Weather Explorer** · **History & Trends** | Interactive maps and historical reanalysis for any place |
| **Model Lab** | How Cloudzy Core was tested: scoreboard, error breakdowns, uncertainty, model registry with version history, and the **station check** |
| **Prediction History** | Every live prediction is stored and scored automatically once the reference data is published (~6 days later) |
| **Alerts** · **Places** · **Settings** | Configurable alerts, saved places, units and preferences |

> **Note:** Earth Intelligence only collects official and scientific feeds for situational awareness. It is not a
> warning service, and "not connected" situations (heavy rain, landslides, lightning) are shown as such, never as
> "no events".

---

## Cloudzy Core: how the model works and was tested

**Task.** Predict New Delhi's 2 m temperature 1, 2, 3, 6, 12 and 24 hours ahead.

**Model.** One gradient-boosting model per horizon (scikit-learn `HistGradientBoostingRegressor`, about 2 MB in
total). Each model predicts the *change* from the current temperature.

**Inputs.** 29 features:
- the last 25 hours of ECMWF IFS model output for one grid cell (temperature, humidity, pressure, wind, rain,
  time of day and year);
- what an ECMWF run from ~48 hours earlier predicted for the target hour. That run is published before
  prediction time, so nothing leaks from the future.

**Target.** ERA5-Land reanalysis temperature, via Open-Meteo.

**How it was evaluated.**
1. **Chronological splits only:**

   | Stage | Period |
   |---|---|
   | Tuning | Jul–Dec 2025 |
   | Test, used once | Jan–Aug 2026 |
   | Final held-out check | Sep 2026 |

2. **Baselines:** persistence, the ECMWF value itself, and a lagged ECMWF forecast.
3. **Uncertainty in comparisons:** week-block bootstrap confidence intervals, because hourly errors are correlated.
4. **Pre-registration:** each experiment's adoption rule is written in its module before it runs.
5. **Reproducibility:** `python -m src.reproduce` recomputes every saved model's recorded score from cached data.
   All four match exactly.
6. **Reality check:** scores against routine METAR readings from Delhi's airports. These were cross-checked
   against NOAA's archive: 99.9% exact match.
7. **Live monitoring:** predictions are recorded with their ranges and evaluated automatically when the
   reference data arrives.

**Results against the reanalysis (Jan–Aug 2026 test, MAE °C):**

| Horizon | Persistence | Lagged ECMWF | Previous Cloudzy | **Current Cloudzy** |
|---|---|---|---|---|
| +1 h | 1.87 | 1.45 | 0.97 | **0.93** |
| +6 h | 4.98 | 1.45 | 1.11 | **0.98** |
| +24 h | 1.87 | 1.45 | 1.44 | **1.07** |

**Against a real thermometer (Palam station, same period), the picture changes.** That's why the station check
exists. See [Key findings](#-key-findings) and Model Lab → *Station check*.

---

## Getting started

**Requirements:** Python 3.11+ (developed and tested on 3.13) and Git. **No API keys:** every data source is free
and key-less.

```bash
git clone https://github.com/WillBettleheim/Cloudzy.git
cd Cloudzy
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

Run the app:

```bash
streamlit run streamlit_app.py
```

Then open <http://localhost:8501>. The trained model is included, so predictions work straight away. Earth
Intelligence's first load fetches nine feeds and can take up to ~25 seconds; after that it's cached.

### Tests and code quality

```bash
python -m pip install -r requirements-dev.txt
python -m pytest                   # 900+ offline tests; the network is faked
python -m pytest -m integration    # live checks against the real APIs
ruff check .
mypy
```

### Reproducing the research

These use cached data where possible; the first run downloads it.

```bash
python -m src.mos              # lagged-forecast upgrade experiment     → reports/mos_delhi.json
python -m src.intervals        # calibrate and validate 80%/95% ranges  → reports/intervals_delhi.json
python -m src.reproduce        # recompute every model's recorded score → reports/reproducibility.json
python -m src.station_eval     # compare with real station readings     → reports/station_eval_delhi.json
python -m src.station_model    # station-target experiment              → reports/station_model_delhi.json
python -m src.refit            # monthly refit with a held-out acceptance check
```

Deployment to Streamlit Community Cloud is described in [DEPLOYMENT.md](DEPLOYMENT.md).

---

## Static edition on GitHub Pages

**Planned address:** <https://willbettleheim.github.io/Cloudzy/>. It is not live until the first deployment below
succeeds.

GitHub Pages serves static files only; it can't run Python. So Pages hosts a separate, clearly labelled static
edition, built from the app's own parts by `tools/build_pages.py`. The full app above is unchanged. What each feature
needs is listed in [docs/FEATURE_INVENTORY.md](docs/FEATURE_INVENTORY.md).

| On the static site | How it works |
|---|---|
| **Weather** for any place, 7-day outlook, 48-hour chart, air quality now | Live: your browser asks Open-Meteo directly (free, no key) |
| **Earth Intelligence** globe, with the situation selector, presets, filters and timeline | The app's own globe, with a snapshot of the hazard feeds taken when the site was built, labelled with that time |
| **Model Lab** results: the upgrade, the station check, the station experiment, prediction ranges | Tables generated from the committed evaluation reports |
| Cloudzy Core live predictions, Prediction History, Weather Explorer, Outdoor Intelligence, alerts, briefings | **Full app only** (Python). The site says so and shows screenshots; nothing is simulated |

### Build and preview locally

```bash
python tools/build_pages.py         # builds _site/ (fetches a hazard snapshot) and verifies it
python tools/serve_pages.py         # http://localhost:8600/Cloudzy/, served like GitHub Pages
```

`--no-live` builds without the hazard snapshot (offline).

The build fails if anything is wrong:
- a page or asset is missing, or a link is broken or root-relative;
- development files (Python, models, databases, `.env`) are present;
- the output contains secret-like strings or local paths.

### Deploy (manual, from `main`)

1. In **Settings → Pages → Build and deployment → Source**, choose **GitHub Actions**.
2. Optional: in **Settings → Environments → github-pages**, limit deployment branches to `main`.
3. Push these files to `main`.
4. Open **Actions → deploy-pages → Run workflow**, choose `main` and run it. It runs lint and tests, builds and
   verifies the site, then publishes it.
5. Watch progress and logs under **Actions → deploy-pages**. The deploy job shows the site's address when it
   finishes.

Nothing deploys automatically. To publish fresh data or changes, run the workflow again. To roll back, revert the
commit on `main` and run it again.

## Security

See [SECURITY.md](SECURITY.md) for:
- how to report a vulnerability;
- the automated checks (dependency vulnerabilities, secret scanning, CodeQL, workflow audit), which run weekly as
  well as on every push;
- the dependency and secret policies;
- the GitHub settings to switch on.

---

## Project structure

```
streamlit_app.py      App entry point and navigation
app_pages/            One file per page (overview, weather, earth, lab, …)
src/                  Everything else, testable without Streamlit
  service.py            Live Cloudzy Core predictions (refuses rather than guesses when inputs are missing)
  features.py, models.py, mos.py, refit.py, intervals.py    ML pipeline and experiments
  stations.py, station_eval.py, station_model.py            Real-observation checks
  monitoring.py, registry.py, reproduce.py                  Tracking, model registry, reproducibility
  hazards/              Earth Intelligence feeds, shared event schema, situations
  earth_view.py         The 3D globe (MapLibre GL JS)
models/               The deployed model and its metadata (checksum, features, metrics)
reports/              Saved experiment results shown in Model Lab
tests/                900+ tests with recorded API fixtures
docs/                 Architecture, Earth Intelligence and air-quality documentation
```

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/EARTH_INTELLIGENCE.md](docs/EARTH_INTELLIGENCE.md) · [docs/AIR_QUALITY.md](docs/AIR_QUALITY.md) ·
[MODEL_CARD.md](MODEL_CARD.md)

---

## Built with

**Python** · **Streamlit** · **scikit-learn** · **pandas / NumPy** · **Plotly** · **MapLibre GL JS** · **SQLite**
(prediction tracking) · **pytest**, **Ruff** and **mypy**

---

## Limitations

- **One place, one variable.** Cloudzy Core predicts temperature for one grid cell in New Delhi. It is
  experimental, not an operational forecast.
- **Reanalysis isn't a thermometer.** Against real station readings, the raw ECMWF forecast is more accurate than
  Cloudzy (see Key findings).
- **Model estimates, not measurements.** Air-quality values are CAMS model estimates, not local monitor readings.
- **Partial hazard coverage.** Official warnings are connected for the United States only, and GDACS covers
  major events worldwide.
- **Not for safety decisions.** Cloudzy is no substitute for official warnings or emergency services. Follow
  your national weather and disaster agencies.

---

## 🗺️ What's next

- Let live tracking build up, then compare live accuracy and range coverage with the offline tests.
- A fair ECMWF baseline matched to what was actually available at prediction time.
- Repeat the pipeline for several cities in different climates.
- Error analysis for heatwaves, the conditions that matter most in Delhi.

---

## Data and attribution

- **Weather:** Open-Meteo.com (CC BY 4.0).
- **Air quality:** Copernicus Atmosphere Monitoring Service (CAMS), via Open-Meteo.
- **Reanalysis:** Copernicus Climate Change Service (ERA5, ERA5-Land).
- **Station observations:** METAR reports via the Iowa Environmental Mesonet, Iowa State University.
- **Hazards:** U.S. Geological Survey, GDACS, NOAA (NHC, NWS, Tsunami Warning Centers), NASA FIRMS and GIBS, the
  U.S. Drought Monitor, and the Smithsonian Global Volcanism Program.
- **Maps:** © OpenFreeMap, © OpenMapTiles, © OpenStreetMap contributors; AWS Terrain Tiles; Natural Earth; search
  by OpenStreetMap Nominatim.

---

## How this was built

<!-- Edit this section in your own words before publishing. -->

I designed and directed this project and made the research decisions:
- the questions;
- the evaluation rules;
- what to adopt and what to reject.

I used AI coding assistants to help write and test the code. Every result reported here was produced by the
code in this repository and can be reproduced with the commands above.

## 📄 License

[MIT](LICENSE) © 2026 Joe Goldberg

**GitHub:** [@WillBettleheim](https://github.com/WillBettleheim) · **Repository:**
[WillBettleheim/Cloudzy](https://github.com/WillBettleheim/Cloudzy)
