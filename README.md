Setup:

RPI5 4GB:
Install RPI OS lite 64-bit (currently Bookworm)
In advanced settings, choose:
    Set Hostname
    Set username and password
    Configure wireless LAN
    Set locale settings
    Enable SSH

sudo apt update && sudo apt upgrade -y
sudo apt install git -y

ssh-keygen -t ed25519 -C "your-email@example.com" -f ~/.ssh/id_ed25519
(don't enter passkey)
cat ~/.ssh/id_ed25519.pub
Then on GitHub: Settings → SSH and GPG keys → New SSH key → paste → Save.
git clone git@github.com:dansantee/poster-wall.git

cd ~/poster-wall
chmod +x setup.sh
./setup.sh


Dev:

Proxy:

cd <unzipped>\poster-wall\proxy
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install flask requests
$env:PLEX_URL="http://192.168.1.5:32400"
$env:PLEX_TOKEN="R9TBSeRe-g6yWqtj5p2s"
$env:SECTION_ID="1"
python app.py


Web app:

cd <unzipped>\poster-wall\web
python -m http.server 8088


