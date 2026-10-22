"""Cloudzy's Terms of Use: the text, kept apart from the page that shows it (app_pages/terms.py).

What this text may and may not say (checked against the code on the date below):
- Only Cloudzy's real features and providers are described (Open-Meteo, OpenStreetMap Nominatim, NOAA GFS and
  GFS-Wave, NASA GIBS imagery, Natural Earth, Copernicus ERA5 for Cloudzy Core's training data).
- The software is MIT-licensed (LICENSE, copyright Joe Goldberg). Nothing here narrows the rights that licence
  grants, and third-party code, data and assets keep their own licences and terms.
- No company, address, contact channel, governing law or privacy practice is invented. The open items are listed
  in REVIEW_ITEMS and docs/LEGAL_REVIEW.md, and the affected sections say what's still to be decided.

This is a carefully worded draft, not legal advice; it hasn't been reviewed by a lawyer.
"""

from __future__ import annotations

from dataclasses import dataclass

LAST_UPDATED = "21 October 2026"
OWNER = "Joe Goldberg"                      # the copyright holder named in LICENSE
REPOSITORY = "https://github.com/WillBettleheim/Cloudzy"

# Must be resolved before Cloudzy is offered publicly (also listed in docs/LEGAL_REVIEW.md).
REVIEW_ITEMS = [
    "Contact: no contact method exists yet. Add one (section 15) before public release.",
    "Governing law and disputes: not decided. Confirm with a lawyer and complete section 14.",
    "Privacy Policy: not written yet. It's a separate required task (section 10).",
    "Legal review: these terms haven't been reviewed by a lawyer; Indian requirements to check are listed in "
    "docs/LEGAL_REVIEW.md.",
]


@dataclass(frozen=True)
class Section:
    anchor: str
    title: str
    body: str            # Markdown
    review: str = ""     # a note that something here still needs a decision


SECTIONS: list[Section] = [
    Section("introduction", "Introduction and acceptance", f"""
These terms explain how you may use Cloudzy ("Cloudzy", "the app"), a weather app with an experimental
machine-learning model, made by {OWNER} (the "project owner"). Please read them before using Cloudzy.

By using Cloudzy you're agreeing to use it in line with these terms. If you don't agree, please don't use it.
There's nothing to sign or click: these terms apply to use of the app, and they don't replace any rights you
have under the law that applies to you.

Cloudzy's source code is published under the MIT licence (see Intellectual property). These terms cover the use
of the running app; the licence covers the code.
"""),
    Section("service", "What Cloudzy does", """
Cloudzy shows weather information gathered from other services and from its own model:

- **Weather:** current conditions, 24-hour and 7-day forecasts, and a short written summary for a place you
  search for, save or (if you allow it) your device's location. Forecasts come from Open-Meteo.
- **Weather Explorer:** a 3D globe and a 2D map of NOAA's GFS weather model (temperature, wind, precipitation,
  cloud, pressure, gusts, humidity, CAPE, snow depth and GFS-Wave wave height), with a forecast timeline,
  country and state boundaries, place names and regional averages.
- **Alerts:** weather alerts that Cloudzy works out from the forecast for the place you're viewing, using
  thresholds you can change, with optional browser notifications.
- **History & Trends:** past weather for a place since 1940, from Open-Meteo's historical reanalysis (model
  reconstructions of past weather, not station observations).
- **Places and Settings:** saved places, a home city, units and other preferences, kept in your own browser.
- **Cloudzy Core:** an experimental machine-learning model that predicts temperature for one location (a grid
  cell in New Delhi), with live tracking that compares its predictions with reanalysis data published a few
  days later (a model reconstruction of past weather, not a station measurement), and an evaluation page
  (Model Lab).
"""),
    Section("weather", "Weather information", """
Weather information is uncertain by nature. Forecasts, model output, maps, alerts and summaries in Cloudzy:

- **Can be wrong**, incomplete, delayed or unavailable, and conditions can change faster than any forecast.
- **Are model data, not measurements:** almost everything Cloudzy shows is a computer model's forecast or
  estimate, labelled as such. Values between grid points are interpolated, and small-scale weather (for example
  a single thunderstorm) may not appear at all.
- **Come from other services** whose data may change, stop, or contain errors Cloudzy can't detect.

Cloudzy's own alerts are worked out by the app from forecast data. They are **not official warnings**.

For any decision that matters, check an authoritative source, such as your national weather service (in
India, the India Meteorological Department).
"""),
    Section("safety", "Emergencies and safety", """
**Cloudzy is not an emergency or warning service.** It doesn't replace official weather warnings, government
alerts, or instructions from emergency services, and you shouldn't rely on it alone for decisions about:

- personal safety or the safety of others;
- travel, driving, flying, sailing or outdoor activity in hazardous conditions;
- severe weather such as storms, floods, heatwaves or cyclones;
- protecting property, crops, animals or equipment.

In an emergency, follow the instructions of the authorities and contact your local emergency services.
"""),
    Section("ml", "Cloudzy Core and machine-learning predictions", """
Cloudzy Core is an **experimental** model. Its predictions are **estimates, not guarantees**:

- **Limited scope:** it was trained on historical reanalysis data for a single location (a grid cell in New
  Delhi) and isn't a global or general-purpose forecast.
- **Past results don't promise future ones:** reported accuracy describes past test periods, and the model can
  do worse when the weather, its inputs or the data sources change.
- **Clearly labelled:** Cloudzy keeps its predictions separate from measured observations and from other
  providers' forecasts, and a prediction is never presented as a measurement or an official forecast.

The Model Card in the repository describes the model, its data and its known limitations.
"""),
    Section("availability", "Availability and changes", """
Cloudzy is provided as it is and as available. Features, data sources, models and these terms may change, and
any part of the app may be slow, interrupted or unavailable, for example when a data provider is down or
limits requests.

The project owner doesn't promise uninterrupted access, that any feature will continue, that errors will be
fixed, or that anything will be kept for any period. Settings kept in your browser can be lost if your browser
clears its storage.
"""),
    Section("ip", "Intellectual property", f"""
Different things in and around Cloudzy are owned and licensed in different ways:

- **Cloudzy's software:** the source code and other files in the Cloudzy repository are © 2026 {OWNER} and
  published under the [MIT licence]({REPOSITORY}/blob/main/LICENSE). You may use, copy, modify and distribute
  them under that licence's conditions, including keeping its copyright and permission notice. Nothing in these
  terms narrows the rights the MIT licence grants.
- **The Cloudzy name and branding:** the name "Cloudzy", the tagline "Weather with a little intelligence" and
  the Cloudzy logo identify this project. Please don't use them in a way that suggests the project owner made,
  endorses or is connected with something they aren't. The MIT licence doesn't, by itself, give permission to
  use the name or logo in that way.
- **Original written content:** the app's own text, explanations and documentation are the project owner's
  work, to the extent the project owner holds rights in them. Where they're part of the repository, they're
  also available under the MIT licence.
- **Third-party material:** weather and geographic data, imagery, fonts, map and 3D libraries and other
  components come from others. They keep their own licences and terms (listed under Third-party services), and
  the project owner doesn't claim ownership of them. Public weather data, such as NOAA's, isn't owned by Cloudzy.
""", review="Ownership of material created with AI assistance can be legally uncertain; the project owner should "
             "confirm which parts they hold rights in before relying on this section."),
    Section("acceptable-use", "Acceptable use", """
When using Cloudzy, please don't:

- use it for anything unlawful, or to harm, harass or deceive anyone;
- try to get unauthorised access to the app, the computer it runs on, its data, or another person's data or
  browser storage;
- interfere with how the app works, overload it, or get around its limits or security;
- use automated tools to make large numbers of requests, scrape data, or otherwise send through Cloudzy traffic
  that breaks the usage limits or terms of the services it relies on (see Third-party services);
- present Cloudzy's output, including Cloudzy Core predictions, as official warnings, or as measurements when
  they aren't;
- remove or hide credits, licence notices or attributions that the app shows.
"""),
    Section("third-party", "Third-party services and attribution", """
Cloudzy relies on other services and data, each with its own terms, limits and credit requirements. Using
Cloudzy doesn't give you any rights in their data beyond what their own terms allow.

| Provider | What Cloudzy uses it for | Terms and credit |
|---|---|---|
| Open-Meteo | forecasts, history, place search | Data CC BY 4.0. Its free API is for non-commercial use within published limits; commercial use needs Open-Meteo's own plan. |
| OpenStreetMap Nominatim | place names for coordinates | © OpenStreetMap contributors (ODbL); Nominatim usage policy (about 1 request per second). |
| GeoNames (via Open-Meteo) | place search results | CC BY 4.0. |
| NOAA GFS and GFS-Wave | Weather Explorer data | US government data, public domain; credit "NOAA/NCEP GFS". |
| NASA GIBS | globe imagery (Blue Marble, Black Marble) | Public domain; GIBS acknowledgment shown in the project. |
| Natural Earth | boundaries, coastlines, place names | Public domain. |
| Copernicus ERA5 / ERA5-Land | Cloudzy Core's training and history data | Copernicus licence; contains modified Copernicus Climate Change Service information. |
| Open-source software | the app itself (Streamlit, Leaflet, three.js, ecCodes, fonts and others) | Their own licences, such as Apache-2.0, BSD-2-Clause, MIT and SIL OFL. |

Credits and notices are shown in the app and in the project's documentation; they have to stay with the data
they belong to. If a provider changes its terms or stops its service, the matching Cloudzy feature may change
or stop too.
"""),
    Section("privacy", "Privacy", f"""
How Cloudzy handles information about you (for example your location, if you choose to share it, and your
settings) is to be described in a separate **Privacy Policy**, and that policy has to match what the app
actually does and the law that applies.

A Privacy Policy hasn't been published yet. Until it is, the project's documentation describes some of the app's current
behaviour: the [README's "Device location" section]({REPOSITORY}#device-location-use-my-location) explains how
a location you choose to share is used, and the Settings page explains where your settings are kept.
Using the services listed above means your requests reach them, under their own privacy terms.
""", review="Write and publish a Privacy Policy based on the app's actual behaviour and applicable law (India's "
             "Digital Personal Data Protection Act, 2023 and its Rules), then link it here."),
    Section("liability", "Limitation of liability", """
To the extent permitted by the law that applies to you:

- Cloudzy is provided "as is" and "as available", without warranties of any kind, express or implied,
  including about accuracy, reliability, fitness for a particular purpose or availability.
- The project owner isn't responsible for loss or damage arising from reliance on Cloudzy's weather
  information, alerts or predictions, or from the app being unavailable, including decisions made using it.

Nothing in these terms excludes or limits any liability, or any right you have, that can't be excluded or
limited under applicable law (for example under consumer-protection law). The MIT licence contains its own
warranty disclaimer for the software, which continues to apply to it.
"""),
    Section("suspension", "Suspension and discontinuation", """
Where it's appropriate and lawful, access to Cloudzy, or to particular features, may be limited or blocked to
deal with misuse, security problems, or excessive load on Cloudzy or the services it relies on. The project
owner may also change, pause or discontinue Cloudzy, in whole or in part.
"""),
    Section("updates", "Changes to these terms", f"""
These terms may be updated as Cloudzy changes. The date at the top shows when they last changed
(currently **{LAST_UPDATED}**). For a significant change, a notice will be shown in the app where that's
practical. The history of this page is also kept in the project's public repository.

Continuing to use Cloudzy after a change means using it under the updated terms.
"""),
    Section("law", "Governing law and disputes", """
Which country's (or state's) law governs these terms, and where disputes would be resolved, **hasn't been
decided yet**. This section will be completed after legal advice. Until then, nothing here limits any rights you
have under the law that applies to you.
""", review="Choose the governing law and dispute forum with a lawyer (Cloudzy is developed in India) and complete "
             "this section."),
    Section("contact", "Contact", f"""
A contact method for questions about these terms **hasn't been set up yet** and will be added before Cloudzy is
offered publicly. In the meantime, questions about the code can be raised on the project's
[GitHub repository]({REPOSITORY}).
""", review="Add a verified contact method (for example a dedicated email address) before public release."),
]


def as_markdown() -> str:
    """The whole text as one Markdown document (for review or export)."""
    parts = [f"# Cloudzy Terms of Use\n\nLast updated: {LAST_UPDATED}\n"]
    for i, s in enumerate(SECTIONS, 1):
        parts.append(f"## {i}. {s.title}\n{s.body.strip()}\n")
        if s.review:
            parts.append(f"> **Review note:** {s.review}\n")
    return "\n".join(parts)
