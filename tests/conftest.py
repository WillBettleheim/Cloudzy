"""Test-wide safety: never read or write the user's real preferences or monitoring logs."""

import pytest


@pytest.fixture(autouse=True)
def isolated_user_data(monkeypatch, tmp_path_factory):
    base = tmp_path_factory.mktemp("user_data")
    monkeypatch.setenv("CLOUDZY_PREFS_PATH", str(base / "preferences.json"))
    monkeypatch.setenv("CLOUDZY_MONITORING_DIR", str(base / "monitoring"))
    monkeypatch.setenv("CLOUDZY_SNAPSHOTS", "0")
    monkeypatch.setenv("CLOUDZY_EXPLORER_DIR", str(base / "explorer"))
    monkeypatch.setenv("CLOUDZY_EXPLORER_REFRESH", "0")   # never download GFS data in tests


@pytest.fixture(autouse=True)
def no_live_air_quality(request, monkeypatch):
    """Offline tests never call the Air Quality API; tests that need data fake it (tests.test_app.fake_network)."""
    if request.node.get_closest_marker("integration"):
        return
    import src.air_quality

    def blocked(place, session=None):
        raise src.air_quality.AirQualityError("network access is disabled in offline tests")
    monkeypatch.setattr(src.air_quality, "fetch_air_quality", blocked)
