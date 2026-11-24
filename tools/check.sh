#!/usr/bin/env bash
# Quality gates for session-snap.
#
# Runs the same checks locally (pre-push hook) and in CI, so a green local
# run predicts a green pipeline. Every gate must pass; the script stops at
# the first failure and returns its exit code.
#
#   0. privacy  no locally listed term in the files, paths, commit messages
#               and tags of the whole history (tools/privacy_scan.py)
#   0. shell    shellcheck for every shell script
#   1. format   pycodestyle, max line length 100
#   2. lint     pylint incl. Google-style docstring checks, score 10.00/10
#   3. types    mypy (config in pyproject.toml)
#   4. security bandit (config in pyproject.toml)
#   5. tests    pytest with branch coverage, threshold in pyproject.toml
#
# Usage: tools/check.sh [--fast]
#   --fast  skip the test gate (used for quick checks while editing)
#
# A tree that already passed ALL gates with the same tools is not checked in
# full again: the release script and the pre-push hook call this script for
# the same code several times. Privacy and shell always run (the privacy scan
# also reads commit messages and tags, which change without the tree); the
# other gates are skipped and the script says so. CHECK_FULL=1 forces a full
# run; CI always runs in full (fresh clone, CI=true).
#
# Runs as a normal user; needs no root privileges.

set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="src:tests${PYTHONPATH:+:$PYTHONPATH}"

# Development venv: an active one is used as it is; otherwise .venv of the
# project is activated for this script only - the activation ends with the
# script, the calling shell stays as it was. CI has none and installs the
# tools directly. Nothing is created or installed here.
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091  # path chosen at run time
    source .venv/bin/activate
    echo "venv: .venv (activated for this check)"
fi

FAST=0
[[ "${1:-}" == "--fast" ]] && FAST=1

gate() {
    local name="$1"; shift
    printf '\n=== %-8s %s\n' "$name" "$*"
    "$@"
}

for tool in pycodestyle pylint mypy bandit pytest; do
    command -v "$tool" >/dev/null || {
        echo "missing tool: $tool - create the development venv once (normal user):" >&2
        echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
        exit 2
    }
done
command -v shellcheck >/dev/null || {
    echo "missing tool: shellcheck  ->  sudo apt install shellcheck" >&2
    exit 2
}

# --- check cache (dev-conventions handover, chapter 5; templates/check-cache.sh) ---
# key = tree of the working copy (incl. uncommitted and untracked files, honouring
# .gitignore) + Python version + installed packages; stored in .git/checked-trees
CHECKED="$(git rev-parse --git-dir)/checked-trees"
check_key() {
    local idx tree
    idx="$(mktemp)"
    cp "$(git rev-parse --git-dir)/index" "$idx" 2>/dev/null || true
    tree="$(GIT_INDEX_FILE="$idx" git add -A >/dev/null 2>&1 \
            && GIT_INDEX_FILE="$idx" git write-tree)" || tree=""
    rm -f "$idx"
    [[ -n "$tree" ]] || return 1
    printf '%s py%s deps-%s\n' "$tree" \
        "$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')" \
        "$(python3 -m pip list --format=freeze 2>/dev/null | sha256sum | cut -c1-12)"
}
KEY=""
if [[ "${CI:-}" != "true" && "${CHECK_FULL:-0}" != "1" ]]; then
    KEY="$(check_key)" || KEY=""
fi

gate privacy  python3 tools/privacy_scan.py
gate shell    shellcheck install.sh tools/*.sh .githooks/pre-push src/session_snap/update/*.sh

if [[ -n "$KEY" && -f "$CHECKED" ]] && grep -qxF "$KEY" "$CHECKED"; then
    printf '\nThis tree passed all gates before (%s).\n' "${KEY%% *}"
    printf 'format, lint, types, security and tests skipped - CHECK_FULL=1 forces them.\n'
    printf '\nAll quality gates passed.\n'
    exit 0
fi
gate format   pycodestyle --max-line-length=100 src tests tools examples
gate lint     pylint --fail-under=10 src tests tools examples
gate types    mypy
gate security bandit -q -c pyproject.toml -r src
if (( FAST == 0 )); then
    gate tests python3 -m pytest -q -p no:cacheprovider \
        --cov=session_snap --cov-report=term
    # remember the tree only after a FULL run; keep the last 50 entries
    if [[ -n "$KEY" ]]; then
        { [[ -f "$CHECKED" ]] && tail -n 49 "$CHECKED"; echo "$KEY"; } > "$CHECKED.tmp"
        mv "$CHECKED.tmp" "$CHECKED"
    fi
fi

printf '\nAll quality gates passed.\n'
