#!/usr/bin/env bash
# after-install.sh - called by tools/release.sh after ./install.sh vX.Y.Z.
#
# Asks a running "session-snap watch" to switch to the installed release
# (prints that nothing runs if none does) and shows the state of the
# watcher. Runs as a normal user.
set -uo pipefail
program="$HOME/.local/bin/session-snap"
"$program" reload || true
"$program" watch --status || true
