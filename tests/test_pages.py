"""Page-level tests for Settings, Places, Predictions and the navigation entry point (Streamlit AppTest)."""

import json

import streamlit as st
from streamlit.testing.v1 import AppTest

from src import monitoring
from src import preferences as P
from src.provider import Place
from tests.test_app import fake_network  # noqa: F401  (fixture)

PARIS = Place("Paris", 48.8534, 2.3488, "Europe/Paris", "France", "Île-de-France", 2138551, "FR")


def run(page):
    st.cache_data.clear()
    at = AppTest.from_file(f"../app_pages/{page}.py", default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_settings_empty_state_and_device_note():
    at = run("settings")
    assert any("settings file on the computer running Cloudzy" in c.value for c in at.caption)  # file mode in tests
    assert any("alert categories on" in c.value for c in at.caption)
    assert not at.checkbox                       # alert categories live on the Alerts page now, not duplicated


def test_settings_saves_summary_style_and_location_choice():
    at = run("settings")
    at.radio[0].set_value("brief").run()
    next(t for t in at.toggle if t.label.startswith("Detect my location")).set_value(True).run()
    assert not at.exception
    saved = P.load()
    assert saved.summary_style == "brief" and saved.auto_locate is True
    raw = json.loads(P.prefs_path().read_text())
    assert set(raw) >= {"summary_style", "auto_locate"} and "latitude" not in json.dumps(raw)


# --- Places -----------------------------------------------------------------------------------------

def test_places_empty_state(fake_network):  # noqa: F811
    at = run("places")
    assert any("No saved places yet" in i.value for i in at.info)


def test_places_home_city_and_removal(fake_network):  # noqa: F811
    prefs = P.load()
    prefs.toggle_favourite(PARIS)
    P.save(prefs)
    at = run("places")
    assert "Paris" in " ".join(h.proto.body for h in at.html)
    next(b for b in at.button if b.label == "Make home").click().run()
    assert P.load().home_place() == PARIS
    assert any(b.label == "Unset home" for b in at.button)
    next(b for b in at.button if b.label == "Remove").click().run()
    assert P.load().favourites == [] and P.load().home is None      # removing the home city clears it


def test_places_open_hands_the_place_to_the_weather_page(fake_network):  # noqa: F811
    prefs = P.load()
    prefs.toggle_favourite(PARIS)
    P.save(prefs)
    at = run("places")
    next(b for b in at.button if b.label == "Open").click().run()
    assert not at.exception
    assert at.session_state["_open_place"] == PARIS                # the Weather page opens it next


def test_predictions_page_empty_state():
    at = run("predictions")
    assert any("No predictions recorded yet" in i.value for i in at.info)


def test_predictions_page_with_records_never_claims_accuracy_without_evaluations():
    from tests.test_monitoring import result
    monitoring.record_prediction(result())
    at = run("predictions")
    # Targets on 1-2 Oct 2026: long past and past the publication delay, so due rather than waiting.
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Records"] == "2" and metrics["Evaluated"] == "0"
    assert metrics["Waiting (normal)"] == "0" and metrics["Due or needs attention"] == "2"
    assert any("No evaluated predictions in this selection yet" in i.value for i in at.info)
    assert len(at.dataframe) >= 1


def test_navigation_lists_every_page():
    with open("streamlit_app.py") as f:
        src = f.read()
    for page in ("weather", "explorer", "history", "alerts", "predictions", "lab", "places", "settings", "terms"):
        assert f"app_pages/{page}.py" in src


def test_figure_table_lists_drawn_values_and_skips_reference_lines():
    import plotly.graph_objects as go

    from src.ui import figure_table
    fig = go.Figure([go.Bar(x=["a", "b"], y=[1.0, 2.0], name="MAE"),
                     go.Scatter(x=[0, 1], y=[0, 1], name="Perfect", hoverinfo="skip")])
    df, cut = figure_table(fig)
    assert list(df["series"]) == ["MAE", "MAE"] and list(df["value"]) == [1.0, 2.0] and not cut
    assert figure_table(go.Figure()) is None


def test_figure_table_counts_histogram_bins_exactly_as_drawn():
    import plotly.graph_objects as go

    from src.ui import figure_table
    errors = [-0.1, -0.04, 0.0, 0.04, 0.05, 0.12, 9.9]      # 9.9 is outside the bins, so it isn't drawn
    fig = go.Figure(go.Histogram(x=errors, xbins={"start": -0.15, "end": 0.15, "size": 0.1}, name="Cloudzy"))
    df, _ = figure_table(fig)
    assert list(df["x"]) == [-0.1, 0.0, 0.1] and list(df["value"]) == [1, 3, 2]
    assert figure_table(go.Figure(go.Histogram(x=errors))) is None   # automatic bins can't be reproduced


def test_lab_error_histogram_has_a_table():
    import pandas as pd

    from src import lab_ui
    from src.ui import figure_table
    df = pd.DataFrame({"observed": [20.0, 21.0, 22.0], "persistence": [20.5, 21.0, 21.0],
                       lab_ui.CLOUDZY: [20.1, 21.0, 22.0]})
    fig = lab_ui.error_histogram(df, "persistence", lab_ui.lab_palette())
    table, _ = figure_table(fig)
    assert table.groupby("series")["value"].sum().tolist() == [3, 3]    # every hour counted once per model


def test_lab_shows_deployed_model_version():
    at = run("lab")
    assert any("Model in the live app" in i.value and "@" in i.value for i in at.info)
    assert any(e.label.startswith("Chart data as a table") for e in at.expander)


def test_predictions_page_keeps_model_versions_separate():
    from datetime import date

    from tests.test_monitoring import Session, archive_payload, result
    monitoring.record_prediction(result())                                   # version ...abc123def456
    monitoring.record_prediction(result(version="cloudzy_core_delhi_serving_compact@999999999999"))
    monitoring.evaluate(date(2026, 10, 19), session=Session(archive_payload(temp=lambda i: 30.5)))
    at = run("predictions")
    headings = [m.value for m in at.markdown if m.value.startswith("##### Model")]
    assert len(headings) == 2 and all("evaluated" not in h for h in headings)
    assert sum("**2 evaluated predictions**" in m.value for m in at.markdown) == 2   # 2 each, never 4 pooled
    assert any("Last check" in c.value for c in at.caption)


def test_places_cards_show_real_current_weather(fake_network):  # noqa: F811
    prefs = P.load()
    prefs.toggle_favourite(PARIS)
    P.save(prefs)
    at = run("places")
    html = " ".join(h.proto.body for h in at.html)
    assert 'class="cz-hero cz-mini atm-' in html and "28°" in html          # temperature from the forecast payload


def test_places_card_without_weather_says_so(fake_network):  # noqa: F811
    from src.provider import ProviderError
    fake_network["forecast_error"] = ProviderError("timed out")
    prefs = P.load()
    prefs.toggle_favourite(PARIS)
    P.save(prefs)
    at = run("places")
    assert "Current weather isn't available right now." in " ".join(h.proto.body for h in at.html)
