#!/usr/bin/env bash
# nested-gate-env-hermetic.sh — a test that runs the stop gate as a CHILD must
# not inherit the OUTER gate's environment.
#
# WHY THIS PIN EXISTS. The reviewer is told to run
#
#     LOOP_FORCE_GATE=1 bash plugins/loopkit/hooks/stop_gate.sh
#
# On this repo the gate's suite is tests/selftest.sh, and that suite runs the
# same stop_gate.sh a dozen times against scratch repos to prove it works. Every
# one of those nested runs inherited the outer gate's environment:
#
#   * LOOP_FORCE_GATE=1 from the reviewer's command line — so the nested
#     "short-circuits with no code change" assertion, and the branch-shapes
#     control "an unchanged branch must still SKIP", ran the suite instead.
#   * every line of .loopkit/config.env, which stop_gate.sh exports with
#     `set -a` so its own children can read it — so the nested gate looked
#     for `bash tests/selftest-report.sh {report}` INSIDE the scratch repo,
#     found nothing, and failed at the test step. Two red-path assertions
#     (red lint, red tests) then "passed" for the wrong reason: they wanted
#     rc=1 and got rc=1 from a broken test step, never reaching the thing
#     they assert.
#
# Measured 2026-09-18 on #46 @ 11a2937 and on main @ de64af9: the forced gate
# reported 6 failures, all of them the suite's own environment; the same tree in
# a clean shell was 332 ok / 0 FAIL. A gate that cannot pass is the same defect
# as a gate that cannot fail — the reviewer's instrument was broken, not the
# code it measured.
#
# The fix is one helper, tests/lib/scrub-gate-env.sh, sourced at the top of
# every test that runs the gate as a child. A scrub per call site is the
# alternative, and repeated scrubs drift: the next LOOP_ knob gets added to the
# gate and to one of the lists. This pin asserts three things:
#
#   A. the real branch-shapes pin, run under the outer gate's environment,
#      still passes — the dynamic proof for the pin the suite delegates to;
#   B. the suite's four nested gate assertions (selftest.sh, the "# stop gate"
#      block), replayed under the same environment through the helper, get the
#      rc they want FOR THE REASON they want it — and a value a test sets on
#      purpose AFTER the scrub (LOOP_FORCE_GATE=1, explicitly) still takes;
#   C. every file under tests/ that invokes stop_gate.sh sources the helper and
#      calls scrub_gate_env before its first invocation — found by grep, never
#      by a person reading scripts, so a new caller without the scrub is red.
#
# usage: bash tests/pins/nested-gate-env-hermetic.sh [<repo-root>]

set -uo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
REPO="$(cd "$REPO" 2>/dev/null && pwd)" || { echo "FAIL no such repo root: $1"; exit 2; }
P="$REPO/plugins/loopkit"
GATE="$P/hooks/stop_gate.sh"
[ -f "$GATE" ] || { echo "FAIL no stop_gate.sh at $GATE"; exit 2; }
HELPER="$REPO/tests/lib/scrub-gate-env.sh"

fails=0
ok()   { echo "  ok   $1"; }
bad()  { echo "  FAIL $1"; fails=$((fails+1)); }

# This pin is itself a caller of the gate (check B below), so it scrubs its own
# entry like every other caller. Resolved next to THIS file, not under $REPO:
# stop-gate-prove-red.sh points pins at a skeleton that holds plugins/ only.
if [ -f "$(dirname "${BASH_SOURCE[0]}")/../lib/scrub-gate-env.sh" ]; then
    . "$(dirname "${BASH_SOURCE[0]}")/../lib/scrub-gate-env.sh" && scrub_gate_env
    ok "the scrub helper exists and sources"
else
    bad "the scrub helper is missing: tests/lib/scrub-gate-env.sh"
fi

# The outer gate's environment, built explicitly so this pin is red or green on
# its own terms and not by accident of whoever runs it. It is exactly what the
# reviewer's command leaves in the suite's environment, as measured: the
# LOOP_FORCE_GATE=1 from the command line, CLAUDE_PROJECT_DIR naming the OUTER
# project, and the repo's config.env lines, exported by the gate's `set -a`
# (values copied from .loopkit/config.env as measured, and deliberately not
# re-sourced from it, so this pin does not move when that file does).
# CLAUDE_PLUGIN_ROOT is pinned separately, in D, because it changes WHICH
# failure the other cases show and would muddle A and B.
polluted() {  # <cmd...> — run it under the outer gate's environment
    env LOOP_FORCE_GATE=1 \
        CLAUDE_PROJECT_DIR="$REPO" \
        LOOP_TEST_JSON_CMD="bash tests/selftest-report.sh {report}" \
        LOOP_TEST_CMD="bash tests/selftest.sh" \
        LOOP_LINT_CMD="python3 plugins/loopkit/scripts/check-skills.py --plugin plugins/loopkit --strict" \
        LOOP_LINT_FALLBACK_CMD= \
        LOOP_TYPECHECK_CMD= \
        LOOP_BUILD_CMD="claude plugin validate ." \
        LOOP_CODE_GLOBS="*.py *.sh *.mjs *.json" \
        "$@"
}

echo "== A. the branch-shapes pin under the outer gate's environment"
bs_out="$(polluted bash "$REPO/tests/pins/stop-gate-branch-shapes.sh" "$REPO" 2>&1)"; bs_rc=$?
if [ "$bs_rc" = 0 ] && grep -q 'BRANCH SHAPES: ALL PASS' <<<"$bs_out"; then
    ok "branch shapes still ALL PASS with LOOP_FORCE_GATE=1 and config.env inherited"
else
    bad "branch shapes inherit the outer gate's env (rc=$bs_rc): $(grep -E '^  FAIL|^BRANCH SHAPES' <<<"$bs_out" | tr '\n' ' ')"
fi

echo "== B. the suite's nested gate assertions, replayed through the helper"
# The body mirrors tests/selftest.sh's "# stop gate" block line for line, run
# in a shell that inherits the pollution and then does what the suite does:
# source the helper, scrub, and go. Each case prints "<label> rc=<n> <marker>"
# where the marker is the reason the gate gave, so a case that gets the wanted
# rc for the wrong reason is visible.
replay="$(polluted bash -c '
    set -uo pipefail
    HELPER="$1"; P="$2"; GATE="$P/hooks/stop_gate.sh"
    if [ -f "$HELPER" ]; then . "$HELPER" && scrub_gate_env >/dev/null; else echo "helper-missing"; fi
    T="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-nested-gate.XXXXXX")"
    ( cd "$T" && git init -q . && git -c user.email=t@t -c user.name=t -c commit.gpgsign=false commit -q --allow-empty -m init )
    export CLAUDE_PROJECT_DIR="$T"
    bash "$P/scripts/loopkit-init.sh" --project "$T" >/dev/null 2>&1
    reason() { grep -oE "SKIP: no changed code files|FAIL: lint failed|FAIL: new test failures|FAIL: test suite failed|No such file or directory|PASS: all gates passed" | sort -u | tr "\n" "," ; }
    out="$(bash "$GATE" 2>&1)"; echo "short-circuit rc=$? $(reason <<<"$out")"
    echo "x" > "$T/app.ts"
    export LOOP_TEST_CMD="true" LOOP_LINT_CMD="true" LOOP_TYPECHECK_CMD="" LOOP_BUILD_CMD="true"
    out="$(bash "$GATE" 2>&1)"; echo "green rc=$? $(reason <<<"$out")"
    out="$(LOOP_LINT_CMD="false" LOOP_LINT_FALLBACK_CMD="" bash "$GATE" 2>&1)"; echo "red-lint rc=$? $(reason <<<"$out")"
    out="$(LOOP_TEST_CMD="false" env LOOP_TEST_JSON_CMD="false" bash "$GATE" 2>&1)"; echo "red-tests rc=$? $(reason <<<"$out")"
    unset LOOP_TEST_CMD LOOP_LINT_CMD LOOP_TYPECHECK_CMD LOOP_BUILD_CMD
    # Control: a knob a test sets ON PURPOSE after the scrub must still take.
    rm -f "$T/app.ts"
    # An empty jest-shaped report, written by python so no shell quoting can
    # strip the braces or the quotes on the way through `bash -c`.
    FAKE_REPORT="python3 -c \"import json,sys; json.dump(dict(testResults=[]), open(sys.argv[1], chr(119)))\" {report}"
    out="$(LOOP_FORCE_GATE=1 LOOP_TEST_CMD="echo FORCED-SUITE-RAN" LOOP_LINT_CMD=true LOOP_TYPECHECK_CMD= LOOP_BUILD_CMD= LOOP_TEST_JSON_CMD="$FAKE_REPORT" bash "$GATE" 2>&1)"
    echo "explicit-force rc=$? $(grep -oE "SKIP: no changed code files|PASS: all gates passed" <<<"$out" | sort -u | tr "\n" ",")"
    find "$T" -delete
' _ "$HELPER" "$P" 2>&1)"

case_line() { grep -E "^$1 rc=" <<<"$replay" | head -1; }
grep -q '^helper-missing' <<<"$replay" && bad "replay ran without the helper (missing)"
l="$(case_line short-circuit)"; case "$l" in *"rc=0"*"SKIP: no changed code files"*) ok "nested: short-circuits with no code change ($l)" ;; *) bad "nested: did not short-circuit — LOOP_FORCE_GATE leaked in ($l)" ;; esac
l="$(case_line green)";         case "$l" in *"rc=0"*"PASS: all gates passed"*)      ok "nested: passes with green commands ($l)" ;;      *) bad "nested: green commands failed — LOOP_TEST_JSON_CMD leaked in ($l)" ;; esac
l="$(case_line red-lint)";      case "$l" in *"rc=1"*"FAIL: lint failed"*)           ok "nested: fails on red lint, for the lint reason ($l)" ;; *) bad "nested: red lint did not fail on LINT ($l)" ;; esac
l="$(case_line red-tests)";     case "$l" in *"rc=1"*"FAIL: new test failures"*)     ok "nested: fails on red tests ($l)" ;;               *) bad "nested: red tests did not fail on the regressions diff ($l)" ;; esac
l="$(case_line explicit-force)"; case "$l" in *"rc=0"*"PASS: all gates passed"*)     ok "control: LOOP_FORCE_GATE=1 set by the test itself still forces ($l)" ;; *) bad "control: the scrub ate a knob the test set on purpose ($l)" ;; esac

echo "== D. a hook-context CLAUDE_PLUGIN_ROOT does not redirect a nested gate"
# stop_gate.sh resolves its siblings through CLAUDE_PLUGIN_ROOT when that is
# set — right for a hook, wrong for a nested test: a gate run from a hook
# context would hand the nested gate the INSTALLED plugin's scripts, not the
# tree under test. Observable: with a bogus root the regressions script is not
# found, the gate silently takes the plain LOOP_TEST_CMD path, and "Running the
# suite once" never prints — so a green nested run tells you nothing about the
# regressions path the suite means to exercise. After the scrub the nested gate
# must resolve the plugin from its own location and print that line.
d_out="$(env CLAUDE_PLUGIN_ROOT="$REPO/nonexistent-plugin-root" bash -c '
    set -uo pipefail
    HELPER="$1"; P="$2"; GATE="$P/hooks/stop_gate.sh"
    if [ -f "$HELPER" ]; then . "$HELPER" && scrub_gate_env >/dev/null; fi
    T="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-nested-gate-d.XXXXXX")"
    ( cd "$T" && git init -q . && git -c user.email=t@t -c user.name=t -c commit.gpgsign=false commit -q --allow-empty -m init )
    export CLAUDE_PROJECT_DIR="$T"
    bash "$P/scripts/loopkit-init.sh" --project "$T" >/dev/null 2>&1
    echo "x" > "$T/app.ts"
    FAKE_REPORT="python3 -c \"import json,sys; json.dump(dict(testResults=[]), open(sys.argv[1], chr(119)))\" {report}"
    LOOP_TEST_CMD=true LOOP_LINT_CMD=true LOOP_TYPECHECK_CMD= LOOP_BUILD_CMD= \
        LOOP_TEST_JSON_CMD="$FAKE_REPORT" bash "$GATE" 2>&1
    find "$T" -delete
' _ "$HELPER" "$P" 2>&1)"
grep -q 'Running the suite once' <<<"$d_out" \
    && ok "nested gate resolves the plugin from its own location, not CLAUDE_PLUGIN_ROOT" \
    || bad "CLAUDE_PLUGIN_ROOT leaked in — the regressions path was never exercised: $(grep -E '^>> (Testing|PASS|FAIL)|^FAIL' <<<"$d_out" | tr '\n' ' ')"

echo "== C. every caller of stop_gate.sh under tests/ scrubs before it calls"
# A caller is a file with a non-comment `bash … hooks/stop_gate.sh` line, or one
# that assigns GATE=…hooks/stop_gate.sh and runs `bash "$GATE"`. Comments and
# messages that merely mention the file are not callers. Found by grep so the
# list cannot be an opinion; printed so the PR can quote it.
callers="$( {
    grep -rlE '^[^#]*bash [^#]*hooks/stop_gate\.sh' "$REPO/tests" 2>/dev/null
    for f in $(grep -rlE '^[^#]*GATE="[^"]*hooks/stop_gate\.sh"' "$REPO/tests" 2>/dev/null); do
        grep -qE '^[^#]*bash "\$GATE"' "$f" && echo "$f"
    done
} | sort -u )"
[ -n "$callers" ] || bad "found no caller of stop_gate.sh under tests/ — the grep is wrong, not the tree"
for f in $callers; do
    rel="${f#$REPO/}"
    first_call="$(grep -nE '^[^#]*(bash [^#]*hooks/stop_gate\.sh|bash "\$GATE")' "$f" | head -1 | cut -d: -f1)"
    scrub_at="$(grep -nE '^[^#]*\bscrub_gate_env\b' "$f" | grep -vE 'scrub_gate_env\(\)' | head -1 | cut -d: -f1)"
    if ! grep -q 'scrub-gate-env\.sh' "$f"; then
        bad "$rel invokes the gate (line $first_call) and never sources tests/lib/scrub-gate-env.sh"
    elif [ -z "$scrub_at" ]; then
        bad "$rel sources the helper but never calls scrub_gate_env"
    elif [ "$scrub_at" -gt "$first_call" ]; then
        bad "$rel calls scrub_gate_env at line $scrub_at, AFTER its first gate call at line $first_call"
    else
        ok "$rel scrubs at line $scrub_at, before its first gate call at line $first_call"
    fi
done

echo
if [ "$fails" = 0 ]; then echo "NESTED GATE ENV: ALL PASS"; else echo "NESTED GATE ENV: $fails FAILURE(S)"; fi
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
