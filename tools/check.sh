#!/usr/bin/env bash
# check.sh - the quality gates of session-snap (from templates/check.sh of the dev-conventions bundle).
#
# Usage:  tools/check.sh [--fast] [--only GATE] [--yes]
#
#   --fast       skip the gate "tests" (quick checks while editing)
#   --only GATE  run one gate only (CI: one step per gate)
#   --yes        no question: the tests take the free threads
#   -h, --help   this text
#
# Parallel tests (pytest-xdist, a dev dependency): in a terminal check.sh
# asks "N of M threads free (load L) - run the tests on N workers? [Y/n]";
# free = threads minus the load of the last minute (rounded), at least 1.
# Without a terminal (pre-push hook) or with --yes it takes the free threads
# without asking; in CI (CI=true) all threads of the runner. CHECK_WORKERS=N
# fixes the number, CHECK_WORKERS=0 or 1 runs them one after another.
# Without pytest-xdist the tests run one after another, with a note.
#
# Gates, in this order: privacy, shell, format, lint, types, security, tests.
# The same script runs locally, in the pre-push hook and in CI, so a green
# local run predicts a green pipeline. Every gate prints one result line
# (PASS / FAIL / SKIP, one fact, duration); the first failing gate stops the
# run and shows the end of its output.
# Exit code: 0 all passed, 1 a gate failed, 2 wrong usage or missing tool/file.
#
#   privacy   no locally listed term in the files, paths, commit messages,
#             tags and identities of the whole history (tools/privacy_scan.py)
#   shell     shellcheck for every shell script
#   format    pycodestyle, max line length 120
#   lint      pylint incl. Google-style docstring checks, score 10.00/10
#   types     mypy (config in pyproject.toml)
#   security  bandit (config in pyproject.toml)
#   tests     pytest with branch coverage, threshold in pyproject.toml
#
# Each tree is checked in full once: a tree that already passed ALL gates
# with the same tools runs only privacy and shell again (the privacy scan
# also reads commit messages and tags, which change without the tree); the
# other gates are skipped and the script says so. CHECK_FULL=1 forces a
# full run; CI (CI=true) always runs in full; --only never uses the cache.
#
# Needs tools/output.sh (dev-conventions bundle) next to this script.
# Runs as a normal user; needs no root privileges.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
if [[ ! -f tools/output.sh ]]; then
    echo "check.sh: tools/output.sh is missing - copy tools/output.sh of the dev-conventions bundle" >&2
    exit 2
fi
# shellcheck source=tools/output.sh disable=SC1091  # path chosen at run time
source tools/output.sh
export PYTHONPATH="src:tests${PYTHONPATH:+:$PYTHONPATH}"

# Development venv: an active one is used as it is; otherwise .venv of the
# project is activated for this script only. CI has none and installs the
# tools directly. Nothing is created or installed here.
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091  # path chosen at run time
    source .venv/bin/activate
    detail "venv: .venv (activated for this check)"
fi

GATES=(privacy shell format lint types security tests)
declare -A ABOUT=(
    [privacy]="tools/privacy_scan.py · files, paths, history"
    [shell]="shellcheck"
    [format]="pycodestyle · max line 120"
    [lint]="pylint · 10.00/10, max line 120"
    [types]="mypy"
    [security]="bandit"
    [tests]="pytest + coverage · branch ≥ 90 %"
)

FAST=false
ONLY=""
YES=false
XDIST=()
WORKERS=1
while (($#)); do
    case "$1" in
        --fast) FAST=true ;;
        --only) ONLY="${2:-}"; shift ;;
        --yes|-y) YES=true ;;
        -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
        *) echo "check.sh: unknown argument $1" >&2; exit 2 ;;
    esac
    shift
done
if [[ -n "$ONLY" && -z "${ABOUT[$ONLY]:-}" ]]; then
    echo "check.sh: unknown gate '$ONLY' - one of: ${GATES[*]}" >&2
    exit 2
fi

for tool in pycodestyle pylint mypy bandit pytest; do
    command -v "$tool" >/dev/null || {
        echo "check.sh: missing tool: $tool - create the development venv once (normal user):" >&2
        echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
        exit 2
    }
done
command -v shellcheck >/dev/null || {
    echo "check.sh: missing tool: shellcheck  ->  sudo apt install shellcheck" >&2
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
if [[ -z "$ONLY" && "${CI:-}" != "true" && "${CHECK_FULL:-0}" != "1" ]]; then
    KEY="$(check_key)" || KEY=""
fi
KNOWN=false
if [[ -n "$KEY" && -f "$CHECKED" ]] && grep -qxF "$KEY" "$CHECKED"; then
    KNOWN=true
fi

# --- the gates: each prints its own output; its last line is the fact -------------
shell_scripts() {
    local files=()
    mapfile -t files < <(git ls-files 'install.sh' 'tools/*.sh' '.githooks/*' 'src/session_snap/update/*.sh')
    ((${#files[@]})) || { echo "no shell scripts found"; return 1; }
    shellcheck "${files[@]}"
    echo "${#files[@]} scripts, no finding"
}
# workers - number of pytest workers (sets WORKERS; 1 = one after another)
workers() {
    local threads load free answer=""
    threads="$(nproc)"
    if [[ -n "${CHECK_WORKERS:-}" ]]; then
        [[ "$CHECK_WORKERS" =~ ^[0-9]+$ ]] || { note "CHECK_WORKERS=$CHECK_WORKERS is no number - one worker"; CHECK_WORKERS=1; }
        WORKERS=$((CHECK_WORKERS > 1 ? CHECK_WORKERS : 1))
        detail "tests: $WORKERS worker(s), set by CHECK_WORKERS"
    elif [[ -n "${CI:-}" ]]; then
        WORKERS=$threads
        detail "tests: $WORKERS workers (all threads of the CI runner)"
    else
        load="$(cut -d ' ' -f 1 /proc/loadavg)"
        free="$(awk -v t="$threads" -v l="$load" 'BEGIN { f = t - int(l + 0.5); print (f < 1 ? 1 : f) }')"
        WORKERS=$free
        if ! $YES && [[ -t 0 && -t 1 ]] && ((free > 1)); then
            attention "$free of $threads threads free - run the tests on $free workers?"
            read -r -p "  $free of $threads threads free (load $load) - run the tests on $free workers? [Y/n] " answer \
                || answer=n
            [[ ! "$answer" =~ ^[nN]$ ]] || WORKERS=1
        fi
        detail "tests: $WORKERS worker(s) - $free of $threads threads free (load $load)"
    fi
    if ((WORKERS > 1)) && ! python3 -c 'import xdist' 2>/dev/null; then
        note "pytest-xdist is not installed - the tests run one after another (.venv/bin/pip install -e '.[dev]')"
        WORKERS=1
    fi
}
run_gate() {
    case "$1" in
        privacy)  python3 tools/privacy_scan.py ;;
        shell)    shell_scripts ;;
        format)   pycodestyle --max-line-length=120 src tests tools examples && echo "no finding" ;;
        lint)     pylint --fail-under=10 src tests tools examples ;;
        types)    mypy ;;
        security) bandit -q -c pyproject.toml -r src && echo "no finding" ;;
        tests)    python3 -m pytest -q -p no:cacheprovider --cov=session_snap --cov-report=term "${XDIST[@]}" ;;
    esac
}

# --- run ---------------------------------------------------------------------------
START=$SECONDS
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
selected=("${GATES[@]}")
[[ -z "$ONLY" ]] || selected=("$ONLY")
title "Quality gates"
n=0 failed="" passed=0 skipped=0
for gate in "${selected[@]}"; do
    n=$((n + 1))
    stage "[$n/${#selected[@]}] $gate" "${ABOUT[$gate]}"
    if $KNOWN && [[ "$gate" != privacy && "$gate" != shell ]]; then
        skip "$gate" "tree passed before"
        skipped=$((skipped + 1))
        continue
    fi
    if $FAST && [[ "$gate" == tests ]]; then
        skip "tests" "--fast"
        skipped=$((skipped + 1))
        continue
    fi
    if [[ "$gate" == tests ]]; then
        workers
        XDIST=()
        # loadscope: the tests of one class (or module) stay on one worker
        ((WORKERS == 1)) || XDIST=(-n "$WORKERS" --dist loadscope)
    fi
    begin=$SECONDS code=0
    if [[ "$gate" == tests ]]; then
        run_gate "$gate" 2>&1 | tee "$LOG" || code=${PIPESTATUS[0]}
    else
        run_gate "$gate" >"$LOG" 2>&1 || code=$?
    fi
    fact="$(grep -v '^[[:space:]]*$' "$LOG" | tail -n 1 | cut -c 1-44)"
    if ((code == 0)); then
        pass "${fact:-$gate}" "$((SECONDS - begin)) s"
        passed=$((passed + 1))
    else
        fail "$gate" "$((SECONDS - begin)) s"
        [[ "$gate" == tests ]] || tail -n 30 "$LOG" | while IFS= read -r line; do detail "$line"; done
        failed="$gate"
        break
    fi
done

title "Result"
if [[ -z "$failed" ]]; then
    if $KNOWN; then
        note "this tree passed all gates before (${KEY%% *}); CHECK_FULL=1 forces a full run"
    elif [[ -n "$KEY" ]] && ! $FAST; then
        # remember the tree only after a FULL run; keep the last 50 entries
        { [[ -f "$CHECKED" ]] && tail -n 49 "$CHECKED"; echo "$KEY"; } > "$CHECKED.tmp"
        mv "$CHECKED.tmp" "$CHECKED"
    fi
    info "${G}${B}DONE${X}  $passed passed, $skipped skipped  $(ts "$((SECONDS - START)) s")"
    exit 0
fi
info "${R}${B}FAILED${X}  gate $failed  $(ts "$((SECONDS - START)) s")"
note "nothing was changed; fix the finding and run tools/check.sh again"
exit 1
