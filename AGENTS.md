# AGENTS.md

## Purpose

This repository drives a Raspberry Pi poster display for Plex. It has two main parts:

- `proxy/`: Flask proxy and config API that talks to Plex and stores runtime settings in `proxy/config.json`
- `web/`: static kiosk UI (`index.html`) and settings UI (`settings.html`)

There is also:

- `setup.sh`: Pi provisioning/setup entrypoint
- `docs/systemd-examples.txt`: example service units for the proxy, static web server, and Chromium kiosk

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

## Validation

When making changes, prefer lightweight validation:

- Run the Flask app if backend logic changed.
- Serve `web/` locally if frontend behavior changed.
- Sanity-check the settings flow against `/api/config`.
- If you cannot fully validate hardware-specific behavior on the local machine, say so clearly.

## Avoid

- Do not overwrite or sanitize `proxy/config.json` unless the user explicitly asks.
- Do not add unnecessary tooling, frameworks, or build steps.
- Do not assume the final device deployment/reload procedure; wait for the user to provide that process.
