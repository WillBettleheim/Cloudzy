"""Historical seismicity analysis: catalogue parsing, completeness, b-value recovery on synthetic data."""

import numpy as np
import pandas as pd
import pytest

from src.hazards import http
from src.hazards import seismicity as S

NOW = pd.Timestamp("2026-10-22", tz="UTC")


def synthetic(b=1.0, mc=4.0, n=3000, seed=3):
    rng = np.random.default_rng(seed)
    mags = np.round(mc - 0.05 + rng.exponential(1 / (b * np.log(10)), n), 1)
    times = pd.date_range("1980-01-01", periods=n, freq="5D", tz="UTC")
    return pd.DataFrame({"time": times, "lat": 35.0, "lon": 139.0, "depth_km": 10.0, "mag": mags, "mag_type": "mw"})


def test_b_value_recovers_a_known_gutenberg_richter_slope():
    r = S.b_value(synthetic(b=1.0)["mag"].to_numpy(), 4.0)
    assert r["b"] == pytest.approx(1.0, abs=0.06) and r["ci90"][0] < r["b"] < r["ci90"][1]
    assert S.b_value(np.full(10, 5.0), 4.0) is None                       # too few events: no estimate


def test_summary_counts_rates_and_says_it_is_not_a_prediction():
    out = S.summarise(synthetic(), NOW, start="1980-01-01")
    assert out["thresholds"][0]["min_mag"] == 5.0 and out["thresholds"][0]["count"] > 0
    assert out["mc"] is not None and "not a prediction" in out["note"]
    assert S.summarise(synthetic().iloc[0:0], NOW)["n"] == 0


def test_parse_catalogue_text_and_reject_garbage():
    text = ("#EventID|Time|Latitude|Longitude|Depth/km|Author|Catalog|Contributor|ContributorID|MagType|Magnitude|"
            "MagAuthor|EventLocationName\n"
            "a|2011-03-11T05:46:24.120|38.3|142.4|29|us|us|us|a|mww|9.1|us|Tohoku\n"
            "b|bad-time|38.3|142.4|29|us|us|us|b|mb|5.0|us|x\n")
    cat = S.parse_catalogue(text)
    assert len(cat) == 1 and cat.iloc[0]["mag"] == 9.1 and cat.iloc[0]["mag_type"] == "mww"
    assert S.parse_catalogue("").empty
    with pytest.raises(http.FeedError):
        S.parse_catalogue("<html>error</html>")
