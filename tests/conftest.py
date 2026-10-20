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
