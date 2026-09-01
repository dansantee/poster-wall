# Development

There is no build step, no bundler and no framework. The frontend is three files
the browser loads directly; the backend is one Flask module.

## Running it locally on Windows

Two processes, two terminals. From the repo root:

**Proxy (port 8811)**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install flask requests
cd proxy
python app.py
```

Run it from `proxy/` — `PW_CONFIG_PATH` defaults to the relative path
`config.json`, so the working directory decides which file you read and write.

**Static site (port 8088)**

```powershell
cd web
python -m http.server 8088
```

Then open:

- kiosk — <http://localhost:8088/index.html>
- settings — <http://localhost:8088/settings.html>

The frontend always calls the proxy at `<current-protocol>://<current-hostname>:8811`,
so `localhost` for both works with no configuration. Point the settings page at
your real Plex server and save; you are now editing your local
`proxy/config.json`, not the Pi's.

To avoid retyping Plex details, export them instead — environment variables
outrank the config file:

```powershell
$env:PLEX_URL = 'http://192.168.1.3:32400'
$env:PLEX_TOKEN = 'xxxxxxxxxxxx'
python app.py
```

## Previewing without Plex playback

The settings page has two preview buttons, which open the kiosk with a query
parameter:

| URL | Effect |
| --- | --- |
| `index.html?preview=rotation` | Normal rotation, "Now Playing" monitoring disabled |
| `index.html?preview=nowplaying` | Renders the marquee immediately using a fake session at 42% progress, 4K + 5.1 badges, and the first poster from your library |

`?preview=nowplaying` is the only way to iterate on the marquee layout without
actually starting playback on a monitored device. Note that the fake session has
no content rating, so it always shows the "not rated" badge.

## Running the tests

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

225 tests, well under a second, no network and no Plex server. They never touch
your real `proxy/config.json`. See [TESTING.md](TESTING.md) for what is covered
and how to add to it.

Run the suite before deploying — most of the ways this project breaks are
name-level mismatches between files, and that is exactly what the contract tests
catch.

## Working on the frontend

Keep four files in step, because they are joined only by strings:

| Change | Also update |
| --- | --- |
| A new setting | `settings.html` control, `settings.js` **both** save paths, `app.js` `loadCfg()` defaults, and [CONFIGURATION.md](CONFIGURATION.md) |
| A new transition | `transitionCheckboxes` in `settings.js`, a checkbox in `settings.html`, and `.poster.transition-<name>` + `.entering` / `.visible` / `.exiting` rules in `styles.css` |
| An element id | Both the HTML and every `el('…')` / `has('…')` / `getElementById('…')` reference |
| A themed colour or size | The `setProperty()` call in `app.js` *and* the `:root` default in `styles.css` |

`settings.js` builds its save payload **twice** — once for the Save button and
once for the `plex-form` submit handler. Any new field must be added to both;
`test_both_save_paths_write_the_same_keys` fails if you miss one.

The kiosk logs freely to the browser console (`swap()` in particular is chatty).
That is intentional — it is the only debugging surface on a device with no
keyboard. Chromium's remote debugging is not enabled by the kiosk service, so on
the Pi the practical approach is `journalctl --user -u poster-kiosk.service -f`
plus the settings page's Test buttons.

## Working on the proxy

`proxy/app.py` is deliberately flat: helpers at the top, one function per route,
no blueprints. Points to keep in mind:

- `SERVER_TOKEN`, `ADMIN_KEY`, `DEFAULT_SECTION`, `TIMEOUT`,
  `ALLOW_INSECURE_DEFAULT` and `CFG_PATH` are read from the environment **at
  import time**. Restart the service after changing them, and monkeypatch the
  module attribute (not the environment) in tests.
- The config file is re-read on every request. There is no caching layer, which
  keeps the settings page and kiosk consistent at the cost of some stat calls.
- Endpoints the kiosk polls (`/api/now-playing`) always return `200` and put
  problems in the body. Endpoints the kiosk calls once (`/api/movies`) return
  real error codes, because `app.js` maps those codes to human-readable messages
  on the error screen.
- Adding a route means updating `EXPECTED_ROUTES` in `tests/test_health.py` and
  [API.md](API.md). That is on purpose — the route list is a contract.

## Deploying to the Pi

Details and the SSH helper are in [RASPBERRY-PI.md](RASPBERRY-PI.md#deploying-changes).
The short version:

- **Repo deploy** — commit, push to `origin/main`, then
  `scripts/poster-wall-remote.ps1 -Action deploy`. The Pi's git commit now
  describes exactly what is running.
- **Direct deploy** — `scripts/poster-wall-remote.ps1 -Action direct-deploy`
  copies your uncommitted changes straight over SSH for fast visual iteration.
  The Pi's commit no longer matches its working tree; the settings page shows
  `dirty` until you push and do a repo deploy.

## Style

From [`AGENTS.md`](../AGENTS.md): minimal targeted changes, no new frameworks or
build steps, prefer extending the existing patterns over introducing
abstractions, and keep saved configs working.
