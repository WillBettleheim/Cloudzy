"""Weather Explorer data pipeline: GFS run discovery, index parsing, GRIB2 decoding, encoding, frames.

The network is faked, but the GRIB2 messages are real: they are built with ecCodes, so the actual
decoding path runs.
"""

from datetime import UTC, datetime

import numpy as np
import pytest

from src import explorer_data as E

RUN = datetime(2026, 10, 20, 0, tzinfo=UTC)


def grib(values: np.ndarray, ni: int = 360, nj: int = 181, lat0: float = 90.0) -> bytes:
    import eccodes
    h = eccodes.codes_grib_new_from_samples("GRIB2")
    for k, v in [("Ni", ni), ("Nj", nj), ("latitudeOfFirstGridPointInDegrees", lat0),
                 ("longitudeOfFirstGridPointInDegrees", 0.0), ("latitudeOfLastGridPointInDegrees", lat0 - nj + 1),
                 ("longitudeOfLastGridPointInDegrees", float(ni - 1)), ("iDirectionIncrementInDegrees", 1.0),
                 ("jDirectionIncrementInDegrees", 1.0), ("jScansPositively", 0)]:
        eccodes.codes_set(h, k, v)
    eccodes.codes_set_values(h, values.ravel())
    msg = eccodes.codes_get_message(h)
    eccodes.codes_release(h)
    return msg


def field(fill) -> np.ndarray:
    lat = np.linspace(90, -90, 181)[:, None]
    lon = np.arange(360)[None, :]
    return np.broadcast_to(fill(lat, lon), (181, 360)).astype(float)


# Physical values per GRIB record (GRIB units: K, Pa, kg m-2 s-1, flags, m/s, %, J/kg).
RECORDS = {
    ("UGRD", "10 m above ground"): field(lambda la, lo: 5.0 + 0 * lo),
    ("VGRD", "10 m above ground"): field(lambda la, lo: -2.0 + 0 * lo),
    ("TMP", "2 m above ground"): field(lambda la, lo: 273.15 + 30 - np.abs(la) * 0.5 + 0 * lo),
    ("PRMSL", "mean sea level"): field(lambda la, lo: 101325.0 + 0 * la + 0 * lo),
    ("TCDC", "entire atmosphere"): field(lambda la, lo: 40.0 + 0 * la + 0 * lo),
    ("PRATE", "surface"): field(lambda la, lo: np.where(la > 0, 1 / 3600, 0.0) + 0 * lo),
    ("CRAIN", "surface"): field(lambda la, lo: np.where(la > 0, 1.0, 0.0) + 0 * lo),
    ("CSNOW", "surface"): field(lambda la, lo: np.where(la > 60, 1.0, 0.0) + 0 * lo),
    ("CFRZR", "surface"): field(lambda la, lo: 0 * la + 0 * lo),
    ("CICEP", "surface"): field(lambda la, lo: 0 * la + 0 * lo),
    ("GUST", "surface"): field(lambda la, lo: 12.0 + 0 * la + 0 * lo),
    ("RH", "2 m above ground"): field(lambda la, lo: 55.0 + 0 * la + 0 * lo),
    ("CAPE", "surface"): field(lambda la, lo: 100.0 + 0 * la + 0 * lo),
}


def build_file(fhr: int, skip=(), extra_avg=True) -> tuple[bytes, str]:
    """A fake GRIB2 file (concatenated messages) and its .idx, like NOAA's."""
    step = "anl" if fhr == 0 else f"{fhr} hour fcst"
    blob, lines, n = b"", [], 1
    for (name, level), values in RECORDS.items():
        if name in skip:
            continue
        if extra_avg and name == "PRATE" and fhr:           # an average record that must be ignored
            msg = grib(values * 0 + 99 / 3600)
            lines.append(f"{n}:{len(blob)}:d=2026102000:{name}:{level}:{fhr - 3}-{fhr} hour ave fcst:")
            blob += msg
            n += 1
        msg = grib(values)
        lines.append(f"{n}:{len(blob)}:d=2026102000:{name}:{level}:{step}:")
        blob += msg
        n += 1
    return blob, "\n".join(lines) + "\n"


class FakeS3:
    """Serves fake GFS files by URL, honouring byte ranges; records every request."""

    def __init__(self, files: dict[str, tuple[bytes, str]], fail=False):
        self.files, self.fail, self.calls = files, fail, []

    def _resp(self, status, content=b"", text=""):
        class R:
            pass
        r = R()
        r.status_code, r.content, r.text = status, content, text
        return r

    def head(self, url, timeout=None):
        self.calls.append(("HEAD", url))
        return self._resp(200 if url.removesuffix(".idx") in self.files and url.endswith(".idx") else 404)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, (headers or {}).get("Range")))
        if self.fail:
            import requests
            raise requests.ConnectionError("offline")
        if url.endswith(".idx"):
            key = url.removesuffix(".idx")
            return self._resp(200, text=self.files[key][1]) if key in self.files else self._resp(404)
        blob = self.files[url][0]
        rng = headers["Range"].removeprefix("bytes=").split("-")
        start, end = int(rng[0]), (int(rng[1]) if rng[1] else len(blob) - 1)
        return self._resp(206, content=blob[start:end + 1])


def s3_for(fhours, **kw) -> FakeS3:
    return FakeS3({E.run_url(RUN, h): build_file(h, **kw) for h in fhours})


# --- Index, decoding, encoding ---------------------------------------------------------------------

def test_index_uses_only_instantaneous_records():
    _, idx = build_file(24)
    ranges = E.parse_index(idx, 24)
    assert set(ranges) == set(E.FIELDS)
    avg_line = next(ln for ln in idx.splitlines() if "ave fcst" in ln)
    assert ranges["prate"][0] != int(avg_line.split(":")[1])          # the average was skipped
    assert E.parse_index(build_file(0)[1], 0)["t2m"][0] >= 0           # analysis step is "anl"


def test_decode_checks_the_grid():
    out = E.decode(grib(field(lambda la, lo: la + 0 * lo)))
    assert out.shape == (181, 360) and out[0, 0] == pytest.approx(90) and out[-1, 0] == pytest.approx(-90)
    with pytest.raises(E.ExplorerDataError, match="Unexpected grid"):
        E.decode(grib(np.zeros((31, 16)), ni=16, nj=31, lat0=60.0))


def test_encoding_round_trip_and_missing_is_never_zero():
    vals = np.full((181, 360), 21.37)
    vals[5, 5] = np.nan
    back = E.decode_stored(E.encode(vals, -120.0, 0.005), -120.0, 0.005)
    assert back[0, 0] == pytest.approx(21.37, abs=0.003)
    assert np.isnan(back[5, 5])
    huge = E.decode_stored(E.encode(np.full((181, 360), 1e9), 0.0, 0.001), 0.0, 0.001)
    assert np.isnan(huge).all()                                         # out of range → missing, not clipped


def test_precipitation_type_from_categorical_flags():
    one = np.ones((181, 360))
    zero = np.zeros((181, 360))
    assert (E.precip_type({"crain": one, "csnow": zero}) == 1).all()
    assert (E.precip_type({"crain": zero, "csnow": one}) == 2).all()
    assert (E.precip_type({"crain": one, "csnow": one}) == 3).all()    # both → mixed
    assert (E.precip_type({"crain": zero, "csnow": zero, "cfrzr": one}) == 3).all()
    assert np.isnan(E.precip_type({})).all()


# --- Runs and frames ---------------------------------------------------------------------------------

def test_latest_run_picks_the_newest_complete_run():
    s = FakeS3({E.run_url(RUN, 72): build_file(72)})
    assert E.latest_run(datetime(2026, 10, 20, 10, tzinfo=UTC), s) == RUN
    assert all(c[1].endswith("f072.idx") for c in s.calls)             # only cheap index checks
    with pytest.raises(E.ExplorerDataError):
        E.latest_run(datetime(2026, 10, 20, 10, tzinfo=UTC), FakeS3({}))


def test_prepare_run_writes_frames_units_and_manifest(tmp_path):
    s = s3_for((0, 3))
    m = E.prepare_run(RUN, s, tmp_path, fhours=(0, 3))
    assert m["status"] == "complete" and m["run_utc"] == "2026-10-20T00:00Z"
    assert [f["valid_utc"] for f in m["frames"]] == ["2026-10-20T00:00Z", "2026-10-20T03:00Z"]
    assert all(f["ready"] and f["missing"] == [] for f in m["frames"])
    assert E.value_at(m, 3, "t2m", 0, 10, tmp_path) == pytest.approx(30.0, abs=0.01)    # K → °C
    assert E.value_at(m, 3, "t2m", 60, 10, tmp_path) == pytest.approx(0.0, abs=0.01)
    assert E.value_at(m, 3, "prmsl", 10, 10, tmp_path) == pytest.approx(1013.25, abs=0.01)  # Pa → hPa
    assert E.value_at(m, 3, "prate", 30, 10, tmp_path) == pytest.approx(1.0, abs=0.002)     # → mm/h, not the average
    assert E.value_at(m, 3, "ptype", 30, 10, tmp_path) == 1 and E.value_at(m, 3, "ptype", 70, 10, tmp_path) == 3
    assert E.read_manifest(tmp_path)["layers"]["wind"]["vars"] == ["u10", "v10"]
    ranged = [c for c in s.calls if c[0] == "GET" and not c[1].endswith(".idx")]
    assert ranged and all(c[2] and c[2].startswith("bytes=") for c in ranged)          # never the whole file


def test_missing_fields_are_reported_not_invented(tmp_path):
    s = s3_for((0,), skip=("CAPE", "CRAIN", "CSNOW", "CFRZR", "CICEP"))
    m = E.prepare_run(RUN, s, tmp_path, fhours=(0,))
    assert set(m["frames"][0]["missing"]) == {"cape", "ptype"}
    assert not E.frame_path(tmp_path, m["run_id"], 0, "cape").exists()


def test_only_the_newest_run_is_kept_and_frames_are_not_refetched(tmp_path):
    old = tmp_path / "20261019_18"
    old.mkdir()
    (old / "f000_t2m.bin").write_bytes(b"x")
    s = s3_for((0,))
    E.prepare_run(RUN, s, tmp_path, fhours=(0,))
    assert not old.exists() and (tmp_path / "20261020_00").exists()
    s2 = s3_for((0,))
    E.prepare_run(RUN, s2, tmp_path, fhours=(0,))                      # resumed: nothing downloaded
    assert s2.calls == []


def test_network_failure_keeps_the_previous_run(tmp_path, monkeypatch):
    E.prepare_run(RUN, s3_for((0,)), tmp_path, fhours=(0,))
    before = E.read_manifest(tmp_path)
    later = datetime(2026, 10, 20, 6, tzinfo=UTC)
    monkeypatch.setattr(E, "_state", {"thread": None, "last_check": 0.0, "error": None})   # restored afterwards
    monkeypatch.setattr(E, "latest_run", lambda now, s: later)
    monkeypatch.setattr(E, "_session", lambda: FakeS3({}, fail=True))
    E._refresh(tmp_path)
    assert E.read_manifest(tmp_path) == before and "ConnectionError" in E._state["error"]
    assert (tmp_path / "20261020_00").exists()


def test_ensure_current_never_blocks_and_throttles(tmp_path, monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target, args, name, daemon):
            started.append(args)

        def start(self):
            pass

        def is_alive(self):
            return False
    monkeypatch.setattr(E.threading, "Thread", FakeThread)
    monkeypatch.setattr(E, "_state", {"thread": None, "last_check": 0.0, "error": None})
    assert E.ensure_current(tmp_path, now=10_000.0)["refreshing"] is False      # tests: downloads switched off
    assert started == []
    monkeypatch.setenv("CLOUDZY_EXPLORER_REFRESH", "1")
    out = E.ensure_current(tmp_path, now=10_000.0)
    assert out == {"manifest": None, "refreshing": True, "error": None} and len(started) == 1
    E.ensure_current(tmp_path, now=10_060.0)                           # 1 minute later: no new check
    assert len(started) == 1
    E.ensure_current(tmp_path, now=10_000.0 + E.CHECK_EVERY_S)
    assert len(started) == 2
