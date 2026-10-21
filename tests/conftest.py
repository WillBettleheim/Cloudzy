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
    monkeypatch.setenv("CLOUDZY_EARTH_DIR", str(base / "earth"))   # hazard static files go to a temp folder


@pytest.fixture(autouse=True)
def no_live_air_quality(request, monkeypatch):
    """Offline tests never call the Air Quality API; tests that need data fake it (tests.test_app.fake_network)."""
    if request.node.get_closest_marker("integration"):
        return
    import src.air_quality

    def blocked(place, session=None):
        raise src.air_quality.AirQualityError("network access is disabled in offline tests")
    monkeypatch.setattr(src.air_quality, "fetch_air_quality", blocked)


@pytest.fixture(autouse=True)
def no_live_hazard_feeds(request, monkeypatch):
    """Offline tests never call disaster-data providers, and each test starts with an empty hazard store."""
    import src.hazards.store
    monkeypatch.setattr(src.hazards.store, "_STORE", None)
    if request.node.get_closest_marker("integration"):
        return
    import src.earth_search
    import src.hazards.http

    def blocked(url, params=None, **kw):
        raise src.hazards.http.FeedError("network access is disabled in offline tests")
    monkeypatch.setattr(src.hazards.http, "get", blocked)
    monkeypatch.setattr(src.earth_search, "nominatim", lambda q, session=None: [])
