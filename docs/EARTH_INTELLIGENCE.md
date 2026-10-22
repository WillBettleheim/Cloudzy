# Cloudzy Earth Intelligence

Real disasters and hazards on an interactive 3D globe. The page is **Earth Intelligence** in the sidebar
(`/earth`). Code: `src/hazards/` (data), `src/earth_view.py` (globe), `src/earth_search.py` (search) and
`app_pages/earth.py` (page).

It's for situational awareness only. It isn't an official warning service, it doesn't cover every country
or hazard, and an empty area on the globe doesn't mean that area is safe.

## Globe technology

**MapLibre GL JS 5.24.0** (BSD-3-Clause), self-hosted in `static/vendor/maplibre/`, in globe projection, as a
Streamlit components-v2 component.

- **Why MapLibre:** it is a true 3D globe that zooms continuously from the whole Earth to street level by
  streaming map tiles.
- **Why not the alternatives:**
  - The existing Weather Explorer globe (three.js on one 4096-pixel image) can't reveal detail on zoom.
  - CesiumJS needs a Cesium ion account for its default imagery and terrain.
  - deck.gl would add a second rendering stack.
- **What stays:** the Weather Explorer is unchanged.

### Map sources and zoom levels

| Zoom | What you see | Source |
|---|---|---|
| Earth, continent (z0–4) | Relief and bathymetry imagery, coastlines, country borders, country labels | NASA GIBS Blue Marble (a 2004 composite, labelled as such); OpenFreeMap |
| Country, state (z4–8) | State and province borders, cities, terrain shading | OpenFreeMap (OpenStreetMap data); AWS Terrain Tiles |
| City, neighbourhood (z8–14) | Roads, railways, waterways, land use, suburbs; imagery fades out | OpenFreeMap |
| Streets, buildings (z14–19) | Street names and **OpenStreetMap building footprints** | OpenFreeMap |

- **Optional imagery:** NASA VIIRS true-colour imagery of yesterday (250 m pixels, so no buildings; clouds
  and swath gaps are real).
- **Buildings:** building outlines come from OpenStreetMap mapping, not imagery, and their completeness
  varies by place. **No connected source shows individual houses from above**, and nothing on the globe
  describes the condition of a building.
- **Borders:** Natural Earth outlines, drawn when a searched country or state is highlighted, follow India's
  official point of view, as elsewhere in Cloudzy. The OpenFreeMap basemap uses OpenStreetMap's own borders.

## Connected data

All endpoints were checked live on 2026-10-22, and none needs an API key.

| Source | Categories | Coverage | Updates | Record kind |
|---|---|---|---|---|
| USGS earthquake feeds and FDSN | Earthquakes | Global; complete from about M4.5 | Every minute | Observed (automatic or reviewed) |
| GDACS (UN and European Commission, JRC) | Earthquakes (orange and red alerts), tropical cyclones, floods, volcanoes, droughts, forest fires | Global | Continuous | **Automatic impact estimate, not official** |
| NOAA National Hurricane Center | Tropical cyclones: advisory position, cone, forecast track, best track | Atlantic and eastern/central Pacific | Advisories every 6 h | Official warning (observed and forecast kept apart) |
| NOAA National Weather Service | Floods, severe storms, tornado, heat, cold, winter, fire weather, tsunami, tropical | **United States only** | Continuous | Official warning |
| NASA FIRMS (VIIRS NOAA-20) | Satellite fire detections, last 24 h | Global | Within about 3 h of the overpass | **Satellite detection, not a confirmed fire** |
| U.S. Drought Monitor | Drought categories D0–D4 | United States only | Weekly (valid Tuesday, out Thursday) | Assessment |
| USGS Volcano Hazards Program | Volcano Alert Level and aviation colour code | U.S. volcanoes only | On notice | Official |
| Smithsonian GVP | Holocene volcano catalogue | Global | Reference | Catalogue, not current activity |
| NOAA Tsunami Warning Centers (NTWC, PTWC) | Tsunami bulletins | The centres' areas of responsibility | Per event | Official |

### Not connected yet

The page lists these too, so a missing layer isn't read as "nothing there".

| Not connected | Why |
|---|---|
| Flood inundation extents (Copernicus EMS, GloFAS maps) | They need per-activation downloads or a CDS/EWDS account |
| NOAA river gauges (NWPS) | The API timed out when checked |
| Smithsonian weekly activity reports | The site was unavailable when checked |
| Lightning | No openly licensed global feed exists |
| National warning feeds outside the U.S. | Each country uses its own formats and terms |
| NIFC wildfire perimeters | Not connected yet |
| Global drought observatories | Not connected yet |
| Population grids | None connected; settlements are Natural Earth places with city populations, and Cloudzy makes no "people affected" counts |

## Shared event schema

Every record becomes a `HazardEvent` (`src/hazards/schema.py`) with these fields:

- **Identity and type:** `id` (`source:own id`), `source`, `category`, `kind`, `title`.
- **Where and when:** `lat` and `lon` (validated WGS84), `time`, `updated` and `ends` (all UTC).
- **Severity:** `severity_rank` 0–4, `severity_label` (the source's own words) and `severity_definition`
  (what the source means).
- **Content:** `summary`, `area`, `country`, `url` (the authoritative record) and `official`.
- **Measurements:** `measurements`, exactly as reported and with units, such as "35 kt (65 km/h), 1-minute
  average".
- **Shape and links:** `geometry` (a warning polygon or cone), `current`, `related` (the same event in other
  sources) and `extra` (for example tracks).

**Record kinds are never mixed:**

| Kind | Meaning |
|---|---|
| observation | Located or measured by the source (USGS) |
| forecast | A prediction (forecast track points) |
| warning | Issued by an official warning authority |
| impact_estimate | A GDACS automatic alert |
| detection | A FIRMS satellite hotspot |
| assessment | The weekly U.S. Drought Monitor |
| catalogue | The GVP volcano list |

**Severity rank:** a documented mapping used only for sorting and filtering, always shown next to the
source's own label.

| Source | Rank mapping |
|---|---|
| USGS | Magnitude <4 → 1, 4–5.9 → 2, 6–6.9 → 3, ≥7 → 4; a PAGER alert (green 1, yellow 2, orange 3, red 4) can raise it |
| GDACS | Green 1, orange 3, red 4 |
| NWS | CAP severity: Minor 1, Moderate 2, Severe 3, Extreme 4 |
| NHC | Wind below 34 kt → 1, tropical storm 2, hurricane 3, major hurricane 4 |
| USGS volcanoes | The higher of alert level and colour code (Normal or Green 1 up to Warning or Red 4) |
| Tsunami | Information 1, Advisory 2, Watch 3, Warning or Threat 4 |

## Freshness

Labels come from the data's own age compared with the source's documented cadence (`schema.freshness`):

| Label | When |
|---|---|
| Live feed | The source updates every 15 minutes or faster, and the data are younger than 2× its cadence |
| Recently updated | The data are within the source's normal, slower schedule |
| Delayed data | The data are older than 3× the cadence, or the last refresh failed and earlier data are shown |
| Historical dataset | A reference catalogue, or a chosen-dates archive query |
| Temporarily unavailable | Nothing could be loaded |

Some feeds publish only when something happens: GDACS, NWS, USGS volcano notices and tsunami bulletins.
For them, freshness comes from Cloudzy's last successful check, because a quiet feed isn't late. A feed
with failed refreshes is never called live.

## Caching, refresh and performance

- **Server-side store** (`src/hazards/store.py`):
  - **One cache per source**, kept for the source's own TTL: 2 min for USGS, 3 min for NWS, 15 min for
    GDACS and NHC, 30 min for FIRMS, 6 h for the Drought Monitor and 7 days for GVP.
  - **De-duplicated requests:** concurrent page runs share one request per source.
  - **Stale data first:** pages get the last good data at once while refreshes run in the background. Only
    the very first load waits, up to 25 s.
  - **Failures:** after a failure Cloudzy keeps the last good data, marks it "Delayed data" and retries no
    more than once a minute. A source that has never loaded is "Temporarily unavailable"; the other sources
    and the globe keep working.
- **Large layers become static files:** fire detections (~130,000, compacted to ~3 MB), drought polygons
  (simplified from 18 MB to ~0.3 MB) and the event list go to `static/earth/`. File names are
  content-hashed, so the browser downloads each version once. Writes are atomic and safe when several
  visitors refresh together, and only 3 versions of each are kept.
- **Requests from map use:** moving the camera, filtering, playback and layer toggles make **no data
  requests**. Map tiles stream from their providers as the camera moves. GDACS polygons for one event load
  only when that event is selected.
- **In the browser:**
  - **Clustering:** minor events are clustered at global scale (cluster radius 46 px, up to zoom 4), while
    severe ones (rank ≥ 3) are never clustered.
  - **Labels:** they declutter by priority.
  - **Fire detections:** drawn as a heat map far out and as points close in.
  - **Requests:** a superseded file request is cancelled.
  - **Cleanup:** the map, observers and timers are released when the component leaves the page.
  - **Failures:** a failed tile is counted and reported, and has no effect on the hazard layers.

## Interaction

| What | How |
|---|---|
| Move and zoom | Drag to rotate, wheel or trackpad and pinch to zoom, double-click to zoom in, keyboard arrows and +/- on the focused map |
| Camera buttons | Zoom, whole globe, reset camera, auto-rotate (stops on any interaction, off under reduced motion), fullscreen |
| Situations and layers panel | The disaster situation selector, record layers, filters (general and per situation), overlays (fire detections, warning areas, cyclone tracks, drought), base imagery, terrain, labels. See *Disaster situations* below |
| Filters | Minimum severity, official sources only, current records only, plus per-situation filters. Each active filter is a removable chip on the globe, with "Clear all". Hiding is stated as hiding, not absence |
| Event list | Text filter, "only in view" and sort. Selecting in the list flies the globe there and highlights the marker; selecting a marker highlights the list entry |
| Event details | The globe's card has: copy coordinates, hide this situation, and (when the record is hidden by the current selection) why, with a one-click fix. Under the globe, the full source-backed record has: focus on the globe, open the source, hide or show its situation, copyable coordinates, the same event in other sources, and related events within 300 km |
| Search | Countries and states (Natural Earth, with outline), cities (Open-Meteo), coordinates, and neighbourhoods, streets and addresses (OpenStreetMap Nominatim: on Enter only, at most 1 request/s, cached). The table can then be limited to that area (inside the outline, or within 150 km) |
| Coordinates | Cursor coordinates, the zoom level's name, and a copy-centre button |
| Timeline | Records are shown by their own times inside a sliding window (1 h to the whole period); play, step and "Now". See below |

## Disaster situations

`src/hazards/situations.py` sorts every loaded record into one of 18 situations. The sorting uses the feed's
category and, for official warnings, the product name the agency used. For example, "Tornado Warning" →
tornadoes, "Dust Storm Warning" → dust storms, "Wind Chill Advisory" → extreme cold.

| Situation | Sources | Situation | Sources |
|---|---|---|---|
| Earthquakes | USGS, GDACS | Severe wind | NWS |
| Floods | NWS, GDACS | Tsunamis | NOAA tsunami centres, NWS |
| Tropical cyclones | NHC, GDACS, NWS | Volcanoes | USGS alert levels, GDACS |
| Drought | U.S. Drought Monitor, GDACS | Landslides | **not connected** |
| Wildfires | NASA FIRMS, GDACS, NWS fire weather | Snowstorms and blizzards | NWS |
| Severe thunderstorms | NWS | Dust storms | NWS |
| Tornadoes | NWS | Lightning | **not connected** |
| Extreme heat | NWS | Other official warnings | NWS (off by default) |
| Extreme cold | NWS | | |
| Heavy rainfall | **not connected** | | |

Situations without a source stay in the list, greyed out with a "not connected" badge. Hovering one says why and
where to look instead. They can't be switched on, so no markers there is never presented as "no events".

**Selector.**
- **Actions:** search the list, switch one on or off, show all, all off, restore defaults.
- **Presets:** All available, Geological, Storms, Water hazards, Climate and heat, Fire and dryness, and My
  selection.
- **My selection:** saved in the viewer's browser (`localStorage`, nothing is sent anywhere). If one is saved it
  is applied when the page opens.
- **Counts:** each situation shows how many records are drawn with the current filters, or "off" with the number
  loaded.

**Record layers** separate what a record is:
- **Observed events:** located earthquakes, satellite detections, drought assessments.
- **Official warnings.**
- **Provider forecasts and automatic estimates:** GDACS model-based alerts.
- **Cloudzy ML predictions:** the newest recorded Cloudzy Core prediction, a single point at the Delhi grid cell.
  It shows its model version, issue and valid times, value and 80% range, and evaluation status. It is read from
  the monitoring store, never fetched or computed for the globe, and is labelled as temperature, not a hazard
  forecast.
- **Historical analysis:** the Holocene volcano catalogue.

Official forecast tracks and cones stay under the cyclone filters.

**Per-situation filters.** Each group appears only while its situation is on:

| Situation | Filters |
|---|---|
| Earthquakes | Magnitude from (on top of the loading threshold); depth bands: shallow < 70 km, intermediate, deep > 300 km |
| Tropical cyclones | Basin: NHC's own bin, otherwise the basin containing the position (approximate near Central America); intensity; forecast positions and cones |
| Floods | Official warnings (NWS) or automatic alerts (GDACS). Flood forecasts and inundation maps are stated as not connected |
| Wildfires | Fire radiative power, detection confidence, detection age: these act on the fire-detection overlay |
| Drought | Minimum U.S. Drought Monitor category |
| Severe-weather warnings | Product (warning, watch, advisory, statement); in force now (not past expiry) |

**Sync without rebuilding the globe.** Every change updates the globe at once: markers, areas, legend, counts,
event list and timeline. The globe then sends its state (`view`) to the page. The page applies the same rules
(`situations.matches`) to the *Situations* table and the event table, so the per-situation counts follow the
selector and a searched place. The page's own *Situations* picker is a fallback without WebGL, and it changes
the globe too. Removing the period or place chip on the globe resets those page controls.

`tests/test_situations.py` checks that the JavaScript filter keys, record layers and trigger names match the
Python side.

**Timeline rules.**

- **Current-only feeds** (NWS, NHC, U.S. drought, volcano alerts, tsunami bulletins) appear only at "Now".
  So do NHC/GDACS forecast positions and cones.
- **Observed cyclone positions** appear as their times pass.
- **Fire detections** use their real times within the last 24 hours.
- **"Chosen dates"** uses the USGS FDSN service (M2.5+, at most 4,000 events) and the GDACS archive, for at
  most 62 days.
- **No static record is animated** as if it were moving.

**Historical seismicity** (`src/hazards/seismicity.py`): for a selected earthquake, the page reads the USGS
catalogue (M4+ within 300 km since 1976) and shows the following, labelled as historical analysis:

- counts and per-decade historical rates for M5, M6 and M7 and above;
- the largest recorded event;
- the magnitude-type mix (mostly mb, which saturates for large events, so the b-value is indicative);
- a Gutenberg–Richter b-value: Aki–Utsu maximum likelihood above a maximum-curvature completeness magnitude
  (+0.2), with a 90% bootstrap interval.

Nothing is forecast. For long-term hazard the page links to the GEM Global Seismic Hazard Map. Results are cached
for a day per 0.25° cell. The b-value estimator is tested on synthetic catalogues with a known slope.

**Nearby context** (`src/hazards/analysis.py`) lists Natural Earth settlements as follows:

| Selected record | Settlements listed |
|---|---|
| Earthquake | Within 100 km |
| Fire detection | Within 25 km |
| Official warning or cone polygon | Inside it (point-in-polygon) |
| Cyclone without a cone | Within 100 km of the forecast track |

Each list includes other loaded events within 300 km. Cloudzy makes no casualty, damage or "affected"
estimates. Proximity is explicitly not presented as impact.

## Licensing and attribution

| Source | Terms | Attribution |
|---|---|---|
| USGS, NOAA (NHC, NWS, tsunami centres) | Public domain (U.S. Government works) | Credit the agency |
| NASA FIRMS and GIBS | Public domain | FIRMS acknowledgement shown |
| GDACS | Its terms of use say information is indicative and not official | Credited. No explicit reuse licence was published (the copyright page returned 404); the page repeats GDACS's disclaimers |
| U.S. Drought Monitor | Credit requested | "The U.S. Drought Monitor (NDMC, USDA, NOAA)" |
| Smithsonian GVP | Citation requested | "Global Volcanism Program, Smithsonian Institution" |
| OpenFreeMap / OpenMapTiles / OpenStreetMap | ODbL; free, no limits on the public instance | Attribution shown (map control and page) |
| Nominatim | OSM Foundation usage policy | 1 request/s, User-Agent, no autocomplete, cached |
| AWS Terrain Tiles (Mapzen) | Free | SRTM, GMTED, ETOPO1 and others credited |
| Natural Earth | Public domain | Credited |
| MapLibre GL JS | BSD-3-Clause | Licence file kept |

## Tests

| Command | Covers |
|---|---|
| `python -m pytest` (offline) | `tests/test_hazards.py`: each provider's parsing against trimmed real responses (`tests/fixtures/hazards/`), malformed fields, time zones, coordinate validation, freshness, categories, forecast/observation separation, HTTP retries, 204 and 429 |
| | `tests/test_hazard_store.py`: caching, de-duplication, stale-while-refreshing, failure isolation, cross-source merging, static files, analysis, search |
| | `tests/test_earth_page.py`: the page, feed failures, globe ↔ page selection, fire detections, search focus and area filtering, custom dates |
| `python -m pytest -m integration` | Live checks of USGS, GDACS, NHC, NWS, USGS volcanoes and tsunami feeds |

Globe rendering, camera, layer toggles, timeline, clicks, search flights and the phone layout were tested by
hand in the browser.
