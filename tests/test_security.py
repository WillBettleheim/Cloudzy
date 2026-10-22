"""Security regression tests: untrusted feed parsing, response limits, workflow hardening, configuration and the
static-site build's own safety checks. See SECURITY.md."""

from __future__ import annotations

import io
import json
import re
import tomllib
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from src.hazards import http, nhc, tsunami

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
REAL_GET = http.get   # captured at import: tests/conftest.py replaces http.get to block the network

BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>
<feed><entry><title>&lol3;</title></entry></feed>"""
EXTERNAL_ENTITY = """<?xml version="1.0"?>
<!DOCTYPE feed [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><feed><entry><title>&xxe;</title></entry></feed>"""


# --- Untrusted input from the hazard feeds ---------------------------------------------------------------

@pytest.mark.parametrize("xml", [BILLION_LAUGHS, EXTERNAL_ENTITY])
def test_tsunami_feed_never_expands_entities(xml):
    with pytest.raises(http.FeedError):
        tsunami.parse(xml, "NTWC", pd.Timestamp("2026-10-22T00:00Z"))


def test_oversized_kmz_is_refused_before_unpacking(monkeypatch):
    monkeypatch.setattr(nhc, "MAX_KML_BYTES", 100)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("cone.kml", "<kml>" + "x" * 10_000 + "</kml>")
    with pytest.raises(http.FeedError, match="too large"):
        nhc._kml(buf.getvalue())


def test_kmz_with_entities_is_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cone.kml", BILLION_LAUGHS)
    with pytest.raises(Exception) as exc:
        nhc._kml(buf.getvalue())
    assert "Entities" in type(exc.value).__name__ or "entit" in str(exc.value).lower()


class _Resp:
    def __init__(self, size):
        self.status_code, self.headers, self.content, self.text = 200, {"Content-Length": str(size)}, b"", ""

    def json(self):
        return {}


class _Session:
    def __init__(self, size):
        self.size = size

    def get(self, *a, **k):
        return _Resp(self.size)


def test_responses_larger_than_the_limit_are_refused():
    with pytest.raises(http.FeedError, match="too large"):
        REAL_GET("https://example.invalid", session=_Session(http.MAX_RESPONSE_BYTES + 1))
    assert REAL_GET("https://example.invalid", session=_Session(1000)) == {}     # fake session: no network


# --- GitHub Actions hardening -----------------------------------------------------------------------------

@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_are_pinned_and_least_privilege(wf):
    text = wf.read_text()
    assert re.search(r"^permissions:", text, re.M), "top-level permissions must be set (read-only by default)"
    assert "pull_request_target" not in text, "untrusted PR code must never run with repository secrets"
    for ref in re.findall(r"uses:\s*([^\s#]+)", text):
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{wf.name}: {ref} is not pinned to a commit SHA"
    for block in re.findall(r"uses: actions/checkout@\S+[^\n]*\n((?:\s{8,}.*\n)*)", text):
        assert "persist-credentials: false" in block, f"{wf.name}: checkout keeps credentials"
    assert not re.search(r"\$\{\{\s*secrets\.", text), "no workflow needs repository secrets"


def test_pages_deploys_only_by_hand_from_main_with_scoped_permissions():
    text = (ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text()
    on = text[text.index("\non:"):text.index("\npermissions:")]
    assert "workflow_dispatch" in on and "push" not in on and "schedule" not in on
    assert "permissions: {}" in text and text.count("github.ref == 'refs/heads/main'") == 2
    build = text[text.index("  build:"):text.index("  deploy:")]
    assert "pages: write" not in build and "id-token" not in build      # only the deploy job may publish
    assert "python -m pytest" in build and "tools/build_pages.py" in build
    assert "path: _site" in build


def test_security_workflow_runs_on_a_schedule_and_never_deploys():
    text = (ROOT / ".github" / "workflows" / "security.yml").read_text()
    assert "schedule:" in text and "cron:" in text
    assert "deploy" not in text.replace("dependency", "").lower().split("never deploys")[1]
    assert "issues: write" in text and text.count("issues: write") == 1   # only the notify job


def test_dependabot_covers_python_and_actions():
    text = (ROOT / ".github" / "dependabot.yml").read_text()
    assert "package-ecosystem: pip" in text and "package-ecosystem: github-actions" in text


# --- Configuration and data exposure -------------------------------------------------------------------------

def test_streamlit_config_keeps_xsrf_and_documents_public_hosting():
    text = (ROOT / ".streamlit" / "config.toml").read_text()
    cfg = tomllib.loads(text)
    assert 'showErrorDetails = "type"' in text            # the step to take before public hosting is written down
    assert cfg["server"]["enableXsrfProtection"] is True and cfg["server"]["maxUploadSize"] <= 1
    assert cfg["browser"]["gatherUsageStats"] is False


def test_private_and_generated_files_stay_out_of_git():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    for pattern in (".env", ".streamlit/secrets.toml", "data/monitoring/", "data/user/", "data/raw/*", "_site/"):
        assert pattern in ignored, pattern


def test_secret_baseline_contains_only_reviewed_non_secrets():
    base = json.loads((ROOT / ".secrets.baseline").read_text())
    for items in base["results"].values():
        assert all(i.get("is_secret") is False for i in items)


# --- Static site build ---------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def offline_site(tmp_path_factory):
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_pages", ROOT / "tools" / "build_pages.py")
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    out = tmp_path_factory.mktemp("site") / "_site"
    bp.build(out, live=False)
    return bp, out


def test_offline_static_build_is_complete_and_clean(offline_site):
    bp, out = offline_site
    assert bp.verify(out) == []
    for page in ("index.html", "weather.html", "earth.html", "lab.html", "404.html"):
        html = (out / page).read_text()
        assert "Content-Security-Policy" in html and "script-src 'self'" in html and "<script>" not in html
    assert "No hazard snapshot" in (out / "earth.html").read_text()     # honest when there's no data
    lab = (out / "lab.html").read_text()
    assert "Against the station, the raw ECMWF forecast is closer than Cloudzy" in lab


def test_verification_catches_secrets_broken_links_and_dev_files(offline_site):
    bp, out = offline_site
    (out / "js" / "leak.js").write_text('const apiKey = "' + "A" * 8 + 'abcdefgh12345678";')
    (out / "notes.py").write_text("print(1)")
    (out / "bad.html").write_text('<meta http-equiv="Content-Security-Policy"><a href="/weather.html">x</a>'
                                  '<img src="missing.png">')
    problems = "\n".join(bp.verify(out))
    assert "secret-like string in js/leak.js" in problems and "notes.py" in problems
    assert "root-relative link" in problems and "broken link in bad.html: missing.png" in problems
    for f in ("js/leak.js", "notes.py", "bad.html"):
        (out / f).unlink()
