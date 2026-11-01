# Security policy

Cloudzy is a solo student project. This document says what protects it, what runs automatically, what needs a
person, and what isn't protected. No setup makes a project "secure"; these are layered checks that reduce the
chance of a vulnerable dependency, a leaked secret, a broken deployment or an unnoticed failure.

## Reporting a vulnerability

Please report privately through **Security → Report a vulnerability** on the GitHub repository (private
vulnerability reporting), not in a public issue. Include what you found and how to reproduce it. Expect a reply
within a week.

## What runs where

| Part | Where | Exposure |
|---|---|---|
| Full app (Python, Streamlit) | Your own computer (`streamlit run streamlit_app.py`) | Local only; not publicly hosted |
| Static edition (`tools/build_pages.py`) | GitHub Pages, once you deploy it | Public, read-only static files |
| Checks and deployment | GitHub Actions | Runs on GitHub's machines |

Cloudzy needs **no API keys, tokens or passwords**: every data source is free and key-less (Open-Meteo, USGS, GDACS,
NOAA, NASA FIRMS public files, U.S. Drought Monitor, Smithsonian GVP, Iowa Environmental Mesonet). No workflow uses a
repository secret, and the tests fail if one starts to.

## Automated checks

| Check | Tool | When | Fails on |
|---|---|---|---|
| Tests and lint | pytest, ruff (`tests.yml`) | Every push to main and pull request; live API checks weekly | Any failure |
| Dependency vulnerabilities | pip-audit (`security.yml`) | Push, pull request, **weekly** | Any known vulnerability in a pinned package |
| Secrets in tracked files | detect-secrets (`security.yml`) | Push, pull request, weekly | A secret-like string not reviewed in `.secrets.baseline` |
| Python security linter | bandit (`security.yml`) | Push, pull request, weekly | High-severity, high-confidence findings |
| Workflow security | zizmor (`security.yml`) | Push, pull request, weekly | Medium or higher (unpinned actions, credential leaks, injection) |
| New vulnerable dependencies in a PR | dependency-review-action | Pull requests | High severity |
| Code scanning | CodeQL (`codeql.yml`): Python, JavaScript, workflows | Push, pull request, **weekly** | Alerts appear under Security → Code scanning |
| Dependency updates | Dependabot (`.github/dependabot.yml`) | Weekly | Opens pull requests; never merges |
| Static site safety | `verify()` in `tools/build_pages.py` | Every Pages build | Missing pages or assets, broken or root-relative links, development files (`.py`, models, databases, `.env`), secret-like strings, local paths |

Scheduled runs happen on GitHub whether or not anyone opens the repository. Run any of them by hand from
**Actions → (workflow) → Run workflow**. Locally: `pip install -r requirements-dev.txt`, then

```
pip-audit -r requirements.txt -r requirements-dev.txt
git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline
bandit -q -r src app_pages tools streamlit_app.py --severity-level high --confidence-level high
zizmor --offline .github/workflows
python -m pytest
```

## Notifications

- **Failed scheduled or main-branch security run:** the workflow opens one issue labelled `security-alert`, or
  comments on the open one. Scheduled test failures do the same with `ci-alert`. GitHub notifies you of new
  issues in your own repository. No email address or token is stored anywhere.
- **Dependabot alerts and security updates:** emailed by GitHub once enabled (see below).
- **Code scanning alerts:** shown under Security; GitHub can email them (notification settings).
- **Failed workflow runs:** GitHub emails you about failures of runs you triggered, and of scheduled runs of
  workflows you last edited, if "Actions: notify for failed workflows" is on in your notification settings.

These notifications have not been tested end to end yet: that needs the workflows on GitHub. To test the alert
issue once pushed, run **Actions → security → Run workflow** on a branch where a check fails, or temporarily add a
known-vulnerable pin on a branch. Successful runs send nothing.

## GitHub settings to switch on (manual)

None of these can be changed from the repository files. They are not on until you switch them on.

1. **Settings → Advanced Security (or Code security):**
   - Dependency graph: on (default for public repositories).
   - **Dependabot alerts**: Enable.
   - **Dependabot security updates**: Enable.
   - **Secret scanning** and **Push protection**: Enable (free for public repositories). Push protection blocks a
     push that contains a recognised credential.
   - **Private vulnerability reporting**: Enable.
   - Code scanning: leave "Default setup" **off**. `codeql.yml` is the advanced setup, and the two conflict.
2. **Settings → Actions → General:**
   - Workflow permissions: **Read repository contents and packages permissions**.
   - Leave "Allow GitHub Actions to create and approve pull requests" **unchecked**.
   - Fork pull request workflows: **Require approval for all external contributors**.
3. **Settings → Rules → Rulesets** (recommended): add a branch ruleset for `main` that blocks force pushes and
   deletion. Optionally require the `tests` and `security` checks before merging pull requests.
4. **Your notification settings** (github.com/settings/notifications): enable email for Actions failures and for
   Dependabot alerts, and watch the repository (Custom → Issues, Security alerts).

## Workflow policy

- **Least privilege:** every workflow starts read-only (`permissions: contents: read`, or none). Only the Pages
  deploy job gets `pages: write` and `id-token: write`, and only the alert jobs get `issues: write`.
- **Pinned actions:** actions are pinned to full commit SHAs, with the version in a comment. Dependabot proposes
  updates. Releases newer than about two weeks are avoided.
- **No leftover credentials:** checkout runs with `persist-credentials: false`, so later steps can't reuse the
  token.
- **Untrusted code:** no `pull_request_target`. Fork pull requests run read-only and get no secrets (there are none).
- **No unsafe interpolation:** values from GitHub reach shell steps through `env:`, never by interpolating
  `${{ }}` into commands.
- **Deployment:** manual (`workflow_dispatch`), from `main` only, after lint, tests and the build verification
  pass. A `pages` concurrency group keeps deployments from overlapping. Security workflows never deploy.

## Dependency policy

- Runtime and development dependencies are pinned exactly (`requirements.txt`, `requirements-dev.txt`) and
  installed from PyPI only. Pins are not "secure" by themselves; they make builds reproducible, so changes are
  deliberate and reviewed.
- Dependabot proposes updates: minor and patch versions grouped, major versions one per pull request. Nothing is
  merged automatically. Merge after its tests, security and CodeQL checks pass, and after reading the release
  notes for major versions.
- **Urgent advisory:**
  1. Read the advisory and check whether Cloudzy uses the affected code.
  2. Update the pin on a branch (`pip-audit` shows the fixed version) and run the tests.
  3. Open a pull request and merge once the checks pass.
  4. Redeploy Pages if the static site is affected.

  The October 2026 Pillow advisories needed Streamlit 1.66.1, the first release that allows Pillow 13; that
  upgrade was tested in a clean checkout first.

## Secret handling

- Never commit credentials. `.env`, `.env.*` and `.streamlit/secrets.toml` are git-ignored.
- Git is not a secret store: anything committed stays in history even after deletion. If a secret is ever
  committed:
  1. Revoke or rotate it at the provider first.
  2. Then remove it from the code.
  3. Rewriting history doesn't un-leak it.
- **Nothing in the browser is secret:** anything the static site or a build-time variable contains is public.
  Features that would need a private key stay out of the static edition until a backend exists.
- `.secrets.baseline` lists reviewed false positives (SHA-256 checksums of the model file). Each entry is marked
  `is_secret: false`, and the tests check that.

## Application security notes

- **Untrusted feed data:**
  - XML feeds (NOAA tsunami, NHC KMZ) are parsed with `defusedxml`: no entity expansion, no external entities.
  - NHC KMZ files are size-checked before unzipping.
  - Every hazard response over 64 MB is refused.
  - Every HTTP request has a timeout.
  - All feeds use HTTPS.
  - **Server-side request forgery:** the hazard data helper (`src/hazards/http.py`) fetches only HTTPS URLs on
    a fixed list of agency hosts (`ALLOWED_HOSTS`). URLs found inside feeds (NHC storm files, GDACS geometry) and
    every redirect must pass the same check, so a tampered feed can't send Cloudzy to an internal or arbitrary
    address. Weather, air-quality and geocoding requests go to fixed Open-Meteo, Nominatim and archive URLs, with
    your input only as query parameters.
  - **Links from feeds** (each event's "source" link) are kept only when they are plain `http(s)` addresses
    (`schema.safe_link`). `javascript:` or `data:` links are dropped, and the globe's own links must be `https`.
  - **Coordinates:** they are range-checked wherever they come from: typed coordinates, device location,
    geocoding results (Open-Meteo, Nominatim) and every hazard record (`valid_coords`).
- **Rendering:** custom HTML escapes provider text (`html.escape` in Python, `esc()` and `textContent` in
  JavaScript). Nothing uses `unsafe_allow_html`.
- **SQL:** queries are parameterised. The two places that put a column name into SQL take it from the database's
  own schema or from a fixed allowlist.
- **Model files** load with joblib (pickle), which can run code.
  - The file's SHA-256 is checked against its metadata **before** it is unpickled. This applies to the app
    (`artifacts.load_trusted`) and to every offline tool (`artifacts.load_verified`), so a swapped model file
    never runs.
  - The check doesn't stop someone who can rewrite both the model and its metadata. Control over who can push
    to the repository is what protects against that.
  - Never load a model file from an untrusted source.
- **ML inputs and load:**
  - Live inputs are validated (units, time zone, plausible ranges, complete hours) before prediction, and
    Cloudzy refuses rather than guesses when an input is missing.
  - Predictions run only for places within 15 km of Delhi and are cached for 10 minutes per place, so visitors
    can't make inference run repeatedly.
- **No user accounts, no authentication, no uploads.** The full app has no login, so it must not be hosted with
  private data. Uploads are capped at 1 MB because there are none.

### Security audit, 2026-11-01

A review of Cloudzy's own code paths, on top of the automated checks above.

| # | Finding | Severity | Fix | Verified by |
|---|---|---|---|---|
| 1 | Model files were unpickled before their checksum was checked; offline tools loaded models with no check | High | Checksum verified before loading, everywhere | A test with a malicious pickle that would create a file is refused and never runs; all 4 models still reproduce exactly |
| 2 | URLs taken from feed responses were fetched, and redirects weren't checked (server-side request forgery) | Medium | HTTPS-only host allowlist for the hazard helper, applied to feed-supplied URLs and every redirect | Tests for metadata IPs, localhost, other hosts, plain HTTP, credentials, ports and redirects; all 9 live feeds still load |
| 3 | Feed links could carry `javascript:` or `data:` URLs into link buttons; one globe link relied on HTML escaping alone | Medium | `safe_link` for feed links; `safeHref` (https only) in the globe | Tests for each scheme |
| 4 | Geocoding results weren't range-checked | Low | Out-of-range or NaN coordinates are skipped | Tests |
| 5 | Pillow 12.3.0 had 35 published advisories (October audit) | High | Pillow 13.0.0 with Streamlit 1.66.1 | pip-audit clean; full regression |

**Already sound (checked, no change needed):**
- No API keys or credentials exist in the code or its history.
- Every request has a timeout and bounded retries.
- Nominatim is limited to 1 request per second; weather and air quality are cached (10 and 15 minutes).
- Map tiles and imagery load over HTTPS.
- Map labels and popups escape their text.
- "Official" status comes from the agency a record is from, never from the record's content.
- Observations, official warnings, automatic estimates, forecasts and Cloudzy ML predictions are labelled
  separately.

**Not changed (would need your approval):**
- **Per-visitor rate limiting** for the full app needs a proxy in front of Streamlit; it has none built in.
- **Requiring a metadata file for every model,** including refit backups, would tighten finding 1 but changes
  the refit workflow.
- **Clickjacking and Permissions-Policy headers** for the static site need a CDN; GitHub Pages can't set them.

**Licences of tools added in this work** (all compatible with Cloudzy's MIT licence):
- `defusedxml`: PSF licence.
- Playwright, pip-audit, detect-secrets and bandit: Apache-2.0.
- zizmor: MIT.
- Material Symbols font (static site): Apache-2.0.

The MIT `LICENSE` is unchanged. No paid or proprietary service was introduced.

### Hosting the full app (not done yet)

If the Streamlit app is ever hosted publicly:
1. Add `showErrorDetails = "type"` under `[client]` in `.streamlit/config.toml`, so visitors never see stack
   traces.
2. Keep XSRF protection on.
3. Put it behind HTTPS.
4. Remember that per-visitor settings live in each visitor's browser, but prediction tracking
   (`data/monitoring/`) and hazard caches are shared server files. Back them up if they matter.
5. Add rate limiting at the proxy if traffic grows. Streamlit has none built in.

## Static hosting limits (GitHub Pages)

- **What the site does protect:** each page carries a **Content Security Policy** as a `<meta>` tag. Scripts can
  come only from the site itself, `eval` is blocked, and connections go only to Open-Meteo and the listed map and
  imagery hosts. Pages also carry a referrer policy. All content is served over HTTPS.
- **Headers GitHub Pages can't set:** `X-Frame-Options` / `frame-ancestors` (clickjacking), `Permissions-Policy`,
  HSTS preload and custom `X-Content-Type-Options`. A `_headers` file has no effect there. These need a CDN or
  proxy in front (for example Cloudflare). The site has no forms or logged-in state, which limits clickjacking
  risk.
- **Inline styles:** the CSP allows them (`'unsafe-inline'` for styles only), because the map library and the
  globe set styles inline. Scripts get no such exception.
- **Rollback:** GitHub Pages has no instant rollback. To roll back:
  1. Revert the commit on `main`.
  2. Run **deploy-pages** again, or re-run an earlier successful deploy run.

  Each build records its commit and time in `build-info.json`.

## Data

| Data | Where | Public? |
|---|---|---|
| Source code, committed model, evaluation reports, screenshots | Git | Public |
| Static site: pages, a hazard snapshot, map library, fonts | GitHub Pages | Public |
| Raw downloads, caches, research models (`data/raw`, `models/*` except the deployed one) | Your computer | Git-ignored |
| Prediction tracking database (`data/monitoring/`) | Your computer | Git-ignored; contains no personal data |
| Saved places and preferences | Each visitor's own browser | Never sent to Cloudzy |

The static site never receives location data: the Weather page sends only the searched place's coordinates to
Open-Meteo, from the visitor's browser.

## Residual risks

Automation can't eliminate these:
- **Upstream compromise:** a compromised dependency or action release before an advisory exists (pins and the
  two-week rule reduce, not remove, this).
- **Unanswered alerts:** an alert issue or Dependabot pull request that nobody acts on.
- **Scanner gaps:** novel secret formats or vulnerability classes the scanners don't recognise.
- **Third-party outages or changes:** data providers changing or failing. The app labels data freshness and
  fails visibly, but can't fix them.
- **Missing headers:** the security headers GitHub Pages can't set (above).
- **No independent review:** the project hasn't had an independent security review.
