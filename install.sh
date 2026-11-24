#!/usr/bin/env bash
# install.sh - install a session-snap release for the current user.
#
# The code is always taken from a release tag (vX.Y.Z) on GitHub, never
# from the working tree of a clone, so "session-snap --version" shows exactly
# the installed release. The release goes into a virtual environment of its
# own and the command is linked into BIN_DIR (XDG layout):
#
#   ${XDG_DATA_HOME:-~/.local/share}/session-snap/venv   the installed release
#   ${BIN_DIR:-~/.local/bin}/session-snap                 link to venv/bin/session-snap
#
# Usage:  ./install.sh              install or update to the newest release
#         ./install.sh v2.0.0       install a given release (also a downgrade)
#         ./install.sh --list       list the available releases
#
# Leaving the layout up to 1.8.0 (only what session-snap created itself):
#   * ~/scripts/session-snap and ~/scripts/test_session_snap (single-file
#     program and its tests, written by "session-snap --install") move into
#     the backup folder ~/.local/state/session-snap/backup/,
#   * the autostart entry of "session-snap watch" is rewritten to the link,
#   * the data in ~/scripts/session-snap.d moves on the first start of the
#     new release (run at the end of this script: "session-snap watch --status").
#
# Runs as a normal user and writes only below the data and state folders,
# BIN_DIR, ~/scripts (the two old files) and ~/.config/autostart. Needs git,
# python3 >= 3.12 with the venv module (Debian/Ubuntu package python3-venv)
# and network access to GitHub and PyPI (build backend).
# PIP_INSTALL_CMD replaces the pip step (tests): called with VENV and TAG.
set -euo pipefail

REPO_URL="${SESSION_SNAP_REPO:-https://github.com/peddanet/session-snap.git}"
xdg_data="${XDG_DATA_HOME:-}"
case "$xdg_data" in /*) ;; *) xdg_data="$HOME/.local/share" ;; esac
xdg_state="${XDG_STATE_HOME:-}"
case "$xdg_state" in /*) ;; *) xdg_state="$HOME/.local/state" ;; esac
DATA_DIR="$xdg_data/session-snap"
VENV="$DATA_DIR/venv"
BACKUP="$xdg_state/session-snap/backup"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
LINK="$BIN_DIR/session-snap"
OLD_PROGRAM="$HOME/scripts/session-snap"
OLD_TESTS="$HOME/scripts/test_session_snap"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/session-snap-watch.desktop"

die() { echo "install.sh: $*" >&2; exit 1; }

# release tags vX.Y.Z of the repository, oldest first (version order)
releases() {
  git ls-remote --tags --refs "$REPO_URL" 'v*' \
    | sed 's#.*refs/tags/##' \
    | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true; } \
    | sort -V
}

# true if the directory $1 is in PATH
in_path() { case ":$PATH:" in *":$1:"*) return 0 ;; *) return 1 ;; esac; }

case "${1:-}" in
  -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
             exit 0 ;;
  --list)    releases || die "cannot read the release tags of $REPO_URL"; exit 0 ;;
  -*)        die "unknown option: $1 (see --help)" ;;
esac

command -v git >/dev/null || die "git is required (sudo apt install git)"
available="$(releases)" || die "cannot read the release tags of $REPO_URL"
[ -n "$available" ] || die "no release tag vX.Y.Z found at $REPO_URL"
TAG="${1:-$(tail -n 1 <<<"$available")}"
grep -qxF "$TAG" <<<"$available" || die "unknown release: $TAG (see ./install.sh --list)"

# checked before anything is installed: replace only what this script
# created (the link into its own venv)
mkdir -p "$BIN_DIR"
if [ -e "$LINK" ] || [ -L "$LINK" ]; then
  if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$VENV/bin/session-snap" ]; then
    :
  else
    die "refusing to overwrite foreign file: $LINK"
  fi
fi

python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
  || die "Python >= 3.12 required"

# a venv whose interpreter vanished (system Python upgraded) is rebuilt
if [ -d "$VENV" ] && ! "$VENV/bin/python" -c '' 2>/dev/null; then
  echo "rebuilding the virtual environment (its Python is gone): $VENV"
  rm -rf "$VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then
  mkdir -p "$DATA_DIR"
  python3 -m venv "$VENV" \
    || die "cannot create $VENV - install the venv module: sudo apt install python3-venv"
fi

echo "installing session-snap $TAG from $REPO_URL"
if [ -n "${PIP_INSTALL_CMD:-}" ]; then
  $PIP_INSTALL_CMD "$VENV" "$TAG"
else
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
    --force-reinstall --no-deps "session-snap @ git+$REPO_URL@$TAG"
fi

installed="$("$VENV/bin/session-snap" --version)"
[ "$installed" = "session-snap ${TAG#v}" ] \
  || die "tag $TAG installed, but the program reports \"$installed\""

ln -sfn "$VENV/bin/session-snap" "$LINK"
echo "$installed  ->  $LINK"

# -- leave the layout up to 1.8.0 (only what session-snap created) -------------
# an old program file is recognised by its module docstring and VERSION line
old_file() {
  [ -f "$1" ] && ! [ -L "$1" ] && head -n 5 "$1" | grep -q "session-snap" \
    && grep -qE '^VERSION = "1\.[0-9]+\.[0-9]+"' "$1"
}
for old in "$OLD_PROGRAM" "$OLD_TESTS"; do
  if old_file "$old"; then
    # only the version number: the line may carry a comment (test file of 1.8.0)
    version="$(sed -n 's/^VERSION = "\([0-9][0-9.]*\)".*/\1/p' "$old" | head -n 1)"
    mkdir -p "$BACKUP"
    target="$BACKUP/$(basename "$old") v$version"
    [ -e "$target" ] && target="$target $(date +%Y-%m-%d_%H-%M-%S)"
    mv -- "$old" "$target"
    echo "moved the old program file: $old -> $target"
  fi
done
if [ -f "$AUTOSTART" ] && grep -qF "Exec=$OLD_PROGRAM watch" "$AUTOSTART"; then
  sed -i "s#^Exec=$OLD_PROGRAM watch#Exec=$LINK watch#" "$AUTOSTART"
  echo "autostart entry of the watcher now starts $LINK: $AUTOSTART"
fi

resolved="$(command -v session-snap || true)"
if ! in_path "$BIN_DIR"; then
  echo "NOTE: $BIN_DIR is not in PATH yet - Ubuntu adds it at the next login"
  echo "      (if it existed then); until then use $LINK"
elif [ -n "$resolved" ] && [ "$resolved" != "$LINK" ]; then
  echo "NOTE: in this shell 'session-snap' resolves to $resolved"
  echo "      (e.g. an active development venv); the release is $LINK"
fi
# first start of the new release: moves the data of the old layout, restarts the watcher
"$LINK" watch --status || echo "NOTE: '$LINK watch --status' failed - see above"
# entry with icon in the application menu (written by the program, unchanged files stay)
"$LINK" desktop-entry || echo "NOTE: '$LINK desktop-entry' failed - see above"
