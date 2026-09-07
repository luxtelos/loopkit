#!/usr/bin/env bash
# stop_gate.sh — the deterministic gate before "done" is allowed.
#
# Wired as a Stop hook. Precheck, tests, lint, typecheck and build must pass; a
# non-zero exit here REJECTS the stop and the model keeps working. "Runs" is
# not "right", and a model's own claim that the tests pass is not evidence.
#
# Every command is configurable per project, through the environment or
# <project>/.loopkit/config.env (sourced below). Set a variable to the EMPTY
# string to skip that step: `${VAR-default}` keeps an explicit empty, only an
# unset variable takes the default.
#
#   LOOP_PRECHECK_CMD       optional, runs first (e.g. a dependency-closure check)
#   LOOP_TEST_CMD           default: npm test
#   LOOP_LINT_CMD           default: npm run lint
#   LOOP_LINT_FALLBACK_CMD  lint only the changed files when the full lint fails
#                           (default: npx eslint --max-warnings=0); empty = none
#   LOOP_TYPECHECK_CMD      default: npx tsc --noEmit
#   LOOP_BUILD_CMD          default: npm run build
#   LOOP_ENV_WRAPPER        prefix for every command, e.g.
#                           "doppler run --project p --config c --"
#   LOOP_CODE_GLOBS         which files count as code (default below)
#   LOOP_FORCE_GATE=1       run the gate even when no code file changed
#
# Tests prefer the regression diff (scripts/test-regressions.sh) over a raw
# suite run whenever state/known-test-failures.txt exists: a suite that carries
# known failures can never pass, and a gate that can never pass gets switched
# off, which is worse than a gate that tolerates a committed baseline.
#
# Every verdict is appended to state/progress.md through scripts/progress.py
# (fail-open) so the next session, and the next compaction, start from a
# record rather than a recollection.

set -euo pipefail

# The Stop hook's JSON arrives on stdin; read it once, here, so the session
# key is available and nothing downstream blocks on a closed pipe.
LOOPKIT_HOOK_STDIN=""
if [ ! -t 0 ]; then
    # Bounded: Claude Code writes the JSON and closes the pipe; a caller that
    # leaves stdin open must not hang the gate. 2 s is far above the real case.
    IFS= read -r -t 2 -d '' LOOPKIT_HOOK_STDIN 2>/dev/null || true
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(dirname "$HERE")}"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT"

if [[ -f "$ROOT/.loopkit/config.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.loopkit/config.env"
    set +a
fi

LOOP_PRECHECK_CMD="${LOOP_PRECHECK_CMD-}"
LOOP_TEST_CMD="${LOOP_TEST_CMD-npm test}"
LOOP_LINT_CMD="${LOOP_LINT_CMD-npm run lint}"
LOOP_LINT_FALLBACK_CMD="${LOOP_LINT_FALLBACK_CMD-npx eslint --max-warnings=0}"
LOOP_TYPECHECK_CMD="${LOOP_TYPECHECK_CMD-npx tsc --noEmit}"
LOOP_BUILD_CMD="${LOOP_BUILD_CMD-npm run build}"
LOOP_ENV_WRAPPER="${LOOP_ENV_WRAPPER-}"
LOOP_CODE_GLOBS="${LOOP_CODE_GLOBS-*.js *.jsx *.ts *.tsx *.mjs *.cjs *.py *.go *.rs *.java *.rb *.kt *.swift}"
read -r -a CODE_GLOBS <<< "$LOOP_CODE_GLOBS"

# The progress log is memory, not a control: a failure to write it never
# changes the verdict.
note() {
    python3 "$PLUGIN_ROOT/scripts/progress.py" append --root "$ROOT" --text "$1" >/dev/null 2>&1 || true
    local result="FAIL"; [[ "$1" == "gate PASS" ]] && result="PASS"
    python3 "$PLUGIN_ROOT/scripts/ticks.py" append --root "$ROOT" --event gate --k "result=$result" --k "reason=${1#gate FAIL: }" >/dev/null 2>&1 || true
}

# Claude Code stops honouring a Stop hook after 8 consecutive blocks and ends
# the turn anyway (code.claude.com/docs/en/best-practices). The eighth block is
# therefore not a block; it is the moment the gate is overridden. Count them
# per session so the seventh says so, and the override is recorded rather
# than silent. Fail-open: the counter can never change a verdict.
STOP_KEY="$(printf '%s' "${LOOPKIT_HOOK_STDIN:-}" | python3 -c 'import hashlib,json,sys
try:
    d=json.load(sys.stdin); s=str(d.get("session_id") or "")
except Exception:
    s=""
print(hashlib.sha256(s.encode()).hexdigest()[:16] if s else "unknown")' 2>/dev/null || echo unknown)"
STOP_COUNTER="${TMPDIR:-/tmp}/loopkit-stopblocks-$STOP_KEY"
consecutive_blocks() { cat "$STOP_COUNTER" 2>/dev/null || echo 0; }

fail() {  # <message> — record, print, reject the stop
    local n; n=$(( $(consecutive_blocks) + 1 )); echo "$n" > "$STOP_COUNTER" 2>/dev/null || true
    note "gate FAIL: $1 (consecutive block $n)"
    echo "FAIL: $1"
    if [[ "$n" -ge 7 ]]; then
        echo "STOP-GATE OVERRIDE IMMINENT: this is consecutive block $n of the 8 Claude Code honours before it ends the turn anyway."
        echo "  The next block is not a block. Record what is failing and why in inbox/needs-human.md now; the human decides, not the override."
        python3 "$PLUGIN_ROOT/scripts/ticks.py" append --root "$ROOT" --event gate_override_imminent --k "blocks=$n" >/dev/null 2>&1 || true
    fi
    exit 1
}

# Plain `bash -c`, never `bash -lc`: a login shell re-sources the profile and
# can reset PATH to a system runtime, silently discarding the caller's — that
# is how a gate once ran a whole suite on the wrong Node for weeks.
run_in_env() {
    local command="$1"
    if [[ -n "$LOOP_ENV_WRAPPER" ]]; then
        local -a wrapper
        read -r -a wrapper <<< "$LOOP_ENV_WRAPPER"
        "${wrapper[@]}" bash -c "$command"
        return
    fi
    bash -c "$command"
}

# Tracked-modified OR untracked code files. Harness and plugin config never
# count. `grep -v` exits 1 when it filters everything, which under pipefail
# would abort the script — `|| true` makes "no files" a normal empty result.
# What counts as "changed" for this turn.
#
# It used to be `git diff HEAD` plus untracked files — the WORKING TREE only.
# That made the gate pass whenever the work was already committed, which is
# every well-behaved agent and exactly what this repo's rules encourage. On
# 2026-09-07 that was 92 commits in 24 hours with zero gate verdicts: the gate
# ran every time, found a clean tree, and short-circuited to PASS without
# running one test. A check that cannot fail, inside the gate.
#
# So: the whole BRANCH against its merge base, plus the working tree. On the
# default branch there is no merge base to compare with, so fall back to the
# working tree and say so out loud — a silent fallback is how the original bug
# would come straight back.
gate_base() {
    local head remote_default d
    for d in main master; do
        if git show-ref --verify --quiet "refs/heads/$d"; then remote_default="$d"; break; fi
    done
    remote_default="${remote_default:-main}"
    head="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
    if [ "$head" = "$remote_default" ] || [ "$head" = "HEAD" ]; then
        echo ""   # no base — caller falls back to the working tree, loudly
        return 0
    fi
    git merge-base "$remote_default" HEAD 2>/dev/null || echo ""
}

collect_changed_code_files() {
    local base; base="$(gate_base)"
    {
        if [ -n "$base" ]; then
            git diff --name-only --diff-filter=ACMRTUXB "$base"...HEAD -- "${CODE_GLOBS[@]}" 2>/dev/null
        fi
        git diff --name-only --diff-filter=ACMRTUXB HEAD -- "${CODE_GLOBS[@]}" 2>/dev/null
        git ls-files --others --exclude-standard -- "${CODE_GLOBS[@]}"
    } | awk 'NF' | { grep -vE '^(\.claude|\.loopkit)/' || true; } | sort -u
}

run_lint_fallback() {
    [[ -n "$LOOP_LINT_FALLBACK_CMD" ]] || return 1
    local -a changed=()
    local f
    while IFS= read -r f; do changed+=("$f"); done < <(collect_changed_code_files)
    if [[ ${#changed[@]} -eq 0 ]]; then
        echo "FAIL: lint failed and there are no changed code files for the fallback"
        return 1
    fi
    local quoted=""
    for f in "${changed[@]}"; do quoted+=" $(printf '%q' "$f")"; done
    echo ">> Lint fallback on changed files:${quoted}"
    run_in_env "$LOOP_LINT_FALLBACK_CMD$quoted"
}

step() {  # <label> <command>
    local label="$1" command="$2"
    if [[ -z "$command" ]]; then
        echo ">> $label: skipped (command set to empty)"
        return 0
    fi
    echo ">> $label: $command"
    if ! run_in_env "$command"; then
        fail "$label failed"
    fi
}

echo ">> stop_gate: running checks before 'done' is allowed"

# --- Short-circuit on no-op turns -------------------------------------------
# The gate verifies CODE changes. A turn that only touched state/, docs, specs
# or the harness has nothing to test, lint, typecheck or build; running the
# full gate there produces false failures and blocks a clean stop.
if [[ "${LOOP_FORCE_GATE:-0}" != "1" ]]; then
    if [[ -z "$(collect_changed_code_files)" ]]; then
        if [[ -z "$(gate_base)" ]]; then
            echo ">> NOTE: on the default branch there is no merge base, so only the"
            echo ">>       working tree was examined. A committed change on this branch"
            echo ">>       is NOT covered by this run."
        fi
        echo ">> SKIP: no changed code files — nothing to verify (conversational/infra turn)."
        echo ">> PASS: gate short-circuited. 'done' condition satisfied."
        exit 0
    fi
fi
# ---------------------------------------------------------------------------

step "Precheck" "$LOOP_PRECHECK_CMD"

echo ">> Testing..."
REGRESSIONS_SCRIPT="$PLUGIN_ROOT/scripts/test-regressions.sh"
KNOWN_FAILURES_BASELINE="$ROOT/state/known-test-failures.txt"
if [[ -f "$REGRESSIONS_SCRIPT" && -f "$KNOWN_FAILURES_BASELINE" ]]; then
    if ! bash "$REGRESSIONS_SCRIPT"; then
        fail "new test failures vs state/known-test-failures.txt"
    fi
elif [[ -z "$LOOP_TEST_CMD" ]]; then
    echo ">> Testing: skipped (LOOP_TEST_CMD set to empty)"
elif ! run_in_env "$LOOP_TEST_CMD"; then
    fail "test suite failed"
fi

if [[ -z "$LOOP_LINT_CMD" ]]; then
    echo ">> Linting: skipped (LOOP_LINT_CMD set to empty)"
else
    echo ">> Linting: $LOOP_LINT_CMD"
    if ! run_in_env "$LOOP_LINT_CMD"; then
        echo ">> Primary lint failed; trying the fallback on changed files"
        if ! run_lint_fallback; then
            fail "lint failed"
        fi
    fi
fi

step "Type checking" "$LOOP_TYPECHECK_CMD"
step "Building" "$LOOP_BUILD_CMD"

rm -f "$STOP_COUNTER" 2>/dev/null || true
note "gate PASS"
echo ">> PASS: all gates passed. 'done' condition satisfied."
exit 0
