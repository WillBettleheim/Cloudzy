# Feature inventory and static-hosting status

Recorded on 2026-10-23, while the static GitHub Pages edition and the security checks were added. The full app
(`streamlit run streamlit_app.py`) keeps every feature listed here. The static edition (`tools/build_pages.py`) is a
separate site; it doesn't replace or change the app.

GitHub Pages categories:
1. **Works fully** on static hosting.
2. **Needs an external service:** runs in the visitor's browser against a public API, or shows data a build fetched.
3. **Needs the Python backend:** can't run on GitHub Pages; the static site says so and shows screenshots.

## Full app features

| Feature (page) | Behaviour | Changed by this work? | GitHub Pages |
|---|---|---|---|
| Overview (`/`) | Tiles for conditions, alerts, air quality, outdoor window, hazards, Cloudzy Core, tracking | No | 3 (screenshot) |
| Weather (`/weather`) | Open-Meteo forecast, briefing, charts, rule-based alerts and suggestions, device location | No | 2: forecast, 7 days, 48 h chart, air quality now (browser → Open-Meteo); briefing and alerts are 3 |
| Cloudzy Core predictions (on Weather) | Live ML temperature for Delhi with an 80% range | No | 3: the model runs in Python |
| Air Quality (`/air-quality`) | CAMS pollutants, regional index, trend, outlook | No | 2 for current AQI and PM (Weather page); full page is 3 |
| Outdoor Intelligence (`/outdoor`) | Rule engine for activity windows | No | 3 |
| Earth Intelligence (`/earth`) | 3D globe, 18 situations, presets, filters, timeline, search, event details, historical seismicity | Hazard parsing hardened (identical results on live feeds, see below) | 2: the same globe with a snapshot of the feeds taken by the build, labelled with its time; live refresh, search, details panel and seismicity are 3 |
| Weather Explorer (`/explorer`) | GFS global maps and 3D globe | No | 3 (GRIB decoding in Python) |
| History & Trends (`/history`) | Reanalysis history for any place | No | 3 |
| Prediction History (`/predictions`) | Tracked predictions evaluated against the reanalysis | No | 3 (server database) |
| Model Lab (`/lab`) | Evaluation, registry, uncertainty, station check | No | 1: results tables generated from the committed reports; interactive charts are 3 |
| Alerts, Places, Settings, Terms | Preferences in each visitor's browser | No | 3 |
| Mobile bottom navigation, responsive layout | Phones to ultrawide | No | 1 (the static site has its own responsive layout) |
| ML pipeline, monthly refit, interval study, station evaluation, reproducibility | Offline commands | No | Not applicable (offline tools) |

## Changes to the existing app in this work, and why

| Change | Why | Effect on behaviour | How it was checked |
|---|---|---|---|
| Streamlit 1.66.0 → 1.66.1 | Needed to allow the Pillow fix | None expected (patch release) | 930/930 original tests and 12/12 live tests pass; full 13-page × 10-size browser audit; browser layout tests |
| Pillow 12.3.0 → 13.0.0 (newly pinned) | 35 published advisories, fixed in 13.0.0 | None found | Same as above, first in a clean checkout |
| `defusedxml` for NOAA tsunami and NHC KMZ feeds | Block XML entity expansion and external entities in untrusted feeds | None for real data: today's live feeds parse to identical trees with both parsers | Side-by-side comparison on the live NHC (4 files) and tsunami (2) feeds; hazard tests |
| 25 MB unzip limit for NHC KMZ; 64 MB limit on hazard responses | Prevent memory exhaustion from a malicious or broken file | None for real data: the largest live feed (FIRMS, 24 h) is 9.9 MB; KMZ files are under 50 kB unzipped | Measured live sizes; new tests |
| `sha1(..., usedforsecurity=False)` for cache file names | Mark a non-security hash as such | None (same digest) | Hazard store tests |
| Comments on two SQL lines | Document why they are safe | None | Monitoring tests |
| `.streamlit/config.toml`: `maxUploadSize = 1`, explicit `enableXsrfProtection = true` | No uploads exist; XSRF already on by default | None | Config test; app runs |
| Larger tap areas for the map's attribution toggle and the situation picker's "remove" crosses (CSS, touch screens only) | Found by the post-upgrade browser audit (24 px and 15 × 7 px) | Visual size unchanged; easier to tap | Earth page re-audited at five touch sizes: no undersized controls |

Security audit, 2026-11-01 (SECURITY.md, "Security audit"):

| Change | Effect on behaviour | How it was checked |
|---|---|---|
| Model checksum verified before unpickling (app and offline tools) | None for genuine models; a tampered file is refused before it runs | Live Cloudzy forecast with ranges; all 4 models reproduce exactly; malicious-pickle test |
| HTTPS host allowlist for hazard requests, feed-supplied URLs and redirects | None: all 9 live feeds load, and no request was refused | Live hazard fetch (1,160 events); 12/12 live integration tests |
| Feed links limited to `http(s)`; globe catalogue links limited to `https` | None for real feeds | Tests |
| Geocoding results range-checked | Results with impossible coordinates are skipped | Tests; search tests |

Not changed: error details. Hiding stack traces (`showErrorDetails = "type"`) matters only for public hosting and
would make local debugging worse, so it's documented in SECURITY.md as a step for when the app is hosted.
