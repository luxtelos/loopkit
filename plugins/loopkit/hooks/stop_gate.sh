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
# So: the whole BRANCH against its merge base, plus the working tree.
#
# The first version of that fix asked `git rev-parse --abbrev-ref HEAD` for a
# branch name and `git show-ref refs/heads/main` for the trunk. Both questions
# have wrong answers in ordinary checkouts, and every wrong answer reverted the
# gate to working-tree-only — the original bug, restored silently:
#
#   * detached HEAD has no branch name. That is how every review worktree in
#     this project is made, and how CI checks out a pull request head.
#   * `refs/heads/<name>` is a LOCAL branch lookup. A CI checkout has no local
#     `main`, and a project whose trunk is `develop` or `trunk` has no `main`
#     at all — this is a distributed plugin, so other repos' trunks matter.
#   * an orphan branch shares no commit with anything.
#
# A merge base does not care what a branch is called, or whether it has a name.
# So never ask. Resolve the trunk from `origin/HEAD` — the remote's own record
# of its default branch, the only name-independent source — and ask merge-base
# about HEAD directly. See tests/pins/stop-gate-branch-shapes.sh, which pins
# each shape and can be proven red.
gate_default_ref() {
    local d
    # origin/HEAD is what `git clone` writes and what `git remote set-head`
    # repairs; it names the trunk whatever the trunk is called.
    d="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
    if [ -n "$d" ] && git rev-parse --verify --quiet "$d^{commit}" >/dev/null 2>&1; then
        printf '%s' "$d"; return 0
    fi
    # Documented fallback, for a clone with no origin/HEAD (a shallow CI
    # fetch, or a repo created by `git init` with no remote at all). This is a
    # last resort by design: it guesses a name, which is the thing that broke.
    for d in origin/main origin/master origin/develop origin/trunk main master develop trunk; do
        if git rev-parse --verify --quiet "$d^{commit}" >/dev/null 2>&1; then
            printf '%s' "$d"; return 0
        fi
    done
    printf ''
}

gate_base() {
    local default_ref base head_branch
    default_ref="$(gate_default_ref)"
    if [ -z "$default_ref" ]; then printf ''; return 0; fi
    base="$(git merge-base "$default_ref" HEAD 2>/dev/null || true)"
    # base == HEAD means HEAD adds nothing the trunk does not already have.
    # Usually that is honest and the empty diff is the right answer (a feature
    # branch with no commits yet). The one exception is a repo with NO remote
    # trunk to compare against, sitting on its own trunk: there the merge base
    # is always HEAD, so the gate would never read a commit made on it. HEAD~1
    # is the base that does. Falling back to the working tree there was a
    # choice, not a necessity.
    if [ -n "$base" ] && [ "$(git rev-parse "$base" 2>/dev/null)" = "$(git rev-parse HEAD 2>/dev/null)" ]; then
        head_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
        case "$default_ref" in
            origin/*) : ;;   # a published trunk is ahead-or-equal: nothing new here
            *)
                if [ "$head_branch" = "$default_ref" ]; then
                    base="$(git rev-parse --verify --quiet 'HEAD~1' 2>/dev/null || true)"
                fi
                ;;
        esac
    fi
    printf '%s' "$base"
}

# Whenever the gate is NOT looking at a merge base it is reading the working
# tree only, and any committed work is invisible to it. Say so — every time,
# and say which situation actually obtains. The previous message asserted "on
# the default branch there is no merge base" in three shapes where you are not
# on the default branch, and did not print at all when the base was empty and
# the tree was dirty: the suite ran, the run looked covered, and the committed
# change was not. That silent fallback is exactly how the original bug returns.
gate_scope_note() {  # <base>
    local base="${1:-}" head default_ref why
    if [ -n "$base" ]; then return 0; fi
    default_ref="$(gate_default_ref)"
    head="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
    if ! git rev-parse --verify --quiet HEAD >/dev/null 2>&1; then
        why="this repository has no commits yet"
    elif [ -z "$default_ref" ]; then
        why="no default branch could be resolved — no origin/HEAD, and no trunk-shaped ref in this clone"
    elif [ -z "$(git merge-base "$default_ref" HEAD 2>/dev/null || true)" ]; then
        # No common ancestor at all — the structural fact, so it is reported
        # before the weaker "root commit" one, which is also true here.
        if [ "$head" = "HEAD" ]; then
            why="detached HEAD shares no history with the default branch ($default_ref) — an orphan or unrelated line"
        else
            why="branch '$head' shares no history with the default branch ($default_ref) — an orphan or unrelated line"
        fi
    elif ! git rev-parse --verify --quiet 'HEAD~1' >/dev/null 2>&1; then
        why="HEAD is a root commit, so there is nothing behind it to compare against"
    else
        why="the merge base against the default branch ($default_ref) could not be resolved"
    fi
    echo ">> NOTE: no merge base — $why."
    echo ">>       ONLY THE WORKING TREE was examined. Anything already committed"
    echo ">>       on this branch is NOT covered by this run."
}

collect_changed_code_files() {
    local base; base="${GATE_BASE-$(gate_base)}"
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

# Resolved once, so every path below reads the same base, and announced before
# any branch can swallow it — including LOOP_FORCE_GATE=1, which used to skip
# the enclosing block and the note with it.
GATE_BASE="$(gate_base)"
gate_scope_note "$GATE_BASE"

# --- Short-circuit on no-op turns -------------------------------------------
# The gate verifies CODE changes. A turn that only touched state/, docs, specs
# or the harness has nothing to test, lint, typecheck or build; running the
# full gate there produces false failures and blocks a clean stop.
if [[ "${LOOP_FORCE_GATE:-0}" != "1" ]]; then
    if [[ -z "$(collect_changed_code_files)" ]]; then
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
