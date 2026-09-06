# Changelog

## 0.2.0-alpha.2 — 2026-09-06 (batch b: memory adapters)

Memory as pluggable adapters instead of RAG, and the recall gate that makes
them matter.

- `loopkit_memory/` — a registry read from `.loopkit/memory.json` with three
  adapter kinds: graph (codebase-memory CLI, grep fallback), memory (mempalace
  CLI for recall/wake-up, note-then-mine for remember, files fallback),
  knowledge (OKF, arrives in alpha.3). Every adapter answers `available()`
  with a reason; every output begins `ADAPTER: <kind>=<name> [available|DEGRADED: …]`.
  In a git worktree the `.venv` and palace resolve from the main checkout.
- `scripts/memory.py` — `status | recall | remember | invalidate | wakeup |
  graph find/callers/callees/snippet/impact`, `--format concise` (30 lines,
  rest to `.loopkit/scratch/`) by default.
- `hooks/require_recall.py` — the recall gate, ported: a storage-invariant
  write (migration path, invariant SQL in code, a shell write into a
  migrations directory) is blocked until memory was consulted this session.
  Rules in `.loopkit/recall-triggers.txt`; what counts as recall comes from the
  registry, never a server name; audited outage escape for content-rule writes.
  Passive without `memory.json`.
- Session start prints the `MEMORY:` line; `protect_governance` guards the
  knowledge bundle when enabled; `init` lays down `memory.json` and
  `recall-triggers.txt`.
- `scripts/run-capped.sh` + `hooks/offload_nudge.py` — big tool output to a
  file with head/tail in context; a non-blocking nudge (and a metrics line)
  when a Bash result exceeds 8 KB.
- `skills/memory` — the protocol, with Gotchas.

## 0.2.0-alpha.1 — 2026-09-06 (batch a: the practice floor)

Anthropic's published practices as checks and hooks, plus the oversight
paper's one lesson. Sources in the README.

- `protect_tests.py` (PreToolUse): a test file's test count may not drop, a
  test path may not be `rm`/`mv`'d, and `state/features.json` may only have
  `passes` flipped — "It is unacceptable to remove or edit tests"
  (effective-harnesses-for-long-running-agents). Escape `TEST_EDIT_OK=1`.
- `progress.py` + `precompact.sh` (PreCompact) + SessionStart `resume|compact`:
  the deterministic half of session memory — Files Modified from git, an
  append-only `state/progress.md` written by the stop gate, a five-section
  pre-compaction snapshot under `.loopkit/session/`.
- `check-claude-md.py`, `check-skills.py`, `check-tools.py`,
  `check-duplicate-hooks.py`: the CLAUDE.md budget (standing instructions,
  one emphasised line, duplicates, derivable lines), skill frontmatter +
  Gotchas + run/read verbs, the MCP surface (no row no server, absolute
  paths, secret literals, count), and project hooks that duplicate plugin
  hooks. All wired into `/loopkit:doctor`.
- `count_approvals.py` (PermissionRequest, never decides) + a doctrine line
  past `LOOPKIT_APPROVAL_FATIGUE_N` (30) prompts: approval fatigue named,
  not ignored (Mitchell, Ghosh & Passi 2026).
- Reviewer and pr-review: judge from the diff and criteria BEFORE reading the
  implementer's summary; flag only gaps that affect correctness. The Stop
  agent prompt carries the same order.
- `references/judge.md`: pairwise twice with swapped positions, justification
  before score, judge ≠ generator. `templates/brief.md`: the four-part
  subagent brief.
- `## Gotchas` on every skill.

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
