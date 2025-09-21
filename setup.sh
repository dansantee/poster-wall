#!/usr/bin/env bash
# setup.sh — Poster Wall one-shot installer for Raspberry Pi 5 (Bookworm Lite)
# - Installs minimal deps (Chromium + cage Wayland compositor, Python venv)
# - Creates user systemd services (proxy, web, kiosk, rotate.path+service)
# - Enables seatd, adds user to required groups (video, render, input)
# - Sets fbcon rotation at boot; applies Wayland session rotation reliably
# - Sets transparent cursor theme and env for kiosk
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh [--web-port 8088] [--proxy-port 8811] \
#              [--fbcon-rotate 0|1|2|3] \
#              [--session-rotate auto|0|90|180|270] \
#              [--session-output HDMI-A-1] \
#              [--cursor hide|show] \
#              [--kiosk-url http://localhost:8088]
#
# Flags default to the values shown in brackets above.

set -euo pipefail

# ------------------------------- Defaults -------------------------------------
WEB_PORT="8088"
PROXY_PORT="8811"
FB_ROTATE=""                 # unset means "don't touch cmdline.txt"
SESSION_ROTATE="auto"        # auto derives from fbcon=rotate:N at boot
SESSION_OUTPUT=""            # e.g., HDMI-A-1; empty means "all connected outputs"
CURSOR_MODE="hide"           # hide|show
KIOSK_URL=""                 # default constructed later

# --------------------------- Parse CLI arguments -------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --web-port)         WEB_PORT="${2:?}"; shift 2;;
    --proxy-port)       PROXY_PORT="${2:?}"; shift 2;;
    --fbcon-rotate)     FB_ROTATE="${2:?}"; shift 2;;
    --session-rotate)   SESSION_ROTATE="${2:?}"; shift 2;;
    --session-output)   SESSION_OUTPUT="${2:?}"; shift 2;;
    --cursor)           CURSOR_MODE="${2:?}"; shift 2;;
    --kiosk-url)        KIOSK_URL="${2:?}"; shift 2;;
    -h|--help)
      sed -n '1,60p' "$0"; exit 0;;
    *)
      echo "Unknown arg: $1" >&2; exit 1;;
  esac
done

# ----------------------------- Paths & bins ------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_SYSTEMD_DIR="${HOME}/.config/systemd/user"
BIN_CHROMIUM="$(command -v chromium-browser || command -v chromium || echo /usr/bin/chromium-browser)"
BIN_CAGE="$(command -v cage || echo /usr/bin/cage)"
BIN_WLR_RANDR="$(command -v wlr-randr || echo /usr/bin/wlr-randr)"
[[ -z "${KIOSK_URL}" ]] && KIOSK_URL="http://localhost:${WEB_PORT}"

# ------------------------------- Helpers ---------------------------------------
log()  { printf "\033[1;36m[setup]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m  %s\n" "$*"; }
die()  { printf "\033[1;31m[err]\033[0m   %s\n" "$*" >&2; exit 1; }

need_sudo() {
  if ! sudo -n true 2>/dev/null; then
    log "This script will run a few commands with sudo (you may be prompted)."
  fi
}

apt_install() {
  local pkgs=("$@")
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${pkgs[@]}"
}

ensure_groups() {
  local g; for g in video render input; do
    if ! id -nG "$USER" | grep -qw "$g"; then
      sudo usermod -aG "$g" "$USER"
      log "Added $USER to group: $g (relogin not required for systemd user services)."
    fi
  done
}

enable_seatd() {
  sudo systemctl enable --now seatd.service
}

enable_linger() {
  loginctl enable-linger "$USER" || true
}

# ------------------------- Transparent cursor theme ---------------------------
ensure_transparent_theme() {
  [[ "$CURSOR_MODE" != "hide" ]] && return 0
  log "Installing transparent Xcursor theme…"
  sudo mkdir -p /usr/share/icons/transparent/cursors
  sudo mkdir -p /usr/share/icons/default

  # Write theme metadata
  sudo tee /usr/share/icons/transparent/index.theme >/dev/null <<'EOT'
[Icon Theme]
Name=transparent
Comment=Fully transparent cursors for kiosk
Inherits=
Directories=cursors
EOT
  sudo tee /usr/share/icons/default/index.theme >/dev/null <<'EOT'
[Icon Theme]
Inherits=transparent
EOT

  # Create a 1x1 transparent PNG from base64 (no ImageMagick dependency)
  sudo tee /usr/share/icons/transparent/cursors/blank.png >/dev/null <<'EOPNG'
iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/aq1p6QAAAAASUVORK5CYII=
EOPNG

  # Generate an Xcursor from the PNG (needs xcursorgen)
  sudo tee /usr/share/icons/transparent/cursors/left_ptr.in >/dev/null <<'EOT'
32 0 0 blank.png 1
24 0 0 blank.png 1
16 0 0 blank.png 1
EOT
  if ! command -v xcursorgen >/dev/null 2>&1; then
    apt_install xcursorgen
  fi
  (cd /usr/share/icons/transparent/cursors && sudo xcursorgen left_ptr.in left_ptr)

  # Symlink common cursor names to left_ptr
  local names=(right_ptr pointer hand1 hand2 xterm text watch cross crosshair sb_h_double_arrow sb_v_double_arrow
               sb_left_arrow sb_right_arrow sb_up_arrow sb_down_arrow fleur grabbing grab)
  for n in "${names[@]}"; do
    sudo ln -sf left_ptr "/usr/share/icons/transparent/cursors/$n"
  done
}

# ------------------------------- Packages -------------------------------------
install_packages() {
  log "Installing packages (Chromium, cage, seatd, Python venv, wlroots utils)…"
  apt_install \
    chromium-browser \
    cage \
    seatd \
    python3-venv python3-pip \
    wlr-randr \
    ca-certificates \
    fonts-dejavu-core
}

# ----------------------------- Kiosk service ----------------------------------
write_kiosk_service() {
  log "Writing poster-kiosk.service (Chromium in cage on tty1)…"
  mkdir -p "${USER_SYSTEMD_DIR}"
  tee "${USER_SYSTEMD_DIR}/poster-kiosk.service" >/dev/null <<EOF
[Unit]
Description=Poster Wall Kiosk (Chromium + cage, Wayland)
After=network-online.target seatd.service
Wants=network-online.target
Requires=seatd.service

[Service]
Type=simple
# Run on tty1
StandardInput=tty
TTYPath=/dev/tty1
TTYReset=yes
# Cursor env (theme installed by setup)
Environment=XCURSOR_THEME=${CURSOR_MODE/hide/transparent}
Environment=XCURSOR_SIZE=24
Environment=XCURSOR_PATH=/usr/share/icons/transparent:/usr/share/icons:/usr/local/share/icons
# Wayland ozone flags for Chromium
Environment=QT_QPA_PLATFORM=wayland
Environment=MOZ_ENABLE_WAYLAND=1
# Ensure HOME is respected for Wayland runtime dir
Environment=XDG_RUNTIME_DIR=%t
# Cage should own the seat for DRM/input
ExecStart=${BIN_CAGE} -s -- ${BIN_CHROMIUM} \\
  --enable-features=UseOzonePlatform \\
  --ozone-platform=wayland \\
  --kiosk \\
  --app=${KIOSK_URL} \\
  --no-first-run --noerrdialogs --disable-session-crashed-bubble --disable-infobars \\
  --autoplay-policy=no-user-gesture-required --overscroll-history-navigation=0 --disable-pinch
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
}

# ------------------------------- Web service ----------------------------------
write_web_service() {
  log "Writing poster-web.service (static http.server on :${WEB_PORT})…"
  tee "${USER_SYSTEMD_DIR}/poster-web.service" >/dev/null <<EOF
[Unit]
Description=Poster Wall Web (static files)
After=network.target

[Service]
Type=simple
WorkingDirectory=${REPO_ROOT}/web
ExecStart=/usr/bin/python3 -m http.server ${WEB_PORT}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF
}

# ------------------------------ Proxy service ---------------------------------
write_proxy_launcher() {
  log "Writing proxy launcher + service (Flask) on :${PROXY_PORT}…"
  sudo tee /usr/local/bin/poster-proxy-launch >/dev/null <<'EOSH'
#!/usr/bin/env bash
set -euo pipefail
PORT="${1:-8811}"
cd "$(dirname "$0")" 2>/dev/null || true
# Try to cd into repo proxy dir if installed there
if [[ -d "$HOME/poster-wall/proxy" ]]; then cd "$HOME/poster-wall/proxy"; fi
if [[ -d "./proxy" ]]; then cd "./proxy"; fi

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
if [[ -f requirements.txt ]]; then pip install -r requirements.txt; fi

# Heuristic: app.py > server.py > module "proxy"
if [[ -f app.py ]]; then
  exec python app.py --port "$PORT"
elif [[ -f server.py ]]; then
  exec python server.py --port "$PORT"
else
  # If user has FLASK_APP, respect it; otherwise try "proxy" module
  export FLASK_RUN_PORT="$PORT"
  if [[ -n "${FLASK_APP:-}" ]]; then
    exec python -m flask run --host 0.0.0.0 --port "$PORT"
  else
    exec python -m proxy
  fi
fi
EOSH
  sudo chmod +x /usr/local/bin/poster-proxy-launch
}

write_proxy_service() {
  tee "${USER_SYSTEMD_DIR}/poster-proxy.service" >/dev/null <<EOF
[Unit]
Description=Poster Wall Proxy API (Flask)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${REPO_ROOT}
ExecStart=/usr/local/bin/poster-proxy-launch ${PROXY_PORT}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF
}

# ------------------------- Rotation: oneshot + path ---------------------------
write_rotate_script() {
  log "Installing rotation script (/usr/local/bin/poster-rotate-oneshot)…"
  sudo tee /usr/local/bin/poster-rotate-oneshot >/dev/null <<'EOSH'
#!/usr/bin/env bash
set -euo pipefail
log(){ echo "[poster-rotate] $*"; }

TR="${TRANSFORM:-auto}"
OUT="${OUTPUT:-}"

# auto -> derive from fbcon rotate
if [[ "$TR" == "auto" || -z "$TR" ]]; then
  fb=$(grep -o 'fbcon=rotate:[0-3]' /proc/cmdline | awk -F: '{print $3}' || true)
  case "$fb" in
    1) TR=90;;
    2) TR=180;;
    3) TR=270;;
    *) TR=0;;
  esac
fi

uid="$(id -u)"; RUNDIR="${XDG_RUNTIME_DIR:-/run/user/$uid}"; mkdir -p "$RUNDIR"
log "target transform: ${TR}${OUT:+ on $OUT}"

# wait up to ~20s for a Wayland socket that reports outputs
for i in $(seq 1 100); do
  for s in "$RUNDIR"/wayland-[0-9]*; do
    [[ -S "$s" ]] || continue
    sock="$(basename "$s")"
    if env WAYLAND_DISPLAY="$sock" wlr-randr >/dev/null 2>&1; then
      sleep 0.5

      # apply
      if [[ -n "$OUT" ]]; then
        env WAYLAND_DISPLAY="$sock" wlr-randr --output "$OUT" --transform "$TR" || true
      else
        env WAYLAND_DISPLAY="$sock" wlr-randr | awk '$2=="connected"{print $1}' | while read -r o; do
          env WAYLAND_DISPLAY="$sock" wlr-randr --output "$o" --transform "$TR" || true
        done
      fi

      # verify: ensure at least one connected output reports desired Transform
      if env WAYLAND_DISPLAY="$sock" wlr-randr | awk -v want="$TR" '
          $2=="connected"{dev=$1}
          $1=="Transform:"{tr=$2; if (dev!=""){print dev, tr; dev=""}}
        ' | awk -v want="$TR" 'NR>0 {if ($2==want) ok=1} END{exit ok?0:1}'
      then
        log "verify OK on $sock"
        exit 0
      else
        log "verify failed on $sock, retrying…"
      fi
    fi
  done
  sleep 0.2
done

log "no usable Wayland outputs; giving up"
exit 1
EOSH
  sudo chmod +x /usr/local/bin/poster-rotate-oneshot
}

write_rotate_units() {
  log "Writing poster-rotate.service + .path (triggered by Wayland socket)…"
  tee "${USER_SYSTEMD_DIR}/poster-rotate.service" >/dev/null <<EOF
[Unit]
Description=Rotate Cage outputs to portrait (session)
After=poster-kiosk.service seatd.service
Requires=seatd.service

[Service]
Type=oneshot
Environment=TRANSFORM=${SESSION_ROTATE}
Environment=OUTPUT=${SESSION_OUTPUT}
ExecStart=/usr/local/bin/poster-rotate-oneshot
RemainAfterExit=yes

[Install]
WantedBy=default.target
EOF

  tee "${USER_SYSTEMD_DIR}/poster-rotate.path" >/dev/null <<'EOF'
[Unit]
Description=Watch for Wayland display socket; trigger rotation

[Path]
PathExistsGlob=%t/wayland-*

[Install]
WantedBy=default.target
EOF
}

# ------------------------------- fbcon rotate ---------------------------------
apply_fbcon_rotate() {
  [[ -z "${FB_ROTATE}" ]] && return 0
  local cmdline="/boot/firmware/cmdline.txt"
  log "Applying fbcon=rotate:${FB_ROTATE} to ${cmdline}…"
  if [[ -f "$cmdline" ]]; then
    sudo sed -i -E 's/fbcon=rotate:[0-3]//g' "$cmdline"
    # Ensure single line; append the arg
    echo | sudo tee -a "$cmdline" >/dev/null
    sudo sed -i 's/  \+/ /g;s/^ //;s/ $//' "$cmdline"
    sudo sed -i -E 's/$/ fbcon=rotate:'"${FB_ROTATE}"'/' "$cmdline"
  else
    warn "Cannot find ${cmdline}; skipping fbcon rotation."
  fi
}

# ------------------------------- Systemd apply --------------------------------
reload_enable_start() {
  systemctl --user daemon-reload
  systemctl --user enable --now poster-web.service
  systemctl --user enable --now poster-proxy.service
  systemctl --user enable --now poster-kiosk.service
  systemctl --user enable --now poster-rotate.path
  # Service will be triggered by the .path when Wayland socket appears
}

# --------------------------------- Main ---------------------------------------
need_sudo
install_packages
enable_seatd
ensure_groups
enable_linger
ensure_transparent_theme

write_kiosk_service
write_web_service
write_proxy_launcher
write_proxy_service

write_rotate_script
write_rotate_units

apply_fbcon_rotate
reload_enable_start

log "Done. Services:"
echo "  - poster-web.service     (static site on :${WEB_PORT})"
echo "  - poster-proxy.service   (Flask proxy on :${PROXY_PORT})"
echo "  - poster-kiosk.service   (Chromium in cage on tty1)"
echo "  - poster-rotate.path/.service (session rotation watcher)"
echo
echo "Useful checks:"
echo "  journalctl --user -u poster-rotate.service -n 120 --no-pager -o cat"
echo "  WAYLAND_DISPLAY=wayland-0 ${BIN_WLR_RANDR} | sed -n '/HDMI-A-1/,/Transform/p'"
echo "  systemctl --user status poster-kiosk.service --no-pager"
echo
[[ -n "${FB_ROTATE}" ]] && warn "fbcon rotation updated; reboot to affect boot-time console rotation."
