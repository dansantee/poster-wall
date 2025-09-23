#!/usr/bin/env bash
# setup.sh
# One-shot installer for the "poster-wall" Raspberry Pi kiosk (Pi 5, OS Lite).
# - Installs minimal deps (Wayland cage, Python venv)
# - Creates user systemd services (proxy, web)
# - Enables seatd for GPU/DRM access, adds user to required groups
# - Applies safe boot tweaks (no screen blanking, GPU mem, fbcon rotation)
# - Starts services immediately and enables at boot
#
# Usage: run from repo root that contains ./proxy and ./web
#   chmod +x setup.sh
#   ./setup.sh --rotate 1
#
# Re-run any time; it's idempotent.

set -euo pipefail

# -------- CLI args --------
FBCON_ROTATE=1   # default 90° clockwise
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rotate)
      shift
      if [[ $# -gt 0 ]]; then
        FBCON_ROTATE="$1"
        shift
      else
        echo "Error: --rotate requires a value (0|1|2|3)"
        exit 1
      fi
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: $0 [--rotate 0|1|2|3]"
      exit 1
      ;;
  esac
done

# -------- Config --------
WEB_PORT="8088"
PROXY_PORT="8811"

# -------- Derived paths --------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
USER_NAME="$(id -un)"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
USER_ID="$(id -u)"
SYSTEMD_USER_DIR="$USER_HOME/.config/systemd/user"
VENV_DIR="$REPO_DIR/.venv"

PROXY_DIR="$REPO_DIR/proxy"
WEB_DIR="$REPO_DIR/web"

PROXY_SERVICE="$SYSTEMD_USER_DIR/poster-proxy.service"
WEB_SERVICE="$SYSTEMD_USER_DIR/poster-web.service"

SEATD_SERVICE="seatd.service"

# -------- Sanity checks --------
echo "==> Poster Wall Installer (no Chromium)"

if [[ ! -d "$PROXY_DIR" || ! -d "$WEB_DIR" ]]; then
  echo "ERROR: Expected ./proxy and ./web subfolders in repo root: $REPO_DIR"
  exit 1
fi

if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  echo "OS: $PRETTY_NAME"
else
  echo "WARN: /etc/os-release missing; proceeding anyway."
fi

# -------- APT packages --------
echo "==> Installing packages (sudo required)..."
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y

sudo apt-get install -y \
  python3-venv python3-pip \
  cage \
  fonts-dejavu fonts-liberation \
  seatd

# -------- Groups & seatd --------
echo "==> Ensuring user is in video/render/input groups (for DRM/input access)..."
NEED_RELOGIN=false
for g in video render input; do
  if ! id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx "$g"; then
    echo " - adding $USER_NAME to $g"
    sudo usermod -aG "$g" "$USER_NAME"
    NEED_RELOGIN=true
  fi
done

echo "==> Enabling seatd (DRM broker) ..."
sudo systemctl enable --now "$SEATD_SERVICE" >/dev/null || true

# -------- Python venv & deps --------
echo "==> Setting up Python venv..."
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip >/dev/null
pip install flask requests requests-cache pillow >/dev/null
deactivate

# -------- systemd user services --------
echo "==> Creating systemd user services..."
mkdir -p "$SYSTEMD_USER_DIR"

# Proxy service
cat >"$PROXY_SERVICE" <<EOF
[Unit]
Description=PosterWall Flask Proxy
After=network-online.target

[Service]
ExecStart=$VENV_DIR/bin/python $PROXY_DIR/app.py
WorkingDirectory=$PROXY_DIR
Restart=on-failure
Environment=PYTHONUNBUFFERED=1
# Optionally pass PORT to your app if it supports env config:
# Environment=PORT=$PROXY_PORT

[Install]
WantedBy=default.target
EOF

# Web (static) service
cat >"$WEB_SERVICE" <<EOF
[Unit]
Description=PosterWall Static Web (http.server :$WEB_PORT)
After=poster-proxy.service

[Service]
ExecStart=/usr/bin/python3 -m http.server $WEB_PORT
WorkingDirectory=$WEB_DIR
Restart=on-failure

[Install]
WantedBy=default.target
EOF

# -------- Enable lingering & start services --------
echo "==> Enabling user lingering so services run at boot..."
sudo loginctl enable-linger "$USER_NAME" >/dev/null || true

echo "==> Reloading user systemd, enabling & starting services..."
systemctl --user daemon-reload
systemctl --user enable poster-proxy.service poster-web.service >/dev/null
systemctl --user restart poster-proxy.service poster-web.service

# -------- Boot/file tweaks --------
BOOT_FW="/boot/firmware"
CMDLINE="$BOOT_FW/cmdline.txt"
CONFIGTXT="$BOOT_FW/config.txt"

if [[ -w "$CMDLINE" && -w "$CONFIGTXT" ]]; then
  echo "==> Applying boot tweaks (consoleblank=0, gpu_mem=256, hdmi_force_hotplug=1, fbcon=rotate:$FBCON_ROTATE)..."
  if ! grep -qw "consoleblank=0" "$CMDLINE"; then
    sudo cp "$CMDLINE" "$CMDLINE.bak.$(date +%s)"
    sudo sed -i 's/$/ consoleblank=0/' "$CMDLINE"
  fi
  sudo cp "$CONFIGTXT" "$CONFIGTXT.bak.$(date +%s)"
  grep -q "^gpu_mem=" "$CONFIGTXT" || echo "gpu_mem=256" | sudo tee -a "$CONFIGTXT" >/dev/null
  grep -q "^hdmi_force_hotplug=" "$CONFIGTXT" || echo "hdmi_force_hotplug=1" | sudo tee -a "$CONFIGTXT" >/dev/null
  if ! grep -qw "fbcon=rotate:$FBCON_ROTATE" "$CMDLINE"; then
    sudo cp "$CMDLINE" "$CMDLINE.bak.$(date +%s)"
    sudo sed -i "s/\$/ fbcon=rotate:$FBCON_ROTATE/" "$CMDLINE"
  fi
else
  echo "WARN: Cannot edit $CMDLINE / $CONFIGTXT. Skipping boot tweaks."
fi

# -------- Finish --------
echo
echo "✅ Poster Wall backend services are running."
echo "   - Web:      http://localhost:$WEB_PORT"
echo
echo "Useful commands:"
echo "  systemctl --user status poster-proxy.service"
echo "  systemctl --user status poster-web.service"
echo
if $NEED_RELOGIN; then
  echo "NOTE: You were added to video/render/input groups. A reboot is recommended."
fi
echo "If the screen ever blanks after boot tweaks, verify /boot/firmware edits and GPU memory."
echo "Done."
