#!/usr/bin/env bash
# setup.sh
# One-shot installer for the "poster-wall" Raspberry Pi kiosk (Pi 5, OS Lite).
# - Installs minimal deps (Sway compositor, Chromium, Python venv)
# - Creates user systemd services (proxy, web, kiosk)
# - Enables seatd for GPU/DRM access, adds user to required groups
# - Applies safe boot tweaks (no screen blanking, GPU mem, fbcon rotation)
# - Starts services immediately and enables at boot
#
# Usage (from repo root containing ./proxy and ./web):
#   chmod +x setup.sh
#   ./setup.sh --rotate 90
#
# Re-run any time; it's idempotent.

set -euo pipefail

# -------- CLI args --------
# --rotate <deg> controls BOTH:
#   - fbcon rotation (as rotate:N where N = deg/90 mod 4)
#   - sway output transform (deg)
ROTATE_DEG=90   # default 90° (portrait)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rotate)
      shift
      if [[ $# -gt 0 ]]; then
        ROTATE_DEG="$1"; shift
      else
        echo "Error: --rotate requires a value (0|90|180|270)"
        exit 1
      fi
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: $0 [--rotate 0|90|180|270]"
      exit 1
      ;;
  esac
done

# Normalize ROTATE_DEG to 0,90,180,270
norm=$(( (ROTATE_DEG % 360 + 360) % 360 ))
case "$norm" in
  0|90|180|270) SWAY_ROTATE_DEG="$norm" ;;
  *)
    echo "Error: --rotate must be 0, 90, 180, or 270"
    exit 1
    ;;
esac
# fbcon rotate index (0..3)
FBCON_ROTATE=$(( SWAY_ROTATE_DEG / 90 ))

# -------- Config --------
WEB_PORT="8088"
PROXY_PORT="8811"

# Chromium flags (Wayland + kiosk, lean)
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

SEATD_SERVICE="seatd.service"

# -------- Sanity checks --------
echo "==> Poster Wall Installer (Sway kiosk)"

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

# Chromium package name differs across builds; try both.
if ! command -v chromium-browser >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1; then
  sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium
fi

sudo apt-get install -y \
  python3-venv python3-pip \
  sway wayland-protocols \
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

# Kiosk (Sway + Chromium)
mkdir -p "$USER_HOME/.config/sway"
cat >"$USER_HOME/.config/sway/config" <<EOF
# Poster Wall Kiosk (Sway)

# Rotate HDMI output to match CLI flag
output HDMI-A-1 transform $SWAY_ROTATE_DEG

# Hide the pointer immediately (compositor-level)
seat * hide_cursor 1

# Launch Chromium fullscreen to the local site
exec $BROWSER_BIN ${CHROMIUM_FLAGS[*]} http://localhost:$WEB_PORT
EOF

cat >"$KIOSK_SERVICE" <<EOF
[Unit]
Description=Poster Wall Kiosk (Sway + Chromium)
After=network-online.target poster-web.service
Wants=poster-web.service

[Service]
Type=simple
Environment=XDG_RUNTIME_DIR=%t
ExecStart=/usr/bin/sway -c %h/.config/sway/config
Restart=always
RestartSec=2
StandardOutput=journal
StandardError=journal

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

# -------- Boot/file tweaks --------
BOOT_FW="/boot/firmware"
CMDLINE="$BOOT_FW/cmdline.txt"
CONFIGTXT="$BOOT_FW/config.txt"

# Use -e (exists) instead of -w (writable), always write with sudo
if [[ -e "$CMDLINE" && -e "$CONFIGTXT" ]]; then
  echo "==> Applying boot tweaks (consoleblank=0, gpu_mem=256, hdmi_force_hotplug=1, fbcon=rotate:$FBCON_ROTATE)..."

  # Ensure consoleblank=0 present
  if ! grep -qw "consoleblank=0" "$CMDLINE"; then
    sudo cp "$CMDLINE" "$CMDLINE.bak.$(date +%s)"
    sudo sed -i 's/$/ consoleblank=0/' "$CMDLINE"
  fi

  # Ensure gpu_mem and hdmi_force_hotplug in config.txt
  sudo cp "$CONFIGTXT" "$CONFIGTXT.bak.$(date +%s)"
  grep -q "^gpu_mem=" "$CONFIGTXT" || echo "gpu_mem=256" | sudo tee -a "$CONFIGTXT" >/dev/null
  grep -q "^hdmi_force_hotplug=" "$CONFIGTXT" || echo "hdmi_force_hotplug=1" | sudo tee -a "$CONFIGTXT" >/dev/null

  # --- fbcon rotation (ensure exactly one entry) ---
  desired="fbcon=rotate:$FBCON_ROTATE"
  sudo cp "$CMDLINE" "$CMDLINE.bak.$(date +%s)"
  current="$(sudo cat "$CMDLINE")"
  cleaned="$(printf '%s\n' "$current" \
    | sed -E 's/(^| )fbcon=rotate:[0-3]( |$)/ /g' \
    | tr -s ' ' \
    | sed 's/[[:space:]]$//')"
  printf '%s %s\n' "$cleaned" "$desired" | sudo tee "$CMDLINE" >/dev/null

else
  echo "WARN: Cannot find $CMDLINE or $CONFIGTXT. Skipping boot tweaks."
fi

# -------- Finish --------
echo
echo "✅ Poster Wall kiosk (Sway + Chromium) is running."
echo "   - Web:      http://localhost:$WEB_PORT"
echo "   - Rotation: Sway ${SWAY_ROTATE_DEG}°, fbcon rotate:${FBCON_ROTATE}"
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
