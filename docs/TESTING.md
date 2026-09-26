# Testing

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

225 tests, sub-second, no network, no Plex server, no browser.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_now_playing_api.py -v   # one file
.\.venv\Scripts\python.exe -m pytest -k episode                          # by name
.\.venv\Scripts\python.exe -m pytest -x --ff                             # stop at first, failures first
```

## What this suite is for

The wall runs unattended on a device with no keyboard. The realistic failure mode
is not a subtle algorithmic bug — it is a **rename that silently unhooks two
files from each other**: an element id that no longer matches the HTML, a
transition name with no CSS, a config key the settings page stopped saving, a
port that drifted between `setup.sh` and `app.js`.

So the suite has two halves:

1. **Behaviour tests** exercise the Flask app through its test client with a fake
   Plex server, asserting the exact JSON the kiosk depends on.
2. **Contract tests** read the source of the frontend, the installer, the systemd
   examples and the PowerShell helper as text, and assert that the names on both
   sides of every join still line up. This is how a project with no JS test
   runner gets a regression net over its frontend.

## Layout

| File | Tests | Covers |
| --- | --- | --- |
| `conftest.py` | — | Fake Plex server, isolated config file, source-reading fixtures |
| `test_health.py` | 13 | `/api/ping`, `/api/build-info`, CORS headers, the full route list |
| `test_config_api.py` | 20 | `GET`/`PUT` `/api/config`: defaults, round-trip, replace-not-merge, URL normalisation, admin key |
| `test_movies_api.py` | 30 | `/api/movies`: URL/token resolution, multi-section fan-out, type + artwork filtering, paging and clamps, poster URL shape |
| `test_poster_api.py` | 23 | `/api/poster`: the transcode URL, streaming, caching, TLS flags, 502 on upstream failure |
| `test_now_playing_api.py` | 77 | `/api/now-playing`: device whitelist, library whitelist, media-type filter, music videos, progress maths, `state`/`offsetAt`/`playerId`, audio-channel labels, the whole episode-artwork fallback chain, the monitor cache and its direct fallback, `monitor_queue()` (up next) |
| `test_plex_events.py` | 45 | `plex_events.py`: the websocket client's framing, ping/pong, close, and overall (not per-read) handshake and receive deadlines; the monitor's trigger filtering, follow-up and safety refreshes, `offsetAt` rule, keepalive, poll fallback and reconnects, and per-queue-item up-next caching |
| `test_restart_kiosk.py` | 10 | `/api/restart-kiosk`: the exact systemctl command, admin key, timeout and failure handling |
| `test_frontend_contract.py` | 60 | Element ids, transitions ↔ CSS, custom properties ↔ `:root`, icon files, fonts, config-key coverage, shared defaults |
| `test_deployment_contract.py` | 31 | Ports, systemd unit names, rotation flags, `SECRETS.md` labels, gitignore hygiene |

## Fixtures worth knowing

Defined in [`conftest.py`](../tests/conftest.py).

| Fixture | Use |
| --- | --- |
| `isolated_proxy` | **Autouse.** Points `CFG_PATH` at a temp file and clears the Plex environment variables, so no test can read or write your real `proxy/config.json` |
| `client` | Flask test client |
| `write_cfg(**keys)` / `read_cfg()` | Write and read the temp config file |
| `plex` | Replaces `requests.get`; route responses by URL substring and inspect what was sent |
| `configured_plex` | `plex` plus a config with a Plex URL, token and section 1 |
| `proxy_app` | The imported `app` module, for monkeypatching import-time constants |
| `source(path)` | Read any repo file as text, cached — the basis of the contract tests |

`conftest.py` scrubs `PLEX_URL`, `PLEX_TOKEN`, `SECTION_ID`, `TIMEOUT`,
`ALLOW_INSECURE`, `PW_ADMIN_KEY` and `PW_CONFIG_PATH` from the environment
*before* importing `app.py`, because those become module-level constants at
import time.

## Writing a behaviour test

```python
from conftest import FakeResponse, plex_container

def test_only_movies_and_shows_are_returned(client, configured_plex):
    configured_plex.route(
        "/library/sections/",
        FakeResponse(plex_container(
            {"type": "movie", "title": "Arrival", "thumb": "/t/1"},
            {"type": "artist", "title": "Bach", "thumb": "/t/2"},
        )),
    )
    items = client.get("/api/movies").get_json()["items"]
    assert [i["title"] for i in items] == ["Arrival"]
```

`route()` also accepts a callable (to vary the response per call) or an exception
instance (to simulate an unreachable Plex). Inspect `plex.calls` for the URL,
query parameters, headers and the `verify` flag of each outbound request.

## Tests that pin known quirks

Some tests assert behaviour that is arguably wrong. They are named and commented
so it is clear they are change detectors, not endorsements — if you fix the
underlying issue, the test failing is the reminder to update the docs too.

- `test_audio_channel_labels` — a 5.1 track is reported as `6.1` because Plex
  counts the LFE channel.
- `test_unused_css_transitions_are_flagged` — `blur-transition` is styled but
  unreachable from the UI.
- `test_the_settings_page_resaves_the_whole_config_document` — the `{ ...cfg }`
  spread is why `hostname` ends up persisted in `config.json`.
- `test_both_save_paths_write_the_same_keys` — `settings.js` duplicates its save
  payload; this keeps the two copies honest until they are factored out.

## When a contract test fails

The failure message names both sides of the mismatch. Read it as "these two
files disagree", not "the test is stale":

| Failing test | Usual cause |
| --- | --- |
| `test_route_surface_is_unchanged` | A route was added or removed — update `EXPECTED_ROUTES` and `docs/API.md` |
| `..._uses_only_ids_that_exist_in_...` | An element id was renamed on one side only |
| `test_every_transition_has_css` | A transition was added to `settings.js` without CSS |
| `test_the_settings_page_can_set_everything_the_kiosk_reads` | `app.js` reads a config key the settings page cannot write |
| `test_both_save_paths_write_the_same_keys` | A new field was added to one of `settings.js`'s two save blocks |
| `test_kiosk_and_settings_page_agree_on_defaults` | A default changed in one file only |
| `test_secret_labels_match_...` | `poster-wall-remote.ps1` and `SECRETS-EXAMPLE.md` disagree on a field label |
| `test_secret_files_are_not_tracked` | `SECRETS.md` or `proxy/config.json` got staged — unstage it before committing |

## Deliberate gaps

- **No browser tests.** `app.js` is a single IIFE with no exports, so its
  functions cannot be imported. Testing rotation, transition timing or canvas
  dimming would mean refactoring the frontend, which is out of scope; the
  contract tests cover the joins instead. Verify motion by eye with
  `?preview=rotation`.
- **No Plex integration tests.** Every upstream call is faked. Use the settings
  page's **Test Plex** button against a real server.
- **No `setup.sh` execution.** It needs Raspberry Pi OS, `sudo`, and it edits
  `/boot/firmware`. Its ports, unit names and rotation handling are asserted as
  text.
- **No PowerShell tests.** `poster-wall-remote.ps1` needs a live SSH host. Its
  parameter set and secret labels are asserted as text.
