# poster-wall

A simple Raspberry Pi kiosk for displaying posters and media (used with Plex in this project).

## Requirements

- Raspberry Pi 5 (4GB tested). Other models may work but are untested.
- Raspberry Pi OS Lite 64-bit (Bookworm recommended).
- SSH enabled and network access.

## Quickstart

1. Flash Raspberry Pi OS Lite (64-bit) to your SD card.
2. In "Advanced options" (on initial setup) configure:
   - Hostname
   - Username and password
   - Wireless LAN
   - Locale settings
   - Enable SSH
3. Boot the Pi and SSH in.

## Setup (on the Pi)

Update packages:

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

(Optional) Add an SSH key for GitHub:

```bash
ssh-keygen -t ed25519 -C "your-email@example.com" -f ~/.ssh/id_ed25519
# don't enter a passphrase when prompted
cat ~/.ssh/id_ed25519.pub
```
Then add the printed key to GitHub: Settings → SSH and GPG keys → New SSH key.

Clone the project:

```bash
git clone git@github.com:dansantee/poster-wall.git
cd poster-wall
```

Run the setup script:

```bash
chmod +x setup.sh
./setup.sh --rotate 1
```

## Kiosk / Plex configuration

To configure Plex access for the kiosk, find your Plex token:

1. In Plex, open the item (movie or show) and click the "..." → "Get Info".
2. Click "View XML".
3. In the URL look for `X-Plex-Token=...` — the value after `=` is your token.

Use the Plex base URL like `http://<local-plex-ip>:32400` (adjust port if custom).

## Extras

- Hide cursor (transparent): `sudo poster-hide-cursor`
- Show cursor (Adwaita / system default): `sudo poster-show-cursor`

## Development

Proxy (example local dev proxy):

```bash
cd proxy
python -m venv .venv
# On Windows PowerShell:
# .\.venv\Scripts\Activate.ps1
pip install flask requests
# Set environment variables and run:
export PLEX_URL="http://192.168.1.5:32400"
export PLEX_TOKEN="R9TBSeRe-g6yWqtj5p2s"
export SECTION_ID="1"
python app.py
```

Web app (static dev server):

```bash
cd web
python -m http.server 8088
```

## Notes

- The README documents tested steps on an RPi5 — adapt as needed for other hardware.
- If you experience display or rotation issues, try adjusting the `--fbcon-rotate` and `--session-rotate` options when running `setup.sh`.

## License & Contributing




