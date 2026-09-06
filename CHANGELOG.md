# Changelog

## 0.2.2 — 2026-09-07

The governance guard, fixed three times in one day. Each defect was found by the
one before it, and the third is the one worth reading.

### Fixed

- **The guard blocked reads.** Shell coverage arrived in 0.2.1 and collected
  every token after `sed`, `perl` and `awk` regardless of an in-place flag, and
  every token after `git` regardless of the subcommand. Reading a protected file
  with `sed -n`, `git diff` or `git show` was refused. That is worse than missing
  a write: a guard that blocks reading teaches everyone to keep the override
  exported, after which it guards nothing. Filters now count only with `-i` or
  `--in-place`; git only for the subcommands that touch the working tree.
- **The escape hatch could not be reached from a shell command.** A hook runs as
  its own process, so an inline `GOVERNANCE_EDIT_OK=1 cmd` prefix lives in the
  command string and never reaches the environment the hook reads. The
  documented door did not open for any shell write. The prefix is now parsed
  from the command itself; the environment variable still works.
- **The pin for that second fix could not fail.** Its probe used a path that
  normalises to a directory and never matches a protected pattern, so both door
  tests reported success whatever the hook did. Fixed with the real path plus a
  control asserting the write IS refused when neither door is open.
  `tests/pins/governance-prove-red.sh` now breaks each half in turn and requires
  the pin to report it; the suite asserts three of three.

### Notes

The case lists live in files rather than inline in the suite, because a shell
command containing the literal write shapes trips the guard under test. Two of
the three defects were found by the guard refusing its own author a read, and
independently by a reviewer.

## 0.2.1 — 2026-09-07

A guard that was never guarding, found by review rather than by the suite.

### Fixed

- **`protect_governance` covered the edit tools and left the shell open.** The
  hook matched the four editing tools while `cat > specs/foo.md`, `tee`,
  `sed -i`, `cp`, `mv` and `rm` walked straight past it. Its sibling
  `protect_tests` has carried shell coverage from the start, three lines away in
  the same file. This is not hypothetical: the runtime specification that landed
  in 0.2.0 was written through this gap. Bash is now matched, and every path a
  writing command might touch is collected — redirect targets and every non-flag
  token after the tools that write. Deliberately over-broad; a false block costs
  one visible override, a miss is a silent write to ratified text. Reads pass
  through. Pinned: eight write shapes blocked, five benign commands allowed, the
  ratified override still open, and the matcher itself asserted from the wiring.
  The first attempt scanned only the token after the tool and missed
  `sed -i "" s/a/b/ specs/x.md`; that miss is in the pin set.
- **The suite called a `section()` helper it never defined.** Three interpreter
  errors printed on every run, on both platforms, since the day before. The
  checks themselves ran and counted, so no verdict was ever wrong, but a helper
  that had never been exercised anywhere shipped in 0.2.0. A run on Linux is
  what made it visible.

### Changed

- The board carries the findings from the first external review of the runtime
  specification, and two of that specification's four escalations were withdrawn
  because the repository already answers them.

## 0.2.0 — 2026-09-07

Consolidation release. Everything from the alpha line reaches `main` in one
piece, plus the knowledge layer's fixes and the first two 0.3 items.

### The audit that produced this release

Six pull requests reported as merged, but four of them merged into an
intermediate branch of the stack rather than into `main`. `main` sat at
`0.2.0-alpha.2` while the knowledge layer, the commerce profile, the fan-out
runner, the judge and the Bash offload existed only on branches. A merged pull
request is not a landed change; only `git merge-base --is-ancestor` says so.
This release is the union, gated as a whole.

### Added

- **Memory adapters** — a registry over a code graph, a memory palace and plain
  files, each degrading to a named fallback with the exact command to fix it.
  One CLI, concise output by default.
- **The knowledge layer** — typed concepts with a mailbox, a rulings extractor
  over the inbox and the decision records, a coverage compiler that asks which
  invariant is actually enforced by something, a ticks ledger and its metrics.
- **Commerce profile** — block patterns, protected paths, recall triggers, an
  EARS constitution and an end-state snapshot check.
- **Fan-out** — briefs to parallel runs in their own worktrees.
- **`judge.py`** — the pairwise, position-swapped judge as a script: two calls
  per criterion with the positions swapped, disagreement is a tie at half
  confidence, and the judge may never be the generator.
- **`offload_rewrite`** — a noisy Bash command is rewritten before it runs so
  its output lands in a file and only a cap reaches the window.
- **The plugin runs its own loop** — a queue, an inbox, the contracts, a
  tracked gate config, and Linux continuous integration.
- **`ROADMAP.md`**, `docs/loop-ladder.md`, `docs/ADOPTION.md`, and the runtime
  plan under `docs/research/`.
- **`tools/release.sh`** — a version, a tag, a release, notes from this file.

### Fixed

- The leak grep skipped directories only, so in a worktree the `.git` pointer
  file failed every run.
- An unquoted multi-word value in a sourced gate config ran its second word as
  a command.
- A default containing a brace closed its own parameter expansion.
- The Bash offload dropped every input field except the command, because the
  hook returned a partial input object where the reference says the returned
  object replaces the whole one.
- A citation pin that could not fail, twice: the quoted text is now vendored
  with the source digest and diffed byte for byte against the header.

## Unreleased

- `hooks/offload_rewrite.py` — PreToolUse on Bash returns
  `hookSpecificOutput.updatedInput` (hooks reference, "Decision control") for
  a command matching a line of `.loopkit/offload-patterns.txt`: it runs as
  `bash <plugin>/scripts/run-capped.sh -- '<original>'`, full output to
  `.loopkit/scratch/`, head+tail in context, one `offload_rewrite` metrics
  event. Passive without the file (init lays down a comment-only one); never
  a permission decision; unchanged when already wrapped, on a heredoc, on a
  newline, and on any error. The commerce profile adds one pattern (payments
  CLI `… list`).
- `scripts/run-capped.sh` — one word after `--` is a shell string and runs
  under `bash -o pipefail -c`, so the hook's single-quoted form keeps the
  exit code (`'false | true'` → 1). Several words are still an argv.
- `scripts/judge.py` — the pairwise judge discipline as a script
  (`specs/pairwise-judge-verdicts.md`): `claude -p --output-format json`
  twice per criterion with positions swapped; the two runs disagreeing is
  `FINAL: TIE confidence 0.5`, agreeing is that verdict with the confidence
  clamped to [0.5, 1.0]; `--judge` equal to `--generator` exits 3 with no
  verdict. The prompt is built from judge.md's Rules section at run time —
  the script carries no rule text. One block per `--criterion` in judge.md's
  output shape, raw responses under `state/judge/<ts>-<pid>.json`, one
  `judge` event (final, confidence) in `state/ticks.jsonl`. Selftest pins
  it with a stub `claude`, as fan-out is pinned; `judge.md` and `/doctor`
  point at it.

## 0.2.0-alpha.4 — 2026-09-06 (batch d: profiles, fan-out, the override counter)

- `loopkit-init.sh --profile commerce` — templates and checks for a project
  built on Anthropic's commerce-agents blueprint, never a product: named block
  patterns (`no-live-keys`, `no-live-mode-flag`, `no-payout-refund-capture-from-shell`,
  `no-ledger-deletes`), protected `pricing/`/`catalog/`/`policies/`, recall
  triggers on checkout/refund/pricing code, EARS constraints appended to
  `constitution.md`, a snapshot-eval template, `check-snapshot.py` (end state,
  never path), and the `commerce-review` skill with its six-line checklist.
  Marker-guarded; a second run is a no-op.
- `scripts/fanout.sh` — one headless `claude -p` per brief, each in its own
  worktree, tools and turns scoped, one JSON result per brief, worktrees
  removed, nothing merged. `templates/brief.md` is the shape.
- The stop gate counts consecutive blocks per session: the seventh says the
  eighth will be overridden by Claude Code and records
  `gate_override_imminent`; a PASS resets. Stdin is read once, bounded.
- `docs/research/watchlist.md` — ads, ACP, AP2, GEO and the DeepSeek
  subagent packages: what is published (nothing, or ad-free), what is claimed,
  when to look again. Research only, by owner ruling.
- Deferred, stated plainly: a `claude -p` judge runner and PreToolUse
  `updatedInput` offload need live semantics not yet verified here.

## 0.2.0-alpha.3 — 2026-09-06 (batch c: OKF and the knowledge layer)

The thesis becomes testable: a project's rulings, traps and invariants as
typed, drift-checked concepts that a tick reads before acting and a check can
prove are enforced.

- `loopkit_memory/vendor/` — `okf_bundle.py` and `knowledge_actor.py`
  vendored verbatim (stdlib, deterministic mailbox actor, exit-code contract)
  with provenance headers; `loopkit_memory/okf.py` wraps them; a mutable
  queue is refused as a source at enqueue.
- `memory.py knowledge init|status|search|get|enqueue|drain|verify|reindex|scan-drift`;
  `recall` now spans notes AND concepts. `init` seeds ten loop-doctrine
  concepts citing the project's own constitution and contracts; three carry
  `enforced_by`.
- K1 `rulings-extract.py` — RESOLVED inbox sections, ADR decisions, ruled
  lines → one upsert each (dry run by default). K2 `ticks.py` +
  `loop-metrics.py` — stage, gate, transition events; verified-success,
  gate pass rate, re-asks, blocked rows, each with `n=`. K3
  `rulings-compile.py` — every Invariant/Gate names an artefact that exists;
  named extra block patterns (`#name`) carry `ruling:` in the BLOCKED line.
  K4 the tick doctrine injects the lane's Traps. K5 `knowledge` is a
  loop-assess layer.
- `protect_governance` guards the bundle once enabled (`KNOWLEDGE_EDIT_OK=1`).

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

First public cut, extracted from a production financial OS systems and stripped
of everything but core usage.

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
