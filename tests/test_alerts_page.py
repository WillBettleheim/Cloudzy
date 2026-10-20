"""The Alerts page: active and dismissed alerts, categories, thresholds and notifications."""

from streamlit.testing.v1 import AppTest

from src import preferences as P
from tests.test_app import fake_network  # noqa: F401  (fixture)


def run_alerts(at=None):
    if at is None:
        at = AppTest.from_file("../app_pages/alerts.py", default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def body(at):
    return " ".join(h.proto.body for h in at.html)


def test_alerts_page_says_alerts_are_unofficial_and_shows_none_on_a_calm_day(fake_network):  # noqa: F811
    at = run_alerts()
    assert any("not official warnings" in i.value for i in at.info)
    assert any("No alerts for the next 24 hours" in s.value for s in at.success)


def test_hot_day_alert_can_be_dismissed_and_shown_again(fake_network):  # noqa: F811
    p = fake_network["payload"]
    p["hourly"]["apparent_temperature"] = [44.0] * len(p["hourly"]["time"])
    at = run_alerts()
    assert "Very hot" in body(at) and "Active" in body(at)
    next(b for b in at.button if b.label == "Dismiss for 24 hours").click().run()
    assert len(P.load().dismissed_alerts) == 1
    assert any(e.label.startswith("Dismissed: Very hot") for e in at.expander)
    next(b for b in at.button if b.label == "Show again").click().run()
    assert P.load().dismissed_alerts == {}


def test_alert_thresholds_form_saves(fake_network):  # noqa: F811
    at = run_alerts()
    at.number_input[0].set_value(80)  # rain chance threshold
    next(b for b in at.button if b.label == "Save thresholds").click().run()
    assert not at.exception
    assert P.load().alerts.rain_probability_pct == 80


def test_categories_and_notifications_are_saved(fake_network):  # noqa: F811
    at = run_alerts()
    at.checkbox(key="cat_uv").uncheck().run()
    assert "uv" not in P.load().enabled_alert_kinds()
    next(t for t in at.toggle if t.label.startswith("Offer browser notifications")).set_value(True).run()
    assert P.load().notifications is True


def test_forecast_failure_is_explained(fake_network):  # noqa: F811
    from src.provider import ProviderError
    fake_network["forecast_error"] = ProviderError("timed out")
    at = run_alerts()
    assert any("alerts can't be checked" in e.value for e in at.error)
    assert any(c.label == "Chance of rain at least (%)" for c in at.number_input)   # settings still usable
