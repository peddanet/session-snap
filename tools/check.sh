#!/usr/bin/env bash
# Quality gates of session-snap. Run as the normal user from the repository root:
#     tools/check.sh            all stages
#     tools/check.sh lint types only the named stages
# Stages: privacy format lint types security tests
# Exit status 0 only if every selected stage passes.
set -u -o pipefail

cd "$(dirname "$0")/.." || exit 2
ROOT=$PWD
# Interpreter with pylint, mypy, bandit and coverage: $PYTHON, else the check environment of
# the clone (python3 -m venv .venv-check, see docs/developer.md), else python3.
if [ -n "${PYTHON:-}" ]; then PY=$PYTHON
elif [ -x "$ROOT/.venv-check/bin/python" ]; then PY=$ROOT/.venv-check/bin/python
else PY=python3
fi
FILES=(session-snap test_session_snap examples/make_demo_media.py)
STAGES=("$@")
[ ${#STAGES[@]} -eq 0 ] && STAGES=(privacy format lint types security tests)

if [ "$(id -u)" -eq 0 ]; then
    echo "check.sh: run as the normal user, not as root (session-snap refuses root)." >&2
    exit 2
fi

# The tools need module names ending in .py: link the two files into a scratch directory.
WORK=$ROOT/.check
rm -rf "$WORK" && mkdir -p "$WORK"
ln -s "$ROOT/session-snap" "$WORK/session_snap.py"
ln -s "$ROOT/test_session_snap" "$WORK/test_session_snap.py"
MODS=("$WORK/session_snap.py" "$WORK/test_session_snap.py" "$ROOT/examples/make_demo_media.py")

failed=()
run() {  # run <stage> <command...>
    local stage=$1; shift
    printf '\n=== %s: %s\n' "$stage" "$*"
    if "$@"; then
        echo "--- $stage: passed"
    else
        echo "--- $stage: FAILED"
        failed+=("$stage")
    fi
}

tracked_files() {
    if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "$ROOT" ls-files
    else
        find . -type f -not -path './.git/*' -not -path './.check/*' \
            -not -path '*/__pycache__/*' -not -path './.mypy_cache/*' \
            -not -path './.venv-check/*' -not -name .coverage -not -name .privacy-patterns \
            | sed 's|^\./||'
    fi
}

privacy() {
    # Generic patterns; personal words (names, host names) go into the untracked file
    # .privacy-patterns, one extended regular expression per line - never into the repository.
    local pat='/home/[a-z_][a-z0-9_-]*'
    local hits=0 f
    while IFS= read -r f; do
        [ -f "$f" ] || continue
        # /home/<name>: only the placeholders USER, tester and snaptest are allowed
        if grep -InEo "$pat" "$f" | grep -vE '/home/(USER|tester|snaptest)$' \
                | sed "s|^|$f:|"; then hits=1; fi
        # e-mail addresses other than noreply/example addresses
        if grep -InEo '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' "$f" \
                | grep -vE '@(users\.noreply\.github\.com|anthropic\.com|example\.(com|org))$' \
                | sed "s|^|$f:|"; then hits=1; fi
        # telephone numbers (+49 ..., 01xx ...)
        if grep -InE '(\+49|\b01[5-7][0-9]{1,2})[ /-]?[0-9]{3,}[ -]?[0-9]{3,}' "$f" \
                | sed "s|^|$f:|"; then hits=1; fi
        if [ -s "$ROOT/.privacy-patterns" ] \
                && grep -IniEf "$ROOT/.privacy-patterns" "$f" | sed "s|^|$f:|"; then hits=1; fi
    done < <(tracked_files)
    return $hits
}

format() {
    local bad=0 f
    for f in "${FILES[@]}"; do
        awk -v f="$f" 'length > 100 {print f ":" FNR ": " length " characters"; bad=1}
                       /[ \t]+$/   {print f ":" FNR ": trailing whitespace"; bad=1}
                       /\t/        {print f ":" FNR ": tab character"; bad=1}
                       END {exit bad}' "$f" || bad=1
    done
    return $bad
}

for s in "${STAGES[@]}"; do
    case $s in
        privacy)  run privacy privacy ;;
        format)   run format format ;;
        lint)     run lint "$PY" -m pylint --rcfile=pyproject.toml --fail-under=10 "${MODS[@]}" ;;
        types)    run types "$PY" -m mypy --config-file pyproject.toml "${MODS[@]}" ;;
        security) run security "$PY" -m bandit -c pyproject.toml -q "${MODS[@]}" ;;
        tests)
            rm -f .coverage
            run tests "$PY" -W error::ResourceWarning -m coverage run --rcfile=pyproject.toml \
                --include='*/session-snap' test_session_snap
            run coverage "$PY" -m coverage report --rcfile=pyproject.toml ;;
        *) echo "unknown stage: $s (privacy format lint types security tests)" >&2; exit 2 ;;
    esac
done

rm -rf "$WORK"
echo
if [ ${#failed[@]} -eq 0 ]; then
    echo "All checks passed: ${STAGES[*]}"
else
    echo "FAILED: ${failed[*]}"
    exit 1
fi
