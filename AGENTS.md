# AGENTS.md

## Purpose

This repository drives a Raspberry Pi poster display for Plex. It has two main parts:

- `proxy/`: Flask proxy and config API that talks to Plex and stores runtime settings in `proxy/config.json`
- `web/`: static kiosk UI (`index.html`) and settings UI (`settings.html`)

There is also:

- `setup.sh`: Pi provisioning/setup entrypoint
- `docs/systemd-examples.txt`: example service units for the proxy, static web server, and Chromium kiosk
- `tests/`: pytest suite covering the proxy API plus cross-file contracts (see Validation)

## Read First

Before changing anything, read the doc for the area you are touching. They are
current and describe real behaviour, including known rough edges:

- `docs/ARCHITECTURE.md`: how the parts fit, kiosk boot sequence, display modes, transitions, auto-dimming, known rough edges
- `docs/API.md`: every proxy endpoint, its parameters, responses, errors, and the environment variables
- `docs/CONFIGURATION.md`: every config key with type, default, clamps and quirks
- `docs/RASPBERRY-PI.md`: what `setup.sh` does, the three services, deploy workflows, troubleshooting
- `docs/DEVELOPMENT.md`: local dev, preview modes, what must stay in sync across files
- `docs/TESTING.md`: how to run and extend the suite, and what is deliberately not covered

Keep these in sync when behaviour changes. A change to ports, endpoints, config
keys, service names or defaults should update the relevant doc in the same edit.

## Working Style

- Make minimal, targeted changes.
- Change only what is needed to satisfy the request. Do not refactor or modernize unrelated code.
- Prefer updating existing files and patterns over introducing new abstractions.
- Do not commit changes unless the user explicitly asks. Leave commits for user review and execution.
- Do not make Raspberry Pi or remote device changes without the user's explicit permission for that action.
- If device deployment, download, or reload steps are needed, pause and ask for the user’s device-specific workflow first.

## Repo-Specific Notes

- `proxy/config.json` contains live local configuration and may include secrets such as a Plex token. Treat it as user data, not sample config.
- `SECRETS.md` is for local-only operational notes, connection details, and credentials. Check it if local-only access details are needed. Never commit it.
- The frontend expects the proxy on the same host at port `8811`.
- The settings page is served from port `8088`.
- `sectionId` is handled as an array in current code, though the README still shows older single-section examples in places.
- The project mixes movies and TV shows from configured Plex libraries and shuffles results server-side.
- The remote deployment workflow pulls from `origin/main` on the Pi. Local commits do not reach the device until they are pushed.
- There is also a separate direct-to-Pi deploy workflow for local testing. It copies selected local files to the Pi without changing the Pi's git commit.
- The settings page now includes build status, preview buttons for idle/now-playing mode, progress track controls, and auto-dim strength controls.

## Local Development

Backend proxy:

```powershell
cd C:\code\poster-wall\proxy
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install flask requests
python app.py
```

Frontend:

```powershell
cd C:\code\poster-wall\web
python -m http.server 8088
```

The README also documents the Raspberry Pi install path using `setup.sh`.

## Editing Guidance

- For backend work, start with `proxy/app.py` and verify whether changes affect `/api/config`, `/api/movies`, poster fetch behavior, or kiosk restart flows.
- For frontend work, keep `web/index.html`, `web/app.js`, `web/settings.html`, and `web/settings.js` aligned. The UI is plain HTML/CSS/JS without a build step.
- Preserve compatibility with saved config where practical. Existing code already supports some backward-compatible normalization.
- Keep README and docs in sync when behavior, ports, setup, or configuration expectations change.
- For remote box operations, prefer `scripts/poster-wall-remote.ps1` instead of ad hoc commands. It reads local-only connection details from `SECRETS.md`.
- Use `scripts/poster-wall-remote.ps1 -Action deploy` to pull the latest pushed code on the Pi and restart the poster services.
- Use `scripts/poster-wall-remote.ps1 -Action direct-deploy` for fast local testing on the Pi without going through GitHub. This should stay a separate workflow from normal repo deployment.
- After remote deploys, verify the Pi repo commit and confirm `poster-proxy.service`, `poster-web.service`, and `poster-kiosk.service` are active.
- After direct deploys, do not rely on the Pi git commit for verification because the working tree may differ from `origin/main`. Verify the affected behavior and confirm the poster services are active.
- `GET /api/build-info` is the lightweight source for the settings-page build indicator and should report hostname, commit, and working-tree state.

## Validation

Run the test suite for any change to `proxy/`, `web/`, `setup.sh`,
`scripts/poster-wall-remote.ps1` or `.gitignore`:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Create the venv first if it is missing (`py -3 -m venv .venv` then
`.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`). The suite
needs no network and no Plex server, and never touches the real
`proxy/config.json`.

Half the suite asserts cross-file contracts: element ids matching the HTML,
transition names matching the CSS, config keys the settings page must be able to
save, ports agreeing between `setup.sh` and the frontend, and the `SECRETS.md`
field labels the PowerShell helper parses. A failure there usually means two
files drifted apart, not that the test is stale — fix the mismatch rather than
loosening the assertion. Some tests deliberately pin known-quirky behaviour and
say so in their docstring; if a change makes one fail because the quirk is now
fixed, update the test and the docs together.

Add tests for new endpoints, new config keys and new transitions. `docs/TESTING.md`
has the fixtures and patterns.

Beyond that, prefer lightweight validation:

- Run the Flask app if backend logic changed.
- Serve `web/` locally if frontend behavior changed; use `index.html?preview=rotation`
  and `?preview=nowplaying` to check the display without live playback.
- Sanity-check the settings flow against `/api/config`.
- If you cannot fully validate hardware-specific behavior on the local machine, say so clearly.

## Avoid

- Do not overwrite or sanitize `proxy/config.json` unless the user explicitly asks.
- Do not add unnecessary tooling, frameworks, or build steps.
- Do not assume the final device deployment/reload procedure; wait for the user to provide that process.
