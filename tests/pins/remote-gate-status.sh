#!/usr/bin/env bash
# remote-gate-status.sh — the remote gate runner must be able to say FAIL.
#
# WHY THIS PIN EXISTS. `tools/remote-gate.sh` shipped with:
#
#     bash tests/selftest.sh 2>&1 | grep -E ... | tail -30
#     echo "suite-rc=$?"
#
# `$?` there is TAIL's status, not the suite's. The reader exits early, the
# writer takes SIGPIPE, and a suite printing "FAILURE: 3 checks failed"
# reported `suite-rc=0` and exited 0. The outer `ssh ... | tail -40` had the
# same shape. So the tool shipped to replace local gate runs could not report a
# failure — the same "check that cannot fail" defect the gate itself was fixed
# for, one layer further out, and the one real run that said PASS was right
# only by coincidence.
#
# This runs the far-side half (tools/remote-gate-body.sh) directly, against a
# local throwaway tree, with a stub suite — no ssh, no docker, no network — and
# requires it to exit non-zero on a failing suite and zero on a passing one.
#
# The COMPANION pin is remote-gate-sends-working-tree.sh: this one asks whether
# the verdict is honest, that one asks whether the verdict is about the tree you
# have. Both are needed. A runner that reports failures accurately about the
# wrong tree is no better than one that cannot report failures at all.
#
# usage: bash tests/pins/remote-gate-status.sh [<repo-root>]

set -uo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
REPO="$(cd "$REPO" && pwd)"   # a relative argument must not break the fixtures
BODY="$REPO/tools/remote-gate-body.sh"
RUNNER="$REPO/tools/remote-gate.sh"
[ -f "$BODY" ]   || { echo "FAIL no remote-gate-body.sh at $BODY"; exit 2; }
[ -f "$RUNNER" ] || { echo "FAIL no remote-gate.sh at $RUNNER"; exit 2; }

fails=0
ok()  { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails+1)); }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-remote-gate-pin.XXXXXX")"
trap 'find "$WORK" -mindepth 1 -delete 2>/dev/null; rmdir "$WORK" 2>/dev/null || true' EXIT

# A throwaway repo standing in for the tree the sender rsynced over, so the real
# "run in a staged tree" path executes.
SRC="$WORK/staged"
mkdir -p "$SRC"
git -c init.defaultBranch=main init -q "$SRC" >/dev/null 2>&1
echo ok > "$SRC/marker.txt"
# `-c commit.gpgsign=false`: a fixture must not depend on the caller's commit
# signing. With signing on and the agent locked, the commit fails, the fixture
# has no branch, and every check below reports a defect that is not there.
git -C "$SRC" -c user.email=t@t -c user.name=t add marker.txt >/dev/null 2>&1
git -C "$SRC" -c user.email=t@t -c user.name=t -c commit.gpgsign=false commit -q -m "pin fixture" >/dev/null 2>&1
git -C "$SRC" rev-parse --verify --quiet main >/dev/null 2>&1 \
    || { echo "FAIL could not build the fixture repo (is commit signing forcing a prompt?)"; exit 2; }

run_body() {  # <suite-command> <run-id> → prints the body's exit status
    # Each on its own line: bash expands every word of a `local` statement
    # BEFORE any of its assignments take effect, so `local id="$2" out=...$id`
    # reads an unset $id.
    local suite="$1"
    local id="$2"
    local input="$WORK/in-$id.sh"
    local out="$WORK/out-$id.log"
    local rc
    {
        printf 'REF=%q\n' main
        printf 'SUITE=%q\n' "$suite"
        printf 'DIRTY=%q\n' 0
        printf 'WORKDIR=%q\n' "$SRC"
        printf 'LOGDIR=%q\n'  "$WORK/log-$id"
        printf 'SKIP_APT=1\n'
        cat "$BODY"
    } > "$input"
    # HOME is redirected because the body declares the staged tree a safe
    # directory with `git config --global`. Without this the pin would append a
    # line to the developer's own ~/.gitconfig on every run.
    HOME="$WORK" bash "$input" >"$out" 2>&1
    rc=$?
    printf '%s' "$rc"
}

echo "== the runner must be able to go RED"
# The exact shape that fooled it: a suite that prints the summary lines the
# display filter matches, and then fails.
red_rc="$(run_body 'echo "  FAIL something genuinely broke"; echo "FAILURE: 3 checks failed"; exit 1' red)"
red_out="$(cat "$WORK/out-red.log")"
[ "$red_rc" != 0 ] && ok "a failing suite makes the runner exit non-zero (rc=$red_rc)" \
                   || bad "a FAILING suite exited 0 — the runner cannot report failure"
grep -q 'suite-rc=1' <<<"$red_out" && ok "the reported suite-rc is the suite's, not the filter's" \
                                   || bad "suite-rc line is wrong: $(grep suite-rc <<<"$red_out")"
grep -q 'FAILURE: 3 checks failed' <<<"$red_out" && ok "the failure text still reaches the operator" \
                                                 || bad "the failing output was swallowed"

echo "== and it must still go GREEN"
green_rc="$(run_body 'echo "== a section"; echo "ALL PASS"; exit 0' green)"
green_out="$(cat "$WORK/out-green.log")"
[ "$green_rc" = 0 ] && ok "a passing suite still exits 0" \
                    || bad "a PASSING suite exited $green_rc — the runner cries wolf"
grep -q 'suite-rc=0' <<<"$green_out" && ok "suite-rc=0 on the passing run" || bad "no suite-rc=0"
grep -q 'ALL PASS'   <<<"$green_out" && ok "the pass summary reaches the operator" || bad "summary lost"

echo "== a non-zero status must survive a suite that prints nothing matchable"
# The filter matches nothing here, so the display path takes its fallback
# branch. The verdict must not care.
quiet_rc="$(run_body 'echo unmatchable; exit 3' quiet)"
[ "$quiet_rc" = 3 ] && ok "exit status passes through the display fallback (rc=3)" \
                    || bad "status lost when no summary line matched (rc=$quiet_rc)"

echo "== a missing staged tree must be loud, not a silent pass"
# The far side runs in whatever was sent. If nothing was, saying so is the whole
# difference between a gate and a rubber stamp.
missing_in="$WORK/in-missing.sh"
{
    printf 'REF=%q\n' main
    printf 'SUITE=%q\n' 'exit 0'
    printf 'DIRTY=%q\n' 0
    printf 'WORKDIR=%q\n' "$WORK/there-is-no-such-tree"
    printf 'LOGDIR=%q\n' "$WORK/log-missing"
    printf 'SKIP_APT=1\n'
    cat "$BODY"
} > "$missing_in"
HOME="$WORK" bash "$missing_in" >"$WORK/out-missing.log" 2>&1
missing_rc=$?
[ "$missing_rc" != 0 ] && grep -q 'no staged tree' "$WORK/out-missing.log" \
    && ok "an absent staged tree fails loudly (rc=$missing_rc)" \
    || bad "an absent staged tree gave rc=$missing_rc: $(tail -1 "$WORK/out-missing.log")"

echo "== structural: the shapes that caused this must not come back"
# `$?` may only ever be read on the line after a redirect-to-file, never after
# a pipe. Checked structurally because the bug is invisible when it is right.
bad_dollar="$(awk 'prev ~ /\|/ && prev !~ /^[[:space:]]*#/ && $0 ~ /\$\?/ {print NR": "$0} {prev=$0}' "$BODY" "$RUNNER")"
[ -z "$bad_dollar" ] && ok "no \$? is read on the line after a pipe" \
                     || bad "\$? read after a pipe: $bad_dollar"
# Comment lines are excluded: this rule is explained in prose in both files,
# and a check that trips over its own documentation teaches people to delete
# the documentation.
lc_hit="$(grep -hn 'bash -lc' "$RUNNER" "$BODY" | grep -vE '^[0-9]+:[[:space:]]*#' || true)"
[ -z "$lc_hit" ] && ok "plain 'bash -c', never 'bash -lc'" \
                 || bad "uses 'bash -lc' — a login shell can reset PATH, and stop_gate.sh forbids it: $lc_hit"
grep -q 'exit "$suite_rc"' "$BODY" && ok "the body exits with the captured suite status" \
                                   || bad "the body does not exit with \$suite_rc"
grep -q 'EXTRA=' "$RUNNER" && bad "the unused EXTRA slot is still there" \
                           || ok "no assigned-and-never-used argument slot"

echo
if [ "$fails" = 0 ]; then echo "REMOTE GATE STATUS: ALL PASS"; else echo "REMOTE GATE STATUS: $fails FAILURE(S)"; fi
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
