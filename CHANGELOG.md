# Changelog

## 0.1.0 — 2026-09-06

First public cut, extracted from a production accounting-OS repo and stripped
of everything that named it.

- Hooks: `block_dangerous` (destructive floor + merge/approve/stage-all refusal,
  per-project extras and disables), `protect_governance` (absolute-path guard on
  `constitution.md` and `specs/`), `require_contracts` (read FILES/TOOLS/COMMANDS
  before work; passive until `init`), `loop_doctrine` (tick discipline injected
  on `/loop`), `notify_needs_human` (webhook on inbox edits), `stop_gate`
  (precheck → regression diff → lint → typecheck → build, every command
  configurable, explicit-empty skips), `session_start`.
- Scripts: `loop-next.sh` + `loop_next_pick.py` (stage as a lookup, lanes,
  blocked rows, fan-out), `loop-scan.py` (two API calls, READY TO MERGE),
  `loop-watch.sh` (cheap PR poll, per-PR baseline), `triage_state.py`,
  `inbox_to_triage.py` (create-only bridge), `test-regressions.sh` (jest/vitest
  JSON or any runner via `--from-list`), `check-citations.py` (structural +
  pinned), `morning-triage.sh`, `loopkit-init.sh`.
- Skills: loop-tick, loop-scan, loop-assess (+ failure table, measurement,
  instruction audit), morning-triage, spec-writer, acceptance-review, pr-review,
  run-state-model (FizzBee / Quint / TLA+ behind one exit-code contract).
- Agents: planner, implementer, reviewer.
- Templates for `init`: constitution, the three contracts, mutation policy,
  triage queue, test baseline, inbox, CLAUDE.md rules block, `.loopkit/` config.
- `tests/selftest.sh`: every unit test plus an end-to-end pass in a scratch repo.
