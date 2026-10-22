"""Station observations (src.stations) and the station evaluation (src.station_eval): parsing, hourly matching,
quality control, alignment, and the committed report's consistency."""

import json

import numpy as np
import pandas as pd
import pytest

from src import station_eval as SE
from src import station_model as SM
from src import stations as ST


def iem_csv(rows):
    return "station,valid,tmpc\n" + "\n".join(f"VIDP,{t},{v}" for t, v in rows)


def test_parse_reads_utc_times_and_missing_values():
    df = ST.parse_metar(iem_csv([("2026-08-01 00:00", "29.00"), ("2026-08-01 00:30", "M")]))
    assert str(df["valid"].dt.tz) == "UTC" and df["tmpc"].iloc[0] == 29.0 and np.isnan(df["tmpc"].iloc[1])
    with pytest.raises(ValueError, match="columns"):
        ST.parse_metar("station,valid,temp\nVIDP,2026-08-01 00:00,29\n")


def test_hourly_takes_the_report_at_the_hour_never_interpolates():
    rows = [("2026-08-01 00:00", 29), ("2026-08-01 00:30", 30),     # :30 report ignored
            ("2026-08-01 01:07", 31),                               # nearest within 10 min → 01:00
            ("2026-08-01 02:20", 33),                               # too far from any hour → no value
            ("2026-08-01 03:00", 32)]
    s, counts = ST.hourly(ST.parse_metar(iem_csv(rows)))
    assert list(s.index.strftime("%H:%M")) == ["00:00", "01:00", "03:00"]
    assert list(s) == [29.0, 31.0, 32.0] and counts["hours_kept"] == 3


def test_hourly_drops_implausible_values_and_spikes():
    times = pd.date_range("2026-08-01", periods=6, freq="h").strftime("%Y-%m-%d %H:%M")
    s, counts = ST.hourly(ST.parse_metar(iem_csv(zip(times, [30, 31, 45, 31, 60, 30]))))
    assert counts["dropped_implausible"] == 1 and counts["dropped_spikes"] == 1
    assert 45.0 not in set(s) and 60.0 not in set(s) and len(s) == 4


def test_primary_station_and_distances():
    assert ST.PRIMARY == "VIDP"
    assert 10 < ST.distance_km(ST.STATIONS["VIDP"].lat, ST.STATIONS["VIDP"].lon, 28.6139, 77.209) < 11
    assert 3 < ST.distance_km(ST.STATIONS["VIDD"].lat, ST.STATIONS["VIDD"].lon, 28.6139, 77.209) < 4


def test_station_rows_align_the_target_hour_and_require_both_readings():
    idx = pd.date_range("2026-01-01", periods=10, freq="h", tz="UTC")
    rows = pd.DataFrame({"x": range(10)}, index=idx)
    obs = pd.Series(np.arange(100, 110, dtype=float), index=idx).drop(idx[5])
    kept, y, at_t = SE.station_rows(rows, 3, obs)
    # target = issue + 3 h; issue 2 (target 5, missing) and issues 7-9 (targets past the data) drop out
    assert list(kept["x"]) == [0, 1, 3, 4, 6]
    assert list(y) == [103, 104, 106, 107, 109] and list(at_t) == [100, 101, 103, 104, 106]


def test_answers_report_significance_honestly():
    def h(d, lo, hi):
        return {"test": {"VIDP": {"candidate_minus_ifs_at_target": {"mae_diff_c": d, "ci95_low_c": lo, "ci95_high_c": hi},
                                  "candidate_minus_control": {"mae_diff_c": d, "ci95_low_c": lo, "ci95_high_c": hi}}}}
    rep = {"primary": "VIDP", "horizons": {1: h(0.2, 0.1, 0.3), 2: h(-0.1, -0.2, -0.01), 3: h(0.0, -0.1, 0.1)}}
    a = SE.answers(rep)
    assert a["1"]["q1_vs_ifs"] == "further" and a["2"]["q1_vs_ifs"] == "closer"
    assert a["3"]["q2_vs_previous"] == "no significant difference"


def test_committed_station_report_is_consistent():
    rep = json.loads(SE.REPORT_PATH.read_text())
    assert rep["primary"] == "VIDP" and rep["answers"] == SE.answers(rep)
    for r in rep["horizons"].values():
        p = r["test"]["VIDP"]
        assert p["candidate"]["n"] == p["ifs_at_target"]["n"] == p["reanalysis"]["n"] > 5000   # same rows
        d = p["candidate_minus_ifs_at_target"]
        assert d["ci95_low_c"] <= d["mae_diff_c"] <= d["ci95_high_c"]
    qc = rep["quality_control"]["VIDP"]
    assert qc["hours_kept"] > 20000 and qc["distance_km_from_grid_point"] < 11


# --- Station-target experiment (src.station_model) ---------------------------------------------------------

def test_station_features_use_only_readings_at_or_before_t_and_target_at_t_plus_h():
    idx = pd.date_range("2026-01-01", periods=48, freq="h", tz="UTC")
    obs = pd.Series(np.arange(48, dtype=float), index=idx)          # reading = hours since start
    live = pd.DataFrame({"temperature_2m": np.full(48, 10.0)}, index=idx)
    table = pd.DataFrame({"temperature_2m": np.full(48, 10.0)}, index=idx)
    out = SM.station_table(table, obs, live, 6)
    row = out.loc[idx[30]]
    assert row[SM.STATION_TARGET] == 36 and row["stn_now"] == 30 and row["stn_change_3h"] == 3
    assert row["stn_minus_ifs_now"] == 20 and row["stn_minus_ifs_24h_ago"] == -4
    assert np.isnan(out.loc[idx[0], "stn_minus_ifs_24h_ago"])           # missing stays missing
    assert out.index.max() == idx[41]                                     # targets past the data are dropped
    for cols in SM.CANDIDATES.values():
        assert SM.STATION_TARGET not in cols and "ifs_at_target" not in cols


def test_hourly_offset_is_by_local_hour_of_the_target():
    idx = pd.date_range("2026-01-01", periods=48, freq="h", tz="UTC")
    rows = pd.DataFrame({SM.STATION_TARGET: 21.0, "ifs_at_target": 20.0}, index=idx)
    off = SM.hourly_offset(rows, 1)
    assert len(off) == 24 and (off == 1.0).all()


def test_decision_rule_requires_every_horizon_and_significance():
    def res(a, raw, hi_raw, hi_bc, sept_a=1.0, sept_raw=1.0):
        t = {"candidate_A": {"mae_c": a}, "candidate_B": {"mae_c": a}, "raw_ecmwf": {"mae_c": raw}}
        for n in ("A", "B"):
            t[f"candidate_{n}_minus_raw_ecmwf"] = {"ci95_high_c": hi_raw}
            t[f"candidate_{n}_minus_bias_corrected_ecmwf"] = {"ci95_high_c": hi_bc}
        s = {"candidate_A": {"mae_c": sept_a}, "candidate_B": {"mae_c": sept_a}, "raw_ecmwf": {"mae_c": sept_raw}}
        return {"test": t, "september": s}
    good = {h: res(1.0, 1.2, -0.05, -0.05) for h in (1, 2, 3, 6, 12, 24)}
    assert SM.decide(good)["A"]["verdict"] == "beats ECMWF and the simple correction"
    assert SM.decide(good | {24: res(1.3, 1.2, 0.1, 0.1)})["A"]["verdict"] == "does not beat ECMWF"
    only_raw = {h: res(1.0, 1.2, -0.05, 0.01) for h in good}
    assert SM.decide(only_raw)["B"]["verdict"].startswith("beats ECMWF, but not more")
    assert SM.decide(good | {6: res(1.0, 1.2, -0.05, -0.05, sept_a=1.1)})["A"]["h3_september_ok"] is False


def test_committed_station_model_report_matches_its_rules():
    rep = json.loads(SM.REPORT_PATH.read_text())
    assert rep["decision"] == SM.decide({int(h): r for h, r in rep["horizons"].items()})
    for r in rep["horizons"].values():
        t = r["test"]
        assert t["candidate_A"]["n"] == t["candidate_B"]["n"] == t["raw_ecmwf"]["n"] > 5000
