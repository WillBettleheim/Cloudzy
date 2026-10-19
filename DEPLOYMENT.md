# Deploying Cloudzy

Everything the app needs is in the repository:
- the code
- the theme (`.streamlit/config.toml`)
- the Lab's data (`reports/lab/`)
- the compact serving model, about 2 MB (`models/cloudzy_core_delhi_serving_compact.joblib`)

No API keys or secrets are required, because Open-Meteo's free tier needs none.

## Option 1: Streamlit Community Cloud (free)

1. Go to <https://share.streamlit.io> and sign in with the GitHub account that
   owns the repository.
2. Click **Create app** → **Deploy a public app from GitHub**, then enter:
   - Repository: `WillBettleheim/Cloudzy`
   - Branch: `main`
   - Main file path: `streamlit_app.py`
3. Open **Advanced settings**:
   - Set **Python version** to **3.13**. pandas 3 needs Python 3.11 or newer.
   - Under **Secrets**, add the line below. It stops the app saving live-input
     snapshots, which a hosted app's temporary disk can't keep anyway.

     ```toml
     CLOUDZY_SNAPSHOTS = "0"
     ```

4. Click **Deploy**. The first build installs `requirements.txt` and takes a
   few minutes.

After that, every push to `main` redeploys automatically.

**Check that it works:**
- The weather page loads for "New Delhi".
- The **Cloudzy Core 🧪** tab shows six predictions.
- `/lab` shows the scoreboard.

If the Cloudzy tab says the inputs could not be used, Open-Meteo is probably
briefly overloaded (HTTP 503). The provider forecast still shows, and
**Try again** retries.

**Usage limits.** Open-Meteo's free API is for non-commercial use, with daily
and per-minute call limits. The app caches every response for 10 minutes per
place. A briefing makes three calls (geocoding, forecast, live IFS inputs), so a
small public demo stays far inside the limits.

## Option 2: run locally

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Updating the deployed model

Run this once a month, after the previous month's reanalysis is published (about
a week into the month):

```bash
python -m src.refit
```

It downloads data up to the last complete month, refits each horizon with the
same hyperparameters, and compares new and current models on that month. It
installs the new bundle only if the new models aren't worse on average, and
moves the old one to a dated backup, which git ignores. Then commit the new
bundle:

```bash
git add models/cloudzy_core_delhi_serving_compact.joblib reports/refit_delhi.json
```

```bash
git commit -m "Monthly refit"
```

```bash
git push
```

The hosted app redeploys with the new model.

## Continuous integration

`.github/workflows/tests.yml` runs on GitHub Actions:
- **Offline test suite:** on every push and pull request.
- **Live Open-Meteo integration checks:** every Monday, or on demand from the
  Actions tab ("Run workflow"). These catch upstream changes such as renamed
  fields or new units.

To run the same checks locally:

```bash
python -m pytest
```

```bash
python -m pytest -m integration
```
