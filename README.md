# ☁️ Cloudzy
### Weather with a little intelligence.

Cloudzy is a weather application that combines real-world weather data, machine learning, and interactive weather exploration to make forecasting more informative and understandable.

Going beyond a conventional weather dashboard, Cloudzy explores how predictive models can learn from historical weather patterns, generate forecasts, and evaluate their predictions against reference data.

## ✨ What is Cloudzy?

Cloudzy brings together weather information, predictive modelling, and software engineering in one evolving project.

### 🧭 Overview
The landing page: today's conditions, alerts, air quality, the next outdoor window, nearby hazards, Cloudzy Core and
tracking, each labelled by the kind of data it is and linked to its full page. Navigation is grouped as **Today**,
**Explore**, **Cloudzy Core** and **You**. The Weather page is at `/weather`; old `/?q=…` links are forwarded.

### 🌦️ Weather Intelligence
- Weather information and forecasts from integrated data sources.
- Location-based weather exploration.
- Weather insights designed to help users understand changing conditions.

### 🌫️ Air Quality Intelligence
- Current air quality for any searched place, with the index standard named every time: **US AQI (EPA)**,
  **European AQI (EEA)** or **India's National AQI (CPCB)**. The place's regional index is shown first, and
  you can switch.
- Concentrations of PM2.5, PM10, NO₂, O₃, SO₂ and CO, compared with the WHO 2021 guidelines.
- An hourly trend chart that keeps the model's earlier hours (grey, "not observed") apart from the forecast
  (blue), a daily outlook, and forecast changes of category with times and values.
- Data: Copernicus Atmosphere Monitoring Service (CAMS) forecasts via the Open-Meteo Air Quality API, about
  4–5 days ahead. These are **model estimates, not monitoring-station readings**, and the app says so.

### 🏃 Cloudzy Outdoor Intelligence
- Better times to run, walk, cycle, exercise or spend time outside, from the air-quality and weather
  forecasts together.
- A transparent rule engine, with no black-box score. It checks air quality (the selected index's own
  guidance), heat (NWS heat index), cold (wind chill), UV (WHO), thunderstorms, freezing rain, rain and
  wind (Beaufort). Critical hazards can't be outweighed by other good conditions.
- Each suggested window shows its values, what supports and limits it, and data limits. An hour-by-hour
  timeline and fact-based explanations come with it, and indoor alternatives are suggested when conditions
  are unfavourable.
- Optional preferences: activity, duration, preferred hours, and more conservative guidance. Nothing
  medical is asked.
- Saved windows are re-checked when new forecasts load, and can raise an alert if they get worse.
- It never promises that a time is safe.

Methods, sources, thresholds and limits are in [docs/AIR_QUALITY.md](docs/AIR_QUALITY.md).

### 🌍 Cloudzy Earth Intelligence
- An interactive 3D globe (MapLibre GL JS) with real disasters and hazards. It zooms from the whole planet to
  streets and OpenStreetMap building footprints (outlines, not imagery).
- Connected sources:
  - **USGS** earthquakes.
  - **GDACS**: global cyclones, floods, fires, volcanoes and droughts, as automatic impact estimates, not
    official warnings.
  - **NOAA NHC** cyclones, with cone, observed track and forecast track.
  - **NWS** warnings (U.S. only).
  - **NASA FIRMS** satellite fire detections (not confirmed fires).
  - The **U.S. Drought Monitor**.
  - **USGS** volcano alert levels.
  - The **Smithsonian** volcano catalogue.
  - **NOAA** tsunami bulletins.
- Each feed shows its real freshness (Live feed, Recently updated, Delayed data, Historical dataset or
  Temporarily unavailable). A failing feed never stops the others.
- The globe has layer controls, filters, a searchable event list kept in sync with the map, and a timeline
  that plays records by their own times. Search covers countries, states, cities, coordinates and addresses.
- Selecting an event shows its source-backed details, a link to the original record, and nearby mapped
  settlements (proximity, never an impact estimate).
- For situational awareness only: it isn't an official warning service and doesn't cover every country.

Details, sources, licences and limits are in [docs/EARTH_INTELLIGENCE.md](docs/EARTH_INTELLIGENCE.md).

### 🧠 Cloudzy Core
Cloudzy Core is the machine-learning component of the project.

- Historical weather data processing.
- Machine-learning-based weather prediction.
- Chronological training, validation, and testing.
- Model evaluation against baseline approaches.
- Prediction tracking and accuracy analysis as reference data becomes available, checked automatically in the
  background once the reanalysis is published (about 6 days later).
- **Validated 80% prediction ranges.** Split conformal, checked on later data: they held 83–86% of September
  2026 hours, a month the deployed model never saw.
- A model registry and an uncertainty report in **Model Lab**, plus live tracking in **Prediction History**
  (MAE, RMSE, bias, skill against the provider value, and range coverage).
- Cloudzy Core predicts temperature only (New Delhi). It does **not** predict air quality: the available
  air-quality history is itself model output, with no independent measurements to evaluate against (see
  [docs/AIR_QUALITY.md](docs/AIR_QUALITY.md#cloudzy-core-and-air-quality)).

### 🗺️ Interactive Weather Exploration
- Interactive 2D weather map.
- Geographic navigation and available weather overlays.
- Exploration of weather patterns across locations.

### 🔔 Alerts and Personalization
- Configurable weather alerts where supported, including air quality, fine particles (PM2.5),
  thunderstorms and saved outdoor plans. In-tab browser notifications are optional; there are no
  background or push notifications.
- Saved locations and user preferences where implemented.
- Weather insights based on available data and application settings.

*Feature availability depends on the current version of the application and its configured data sources.*

## 🧪 Engineering and Reliability

Cloudzy is developed with an emphasis on more than just displaying weather data.

Key engineering priorities include:

- Reproducible machine-learning evaluation.
- Time-aware data splitting to reduce future-data leakage.
- Automated testing and code-quality checks.
- Reliable handling of unavailable external services.
- Transparent prediction status and evaluation results.

## 🛠️ Technology

The project uses a Python-based application and machine-learning workflow, supported by development tools such as:

- **Python** — application logic and machine learning.
- **Ruff** — linting and code quality.
- **mypy** — static type checking.
- **pytest** — automated testing.
- **Weather data APIs** — weather information and historical reference data.

The complete stack and provider list should be verified against the current repository and its dependency files.

## 🚀 Getting Started

### Prerequisites

- Git
- Python compatible with the project's dependency configuration
- Any API credentials required by configured data providers

### Installation

Clone the repository:

```bash
git clone https://github.com/WillBettleheim/Cloudzy.git
cd Cloudzy
```

Create a virtual environment on macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies using the repository's supported dependency file. If the project uses `requirements.txt`, run:

```bash
python -m pip install -r requirements.txt
```

Configure any required environment variables according to the project's setup instructions. Never commit API keys or other secrets.

### Running Cloudzy

From the project folder, with the virtual environment active:

```bash
streamlit run streamlit_app.py
```

Then open http://localhost:8501. Earth Intelligence is at http://localhost:8501/earth; its first load
fetches nine feeds and takes up to about 25 seconds, and later views are instant.

No API keys are needed. Cloudzy uses Open-Meteo's free, key-less APIs (non-commercial use; 600 requests per
minute, 5,000 per hour, 10,000 per day), and Earth Intelligence's sources are also key-less. Responses are
cached: 10 minutes for weather, 15 minutes for air quality, and each hazard feed for its own update
interval.

### Testing

```bash
python -m pytest                 # offline suite, network faked
python -m pytest -m integration  # live checks against the Open-Meteo APIs
ruff check .
mypy
```

### Data sources and attribution

- Weather data by Open-Meteo.com (CC BY 4.0).
- Air quality: Copernicus Atmosphere Monitoring Service (CAMS) forecasts, via Open-Meteo.com (CC BY 4.0).
  Contains modified Copernicus Atmosphere Monitoring Service information.
- Earth Intelligence:
  - U.S. Geological Survey, GDACS, NOAA (NHC, NWS, Tsunami Warning Centers), NASA FIRMS and GIBS, the U.S.
    Drought Monitor, and the Smithsonian Global Volcanism Program.
  - Basemap © OpenFreeMap, © OpenMapTiles, © OpenStreetMap contributors.
  - Terrain from the AWS Terrain Tiles, and search by OpenStreetMap Nominatim.
- Reanalysis: Copernicus Climate Change Service (ERA5, ERA5-Land).

## 📊 Model Evaluation

Cloudzy distinguishes between a generated prediction and an evaluated prediction.

A prediction should only be evaluated when suitable reference data is available and can be matched appropriately by time, location, and measurement units.

Model metrics should be calculated from valid evaluated records rather than treating pending predictions as successful or failed results.

Historical evaluation scores are not guarantees of future forecasting accuracy.

What has actually been measured for the deployed model:

| Measure | Value |
|---|---|
| 2025 test MAE, +1 h to +24 h | 1.12 to 1.34 °C |
| IFS value MAE on the same test | 1.42 °C |
| Persistence MAE on the same test | 1.69 to 6.33 °C |
| September 2026 held-out MAE, +1 h to +24 h | 0.83 to 1.17 °C |
| 80% range coverage, September 2026 | 83–86% |

Details are in [MODEL_CARD.md](MODEL_CARD.md).

## ⚠️ Important Disclaimer

Cloudzy is an independent weather and machine-learning project. Weather information and model predictions may be incomplete, delayed, or inaccurate.

Cloudzy is not a substitute for official weather warnings, emergency services, or instructions from local authorities. Do not rely on it as the sole source for safety-critical decisions.

Air-quality values are model estimates for an area, not local measurements. Outdoor suggestions come from
forecasts and published guidance, not from a medical assessment. Forecasts change and people react
differently to heat and pollution, so Cloudzy never promises that a time is safe.

## 🗺️ Development Roadmap

Ongoing development focuses on:

- Refining the visual experience and responsive interface.
- Improving interactive weather-map usability.
- Strengthening prediction tracking and model evaluation.
- Improving performance, reliability, and automated testing.
- Expanding documentation and transparency around weather data.

These are development goals, not a guarantee that every capability is already available.

## 📄 License

Refer to the repository's `LICENSE` file for the applicable terms governing use, modification, and distribution of Cloudzy.

## 👨‍💻 Author

**Project:** Cloudzy — Weather with a little intelligence.

**GitHub:** [@WillBettleheim](https://github.com/WillBettleheim)

**Repository:** [WillBettleheim/Cloudzy](https://github.com/WillBettleheim/Cloudzy)

---

*Built to explore the intersection of weather data, machine learning, and software engineering.*
