#!/usr/bin/env bash
# setup-poster-wall.sh
# One-shot installer for the "poster-wall" Raspberry Pi kiosk (Pi 5, OS Lite).
# This version resets to Cage + Chromium kiosk (cursor visible), no Weston.
# - Installs deps (Chromium, cage, seatd, Python venv)
# - Creates user systemd services (proxy, web, kiosk)
# - Cleans up conflicting Weston/system kiosk or user overrides
# - Applies safe boot tweaks (no screen blanking, GPU mem, HDMI hotplug)
# - Starts services now and at boot
#
# Usage:
#   chmod +x setup-poster-wall.sh
#   ./setup-poster-wall.sh
#
# Re-run anytime; it's idempotent.

set -euo pipefail

# -------- Config --------
WEB_PORT="8088"
PROXY_PORT="8811"

# Chromium flags tuned for kiosk under Wayland/cage
CHROMIUM_FLAGS=(
  "--no-first-run"
  "--no-default-browser-check"
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
SYSTEMD_USER_DIR="$USER_HOME/.config/systemd/user"
VENV_DIR="$REPO_DIR/.venv"

PROXY_DIR="$REPO_DIR/proxy"
WEB_DIR="$REPO_DIR/web"

PROXY_SERVICE="$SYSTEMD_USER_DIR/poster-proxy.service"
WEB_SERVICE="$SYSTEMD_USER_DIR/poster-web.service"
KIOSK_SERVICE="$SYSTEMD_USER_DIR/poster-kiosk.service"

CHROMIUM_BIN=""
SEATD_SERVICE="seatd.service"
CHROMIUM_URL="http://localhost:${WEB_PORT}"

echo "==> Poster Wall Kiosk Installer (Cage + Chromium)"

# -------- Sanity checks --------
if [[ ! -d "$PROXY_DIR" || ! -d "$WEB_DIR" ]]; then
  echo "ERROR: Expected ./proxy and ./web subfolders in repo root: $REPO_DIR"
  exit 1
fi

if [[ -f /etc/os-release ]]; then . /etc/os-release; echo "OS: $PRETTY_NAME"; fi

# -------- Clean up conflicting bits (safe if absent) --------
echo "==> Cleaning up any conflicting kiosk units or overrides..."
# Disable/remove a system-level Weston kiosk if present
if systemctl list-unit-files | grep -q '^poster-kiosk.service'; then
  sudo systemctl disable --now poster-kiosk.service || true
  # Only remove if it's in /etc/systemd/system (our Weston unit location)
  [[ -f /etc/systemd/system/poster-kiosk.service ]] && sudo rm -f /etc/systemd/system/poster-kiosk.service
  sudo systemctl daemon-reload || true
fi
# Remove user drop-in override (we'll write a clean unit)
rm -rf "$SYSTEMD_USER_DIR/poster-kiosk.service.d" 2>/dev/null || true
# Revert any previous hwdb that tried to hide the cursor
if [[ -f /etc/udev/hwdb.d/99-hide-hdmi-pointer.hwdb ]]; then
  echo " - Removing /etc/udev/hwdb.d/99-hide-hdmi-pointer.hwdb"
  sudo rm -f /etc/udev/hwdb.d/99-hide-hdmi-pointer.hwdb
  sudo systemd-hwdb update || true
  sudo udevadm control --reload || true
  sudo udevadm trigger || true
fi

# -------- APT packages --------
echo "==> Installing packages (sudo required)..."
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y

# Chromium may be chromium-browser or chromium
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
  CHROMIUM_BIN="$(command -v chromium-browser)"
elif command -v chromium >/dev/null 2>&1; then
  CHROMIUM_BIN="$(command -v chromium)"
else
  echo "ERROR: Chromium not found after install."
  exit 1
fi
echo "Chromium: $CHROMIUM_BIN"

# -------- Groups & seatd --------
echo "==> Ensuring user is in video/render/input groups..."
NEED_RELOGIN=false
for g in video render input; do
  if ! id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx "$g"; then
    echo " - adding $USER_NAME to $g"
    sudo usermod -aG "$g" "$USER_NAME"
    NEED_RELOGIN=true
  fi
done

echo "==> Enabling seatd ..."
sudo systemctl enable --now "$SEATD_SERVICE" >/dev/null 2>&1 || true

# -------- Python venv & deps --------
echo "==> Setting up Python venv..."
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip >/dev/null
pip install flask requests >/dev/null   # requests-cache/pillow optional
deactivate

# -------- systemd user services --------
echo "==> Creating systemd user services..."
mkdir -p "$SYSTEMD_USER_DIR"

# Proxy service (Flask app on :8811; working dir = proxy/)
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

# Web (static) service on :8088; working dir = web/
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

# Kiosk: Cage + Chromium → http://localhost:$WEB_PORT
# NOTE: No TTYPath/StandardInput=tty (fixes 208/STDIN); add small delay
KIOSK_CMD="cage -s -- \"$CHROMIUM_BIN\" ${CHROMIUM_FLAGS[*]} \"$CHROMIUM_URL\""
cat >"$KIOSK_SERVICE" <<EOF
[Unit]
Description=Wayland Kiosk for PosterWall (cage + Chromium)
After=network-online.target poster-web.service
Wants=poster-web.service

[Service]
Type=simple
StandardOutput=journal
StandardError=journal
ExecStartPre=/bin/sleep 2
ExecStart=/bin/sh -lc '$KIOSK_CMD'
StandardInput=null
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
BOOT_FW="/boot/firmware"; [ -e /boot/cmdline.txt ] && BOOT_FW="/boot"
CMDLINE="$BOOT_FW/cmdline.txt"; CONFIGTXT="$BOOT_FW/config.txt"

if [[ -e "$CMDLINE" && -e "$CONFIGTXT" ]]; then
  echo "==> Applying boot tweaks (consoleblank=0, gpu_mem=256, hdmi_force_hotplug=1)..."
  sudo cp "$CMDLINE"   "$CMDLINE.bak.$(date +%s)"
  sudo cp "$CONFIGTXT" "$CONFIGTXT.bak.$(date +%s)"

  grep -qw "consoleblank=0" "$CMDLINE" || sudo sed -i 's/$/ consoleblank=0/' "$CMDLINE"
  sudo sed -i '/^gpu_mem=/d' "$CONFIGTXT"
  echo "gpu_mem=256" | sudo tee -a "$CONFIGTXT" >/dev/null
  grep -q '^hdmi_force_hotplug=' "$CONFIGTXT" || echo "hdmi_force_hotplug=1" | sudo tee -a "$CONFIGTXT" >/dev/null
else
  echo "WARN: couldn't find cmdline.txt/config.txt; skipping boot tweaks."
fi

# -------- Finish --------
echo
echo "✅ Poster Wall kiosk (cage + Chromium) is running."
echo "   - Web:      http://localhost:$WEB_PORT"
echo "   - Proxy:    http://localhost:$PROXY_PORT"
echo
echo "Useful commands:"
echo "  systemctl --user status poster-proxy.service"
echo "  systemctl --user status poster-web.service"
echo "  systemctl --user status poster-kiosk.service"
echo "  journalctl --user -u poster-kiosk.service -e -f"
echo
$NEED_RELOGIN && echo "NOTE: You were added to video/render/input groups. Reboot recommended."
echo "Done."
