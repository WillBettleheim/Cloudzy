"""Every connected provider: what it covers, how often it updates, and its terms.

All were checked on 2026-10-22 (endpoints responded; terms read where published). None needs an API key.
`cadence` is the provider's documented or observed update interval and drives the freshness label;
`ttl` is how long Cloudzy keeps a response before asking again (never more often than the source changes).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    publisher: str
    coverage: str
    global_coverage: bool
    cadence: timedelta
    cadence_text: str
    ttl: timedelta
    licence: str
    attribution: str
    home: str
    history: bool              # can the feed show earlier times (time travel)?
    official: bool             # an official warning or monitoring authority
    note: str = ""
    event_driven: bool = False  # publishes only when something happens: freshness = Cloudzy's last good check


SOURCES = {s.key: s for s in (
    Source("usgs", "USGS earthquakes", "U.S. Geological Survey, Earthquake Hazards Program",
           "Global. Complete worldwide from about M4.5; smaller events mainly in and near the United States.",
           True, timedelta(minutes=1), "Summary feeds regenerate every minute",
           timedelta(minutes=2), "Public domain (U.S. Government work)", "Earthquake data: U.S. Geological Survey",
           "https://earthquake.usgs.gov/earthquakes/feed/", history=True, official=True,
           note="Automatic solutions are revised by seismologists; status shows 'automatic' or 'reviewed'."),
    Source("gdacs", "GDACS alerts", "Global Disaster Alert and Coordination System (United Nations and European "
           "Commission, JRC)", "Global: earthquakes, tropical cyclones, floods, volcanoes, droughts and forest fires.",
           True, timedelta(minutes=30), "Continuous; events update as new data arrive", timedelta(minutes=15),
           "GDACS terms of use: information is indicative and not official", "Disaster alerts: GDACS (UN, European "
           "Commission)", "https://www.gdacs.org/", history=True, official=False, event_driven=True,
           note="GDACS alert levels are automatic, model-based impact estimates. They aren't reviewed before issue "
                "and aren't official warnings."),
    Source("nhc", "NHC tropical cyclones", "NOAA National Hurricane Center / Central Pacific Hurricane Center",
           "Atlantic and eastern and central North Pacific only. Other basins come from GDACS (JTWC, RSMC data).",
           False, timedelta(hours=6), "Advisories every 6 hours (3-hourly intermediate when watches are up)",
           timedelta(minutes=15), "Public domain (U.S. Government work)", "Tropical cyclone data: NOAA/NHC",
           "https://www.nhc.noaa.gov/", history=False, official=True),
    Source("nws", "NWS warnings", "NOAA National Weather Service",
           "United States and its territories and coastal waters only.", False, timedelta(minutes=2),
           "Alerts are issued continuously", timedelta(minutes=3), "Public domain (U.S. Government work)",
           "Warnings: NOAA National Weather Service", "https://www.weather.gov/", history=False, official=True, event_driven=True,
           note="Only alerts the NWS issues with a polygon can be drawn; zone-based alerts are listed."),
    Source("firms", "NASA FIRMS fire detections", "NASA Fire Information for Resource Management System",
           "Global. VIIRS (NOAA-20) 375 m thermal anomalies, last 24 hours.", True, timedelta(hours=3),
           "Near real time: within about 3 hours of the satellite overpass", timedelta(minutes=30),
           "NASA open data; acknowledgement requested",
           "Fire detections: NASA FIRMS (NASA ESDIS), VIIRS NOAA-20 375 m",
           "https://firms.modaps.eosdis.nasa.gov/", history=False, official=False,
           note="A detection is a satellite hotspot, not a confirmed wildfire: industry, gas flares and volcanoes "
                "also appear."),
    Source("usdm", "U.S. Drought Monitor", "National Drought Mitigation Center (UNL), USDA and NOAA",
           "United States only (CONUS, Alaska, Hawaii, Puerto Rico).", False, timedelta(days=7),
           "Weekly: maps valid Tuesday, released Thursday", timedelta(hours=6),
           "Free to use with credit to the U.S. Drought Monitor",
           "Drought: The U.S. Drought Monitor (NDMC, USDA, NOAA)", "https://droughtmonitor.unl.edu/",
           history=False, official=True),
    Source("usgs_volcano", "USGS volcano alerts", "USGS Volcano Hazards Program (U.S. volcano observatories)",
           "United States volcanoes only (Alaska, Cascades, Hawaii, Yellowstone, CNMI…).", False,
           timedelta(hours=1), "Updated when an observatory issues a notice", timedelta(minutes=30),
           "Public domain (U.S. Government work)", "Volcano alerts: USGS Volcano Hazards Program",
           "https://www.usgs.gov/programs/VHP", history=False, official=True, event_driven=True),
    Source("gvp", "GVP volcano catalogue", "Smithsonian Institution, Global Volcanism Program",
           "Global catalogue of Holocene volcanoes (reference, not current activity).", True, timedelta(days=30),
           "Reference database, updated irregularly", timedelta(days=7),
           "Citation requested: Global Volcanism Program, Smithsonian Institution",
           "Volcanoes of the World: Smithsonian Institution, Global Volcanism Program",
           "https://volcano.si.edu/", history=False, official=False),
    Source("tsunami", "Tsunami bulletins", "NOAA U.S. Tsunami Warning System (NTWC Palmer, PTWC Honolulu)",
           "NTWC: U.S. and Canadian coasts. PTWC: Hawaii, U.S. Pacific territories, Caribbean, and international "
           "Pacific and Indian Ocean messages. Other agencies' warnings aren't connected.", False,
           timedelta(minutes=5), "Issued per event", timedelta(minutes=5),
           "Public domain (U.S. Government work)", "Tsunami bulletins: NOAA Tsunami Warning Centers",
           "https://www.tsunami.gov/", history=False, official=True, event_driven=True),
)}

# Not connected, and why (shown in the app and the docs, so a missing layer isn't mistaken for "nothing there").
NOT_CONNECTED = [
    ("Flood inundation extents (Copernicus EMS, GloFAS maps)", "Rapid-mapping products and GloFAS grids need a "
     "per-activation download or a CDS/EWDS account; flood events here are GDACS reports and NWS warnings."),
    ("River gauges (NOAA NWPS)", "The NWPS gauge API timed out (HTTP 504) when checked on 2026-10-22."),
    ("Weekly global volcanic activity (GVP)", "The Smithsonian website was unavailable when checked; global "
     "eruptions come from GDACS and U.S. alert levels from USGS."),
    ("Lightning", "No openly licensed global lightning feed is available."),
    ("Global official warnings outside the U.S.", "National warning feeds use different formats and terms; only "
     "U.S. NWS and NOAA tsunami warnings are connected. GDACS covers major events worldwide but isn't official."),
    ("Wildfire perimeters (NIFC)", "Not connected yet; GDACS forest-fire events carry GWIS burnt-area estimates."),
    ("Global drought monitoring (GDO, GIDMaPS)", "Not connected yet; GDACS drought events are shown when active."),
    ("Population grids", "Settlement context uses Natural Earth populated places (city populations), not a "
     "population grid, so no counts of people affected are made."),
]
