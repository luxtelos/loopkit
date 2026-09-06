# Getting started with LoopKit — first-timer guide

Thirty minutes, one project, one tick. Every step says what you should see, so
you know when something is wrong rather than wondering.

## 0. What you need

- Claude Code (the `claude` CLI, any recent version with `/plugin`)
- `python3`, `bash`, `git`
- `gh` logged in, if you want PR/issue scanning (`gh auth status`)
- `node`, only if you want the model checker (step 8)

Check:

```bash
claude --version && python3 --version && git --version && gh auth status
```

## 1. Get the plugin

**From GitHub** (once published):

```
/plugin marketplace add luxtelos/loopkit
/plugin install loopkit@loopkit
```

**From a local folder** (a clone, or before it is on GitHub):

```bash
git clone https://github.com/luxtelos/loopkit ~/loopkit     # or use an existing checkout
claude plugin marketplace add ~/loopkit
claude plugin install loopkit@loopkit
```

Both forms work inside a session (`/plugin …`) or from a shell (`claude plugin …`).
Add `--scope project` to either command if the plugin should belong to one
repo rather than to you.

You should see the marketplace `loopkit` and the plugin `loopkit` listed:

```bash
claude plugin marketplace list
claude plugin details loopkit@loopkit      # component inventory + token cost
```

## 2. Prove the plugin itself works (optional, 20 seconds)

From the clone:

```bash
bash tests/selftest.sh
```

Ends with `ALL PASS`. If it does not, stop here and read the `FAIL` line — do
not initialise a project with a plugin that cannot pass its own tests.

## 3. Initialise your project

Open Claude Code **in the project you want the loop to run in**, then:

```
/loopkit:init
```

You should see one line per file, `CREATED` on the first run and `KEPT` on
every later run — it never overwrites:

```
CREATED  state/
CREATED  state/triage.md
CREATED  inbox/needs-human.md
CREATED  constitution.md
CREATED  FILES.md
CREATED  TOOLS.md
CREATED  COMMANDS.md
CREATED  docs/MUTATION_POLICY.md
CREATED  .loopkit/scopes.json
…
APPENDED .gitignore: .loopkit/config.env
APPENDED .prettierignore: state/triage.md
APPENDED CLAUDE.md: LoopKit standing rules
```

Without a session, the same thing from a shell:

```bash
bash "$(claude plugin details loopkit@loopkit 2>/dev/null | grep -oE '/[^ ]+/plugins/loopkit' | head -1)/scripts/loopkit-init.sh"
```

(or simply `bash ~/loopkit/plugins/loopkit/scripts/loopkit-init.sh` from a clone).

From the next tool call, three guards are live: destructive commands, edits to
`constitution.md` and `specs/`, and a gate that blocks work tools until the
three contracts have been read. Read them now — `FILES.md`, `TOOLS.md`,
`COMMANDS.md` — they are short.

## 4. Tell the gate how to test your project

Copy the example and fill in your commands:

```bash
cp .loopkit/config.env.example .loopkit/config.env
```

Minimum for a Node project:

```
LOOP_TEST_CMD=npm test
LOOP_LINT_CMD=npm run lint
LOOP_TYPECHECK_CMD=npx tsc --noEmit
LOOP_BUILD_CMD=npm run build
LOOP_TEST_JSON_CMD=npx vitest run --reporter=json --outputFile={report}
```

Rules that save an hour:

- **An empty value skips the step.** `LOOP_BUILD_CMD=` means "no build step",
  not "use the default".
- **Non-JS project?** Any runner that writes a jest-shaped JSON report works
  for `LOOP_TEST_JSON_CMD`. Otherwise produce a plain list (one
  `<file> :: <test name>` per line) and point the gate at it — see step 5.
- **Secrets manager?** `LOOP_ENV_WRAPPER=doppler run --project p --config c --`
  (or `op run --`, `dotenvx run --`). Every gate command runs behind it. The
  values never touch a file.
- `.loopkit/config.env` is gitignored by `init` because it may hold a webhook
  URL. Everything else under `.loopkit/` is meant to be committed.

## 5. Seed the test baseline

Most real suites have a few known failures. The gate does not demand zero
failures; it demands zero **new** ones. Record today's set once:

```bash
bash <plugin>/scripts/test-regressions.sh --update-baseline
```

You should see `>> Baseline rewritten: state/known-test-failures.txt (N entries)`.
Commit that file. From now on the gate diffs one run against it and prints
`FIXED:` lines when a baseline entry stops failing — delete those lines as you
go; the goal is zero.

For a runner with no JSON report:

```bash
bash <plugin>/scripts/test-regressions.sh --from-list my-failures.txt
```

`<plugin>` is the plugin's install path; `/loopkit:doctor` prints it, and so
does the one-line message at the start of every session.

## 6. Find work

```
/loopkit:morning-triage
```

It reads open GitHub issues (`bug`, `enhancement`, `Priority N` labels), and
bridges any open `##` heading in `inbox/needs-human.md`, into `state/triage.md`
at status `new`. Open the file: one row per finding, columns
`finding | source | priority | spec | status`.

No GitHub? Add rows by hand — through the helper, never by editing the table:

```bash
python3 <plugin>/scripts/triage_state.py upsert --state state/triage.md \
  --finding "Checkout returns 500 when the customer has no subscription row" \
  --source "incident 2026-09-06" --priority high --status new
```

## 7. Run one tick

```
/loop work the loopkit backlog
```

That is the whole prompt. A hook injects the discipline the moment it sees
`/loop`, so you never paste a procedure. What happens:

1. `loop-next.sh` prints the backlog counts and **one** stage:
   ```
   BACKLOG: new=3 spec-draft=0 spec-ready=0 fixing=0 pr-open=0
   STAGE: new
   TARGET: Checkout returns 500 when the customer has no subscription row
   TARGETS: … [source: incident 2026-09-06]
   ACTION: run loop-assess Mode A: baseline, classify, route …
   ```
2. If a PR is in flight you will see a `POLL:` line first — the cheap watch
   runs, and only `ACT`/`ESCALATE` preempts the tick.
3. The agent advances that stage for every `TARGETS:` row — for `new`, that
   means establishing what is true today, classifying the finding
   (measurement / code / tool / spec / process / architecture / prompt /
   decision), and routing it. Only `code` goes on to a spec.
4. Each transition is recorded:
   ```
   python3 <plugin>/scripts/triage_state.py update --state state/triage.md --source "<row source>" --status spec-draft
   ```
5. It stops. Run `/loop work the loopkit backlog` again (or let `/loop`
   self-pace) for the next stage.

When the agent tries to say "done" after changing code, the Stop hook runs
your gate — precheck, regression diff, lint, typecheck, build — and then an
acceptance review against `specs/`. Red blocks the stop. That is the point.

## 8. Optional: the knowledge layer

```
python3 <plugin>/scripts/memory.py knowledge init
```

Enables the bundle and seeds ten concepts about the loop itself (three of
them Gates and an Invariant with `enforced_by`). Then:

```bash
python3 <plugin>/scripts/rulings-extract.py            # dry run: what your inbox, ADRs and specs already ruled
python3 <plugin>/scripts/rulings-extract.py --apply    # one message per ruling
python3 <plugin>/scripts/memory.py knowledge drain     # apply; a human ratifies with status: stable
python3 <plugin>/scripts/rulings-compile.py            # which rulings have a gate that can fail
python3 <plugin>/scripts/loop-metrics.py               # verified success, re-asks — with n=
```

Record a trap with a lane tag (`--tags lane/billing`) and the next tick scoped
to that lane starts with it in front of the model.

## 9. Optional: the model checker

For any design with two writers, a retry, or a guard followed by a write:

```bash
bash <plugin>/skills/run-state-model/install.sh      # ~100 MB under ~/.loopkit/tools
node <plugin>/skills/run-state-model/driver.mjs doctor
node <plugin>/skills/run-state-model/driver.mjs check <plugin>/skills/run-state-model/examples/role-change-saga-buggy.fizz
```

The last one prints a counterexample and exits 1 — a real shipped bug, reduced
to its state machine. The fixed twin exits 0.

## 10. Status and health

```
/loopkit:scan      # open PRs with their real blockers; READY TO MERGE is the only actionable line
/loopkit:watch 123 # did PR 123 move? a few hundred bytes instead of a full read
/loopkit:doctor    # is the install healthy; runs the hook tests
```

## What to do when

| You see | It means | Do |
| --- | --- | --- |
| `BLOCKED [git-add-all]` | you (or the agent) ran `git add -A` / `.` | name the files |
| `BLOCKED [gh-pr-merge]` | the loop never merges | a human merges |
| `BLOCKED: constitution.md is ratified governance` | a guarded file | re-run with `GOVERNANCE_EDIT_OK=1` if a human ratified it |
| `BLOCKED — LoopKit contracts not read yet` | start of session | Read `FILES.md`, `TOOLS.md`, `COMMANDS.md` |
| `STAGE: error — no state file` | project not initialised | `/loopkit:init` |
| `PRS: could not read` | `gh` auth or network | not the same as zero PRs; fix `gh auth status` |
| `FAIL: N NEW test failure(s)` | a regression | fix it, or if it is pre-existing and you can prove it, add it to the baseline in the same PR |
| `VERDICT: ESCALATE` from watch | 24 quiet ticks | chase the human or stop the loop |
| a row at `blocked` | waiting on a ruling in `inbox/needs-human.md` | answer the section; the agent moves the row |

## Uninstall

```
/plugin uninstall loopkit@loopkit
/plugin marketplace remove loopkit
```

The files `init` created stay in your project; delete them if you do not want
them. Nothing else was written outside the project except `~/.loopkit/tools`
if you ran step 8.

## Platforms

- **macOS** (bash 3.2, the default shell) — the development platform; every script is written for it.
- **Linux** — `tests/selftest.sh` passes on `node:22-bookworm` (Debian 12: Python 3.11, Node 22, bash 5.2). `.github/workflows/selftest.yml` runs it on `ubuntu-latest` for every push and pull request. The CI job runs the selftest only; `install.sh`'s Linux branch is not exercised by it.

## Updating LoopKit in a project that uses it

An installed plugin does not follow the repository. Two things stand between a
merge here and a project that has LoopKit installed, and both are worth knowing
before you rely on a fix reaching anyone.

**1. The marketplace is a git clone, and it is not refreshed automatically.**
Adding the marketplace clones this repository to
`~/.claude/plugins/marketplaces/<name>` and records it in `settings.json` under
`extraKnownMarketplaces`. That clone stays at whatever commit it was cloned at
until it is updated:

```bash
claude
# then, inside the session:
/plugin marketplace update loopkit
/plugin install loopkit@loopkit
```

The clone tracks this repository's **default branch**. Work that sits on a
feature branch reaches nobody, however many pull requests report as merged
against it. `git merge-base --is-ancestor <branch> main` is the only honest
check.

**2. The cache is keyed by version string.** An installed copy lives at
`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>`. If the manifest
version has not changed, that directory already exists and the old copy keeps
being used, even after the marketplace clone is refreshed. So a consumer-visible
change that does not bump `plugins/loopkit/.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` is invisible in practice.

`tools/release.sh` refuses to tag when the manifests disagree with the version
being released, which is the enforcement of that rule rather than a reminder
about it.

### The release sequence, in order

1. Merge to the default branch. Confirm with `git merge-base --is-ancestor`.
2. Bump both manifests to the new version, in the same pull request as the
   change if it is user-visible.
3. Write the `CHANGELOG.md` section. The release notes are read from it.
4. `bash tools/release.sh <version>` — it refuses on a dirty tracked tree, a
   missing changelog section, an existing tag, or a manifest that disagrees.
5. Consumers run `/plugin marketplace update <name>` and reinstall.

### Pinning instead of following

A project that wants a known-good version rather than the newest can point its
marketplace entry at a tag or a fork. Nothing here forces an upgrade, and a
plugin that changes hook behaviour is exactly the kind of dependency worth
pinning until its changelog has been read.
