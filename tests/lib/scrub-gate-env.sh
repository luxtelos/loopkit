#!/usr/bin/env bash
# scrub-gate-env.sh — source this, then call scrub_gate_env, at the TOP of any
# test that runs plugins/loopkit/hooks/stop_gate.sh (or test-regressions.sh) as
# a child. Not executable on its own; it only defines the function.
#
#     . "$(dirname "${BASH_SOURCE[0]}")/../lib/scrub-gate-env.sh" && scrub_gate_env
#
# WHY. The reviewer runs `LOOP_FORCE_GATE=1 bash plugins/loopkit/hooks/stop_gate.sh`.
# On this repo the gate's suite is tests/selftest.sh, which runs that same gate
# a dozen times against scratch repos to prove it works — and every nested run
# inherited the OUTER gate's environment: the LOOP_FORCE_GATE=1 from the command
# line, and every line of .loopkit/config.env, which stop_gate.sh exports with
# `set -a` so its own children can read it. So the nested "unchanged tree must
# SKIP" assertions ran a suite instead, and the nested gate went looking for
# `bash tests/selftest-report.sh {report}` inside a scratch repo that has no
# such file. Measured 2026-09-18 (#46 @ 11a2937, main @ de64af9): the forced
# gate reported 6 failures, all its own environment; the same tree in a clean
# shell had none. A gate that cannot pass is the same defect as one that cannot
# fail. tests/pins/nested-gate-env-hermetic.sh pins it and can be proven red.
#
# WHY ONE HELPER and not an `unset` block per call site: repeated scrubs drift.
# The next knob gets added to the gate and to one of three lists, and the list
# that was missed is the one the reviewer's run hits. This file is the list;
# and it is a PREFIX, not names, so a knob added to the gate tomorrow is
# scrubbed today.
#
# WHAT IS SCRUBBED — every EXPORTED variable in the gate's namespace:
#   LOOP_*              every knob stop_gate.sh and test-regressions.sh read:
#                       the *_CMD set, FORCE_GATE, ENV_WRAPPER, CODE_GLOBS,
#                       TEST_BASELINE, MIN_NODE, and whatever comes next
#   GATE_BASE           the gate's resolved base, honoured via ${GATE_BASE-…}
#   CLAUDE_PROJECT_DIR  set by Claude Code for a hook; names the OUTER project,
#                       so a nested gate would cd there instead of its scratch
#   CLAUDE_PLUGIN_ROOT  set by Claude Code for a hook; names the INSTALLED
#                       plugin, so a nested gate would run that plugin's
#                       scripts instead of the tree under test
#
# WHAT IS KEPT — everything a test sets AFTER calling this. The scrub is a floor
# at entry, never a wall around each call: `export LOOP_TEST_CMD=true` on the
# next line takes, and so does an explicit `LOOP_FORCE_GATE=1 bash "$GATE"`.
# LOOPKIT_* is not touched: LOOPKIT_AGENT, LOOPKIT_DRIVER_LOCK, LOOPKIT_REMOTE
# and LOOPKIT_GATE_RUNNER are read by pins on purpose, and none of them is a
# gate knob. Only exported variables can reach a child, so only those are
# scrubbed; `compgen -A export` lists exactly that set on bash 3.2 and 5.

scrub_gate_env() {
    local v scrubbed=""
    for v in $(compgen -A export | grep -E '^(LOOP_|GATE_BASE$|CLAUDE_PROJECT_DIR$|CLAUDE_PLUGIN_ROOT$)' || true); do
        unset "$v"
        scrubbed="$scrubbed $v"
    done
    # Names only, never values: LOOP_ENV_WRAPPER can carry a secrets wrapper.
    # Two leading spaces so the line sits with the suite's NOTE lines and is
    # not an assertion — selftest-report.sh counts `  ok` and `  FAIL` only.
    if [ -n "$scrubbed" ]; then
        echo "  NOTE scrubbed inherited gate env (names):$scrubbed"
    fi
    return 0
}
