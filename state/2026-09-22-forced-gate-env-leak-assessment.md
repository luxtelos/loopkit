# Assessment — the forced stop gate cannot go green on this repo (2026-09-22)

Owner-observed 2026-09-18 on #46 @ `11a2937`, same on `main`. Assessed here on
`main` @ `de64af9` with loop-assess Mode A. No prior ruling found: `grep -rni
'LOOP_FORCE_GATE\|forced gate' inbox/ state/ docs/ specs/` is empty.

## Observed

`LOOP_FORCE_GATE=1 bash plugins/loopkit/hooks/stop_gate.sh` — the exact command
`plugins/loopkit/agents/reviewer.md:55` gives the reviewer — fails on an
unchanged `main` with six suite failures, while `bash tests/selftest.sh` on the
same tree in a clean shell has none.

## Evidence

Every `file:line` below is as of `de64af9`, the tree assessed; the fix moves
`tests/selftest.sh` down by 12 lines from its top.

Two runs on a pristine detached worktree of `de64af9`, nothing edited between:

1. The reviewer's command, from a Claude Code Bash tool (so its environment is
   the reviewer's). Verdict lines, quoted:

   ```
   >> bash tests/selftest-report.sh /Volumes/evm/tmp-agent/test-report.json.7rEvoN
   selftest-report: 332 assertions, 6 failed, rc=1
   FAIL: 6 NEW test failure(s) not in the baseline:
   FAIL: new test failures vs state/known-test-failures.txt
   GATE_RC=1
   ```

   The suite's own log (`selftest-log.G158na`, read directly — `selftest.sh:21`
   discards sub-suite output, so the summary count is not to be trusted):
   326 ok, 6 FAIL:

   ```
   FAIL stop gate short-circuits with no code change (rc=1, want 0)
   FAIL stop gate passes with green commands (rc=1, want 0)
   FAIL gate PASS not recorded
   FAIL no gate events
   FAIL gate now runs on a no-op turn: >> Type checking: skipped (command set to empty)
   FAIL branch shapes: BRANCH SHAPES: 1 FAILURE(S)
   ```

   Five distinct assertions; the fifth line is the branch-shapes sub-suite's
   own FAIL echoed by `selftest.sh:1127`, so it and the sixth are one failure.

2. Control, same tree: `env -i PATH HOME TMPDIR bash tests/selftest.sh` —
   recorded in the PR (running as this was written; the owner measured 332 ok /
   0 FAIL / rc=0 on 2026-09-18).

Could the measurement have failed? Yes: had the code been broken the same six
lines would appear in the clean run too, and had the environment been the cause
they would not. They do not. So the diff between the two runs is the
environment, not the tree.

Which variables, isolated with a probe that replays `selftest.sh:269-274` in a
scratch repo under three environments:

| environment                                        | 269 short-circuit | 272 green | 273 red lint | 274 red tests |
| -------------------------------------------------- | ----------------- | --------- | ------------ | ------------- |
| clean                                              | rc=0 SKIP         | rc=0 PASS | rc=1 lint    | rc=1 diff     |
| `LOOP_FORCE_GATE=1` only                           | **rc=1** (ran `npm run lint`) | rc=0 PASS | rc=1 lint | rc=1 diff |
| `LOOP_FORCE_GATE=1` + `config.env` exported (`set -a`, as the gate does) | **rc=1** `tests/selftest-report.sh: No such file` | **rc=1** same | rc=1 *at the test step* | rc=1 |

Two leaks, not one:

- `LOOP_FORCE_GATE=1` from the command line reaches every nested gate, so the
  two "unchanged tree must SKIP" assertions (`selftest.sh:269` and the
  branch-shapes control at `tests/pins/stop-gate-branch-shapes.sh:208`) run a
  suite instead. That is failures 1, 5 and 6.
- `stop_gate.sh:50-55` sources `.loopkit/config.env` with `set -a`, on purpose,
  so `test-regressions.sh` and the wrapper's children can read it. In the nested
  gate that same export makes `LOOP_TEST_JSON_CMD="bash tests/selftest-report.sh
  {report}"` run inside the scratch repo, where the file does not exist. That is
  failure 2, and failures 3 and 4 follow from it: no nested PASS was ever
  recorded, so `progress.md` and `ticks.jsonl` never carry one.
- The same leak hollows out two assertions that still report ok: `273` and
  `274` want rc=1 and get it from the broken test step, never reaching the lint
  or the regressions diff they assert. Green for the wrong reason.

## Control case

1. Plain `bash tests/selftest.sh` in a clean environment: 332 ok, 0 FAIL, rc=0.
   A scrub that changed that would be a regression wearing a fix's name.
2. Every gate knob a test sets on purpose must still take after the scrub:
   `selftest.sh:271` (`export LOOP_TEST_CMD=true …`), `:273-274` (red lint, red
   tests), `:800-813` (the seven-block override sequence), and branch-shapes
   case 8 (`LOOP_FORCE_GATE=1` passed explicitly). A scrub that wraps each call
   in `env -u` would eat those; a scrub at the entry of the file does not.
3. The outer gate must still be forceable — the scrub lives in the suite, not
   in the gate, so it cannot touch this.

## Classification

`measurement` — the harness layer, confirming the owner's expectation. The
reflex to resist is editing `stop_gate.sh`: drop the `set -a`, or `unset
LOOP_FORCE_GATE` before running the suite. Both are wrong. The export is
load-bearing for every consumer (`test-regressions.sh:45` reads
`LOOP_TEST_JSON_CMD` from the environment), and a gate that scrubs its own
environment for the benefit of one project's test suite bakes this repo's test
shape into a distributed plugin. The gate did nothing wrong: it ran the suite it
was told to, in the environment it was given. The suite is the instrument, and
an instrument that runs the thing it measures as a child must define that
child's environment rather than inherit whatever the caller had. Failure-table
row: "green in isolation, red together — it is usually the environment, not
pollution: one variable present under the runner and absent bare. Diff the env
of the two runs before touching a test."

## Route

A test-harness change in this repo, no spec: one helper,
`tests/lib/scrub-gate-env.sh`, sourced at the top of every file under `tests/`
that runs `stop_gate.sh` as a child, plus a pin,
`tests/pins/nested-gate-env-hermetic.sh`, that is red today and finds the
callers by grep. Callers found mechanically (`grep -rn stop_gate.sh tests/`,
then keeping only non-comment invocation lines): `tests/selftest.sh` (lines
269-274, 801, 810, 813) and `tests/pins/stop-gate-branch-shapes.sh` (line 79).
`tests/pins/stop-gate-prove-red.sh` copies the gate and mutates it but never
runs it — it runs the branch-shapes pin, which scrubs itself.

## Not a code problem

- `plugins/loopkit/hooks/stop_gate.sh` — unchanged, on purpose (see
  Classification). `plugins/loopkit/agents/reviewer.md:55` — the instruction is
  right; the suite under it was not.
- Observed in passing, not fixed here: the clean run of `selftest.sh:272`
  passes only because the nested `test-regressions.sh` falls back to
  `npx vitest run …` in the scratch repo, and `npx` fetches `vitest@3.2.7` from
  the registry (`npm warn exec The following package was not found and will be
  installed`). Offline, that assertion fails with `no JSON report produced`.
  The suite's README claim of "no network" is not true for that line. Cheap
  fix: the scratch repo gets a `LOOP_TEST_JSON_CMD` that writes an empty jest
  report, as `selftest.sh:808` already does further down. Left for its own
  finding so this PR stays one change.
- `selftest.sh:21` discards every sub-suite's output. This assessment read the
  suite log directly, as the task said to; the design choice is not touched.
