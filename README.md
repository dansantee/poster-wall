# poster-wall

Turn your Raspberry Pi into a movie poster kiosk that shows off your Plex library.

## What you need

- Raspberry Pi 5 (4GB) - this is what I've tested on, other models might work
- Raspberry Pi OS Lite 64-bit (Bookworm)
- SSH enabled and wifi/ethernet setup

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

## Getting your Plex token

You need a Plex token to access your library. Here's how to get it:

1. Open any movie or show in Plex and click the "..." → "Get Info"
2. Click "View XML"
3. Look at the URL for `X-Plex-Token=...` — that string is your token

Your Plex URL will be like `http://192.168.1.100:32400` (use your actual Plex server IP).

Once everything's running, go to `http://your-pi-hostname.local:8088/settings.html` to configure the display.

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
- Shows live progress bar
- Automatically switches when someone starts watching something
- Displays resolution badges, audio format, ratings
- Works with the libraries selected
- Includes preview buttons on the settings page for idle mode and now-playing mode

**Other features:**
- Auto-dims overly bright posters, white backgrounds, etc.
- Works with both movies and TV shows
- Shows build status on the settings page, including commit and dirty/clean state

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

The local helper script for remote operations reads connection details from `SECRETS.md`. A safe template is provided in `SECRETS-EXAMPLE.md`.

## Notes

- I've only tested this on a Pi 5, but other models might work fine
- If you need different rotations, use different `--rotate` values (0, 90, 180, 270) when running `setup.sh` (it can be run multiple times)

## License

This is licensed under Creative Commons Attribution-NonCommercial 4.0. 

**TL;DR:** You can use it, modify it, share it for personal/educational stuff, just give me credit. Want to use it commercially? [Drop me a line](mailto:dan@santee.ws) and we can work something out.

Full license: https://creativecommons.org/licenses/by-nc/4.0/

Pull requests welcome!




