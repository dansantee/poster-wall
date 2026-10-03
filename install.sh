#!/usr/bin/env bash
# install.sh: one-line install of Poster Wall on a fresh Raspberry Pi OS Lite (64-bit).
#
#   curl -fsSL https://raw.githubusercontent.com/dansantee/poster-wall/main/install.sh | bash
#
# Pass setup.sh options after `bash -s --`, e.g. a landscape display:
#   curl -fsSL https://raw.githubusercontent.com/dansantee/poster-wall/main/install.sh | bash -s -- --rotate 0
#
# It updates the Pi, installs git, clones (or updates) the repo into ~/poster-wall and runs
# setup.sh. Then open the settings page shown on the screen to sign in with Plex.
# Safe to re-run. POSTER_WALL_REPO / POSTER_WALL_DIR / POSTER_WALL_BRANCH override the defaults.

# Everything is inside main(), and the call on the last line is wrapped in { ... }: bash reads a
# whole block before running it, so a download cut off anywhere, even mid-call, runs nothing.
main() {
  set -euo pipefail

  local repo="${POSTER_WALL_REPO:-https://github.com/dansantee/poster-wall.git}"
  local dir="${POSTER_WALL_DIR:-$HOME/poster-wall}"
  local branch="${POSTER_WALL_BRANCH:-main}"

  if [[ "$(id -u)" -eq 0 ]]; then
    echo "Run this as your normal user (the one that will run the wall), not as root or with sudo." >&2
    exit 1
  fi

  echo "==> Updating the Pi (this can take a while on a fresh install)..."
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -y \
    -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confnew" full-upgrade

  echo "==> Installing git..."
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git

  if [[ -d "$dir/.git" ]]; then
    echo "==> Updating $dir..."
    git -C "$dir" pull --ff-only
  else
    echo "==> Cloning $repo into $dir..."
    git clone --branch "$branch" "$repo" "$dir"
  fi

  echo "==> Running setup.sh $*"
  cd "$dir"
  # stdin is this script when piped from curl; setup.sh must not read it
  bash ./setup.sh "$@" </dev/null

  echo
  echo "Next: reboot (sudo reboot). The screen then shows the address of the settings page,"
  echo "where you sign in with Plex and choose your libraries."
}

{ main "$@"; }
