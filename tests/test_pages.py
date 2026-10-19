"""Page-level tests for Settings, Predictions and the navigation entry point (Streamlit AppTest)."""

import json

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from src import monitoring
from src import preferences as P
from src.provider import Place

PARIS = Place("Paris", 48.8534, 2.3488, "Europe/Paris", "France", "Île-de-France", 2138551, "FR")


def run(page):
    st.cache_data.clear()
    at = AppTest.from_file(f"../app_pages/{page}.py", default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_settings_empty_state_and_device_note():
    at = run("settings")
    assert any("Saved on this device only" in c.value for c in at.caption)
    assert any("No favourite places yet" in i.value for i in at.info)
    assert any("no background or push" in c.value for c in at.caption)


def test_settings_save_style_categories_and_home(tmp_path):
    prefs = P.load()
    prefs.toggle_favourite(PARIS)
    P.save(prefs)
    at = run("settings")
    at.radio[0].set_value("brief").run()
    at.checkbox(key="cat_uv").uncheck().run()
    at.selectbox[0].set_value(PARIS.label).run()
    assert not at.exception
    saved = P.load()
    assert saved.summary_style == "brief" and "uv" not in saved.enabled_alert_kinds()
    assert saved.home_place() == PARIS
    raw = json.loads(P.prefs_path().read_text())
    assert set(raw) >= {"summary_style", "alert_categories", "home"}


def test_settings_remove_favourite_clears_home():
    prefs = P.load()
    prefs.toggle_favourite(PARIS)
    prefs.home = {k: getattr(PARIS, k) for k in PARIS.__dataclass_fields__}
    P.save(prefs)
    at = run("settings")
    next(b for b in at.button if b.label == "Remove").click().run()
    assert P.load().favourites == [] and P.load().home is None


def test_predictions_page_empty_state():
    at = run("predictions")
    assert any("No predictions recorded yet" in i.value for i in at.info)


def test_predictions_page_with_records_never_claims_accuracy_without_evaluations():
    from tests.test_monitoring import result
    monitoring.record_prediction(result())
    at = run("predictions")
    assert [m.value for m in at.metric][:3] == ["2", "0", "2"]
    assert any("No evaluated predictions in this selection yet" in i.value for i in at.info)
    assert len(at.dataframe) >= 1


def test_navigation_lists_every_page():
    src = open("streamlit_app.py").read()
    for page in ("weather", "history", "predictions", "lab", "settings"):
        assert f"app_pages/{page}.py" in src


def test_figure_table_lists_drawn_values_and_skips_reference_lines():
    import plotly.graph_objects as go
    from src.ui import figure_table
    fig = go.Figure([go.Bar(x=["a", "b"], y=[1.0, 2.0], name="MAE"),
                     go.Scatter(x=[0, 1], y=[0, 1], name="Perfect", hoverinfo="skip")])
    df, cut = figure_table(fig)
    assert list(df["series"]) == ["MAE", "MAE"] and list(df["value"]) == [1.0, 2.0] and not cut
    assert figure_table(go.Figure()) is None


def test_lab_shows_deployed_model_version():
    at = run("lab")
    assert any("Model in the live app" in i.value and "@" in i.value for i in at.info)
    assert any(e.label.startswith("Chart data as a table") for e in at.expander)
