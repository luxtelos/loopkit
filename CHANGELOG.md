# Changelog

## Unreleased — after 0.2.3

Not in any installed copy yet: Claude Code caches a plugin per version string,
so this reaches consumers only when the manifest version is bumped.

### Added

- **A new inbox section says what was searched before a human was asked.**
  Every NEW open `## ` section of `inbox/needs-human.md` carries
  `Precedent searched: <queries run> → <result>`. The `decision` route used to
  ask for the whole context and the cost of both options and never asked
  whether anybody had looked, so a question whose answer was already a ruling
  encoded in code went to the human anyway.
  `scripts/check-inbox-precedent.py` reads the FILE, not the command that wrote
  it, and runs where every write path converges: `loop-commit.sh` refuses the
  commit before staging (exit 65; not a git hook, so `--no-verify` does not
  skip it), and `stop_gate.sh` refuses the stop — before its no-code
  short-circuit, and against its own merge base, so a section that arrived by
  a bare `git commit` is still judged.
  The shape is exact: the label `Precedent searched:` at the start of one
  plain line, the arrow `→` (U+2192; `->`, `-->`, `=>` are refused by name),
  text on both sides. A line inside a fenced or indented code block or an HTML
  comment does not count, and neither does a label with its result on the next
  line. `→ no ruling found` is a real result; an empty side, the template's
  placeholders, or `none → none` is refused.
  A section is old by IDENTITY: heading and body unchanged from the base. An
  old section left as it was is never judged, and neither is a closed heading
  (`RESOLVED <date> — …`), so no existing inbox needs rewriting. An edited old
  section, or a new question under an old heading, is judged.
  `loop-commit.sh` asks the stop gate for its base (`stop_gate.sh
  --print-base`), so the two always give the same answer for the inbox.
  **Upgrade note:** an open branch that added a section without the line,
  before this rule existed, is new against the trunk. Once you upgrade, the
  stop gate blocks that branch, and `loop-commit.sh` refuses any commit that
  names the inbox, until the line is added. Commits that do not name the
  inbox still go through. Editing an old open section also needs the line,
  or a RESOLVED stamp.
- `skills/loop-assess` — the `decision` route now searches first (memory
  adapter, code, `docs/adr/` and `specs/`) and records the line; a hit reroutes
  the finding to `knowledge` and nobody is asked.
- `templates/needs-human.md` lists the line as the fifth thing a section
  carries; `templates/brief.md` gains an "Owner questions" section. The brief
  half is a rule an agent keeps — nothing reads a brief, so nothing enforces it.
- `tests/pins/inbox-precedent-gate.py` and `inbox-precedent-prove-red.sh` —
  71 cases, and fifteen mutations that each have to turn their own tag red.

## 0.2.3 — 2026-10-03

The guard rail that was only a sentence.

This version exists because #50, #51 and #52 merged while both manifests still
said 0.2.2. Claude Code caches an installed plugin per version string, so an
unbumped manifest reaches nobody: every consumer kept the 0.2.2 copy. Merged in
this version:

- #50 — `git-add-force` is case-sensitive, so `-F` no longer reads as `-f`.
- #51 — prose in a heredoc body is not a commit for `require_commit_lock`.
- #52 — push rules read the shell, not a regex (`loopkit_core/push_guard.py`).
- #56 — `require_recall` ignores comments, by the file's own comment syntax
  (below), and the version bump itself.

Everything else in this section had also merged after the 0.2.2 tag and was
never released until now.

### Added

- **A secret guard on Bash, not only on `.mcp.json`.** `hooks/block_dangerous.py`
  now refuses a command whose TEXT carries a secret-looking literal. 0.2.2 taught
  `check-tools.py` the GitHub token family, and that was right as far as it went,
  but `check-tools.py` reads `.mcp.json` files and nothing else — while the token
  that was actually burned on 2026-09-07 never touched one. It was passed inline
  on an SSH command line, into the remote host's process table where any user can
  read it with `ps`, and into a transcript. With 0.2.2 merged that command still
  ran unblocked. It is refused now.
  The refusal reports the SHAPE (`github`, `doppler`) and never the value, and it
  says what to do instead — stdin, `--env-file`, a credential helper — because a
  guard that only says no gets worked around and the workaround is usually worse.
  Switchable off by naming `secret-on-command-line` in
  `.loopkit/block-disabled.txt`, like any other pattern.
- **`loopkit_core/secrets.py`** — the token-shape table, written once and
  imported by both scanners. Two copies of one regex are two regexes by the end
  of the month; `tests/pins/secret-shapes.py` asserts the two callers hold the
  same regex OBJECT, not merely the same behaviour.
- **Fourteen shapes** the table did not know: Doppler (`dp.pt.`) and Render
  (`rnd_`) — this repo's own toolchain, so not hypothetical — plus GitLab,
  Google, npm, HuggingFace, legacy OpenAI, Stripe restricted, Slack app-level and
  session, SendGrid, Twilio, an AWS secret access key, and a PEM `BEGIN … PRIVATE
  KEY` block.
- **A real driver lock.** `scripts/driver_lock.py` holds an `flock` on
  `<worktree>/.loopkit/driver.lock` — the worktree, because the thing actually
  shared is the git INDEX, one file per worktree and process-global within it.
  The kernel releases it when the holder exits, including on a `SIGKILL`, so
  there is no stale lock to reap. `driver_lock.py run -- <cmd>` puts any command
  under it; `driver_lock.py status` says who holds it.
- **`scripts/loop-commit.sh`** — the one way a loop process commits. It stages
  and commits the paths you name inside ONE critical section, and uses the
  `git commit -- <paths>` pathspec form as a second line of defence. There is
  deliberately no "commit everything" spelling.
- **`hooks/require_commit_lock.py`** (PreToolUse, Bash) refuses a bare
  `git commit` in a LoopKit project, naming the wrapper. Passive where there is
  no `.loopkit/`; openable with `LOOPKIT_COMMIT_UNLOCKED=1` (inline or in the
  environment) or by naming `commit-without-lock` in
  `.loopkit/block-disabled.txt`. "LoopKit project" means `.loopkit/` PLUS
  something init lays down (`state/triage.md` or `.loopkit/scopes.json`) — a
  bare `.loopkit/` is created by passive hooks in repos that never ran init,
  and the first cut of this gate was ready to refuse every commit in one.
- `scripts/test_driver_lock.py` and `hooks/test_require_commit_lock.py`, wired
  into `tests/selftest.sh`. The first carries a CONTROL that reproduces the
  original index sweep before measuring the fixed case, plus mutual exclusion
  under six concurrent holders, release after a `SIGKILL`, re-entrancy, and the
  `EX_TEMPFAIL` timeout. The selftest also asserts ten concurrent
  `triage_state.py upsert` calls keep all ten rows.

### Fixed

- **A comment that names an invariant no longer blocks.** `require_recall.py`
  matched its content rule against the whole body, so `# rows are keyed by the
  primary key of users` in a `.py` file, or a `//` line in a `.ts` file, was
  refused while changing no schema. The body is now read with the comment
  syntax of the file's language, picked from its extension (`comment_style`),
  and comment text is dropped before matching. Strings are always kept (real
  DDL lives in strings), `/*` opens a block only where it starts a word (in
  shell and YAML it is a glob), an unclosed block gives its text back, and a
  file type not in the table is not stripped at all. Each `edits[]` fragment is
  read on its own. Ported from a downstream fix that passed two rounds of
  independent review, with that review's three follow-ups:
  `dockerfile` matches only `dockerfile`, `dockerfile.<x>` with no code
  extension, or `<x>.dockerfile` (a prefix match read `dockerfile-render.ts`
  with `#` comments); `.fizz` uses Python comments; and YAML and JSX are each
  read twice — once as before, once opening a quote only where a YAML scalar
  or shell word can start (so `it's` is not a quote) or a backtick only where
  a JS expression can start (so a backtick in JSX text is not a template) —
  and the gate blocks if either reading shows an invariant. The second reading
  can only add blocks. Pinned in `hooks/test_require_recall.py` (126 cases,
  including the reviewer's round-2 attack corpus); the four accepted holes —
  an Edit fragment that starts inside a string — are reported, not asserted.
- **Prose in a heredoc is not a commit.** `require_commit_lock.py` refused a
  `gh issue create` whose body, written with `cat > f <<'EOF'`, said
  "`git add x && git commit -F m` passes there": the `&&` inside the prose read
  as a command position. A heredoc body is now blanked before matching only
  when a data sink opens it (`cat`, `tee`, `gh`, `paste`) and nothing after
  the operator is piped. Every other body stays visible — `bash`, `sudo bash`,
  `env sh`, `docker exec -i c sh`, `cat <<EOF | sh` — and a `<<` inside quotes,
  in a comment, escaped, or in `$(( ))` opens no heredoc at all. The first
  version used a blocklist and read any `<<WORD` as a heredoc; independent
  review found twelve commit shapes it let through that main blocked. Cases
  pinned in `test_require_commit_lock.py`.
- **The refusal message printed the command back.** `block_dangerous.py` ended
  every block with `dangerous command pattern detected: <the whole command>`. The
  commerce profile's `no-live-keys` pattern fires on `sk_live_` literals, so
  catching one echoed it straight into the transcript — the guard moved the
  secret rather than stopping it. The secret branch runs first and prints no
  command at all; the pattern branch now redacts, as a backstop for a shape the
  table does not know yet. A pattern that also matched is still named, so a
  project's named ruling does not silently stop appearing.
- **The leading `\b` made a glued token invisible.** `\b` between two word
  characters does not exist, so `my_gho_<body>` matched nothing. A token glued to
  a preceding word character is a leak, not a false positive; the anchor is gone.
- **Force push and remote delete are read the way the shell reads them.**
  `git-force-push` and `git-push-delete` were regexes with `[^\n]*`, so a plain
  push chained into `gh pr create` whose title said "-F ... -f" was refused.
  Stopping the regex at `;`, `&`, `|` was tried first and failed independent
  review: it let nine real force pushes and deletes through (a quoted `"-f"`,
  a `;` inside a quoted ref, `2>&1 -f`, `$(a; b) -f`, …), because a regex
  cannot read shell quoting. New `loopkit_core/push_guard.py`, ported from the
  copy a downstream project landed after three rounds of independent review: it splits
  the command at REAL separators only (quotes, `$( )`, backticks, heredocs,
  redirects honoured), tokenises each piece with `shlex`, and reads every
  `git … push` argv. It also catches what the regexes never did: `-uf`, `+main`,
  `-d`, `git -C dir push -f`, `--mirror`, `--prune`, abbreviated `--forc`/`--del`,
  `bash -c`/`eval`/heredocs that run a push. It FAILS CLOSED — unreadable
  quoting, a `$F` before the remote, or a crash on a command that mentions git
  and push is blocked. Rule names unchanged; each is still switchable off in
  `.loopkit/block-disabled.txt` on its own, and a short-flag cluster is read
  whole, so with `git-force-push` off, `-fd` / `-ufd` / `-df` still trip
  `git-push-delete` (review found them deleting a branch when the cluster was
  read only to its first letter). Pinned in `test_block_dangerous.py`.
- **Encodings that occur in ordinary config are now decoded before scanning**:
  base64, percent-encoding, `\uXXXX` inside a JSON string, and a shell line
  continuation (which is not an evasion — it is what the shell will actually
  run). Reversed text is deliberately NOT decoded, and the module says so: it is
  not a shape anybody produces by accident, and it would widen the
  false-positive surface for no real case.
- **`-F` no longer reads as `-f`.** Every pattern is matched case-insensitively,
  so `git-add-force` also caught an uppercase `-F` in a `git add` segment, and
  commit-message-from-file commands were refused. Agents dodged it with
  `--file=`. The flag part of the rule is now a case-sensitive `(?-i:...)`
  group: lowercase `-f` (also inside `-vf`, `-Af`) or `--force` only.
  `git-add-all` is unchanged. Control cases pinned in `test_block_dangerous.py`.

### Changed

- `triage_state.py` takes the driver lock around `upsert`, `update` and
  `ensure-schema` — every one of them was parse-then-write with nothing
  serialising it. `list` stays lock-free: a status question must never block on
  a driver mid-commit.
- `loopkit-init.sh` adds `.loopkit/driver.lock` to `.gitignore` (once, then it
  respects the project's decision, like every other ignore line it writes).
- COMMANDS.md, the `CLAUDE.md` standing rules, TOOLS.md, the `loop-tick` skill
  and the implementer/reviewer agents now point at the mechanism. The old row
  read "never run two loop drivers concurrently against `state/triage.md`; the
  state file is the lock", which named the wrong resource and enforced nothing.

### Why

On 2026-09-07, in a downstream project, a reviewer staged exactly one file by
name and a concurrent driver ran `git add … && git commit` in the same
worktree. The driver's commit published the reviewer's file under its own
message; the reviewer's own commit reported "nothing added to commit". Nothing
was lost and the provenance is wrong — a review verdict sits inside a commit
about something else. "Name the files" could not have prevented it: the race is
between an agent's own add and its own commit, and no prose rule can hold a
lock.

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
