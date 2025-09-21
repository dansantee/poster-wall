#!/usr/bin/env bash
# Poster Wall kiosk installer (Pi 5, Raspberry Pi OS Bookworm)
# - Cage + Chromium kiosk
# - Proxy (Flask) + static web server
# - Transparent cursor theme (hidden by default; toggle with scripts)
# - Robust Wayland rotation (oneshot + path)
# - Optional console (fbcon) rotation, resolution-agnostic
set -euo pipefail

### ---------- Defaults (can be overridden by CLI flags) ----------
WEB_PORT=${WEB_PORT:-8088}
PROXY_PORT=${PROXY_PORT:-8811}

# Rotation:
# fbcon rotate: 0=0°, 1=90°, 2=180°, 3=270°. Empty = don't touch boot TTY.
FBCON_ROTATE=${FBCON_ROTATE:-}          # e.g. 1 or 3
# session rotation: auto|0|90|180|270 (auto = follow fbcon if set, else 0°)
SESSION_ROTATE=${SESSION_ROTATE:-auto}
# Wayland output to rotate: empty = rotate all connected (recommended)
SESSION_OUTPUT=${SESSION_OUTPUT:-}      # e.g. HDMI-A-1

# Cursor hidden by default
CURSOR_MODE=${CURSOR_MODE:-hide}        # hide|show

### ---------- CLI flags ----------
usage() {
  cat <<EOF
Usage: $0 [--fbcon-rotate {0|1|2|3}] [--session-rotate {auto|0|90|180|270}]
          [--session-output HDMI-A-1] [--cursor {hide|show}]
Examples:
  $0 --fbcon-rotate 1 --session-rotate auto --cursor hide
  $0 --session-rotate 270 --cursor hide
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fbcon-rotate)       FBCON_ROTATE="${2:?}"; shift 2;;
    --session-rotate)     SESSION_ROTATE="${2:?}"; shift 2;;
    --session-output)     SESSION_OUTPUT="${2:?}"; shift 2;;
    --cursor)             CURSOR_MODE="${2:?}"; shift 2;;
    -h|--help)            usage; exit 0;;
    *) echo "Unknown arg: $1"; usage; exit 1;;
  esac
done

### ---------- Paths & basics ----------
say(){ echo -e "\033[1;36m==>\033[0m $*"; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
USER_NAME="$(id -un)"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
SYSTEMD_USER_DIR="$USER_HOME/.config/systemd/user"
DROPIN_DIR="$SYSTEMD_USER_DIR/poster-kiosk.service.d"
VENV_DIR="$REPO_DIR/.venv"
PROXY_DIR="$REPO_DIR/proxy"
WEB_DIR="$REPO_DIR/web"
PROXY_SERVICE="$SYSTEMD_USER_DIR/poster-proxy.service"
WEB_SERVICE="$SYSTEMD_USER_DIR/poster-web.service"
KIOSK_SERVICE="$SYSTEMD_USER_DIR/poster-kiosk.service"
ROTATE_SERVICE="$SYSTEMD_USER_DIR/poster-rotate.service"
ROTATE_PATH="$SYSTEMD_USER_DIR/poster-rotate.path"
CHROMIUM_BIN=""

[[ -f /etc/os-release ]] && . /etc/os-release && say "OS: $PRETTY_NAME"

if [[ ! -d "$PROXY_DIR" || ! -d "$WEB_DIR" ]]; then
  echo "ERROR: expected ./proxy and ./web under $REPO_DIR"; exit 1
fi

### ---------- Packages ----------
say "Installing deps (sudo)…"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y
# chromium can be chromium-browser or chromium
if ! command -v chromium-browser >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1; then
  sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium
fi
sudo apt-get install -y python3-venv python3-pip cage seatd x11-apps imagemagick

# Chromium path
if command -v chromium-browser >/dev/null 2>&1; then
  CHROMIUM_BIN="$(command -v chromium-browser)"
else
  CHROMIUM_BIN="$(command -v chromium)"
fi
say "Chromium: $CHROMIUM_BIN"

### ---------- Groups & seatd ----------
say "Ensuring user in video/render/input…"
NEED_REBOOT=false
for g in video render input; do
  if ! id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx "$g"; then
    sudo usermod -aG "$g" "$USER_NAME"
    NEED_REBOOT=true
  fi
done
say "Enabling seatd…"
sudo systemctl enable --now seatd.service >/dev/null 2>&1 || true

### ---------- Python venv ----------
say "Setting up Python venv…"
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip >/dev/null
pip install flask requests requests-cache pillow >/dev/null
deactivate

### ---------- Transparent cursor theme (no system-wide nuke) ----------
say "Installing transparent cursor theme…"
ensure_transparent_theme() {
  local tmp; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
  sudo rm -f /usr/share/icons/transparent/cursors || true
  sudo mkdir -p /usr/share/icons/transparent/cursors
  convert -size 1x1 xc:none "$tmp/transparent.png"
  cat >"$tmp/left_ptr.in" <<'EOF'
16 1 1 transparent.png 0 0
24 1 1 transparent.png 0 0
32 1 1 transparent.png 0 0
48 1 1 transparent.png 0 0
64 1 1 transparent.png 0 0
EOF
  xcursorgen "$tmp/left_ptr.in" "$tmp/left_ptr"
  sudo install -m 0644 "$tmp/left_ptr" /usr/share/icons/transparent/cursors/left_ptr
  for n in default arrow top_left_arrow hand1 hand2 hand pointer \
           watch left_ptr_watch progress wait xterm text ibeam cell crosshair \
           all-scroll not-allowed no-drop context-menu help question_arrow \
           grab grabbing fleur move \
           n-resize s-resize e-resize w-resize ne-resize nw-resize se-resize sw-resize \
           ns-resize ew-resize nesw-resize nwse-resize col-resize row-resize \
           zoom-in zoom-out alias copy link; do
    sudo ln -sfn left_ptr "/usr/share/icons/transparent/cursors/$n"
  done
}
ensure_transparent_theme

### ---------- systemd user services ----------
say "Writing systemd user services…"
mkdir -p "$SYSTEMD_USER_DIR"

# Proxy
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

# Static web
cat >"$WEB_SERVICE" <<EOF
[Unit]
Description=PosterWall Static Web (:${WEB_PORT})
After=poster-proxy.service

[Service]
ExecStart=/usr/bin/python3 -m http.server ${WEB_PORT}
WorkingDirectory=$WEB_DIR
Restart=on-failure

[Install]
WantedBy=default.target
EOF

# Kiosk (Cage + Chromium) – minimal, stable
KIOSK_CMD="cage -s -- \"$CHROMIUM_BIN\" --no-first-run --no-default-browser-check --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000 --ozone-platform=wayland --overscroll-history-navigation=0 --disable-pinch \"http://localhost:${WEB_PORT}\""
cat >"$KIOSK_SERVICE" <<EOF
[Unit]
Description=Wayland Kiosk for PosterWall (cage + Chromium)
After=network-online.target poster-web.service
Wants=poster-web.service

[Service]
Type=simple
ExecStartPre=/bin/sleep 2
ExecStart=/bin/sh -lc '$KIOSK_CMD'
StandardInput=null
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

# Cursor drop-in
mkdir -p "$DROPIN_DIR"
if [[ "$CURSOR_MODE" == "hide" ]]; then
cat >"$DROPIN_DIR/cursor.conf" <<'EOF'
[Service]
Environment=XCURSOR_THEME=transparent
Environment=XCURSOR_PATH=/usr/share/icons/transparent:/usr/share/icons:/usr/local/share/icons
Environment=XCURSOR_SIZE=24
EOF
else
rm -f "$DROPIN_DIR/cursor.conf" 2>/dev/null || true
fi

### ---------- Rotation oneshot + path ----------
say "Installing rotation oneshot + path…"
sudo tee /usr/local/bin/poster-rotate-oneshot >/dev/null <<'EOF'
#!/usr/bin/env bash
# Rotate Cage outputs in the current user Wayland session, then exit 0.
# TRANSFORM: 0|90|180|270|auto ; OUTPUT: e.g. HDMI-A-1 ; default rotate all
set -euo pipefail
TR="${TRANSFORM:-auto}"
OUT="${OUTPUT:-}"
# auto -> derive from fbcon
if [[ "$TR" == "auto" || -z "$TR" ]]; then
  fb=$(grep -o 'fbcon=rotate:[0-3]' /proc/cmdline | awk -F: '{print $3}' || true)
  case "$fb" in
    1) TR=90;; 2) TR=180;; 3) TR=270;; *) TR=0;;
  esac
fi
uid="$(id -u)"; RUNDIR="${XDG_RUNTIME_DIR:-/run/user/$uid}"; mkdir -p "$RUNDIR"
# wait up to ~15s for a usable wayland socket with outputs
for i in $(seq 1 75); do
  for s in "$RUNDIR"/wayland-[0-9]*; do
    [ -S "$s" ] || continue
    sock="$(basename "$s")"
    if env WAYLAND_DISPLAY="$sock" wlr-randr >/dev/null 2>&1; then
      sleep 0.4
      if [[ -n "$OUT" ]]; then
        env WAYLAND_DISPLAY="$sock" wlr-randr --output "$OUT" --transform "$TR" || true
      else
        env WAYLAND_DISPLAY="$sock" wlr-randr | awk '$2=="connected"{print $1}' | while read -r o; do
          env WAYLAND_DISPLAY="$sock" wlr-randr --output "$o" --transform "$TR" || true
        done
      fi
      exit 0
    fi
  done
  sleep 0.2
done
exit 0
EOF
sudo chmod +x /usr/local/bin/poster-rotate-oneshot

# user oneshot service + path trigger
cat >"$ROTATE_SERVICE" <<EOF
[Unit]
Description=Rotate Cage outputs to portrait (session)

[Service]
Type=oneshot
Environment=TRANSFORM=${SESSION_ROTATE}
$( [[ -n "$SESSION_OUTPUT" ]] && echo "Environment=OUTPUT=${SESSION_OUTPUT}" )
ExecStart=/usr/local/bin/poster-rotate-oneshot
RemainAfterExit=yes
EOF

cat >"$ROTATE_PATH" <<'EOF'
[Unit]
Description=Trigger poster-rotate when Wayland socket appears

[Path]
PathExistsGlob=%t/wayland-[0-9]*
Unit=poster-rotate.service

[Install]
WantedBy=default.target
EOF

### ---------- Helper toggles ----------
say "Installing helper toggles…"
sudo tee /usr/local/bin/poster-hide-cursor >/dev/null <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
USER_NAME="${SUDO_USER:-$USER}"
DROPIN_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)/.config/systemd/user/poster-kiosk.service.d"
sudo mkdir -p /usr/share/icons/transparent/cursors
# ensure theme exists
if [ ! -f /usr/share/icons/transparent/cursors/left_ptr ]; then
  tmp="$(mktemp -d)"; convert -size 1x1 xc:none "$tmp/t.png"
  cat >"$tmp/left_ptr.in" <<'EOF'
16 1 1 t.png 0 0
24 1 1 t.png 0 0
32 1 1 t.png 0 0
48 1 1 t.png 0 0
64 1 1 t.png 0 0
EOF
  xcursorgen "$tmp/left_ptr.in" "$tmp/left_ptr"
  sudo install -m 0644 "$tmp/left_ptr" /usr/share/icons/transparent/cursors/left_ptr
fi
mkdir -p "$DROPIN_DIR"
cat >"$DROPIN_DIR/cursor.conf" <<'EOF'
[Service]
Environment=XCURSOR_THEME=transparent
Environment=XCURSOR_PATH=/usr/share/icons/transparent:/usr/share/icons:/usr/local/share/icons
Environment=XCURSOR_SIZE=24
EOF
systemctl --user daemon-reload
systemctl --user restart poster-kiosk.service
echo "Cursor hidden."
EOS
sudo chmod +x /usr/local/bin/poster-hide-cursor

sudo tee /usr/local/bin/poster-show-cursor >/dev/null <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
USER_NAME="${SUDO_USER:-$USER}"
DROPIN_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)/.config/systemd/user/poster-kiosk.service.d"
rm -f "$DROPIN_DIR/cursor.conf" || true
systemctl --user daemon-reload
systemctl --user restart poster-kiosk.service
echo "Cursor shown."
EOS
sudo chmod +x /usr/local/bin/poster-show-cursor

### ---------- Enable lingering & services ----------
say "Enabling user lingering…"
sudo loginctl enable-linger "$USER_NAME" >/dev/null || true

say "Enabling & starting services…"
systemctl --user daemon-reload
systemctl --user enable poster-proxy.service poster-web.service poster-kiosk.service poster-rotate.path poster-rotate.service >/dev/null
systemctl --user restart poster-proxy.service poster-web.service poster-kiosk.service
# start rotator once now (path will also trigger when socket appears)
systemctl --user start poster-rotate.service || true

### ---------- Optional boot console rotation (safe, resolution-agnostic) ----------
if [[ -n "${FBCON_ROTATE}" ]]; then
  say "Setting boot console rotation to fbcon=rotate:${FBCON_ROTATE}…"
  BOOT=/boot/firmware; [[ -f /boot/cmdline.txt ]] && BOOT=/boot
  sudo cp "$BOOT/cmdline.txt" "$BOOT/cmdline.txt.bak.$(date +%s)"
  sudo sed -i 's/ fbcon=rotate:[0-3]//g' "$BOOT/cmdline.txt"
  sudo sed -i "1 s/\$/ fbcon=rotate:${FBCON_ROTATE}/" "$BOOT/cmdline.txt"
  say "fbcon set. Reboot to see boot/shutdown text rotated."
fi

### ---------- Boot tweaks (optional, harmless) ----------
BOOT_FW="/boot/firmware"; [ -e /boot/cmdline.txt ] && BOOT_FW="/boot"
CMDLINE="$BOOT_FW/cmdline.txt"; CONFIGTXT="$BOOT_FW/config.txt"
if [[ -e "$CMDLINE" && -e "$CONFIGTXT" ]]; then
  say "Applying boot tweaks (consoleblank=0, gpu_mem=256, hdmi_force_hotplug=1)…"
  sudo cp "$CMDLINE"   "$CMDLINE.bak.$(date +%s)"
  sudo cp "$CONFIGTXT" "$CONFIGTXT.bak.$(date +%s)"
  grep -qw "consoleblank=0" "$CMDLINE" || sudo sed -i 's/$/ consoleblank=0/' "$CMDLINE"
  sudo sed -i '/^gpu_mem=/d' "$CONFIGTXT"
  echo "gpu_mem=256" | sudo tee -a "$CONFIGTXT" >/dev/null
  grep -q '^hdmi_force_hotplug=' "$CONFIGTXT" || echo "hdmi_force_hotplug=1" | sudo tee -a "$CONFIGTXT" >/dev/null
fi

echo
echo "✅ Poster Wall kiosk installed."
echo "   Web:   http://localhost:${WEB_PORT}"
echo "   Proxy: http://localhost:${PROXY_PORT}"
echo "   Cursor: ${CURSOR_MODE}  |  Session rotate: ${SESSION_ROTATE}  |  fbcon: ${FBCON_ROTATE:-none}"
$NEED_REBOOT && echo "NOTE: you were added to video/render/input; reboot recommended."
