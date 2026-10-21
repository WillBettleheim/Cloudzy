# Legal review before public release

Cloudzy's Terms of Use (`src/terms.py`, shown at `/terms`) are a carefully worded draft, not legal advice. Having
a Terms page doesn't make Cloudzy compliant with anything. These items need a decision, or a qualified lawyer's
review, before Cloudzy is offered to the public.

## Open items in the Terms

| Item | Where | What's needed |
|---|---|---|
| Contact method | Terms §15 | A real, monitored contact (for example a dedicated email). None exists yet. |
| Governing law and disputes | Terms §14 | Choose the law and forum with a lawyer. Cloudzy is developed in India. |
| Privacy Policy | Terms §10 | A separate task: write it from the app's actual behaviour, then link it. |
| Ownership of AI-assisted material | Terms §7 | Confirm which parts the project owner holds rights in. |
| Name and logo | Terms §7 | "Cloudzy" isn't known to be a registered trade mark; check availability before relying on it. |

## Indian law to check

Check the current text and status of each: some were recently made or are being phased in.

- **Digital Personal Data Protection Act, 2023 and the DPDP Rules:** notice, consent, purpose limitation and
  data-principal rights for personal data. Location from the device and anything stored on the server count if
  they're personal data. The Privacy Policy should be built around this.
- **Information Technology Act, 2000:** including the Reasonable Security Practices Rules, 2011 (in force until
  replaced by the DPDP regime), and the Intermediary Guidelines, 2021 (only if Cloudzy ever hosts content from
  users).
- **Consumer Protection Act, 2019:** unfair terms and misleading claims, especially if Cloudzy is ever charged
  for or advertised. Also the E-Commerce Rules, 2020, if it's ever sold online.
- **Indian Contract Act, 1872:** whether terms that are read but not clicked ("browse-wrap") bind users. Cloudzy
  deliberately has no "accept" button; a lawyer should confirm what's appropriate.
- **Maps of India:** Cloudzy shows India's borders in India's official point of view (Natural Earth "IND"
  worldview). Confirm this meets current guidance on depicting India's boundaries (Survey of India; the
  geospatial guidelines).

## Obligations from third parties (keep in place)

- **Open-Meteo:** data CC BY 4.0 with attribution. The free API is for non-commercial use within its limits;
  commercial use needs one of Open-Meteo's own plans.
- **OpenStreetMap Nominatim:** attribution (ODbL) and the usage policy (light use, about 1 request per second,
  caching).
- **Copernicus ERA5:** attribution and the Copernicus licence.
- **NASA GIBS:** the acknowledgment, kept in `static/vendor/earth/README.md` and the README.
- **Open-source components:** their licence notices: Streamlit (Apache-2.0), Leaflet (BSD-2-Clause), three.js
  (MIT), ecCodes (Apache-2.0), fonts (SIL OFL) and others in `requirements.txt` and `static/vendor/`.
- **The project's own MIT licence:** keep the copyright and permission notice in copies; the Terms must not
  narrow it.
