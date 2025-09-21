## Setup:

## RPI5 4GB is what I used, others might work but untested
# Install RPI OS lite 64-bit (currently Bookworm)
In advanced settings, choose:
    Set Hostname
    Set username and password
    Configure wireless LAN
    Set locale settings
    Enable SSH

## Once the pi is up and running
# Update packages
sudo apt-get update && \
    sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a \
    apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confnew" \
    full-upgrade

# Install Git
sudo apt install git -y

# Optional - auth to github
ssh-keygen -t ed25519 -C "your-email@example.com" -f ~/.ssh/id_ed25519
(don't enter passkey)
cat ~/.ssh/id_ed25519.pub
Then on GitHub: Settings → SSH and GPG keys → New SSH key → paste → Save.
git clone git@github.com:dansantee/poster-wall.git

# Run setup
cd ~/poster-wall
chmod +x setup.sh
./setup.sh --fbcon-rotate 1 --session-rotate auto --cursor hide

# Setup kiosk
In Plex, go to a movie or show and click on ... -> Get Info. Click on the View XML link. At the end
of the URL, there will be the plex token: &X-Plex-Token=ABCDE-12345, just everything after the equals is the value for the token box.

Use http://<local plex server ip>:32400 (or your port if different).

## Extras
sudo poster-hide-cursor    # hide (transparent)
sudo poster-show-cursor    # show (Adwaita or system default)

## Dev stuff
# Proxy

cd <unzipped>\poster-wall\proxy
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install flask requests
$env:PLEX_URL="http://192.168.1.5:32400"
$env:PLEX_TOKEN="R9TBSeRe-g6yWqtj5p2s"
$env:SECTION_ID="1"
python app.py

# Web app

cd <unzipped>\poster-wall\web
python -m http.server 8088




