#!/usr/bin/env bash
# stop-gate-worktree-root.sh — gate the session worktree, not CLAUDE_PROJECT_DIR.
#
# WHY. Claude Code / Cursor set CLAUDE_PROJECT_DIR to the main checkout even when
# the session's tools run in a linked worktree. The Stop hook then cds into the
# main tree and diffs ITS suite against ITS baseline — Balancia recorded 800+
# consecutive "new test failures vs state/known-test-failures.txt" blocks while
# the session worktrees had zero gate events.
#
# This pin requires:
#   1. linked worktree + CLAUDE_PROJECT_DIR=main → suite runs IN the worktree
#   2. CLAUDE_PROJECT_DIR pointing at an unrelated repo → SKIP (not FAIL)
#   3. matching CLAUDE_PROJECT_DIR and cwd → still gates as before
#
# usage: bash tests/pins/stop-gate-worktree-root.sh [<repo-root>]

set -uo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
REPO="$(cd "$REPO" 2>/dev/null && pwd)" || { echo "FAIL no such repo root: $1"; exit 2; }
GATE="$REPO/plugins/loopkit/hooks/stop_gate.sh"
[ -f "$GATE" ] || { echo "FAIL no stop_gate.sh at $GATE"; exit 2; }

fails=0
ok()   { echo "  ok   $1"; }
bad()  { echo "  FAIL $1"; fails=$((fails+1)); }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-worktree-root.XXXXXX")"
trap 'find "$WORK" -mindepth 1 -delete 2>/dev/null; rmdir "$WORK" 2>/dev/null || true' EXIT

git_q() { git -c user.email=t@t -c user.name=t -c init.defaultBranch=main -c advice.detachedHead=false -c commit.gpgsign=false "$@"; }

# Main checkout + linked worktree. Each tree has its own marker script so we
# can see WHICH tree the gate ran in.
MAIN="$WORK/main"
WT="$WORK/wt"
mkdir -p "$MAIN"
git_q init -q -b main "$MAIN" >/dev/null 2>&1
printf 'echo MAIN-SUITE\n' > "$MAIN/suite.sh"
chmod +x "$MAIN/suite.sh"
git_q -C "$MAIN" add suite.sh >/dev/null 2>&1
git_q -C "$MAIN" commit -q -m base >/dev/null 2>&1
git_q -C "$MAIN" worktree add -q -b fix/session "$WT" >/dev/null 2>&1
printf 'echo WT-SUITE\n' > "$WT/suite.sh"
chmod +x "$WT/suite.sh"
# Commit the worktree change so collect_changed_code_files sees branch code.
git_q -C "$WT" add suite.sh >/dev/null 2>&1
git_q -C "$WT" commit -q -m "wt change" >/dev/null 2>&1

run_gate() {  # <cwd> <CLAUDE_PROJECT_DIR> [extra env...]
    local cwd="$1" project="$2"; shift 2
    ( cd "$cwd" && env CLAUDE_PROJECT_DIR="$project" \
        LOOP_TEST_CMD="bash ./suite.sh" \
        LOOP_LINT_CMD="true" \
        LOOP_TYPECHECK_CMD="" \
        LOOP_BUILD_CMD="" \
        LOOP_FORCE_GATE=1 \
        "$@" bash "$GATE" </dev/null 2>&1 )
}

echo "== worktree root: linked session worktree wins over CLAUDE_PROJECT_DIR"
out="$(run_gate "$WT" "$MAIN")"
if grep -q 'WT-SUITE' <<<"$out" && ! grep -q 'MAIN-SUITE' <<<"$out"; then
    ok "linked worktree: suite ran in the session worktree"
else
    bad "linked worktree should run WT-SUITE, not MAIN: $(tail -5 <<<"$out")"
fi
grep -q 'session worktree' <<<"$out" && ok "linked worktree: announced the redirect" \
    || bad "linked worktree should announce session worktree redirect"

echo "== worktree root: unrelated CLAUDE_PROJECT_DIR skips (does not FAIL)"
OTHER="$WORK/other"
mkdir -p "$OTHER"
git_q init -q -b main "$OTHER" >/dev/null 2>&1
printf 'echo OTHER\n' > "$OTHER/suite.sh"
chmod +x "$OTHER/suite.sh"
git_q -C "$OTHER" add suite.sh >/dev/null 2>&1
git_q -C "$OTHER" commit -q -m other >/dev/null 2>&1
# cwd=WT (has suite), CLAUDE_PROJECT_DIR=OTHER — different repos → SKIP
# (do not set LOOP_FORCE_GATE here — FORCE is the override path below)
out="$( ( cd "$WT" && env CLAUDE_PROJECT_DIR="$OTHER" \
    LOOP_TEST_CMD="bash ./suite.sh" LOOP_LINT_CMD="true" \
    LOOP_TYPECHECK_CMD="" LOOP_BUILD_CMD="" \
    bash "$GATE" </dev/null 2>&1 ); echo EXIT:$? )"
if grep -q 'SKIP: gate root' <<<"$out" && grep -q 'EXIT:0' <<<"$out"; then
    ok "unrelated project dir: SKIP with exit 0"
else
    bad "unrelated project dir should SKIP pass: $(tail -5 <<<"$out")"
fi
# FORCE must still gate CLAUDE_PROJECT_DIR when the caller insists.
out="$(run_gate "$WT" "$OTHER")"
grep -q 'OTHER' <<<"$out" && ok "LOOP_FORCE_GATE=1 gates CLAUDE_PROJECT_DIR despite cwd mismatch" \
    || bad "FORCE should run OTHER suite: $(tail -5 <<<"$out")"

echo "== worktree root: matching project dir still gates"
out="$(run_gate "$MAIN" "$MAIN")"
# MAIN has no branch changes vs itself if we're on main — force gate so suite runs
# MAIN suite.sh is the only code; on main trunk collect may still see working tree.
# LOOP_FORCE_GATE=1 in run_gate ensures the suite runs.
grep -q 'MAIN-SUITE' <<<"$out" && ok "matching CLAUDE_PROJECT_DIR and cwd still gates" \
    || bad "matching roots should still run: $(tail -5 <<<"$out")"

if [ "$fails" -eq 0 ]; then
    echo "ALL PASS ($fails failures)"
    exit 0
fi
echo "FAILED: $fails"
exit 1
