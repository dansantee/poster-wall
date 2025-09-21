#!/usr/bin/env bash
# setup-poster-wall.sh
# One-shot installer for the "poster-wall" Raspberry Pi kiosk (Pi 5, OS Lite).
# - Installs minimal deps (Chromium + Wayland cage, Python venv)
# - Creates user systemd services (proxy, web, kiosk)
# - Enables seatd for GPU/DRM access, adds user to required groups
# - Applies safe boot tweaks (no screen blanking, GPU mem)
# - Starts services immediately and enables at boot
#
# Usage: run from repo root that contains ./proxy and ./web
#   chmod +x setup-poster-wall.sh
#   ./setup-poster-wall.sh
#
# Re-run any time; it's idempotent.

set -euo pipefail

# -------- Config (change if you must; keep ports aligned with your app) --------
WEB_PORT="8088"
# Your Flask app should default to port 8811 (as in the example app.py)
PROXY_PORT="8811"

# Chromium flags (keep Wayland + kiosk lean)
CHROMIUM_FLAGS=(
  "--kiosk"
  "--noerrdialogs"
  "--disable-infobars"
  "--disable-session-crashed-bubble"
  "--check-for-update-interval=31536000"
  "--ozone-platform=wayland"
  "--autoplay-policy=no-user-gesture-required"
  "--password-store=basic"
)

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
KIOSK_SERVICE="$SYSTEMD_USER_DIR/poster-kiosk.service"

BROWSER_BIN=""
SEATD_SERVICE="seatd.service"

# -------- Sanity checks --------
echo "==> Poster Wall Kiosk Installer"

if [[ ! -d "$PROXY_DIR" || ! -d "$WEB_DIR" ]]; then
  echo "ERROR: Expected ./proxy and ./web subfolders in repo root: $REPO_DIR"
  exit 1
fi

# Ensure running on Raspberry Pi OS (not strictly required, but helpful)
if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  echo "OS: $PRETTY_NAME"
else
  echo "WARN: /etc/os-release missing; proceeding anyway."
fi

# -------- APT packages (non-interactive) --------
echo "==> Installing packages (sudo required)..."
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y

# Chromium package name differs across builds; try both.
if ! command -v chromium-browser >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1; then
  sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium
fi

sudo apt-get install -y \
  python3-venv python3-pip \
  cage \
  fonts-dejavu fonts-liberation \
  seatd

# -------- Choose Chromium path --------
if command -v chromium-browser >/dev/null 2>&1; then
  BROWSER_BIN="$(command -v chromium-browser)"
elif command -v chromium >/dev/null 2>&1; then
  BROWSER_BIN="$(command -v chromium)"
else
  echo "ERROR: Chromium not found after install."
  exit 1
fi
echo "Chromium: $BROWSER_BIN"

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
sudo systemctl enable --now "$SEATD_SERVICE" >/dev/null 2>&1 || true

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

# Kiosk (Wayland cage + Chromium)
# Use tty1; user services work thanks to lingering + seatd. XDG_RUNTIME_DIR is provided by user systemd.
CHROMIUM_URL="http://localhost:$WEB_PORT"
CHROMIUM_ARGS="${CHROMIUM_FLAGS[*]} $CHROMIUM_URL"

cat >"$KIOSK_SERVICE" <<EOF
[Unit]
Description=Wayland Kiosk for PosterWall
After=network-online.target poster-web.service
Wants=poster-web.service

[Service]
Type=simple
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal
StandardError=journal
# Launch cage (tiny Wayland compositor) and run Chromium in kiosk
ExecStart=/bin/sh -lc 'cage -s -- "$BROWSER_BIN" $CHROMIUM_ARGS'
Environment=BROWSER_BIN=$BROWSER_BIN
Environment=CHROMIUM_ARGS=$CHROMIUM_ARGS
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

# -------- Enable lingering & start services --------
echo "==> Enabling user lingering so services run at boot..."
sudo loginctl enable-linger "$USER_NAME" >/dev/null || true

echo "==> Reloading user systemd, enabling & starting services..."
systemctl --user daemon-reload
systemctl --user enable poster-proxy.service poster-web.service poster-kiosk.service >/dev/null
systemctl --user restart poster-proxy.service poster-web.service poster-kiosk.service

# -------- Boot/file tweaks (safe, idempotent) --------
# Detect boot dir (Bookworm: /boot/firmware; some images: /boot)
BOOT_FW="/boot/firmware"
[ -e /boot/cmdline.txt ] && BOOT_FW="/boot"

CMDLINE="$BOOT_FW/cmdline.txt"
CONFIGTXT="$BOOT_FW/config.txt"

if [[ -e "$CMDLINE" && -e "$CONFIGTXT" ]]; then
  echo "==> Applying boot tweaks (consoleblank=0, gpu_mem=256, hdmi_force_hotplug=1)..."

  sudo cp "$CMDLINE"   "$CMDLINE.bak.$(date +%s)"
  sudo cp "$CONFIGTXT" "$CONFIGTXT.bak.$(date +%s)"

  if ! grep -qw "consoleblank=0" "$CMDLINE"; then
    sudo sed -i 's/$/ consoleblank=0/' "$CMDLINE"
  fi

  # Replace any existing gpu_mem= line, then append ours
  sudo sed -i '/^gpu_mem=/d' "$CONFIGTXT"
  echo "gpu_mem=256" | sudo tee -a "$CONFIGTXT" >/dev/null

  # Add hdmi_force_hotplug=1 if not present
  grep -q '^hdmi_force_hotplug=' "$CONFIGTXT" || \
    echo "hdmi_force_hotplug=1" | sudo tee -a "$CONFIGTXT" >/dev/null
else
  echo "WARN: couldn't find cmdline.txt/config.txt under /boot or /boot/firmware. Skipping boot tweaks."
fi

# -------- Finish --------
echo
echo "✅ Poster Wall kiosk services are running."
echo "   - Web:      http://localhost:$WEB_PORT"
echo "   - Chromium: should be in kiosk showing the site."
echo
echo "Useful commands:"
echo "  systemctl --user status poster-proxy.service"
echo "  systemctl --user status poster-web.service"
echo "  systemctl --user status poster-kiosk.service"
echo "  journalctl --user -u poster-kiosk.service -e -f"
echo
if $NEED_RELOGIN; then
  echo "NOTE: You were added to video/render/input groups. A reboot is recommended."
fi
echo "If the screen ever blanks after boot tweaks, verify /boot/firmware edits and GPU memory."
echo "Done."
