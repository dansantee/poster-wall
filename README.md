# poster-wall

Turn your Raspberry Pi into a movie poster kiosk that shows off your Plex library.

Posters rotate on a portrait display like a cinema one-sheet. When someone starts
playing something on a device you've nominated, the wall switches to a "Now
Showing" marquee with a live progress bar and format badges, then goes back to
rotating when playback stops. Music videos get their own Spotify-style screen:
album art, song and artist, a progress bar in a colour taken from the art, and
what's up next in the play queue.

## How it works

Three small pieces run on the Pi as user systemd services:

- **`proxy/app.py`** (port 8811) — a Flask proxy that talks to Plex, holds your
  token, aggregates and shuffles your selected libraries, and stores all settings
  in `proxy/config.json`. A background thread (`proxy/plex_events.py`) listens to
  Plex's notification websocket, so the wall hears about play, pause, seek and
  stop within about a second without polling Plex.
- **`web/`** (port 8088) — the kiosk page and the settings page, plain HTML/CSS/JS
  with no build step
- **Chromium in kiosk mode**, launched by Sway, pointed at the local site

Full picture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Documentation

| Doc | What's in it |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pieces fit, boot sequence, how "now playing" stays current, display modes, transitions, auto-dimming, known rough edges |
| [API.md](docs/API.md) | Every proxy endpoint, parameter, response and error, plus environment variables |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting: type, default, valid range, quirks |
| [RASPBERRY-PI.md](docs/RASPBERRY-PI.md) | What `setup.sh` does, the services, deploy workflows, troubleshooting |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local dev on Windows, preview modes, what to keep in sync |
| [TESTING.md](docs/TESTING.md) | Running and extending the test suite |

## What you need

- Raspberry Pi 5 (4GB) - this is what I've tested on, other models might work
- Raspberry Pi OS Lite 64-bit (Bookworm)
- SSH enabled and wifi/ethernet setup
- A Plex server on the same network

## Quick setup

1. Flash Raspberry Pi OS Lite (64-bit) to your SD card
2. In the imager's "Advanced options" set up:
   - Hostname
   - Username and password  
   - WiFi credentials
   - Locale
   - Enable SSH
3. Boot it up and SSH in

## Installation

Update the RPI:

```bash
sudo apt-get update && \
  sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a \
  apt-get -y -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confnew" full-upgrade
```

Install Git:

```bash
sudo apt install git -y
```

Clone this repo:

```bash
git clone https://github.com/your-username/poster-wall.git
cd poster-wall
```

Run the setup script (the `--rotate 90` is for portrait displays):

```bash
chmod +x setup.sh
./setup.sh --rotate 90
```

It's idempotent — re-run it any time, including with a different `--rotate`
value. See [RASPBERRY-PI.md](docs/RASPBERRY-PI.md) for exactly what it changes.

Once everything's running, go to `http://your-pi-hostname.local:8088/settings.html`
to configure the display.

## Getting your Plex token

You need a Plex token to access your library. Here's how to get it:

1. Open any movie or show in Plex and click the "..." → "Get Info"
2. Click "View XML"
3. Look at the URL for `X-Plex-Token=...` — that string is your token

Your Plex URL will be like `http://192.168.1.100:32400` (use your actual Plex server IP).

You'll also need the **section ID** of each library you want to show. Open
`http://your-plex:32400/library/sections?X-Plex-Token=your-token` and read the
`key` of each library.

The token is stored in plain text in `proxy/config.json`, which is gitignored.

## Features

The settings page lets you customize the setup:

**Basic settings:**
- Pick which Plex libraries to show (Movies, TV, etc.)
- Control rotation speed (3 seconds to 1 hour)
- Change the "Now Showing" text
- Choose from some fonts
- Change font sizes, spacing, colors
- Tune progress bar fill and track appearance
- Tune auto-dim strength for overly bright posters

**Poster transitions:**
- Basic crossfade (default)
- Slide transitions (left, right, up, down)
- 3D flip animation
- Scale and fade effects
- Or pick multiple and let it randomly choose

**"Now Playing" mode:**
- Monitors your Plex clients for active playback
- Reacts within about a second to play, pause, seek, stop and the next item,
  pushed from Plex's notification websocket rather than polled
- Smooth progress bar that runs between Plex's ~10 s position reports
- Pause dims the art and shows a pause badge. Between queued items (a
  playlist, shuffle or next episode) the wall waits for the next one with a
  spinner instead of flashing back to posters.
- Displays resolution badges, audio format, ratings
- Works with the libraries selected
- Includes preview buttons on the settings page for idle mode, now-playing mode
  and music-video mode

**Music videos:**
- Point `musicVideoSectionId` at a Plex "Other Videos" library of
  `Artist - Title` files
- Shows square album art on a background coloured from the art, the song and
  artist, and a progress bar in an accent colour picked from the art
- "Up next" row with the next three items in the play queue, shuffle included
- Plex shows a video frame for "Other Videos" by default. For real album art,
  put an `Artist - Title.jpg` next to each video: Plex uses it as the poster,
  and the wall shows that poster.

**Other features:**
- Auto-dims overly bright posters, white backgrounds, etc.
- Works with both movies and TV shows
- Shows build status on the settings page, including commit and dirty/clean state

Every setting is documented in [CONFIGURATION.md](docs/CONFIGURATION.md).

## Development

Local dev work:

**Backend proxy:**
```bash
cd proxy
python -m venv .venv
# Windows PowerShell:
# .\.venv\Scripts\Activate.ps1
pip install flask requests
# Set your environment variables:
export PLEX_URL="http://192.168.1.5:32400"
export PLEX_TOKEN="your-plex-token-here"
export SECTION_ID="1"
python app.py
```

**Frontend:**
```bash
cd web
python -m http.server 8088
```

Run this way, `app.py` also starts the Plex websocket monitor. More detail,
including the preview modes for iterating on the marquee without starting
playback, in [DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Tests

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

306 tests, a couple of seconds, no Plex server or network needed. They cover the proxy
API's behaviour end to end, and — since the frontend has no build step or test
runner — they also assert the string-level contracts that hold the project
together: element ids matching the HTML, transition names matching the CSS,
config keys the settings page must be able to save, ports agreeing between
`setup.sh` and the JS, and the secret files staying out of git.

Worth running before any deploy. See [TESTING.md](docs/TESTING.md).

## Deploying To The Pi

There are two deployment workflows:

**Normal repo deploy:**
- Commit and push to `origin/main`
- On the Pi, pull the latest code and restart services
- This keeps the Pi aligned with Git history

**Direct test deploy:**
- Copy selected local files straight to the Pi without going through GitHub
- Restart services on the Pi
- This is useful for fast visual iteration on the real display
- After testing, commit/push and do a normal repo deploy to bring the Pi back into sync

Both run through `scripts/poster-wall-remote.ps1`, which reads connection details
from `SECRETS.md`. A safe template is provided in `SECRETS-EXAMPLE.md`. Commands
and verification steps are in
[RASPBERRY-PI.md](docs/RASPBERRY-PI.md#deploying-changes).

## Notes

- I've only tested this on a Pi 5, but other models might work fine
- If you need different rotations, use different `--rotate` values (0, 90, 180, 270) when running `setup.sh` (it can be run multiple times)
- Settings take effect on the wall at the next page load — hit "Restart Kiosk"
  after saving

## License

This is licensed under Creative Commons Attribution-NonCommercial 4.0. 

**TL;DR:** You can use it, modify it, share it for personal/educational stuff, just give me credit. Want to use it commercially? [Drop me a line](mailto:dan@santee.ws) and we can work something out.

Full license: https://creativecommons.org/licenses/by-nc/4.0/

Pull requests welcome!
