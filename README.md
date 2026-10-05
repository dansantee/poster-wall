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

- A Raspberry Pi 5 (4GB is what it's tested on; other models may work)
- A microSD card, and a display (it's built for a portrait TV)
- A Plex server on the same network

## Setup

1. **Flash the SD card.** In [Raspberry Pi Imager](https://www.raspberrypi.com/software/),
   choose **Raspberry Pi OS Lite (64-bit)**. In its settings, set a hostname
   (`poster-wall` is a good one), a username and password, your Wi-Fi, and turn on SSH.
2. **Boot the Pi and connect to it:** `ssh <your-username>@poster-wall.local`
3. **Install Poster Wall** with one command:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/dansantee/poster-wall/main/install.sh | bash
   ```

   It updates the Pi, installs everything and sets the wall up for a portrait screen.
   For a landscape screen, add the rotation:
   `curl -fsSL https://raw.githubusercontent.com/dansantee/poster-wall/main/install.sh | bash -s -- --rotate 0`

4. **Reboot** (`sudo reboot`). The screen shows the address of the settings page.
5. **Open that address** on a phone or computer, press **Sign in with Plex** and enter
   the code at [plex.tv/link](https://plex.tv/link). Pick your server, choose what each
   library is for (posters, music videos, or not shown), and press **Save Settings**. The
   wall starts by itself.
6. **Pick the TV to watch.** Start something playing on it, press **Find players** in the
   settings, and **Add** it, then Save again. Within about 15 seconds the wall picks the
   change up by itself (any saved setting does, while posters are showing), and playback on
   that TV switches it to Now Showing.

The installer is safe to re-run; it updates the code and re-applies the setup. See
[RASPBERRY-PI.md](docs/RASPBERRY-PI.md) for exactly what it changes and the options
`setup.sh` takes (`--rotate`, `--mode`).

### Setting it up by hand

The settings page also takes the Plex URL (like `http://192.168.1.100:32400`), a token and
library section IDs directly. To find a token yourself: open any item in Plex, choose
**Get Info → View XML**, and copy the `X-Plex-Token=` value from the address. The token is
stored in plain text in `proxy/config.json`, which is gitignored.

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
- "Up next" row with the next three items in the play queue, shuffle included;
  when the next song starts, its cover flies up into the main art slot and the
  row slides along
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

484 tests, a couple of seconds, no Plex server or network needed. They cover the proxy
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
- On a 4K TV, run the wall at 1080p60: `./setup.sh --rotate 90 --mode 1920x1080@60Hz`. A Pi 5 driving a rotated 4K screen manages only 15-20 fps, so every animation judders; at 1080p it holds 60 fps and the TV upscales. See [RASPBERRY-PI.md](docs/RASPBERRY-PI.md)
- Settings take effect on the wall at the next page load — hit "Restart Kiosk"
  after saving

## License

This is licensed under Creative Commons Attribution-NonCommercial 4.0. 

**TL;DR:** You can use it, modify it, share it for personal/educational stuff, just give me credit. Want to use it commercially? [Drop me a line](mailto:dan@santee.ws) and we can work something out.

Full license: https://creativecommons.org/licenses/by-nc/4.0/

Pull requests welcome!
