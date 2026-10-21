"""Terms of Use: the page, how it's reached, and what the text may and may not say."""

import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src import terms as TU

ROOT = Path(__file__).resolve().parents[1]
TEXT = " ".join(TU.as_markdown().split())          # wrapped lines joined, for phrase checks


def run_page():
    at = AppTest.from_file("../app_pages/terms.py", default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_page_shows_every_section_in_order_with_anchors_and_the_date():
    at = run_page()
    heads = [h.value for h in at.subheader]
    assert heads == [f"{i}. {s.title}" for i, s in enumerate(TU.SECTIONS, 1)]
    assert len(TU.SECTIONS) == 15
    assert len({s.anchor for s in TU.SECTIONS}) == 15
    assert any(f"Last updated: {TU.LAST_UPDATED}" in m.value for m in at.markdown)
    contents = next(e for e in at.expander if e.label == "Contents")
    links = contents.markdown[0].value
    assert all(f"(#{s.anchor})" in links for s in TU.SECTIONS)


def test_no_draft_box_and_no_fake_consent():
    at = run_page()
    assert not at.warning                                           # the draft-for-review box was removed
    assert not at.button and not at.checkbox                       # nothing to click to "accept"


def test_reachable_from_the_sidebar_footer_settings_and_its_own_url():
    app = (ROOT / "streamlit_app.py").read_text()
    assert 'st.Page("app_pages/terms.py"' in app and 'url_path="terms"' in app
    assert 'st.sidebar.page_link("app_pages/terms.py"' in app
    assert 'nav.page_link("app_pages/terms.py"' in (ROOT / "app_pages" / "settings.py").read_text()


def test_the_text_keeps_the_mit_licence_and_third_party_terms_intact():
    lic = (ROOT / "LICENSE").read_text()
    assert lic.startswith("MIT License") and f"Copyright (c) 2026 {TU.OWNER}" in lic   # licence unchanged
    assert "MIT licence" in TEXT and "Nothing in these terms narrows the rights the MIT licence grants" in TEXT
    for provider in ("Open-Meteo", "Nominatim", "NOAA", "NASA GIBS", "Natural Earth", "Copernicus"):
        assert provider in TEXT
    assert "non-commercial" in TEXT                                    # Open-Meteo's free API limit


def test_the_text_makes_no_unsupported_promises_or_invented_facts():
    low = TEXT.lower()
    assert "not an emergency or warning service" in low and "estimates, not guarantees" in low
    assert "to the extent permitted by the law" in low and "can't be excluded" in low
    for overclaim in ("we never collect", "guaranteed", "uninterrupted service", "indemnif", "waive all",
                      "exclusive owner", "all rights reserved", "registered office", "pvt. ltd", "llp"):
        assert overclaim not in low, overclaim
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", TEXT)            # no invented email address
    law = next(s for s in TU.SECTIONS if s.anchor == "law")
    assert "hasn't been" in " ".join(law.body.split()) and law.review                    # governing law is flagged, not invented
    contact = next(s for s in TU.SECTIONS if s.anchor == "contact")
    assert "hasn't been set up" in " ".join(contact.body.split()) and contact.review


def test_legal_review_notes_exist():
    notes = (ROOT / "docs" / "LEGAL_REVIEW.md").read_text()
    assert "Digital Personal Data Protection Act, 2023" in notes and "Consumer Protection Act, 2019" in notes


def test_feature_descriptions_match_the_app():
    weather = (ROOT / "app_pages" / "weather.py").read_text()
    hours = re.search(r"^HOURS_AHEAD = (\d+)", weather, re.M).group(1)
    assert f"{hours}-hour and 7-day forecasts" in TEXT                       # the Weather page's real span
    low = TEXT.lower()
    assert "later measured" not in low and "not a station measurement" in low  # tracking uses reanalysis
    assert "for the place you're viewing" in TEXT                             # alerts aren't per saved place
