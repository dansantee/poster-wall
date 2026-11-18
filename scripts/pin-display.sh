#!/usr/bin/env bash
# pin-display.sh: let the kiosk boot with the TV off.
#
# The Pi 5's KMS display driver ignores config.txt's hdmi_force_hotplug, so a TV that is off at
# boot can leave the screen on the console's login prompt instead of the kiosk. This script:
#   - saves the connected TV's EDID (run it once with the TV on) to
#     /usr/lib/firmware/edid/poster-wall-<connector>.bin, and adds it to the initramfs
#     (vc4 loads from there);
#   - adds `drm.edid_firmware=<connector>:edid/...` and `video=<connector>:...D` to
#     /boot/firmware/cmdline.txt, so the connector is always on with that TV's modes;
#   - masks getty/autovt on tty1, so no login prompt can take the screen.
#
# Usage: scripts/pin-display.sh [--unpin] [CONNECTOR]     (CONNECTOR defaults to HDMI-A-1)
# Re-run any time; it only writes what changed. With the TV off and nothing saved yet, it warns
# and changes nothing. --unpin removes the cmdline entries and unmasks the getty (the saved EDID
# stays); reboot, then re-run with the new TV on to pin a different TV.
#
# PW_ROOT, PW_SUDO, PW_SYSTEMCTL and PW_UPDATE_INITRAMFS exist for the tests only.

set -euo pipefail

UNPIN=0
if [[ "${1:-}" == "--unpin" ]]; then UNPIN=1; shift; fi
CONNECTOR="${1:-HDMI-A-1}"
if [[ ! "$CONNECTOR" =~ ^[A-Za-z]+-[A-Za-z]-[0-9]+$ ]]; then
  echo "Error: connector must look like HDMI-A-1"
  exit 1
fi

ROOT="${PW_ROOT:-}"
SUDO="${PW_SUDO-sudo}"
SYSTEMCTL="${PW_SYSTEMCTL:-systemctl}"
UPDATE_INITRAMFS="${PW_UPDATE_INITRAMFS:-update-initramfs}"

EDID_NAME="poster-wall-$CONNECTOR.bin"
EDID_FILE="$ROOT/usr/lib/firmware/edid/$EDID_NAME"
CMDLINE="$ROOT/boot/firmware/cmdline.txt"
HOOK_DIR="$ROOT/etc/initramfs-tools/hooks"
HOOK="$HOOK_DIR/poster-wall-edid"
TTY_UNITS=(getty@tty1.service autovt@tty1.service)

# A usable EDID, as the kernel checks firmware EDIDs: the fixed header, exactly as many 128-byte
# blocks as the base block declares (byte 126 = extension count), every block summing to 0 mod 256.
edid_ok() {
  local f="$1" size ext
  size=$(wc -c <"$f")
  (( size >= 128 )) || return 1
  [[ "$(head -c 8 "$f" | od -An -tx1 | tr -d ' \n')" == "00ffffffffffff00" ]] || return 1
  ext=$(od -An -tu1 -j126 -N1 "$f" | tr -d ' \n')
  (( size == (ext + 1) * 128 )) || return 1
  od -An -v -tu1 "$f" | tr -s ' \n' '\n' \
    | awk 'NF { s += $1; n++; if (n % 128 == 0) { if (s % 256) bad = 1; s = 0 } } END { exit bad }'
}

# cmdline.txt without this connector's video= and drm.edid_firmware entry, then with $1 added to
# drm.edid_firmware and $2 appended (either may be empty). Other connectors' entries are kept.
cmdline_with() {
  local tok entry out=() edid=() joined
  set -f
  for tok in $(cat "$CMDLINE"); do
    case "$tok" in
      "video=$CONNECTOR:"*) ;;
      drm.edid_firmware=*)
        for entry in $(printf '%s' "${tok#drm.edid_firmware=}" | tr ',' ' '); do
          if [[ "$entry" != "$CONNECTOR:"* ]]; then edid+=("$entry"); fi
        done
        ;;
      *) out+=("$tok") ;;
    esac
  done
  set +f
  if [[ -n "$1" ]]; then edid+=("$1"); fi
  if (( ${#edid[@]} )); then
    joined="$(IFS=,; echo "${edid[*]}")"
    out+=("drm.edid_firmware=$joined")
  fi
  if [[ -n "$2" ]]; then out+=("$2"); fi
  echo "${out[*]}"
}

# Replace cmdline.txt without ever leaving it truncated: back it up, write a sibling file on the
# same filesystem, check it, then rename it over the original.
write_cmdline() {
  local new="$1" tmp="$CMDLINE.new"
  [[ "$new" != "$(cat "$CMDLINE")" ]] || return 0
  if [[ " $new " != *" root="* ]]; then
    echo "ERROR: refusing to write a cmdline.txt without root=; nothing changed."
    exit 1
  fi
  $SUDO cp "$CMDLINE" "$CMDLINE.bak.$(date +%s)"
  printf '%s\n' "$new" | $SUDO tee "$tmp" >/dev/null
  if [[ "$(cat "$tmp")" != "$new" ]]; then
    $SUDO rm -f "$tmp"
    echo "ERROR: could not write $tmp; cmdline.txt unchanged."
    exit 1
  fi
  sync
  $SUDO mv "$tmp" "$CMDLINE"
  sync
  echo " - updated $CMDLINE (reboot to apply)"
}

if [[ ! -e "$CMDLINE" ]]; then
  echo "WARN: $CMDLINE not found; display not pinned."
  exit 0
fi

if [[ $UNPIN -eq 1 ]]; then
  write_cmdline "$(cmdline_with "" "")"
  $SUDO $SYSTEMCTL unmask "${TTY_UNITS[@]}" >/dev/null
  echo "Unpinned $CONNECTOR. Reboot, then re-run with the TV on to pin it again."
  exit 0
fi

# -------- Save the TV's EDID while it's connected --------
EDID_CHANGED=0
for status in "$ROOT"/sys/class/drm/card*-"$CONNECTOR"/status; do
  [[ -e "$status" && "$(cat "$status")" == "connected" ]] || continue
  live="$(mktemp)"
  cat "$(dirname "$status")/edid" >"$live"
  if edid_ok "$live"; then
    if [[ ! -e "$EDID_FILE" ]] || ! cmp -s "$live" "$EDID_FILE"; then
      if [[ -e "$EDID_FILE" ]]; then $SUDO cp "$EDID_FILE" "$EDID_FILE.bak.$(date +%s)"; fi
      $SUDO install -D -m 0644 "$live" "$EDID_FILE"
      EDID_CHANGED=1
      echo " - saved the $CONNECTOR display's EDID to $EDID_FILE"
    fi
  fi
  rm -f "$live"
  break
done

if [[ ! -e "$EDID_FILE" ]]; then
  echo "WARN: no display EDID on $CONNECTOR (TV off or unplugged?). Display not pinned;"
  echo "      re-run $0 with the TV on."
  exit 0
fi

# -------- Put the EDID in the initramfs (vc4 probes the display from there) --------
if [[ -d "$ROOT/etc/initramfs-tools" ]]; then
  hook_body='#!/bin/sh
# Installed by poster-wall scripts/pin-display.sh: ship the pinned display EDID in the initramfs.
PREREQ=""
prereqs() { echo "$PREREQ"; }
case "$1" in prereqs) prereqs; exit 0 ;; esac
. /usr/share/initramfs-tools/hook-functions
for f in /usr/lib/firmware/edid/poster-wall-*.bin; do
  [ -e "$f" ] || continue
  # copy_file also returns 1 when the file is already in the image; fail unless an exact copy is there.
  copy_file firmware "$f" || cmp -s "$f" "${DESTDIR}$f" || exit 1
done
exit 0'
  if [[ ! -e "$HOOK" || "$(cat "$HOOK")" != "$hook_body" ]]; then
    $SUDO mkdir -p "$HOOK_DIR"
    printf '%s\n' "$hook_body" | $SUDO tee "$HOOK" >/dev/null
    $SUDO chmod 0755 "$HOOK"
    EDID_CHANGED=1
  fi
  if [[ $EDID_CHANGED -eq 1 ]]; then
    echo " - rebuilding the initramfs with the EDID (takes a minute)..."
    if ! $SUDO $UPDATE_INITRAMFS -u -k all >/dev/null; then
      # Drop the hook so the next run sees it missing and rebuilds again.
      $SUDO rm -f "$HOOK"
      echo "ERROR: update-initramfs failed; cmdline.txt not changed, display not pinned."
      exit 1
    fi
  fi
fi

# -------- Kernel cmdline: always-on connector with the saved EDID --------
mode=""
set -f
for tok in $(cat "$CMDLINE"); do
  if [[ "$tok" == "video=$CONNECTOR:"* ]]; then mode="${tok#"video=$CONNECTOR:"}"; fi
done
set +f
# Keep a mode the cmdline already pinned; just force the connector on (D) after it.
spec="${mode%%,*}"
opts=""
if [[ "$mode" == *,* ]]; then opts=",${mode#*,}"; fi
spec="${spec%[eDd]}"
write_cmdline "$(cmdline_with "$CONNECTOR:edid/$EDID_NAME" "video=$CONNECTOR:${spec}D$opts")"

# -------- No login prompt on the kiosk's screen --------
$SUDO $SYSTEMCTL mask --now "${TTY_UNITS[@]}" >/dev/null 2>&1 || $SUDO $SYSTEMCTL mask "${TTY_UNITS[@]}" >/dev/null

echo "Display $CONNECTOR pinned: the kiosk no longer needs the TV on at boot."
