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

The suite also runs `check-citations.py` against **this checkout** — not
against a fixture — so a `file:line` that has rotted in these documents turns
the run red before you push. The invocation is at `tests/selftest.sh:332`, and
CI reaches it by running the whole suite at
`.github/workflows/selftest.yml:20`. Both lines are pinned in
`.loopkit/citations.json`: move either one and the gate says so, and prints
the line number it should now say.

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

Those two `APPENDED` lines are written **once**. Init leaves a marker comment
beside each one:

```
# loopkit:decided .loopkit/config.env — init wrote this line once and will not re-add it. To opt out, delete the line and keep this comment.
.loopkit/config.env
```

**If you do not want one of them, delete the line and keep the marker.** Init
reads the marker, not the line, so the next run prints `KEPT ... removed by the
project on purpose` and leaves your file alone. (A project whose gate config
holds no webhook and no secret is right to track `.loopkit/config.env`; this
plugin's own repository does exactly that.) Delete the marker as well and init
treats the line as one you have never seen, and adds it back — which is what it
used to do on every single run, silently reversing the decision.

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
- `.loopkit/config.env` is gitignored by `init` because it **may** hold a
  webhook URL. May, not does: if yours holds only commands, track it and delete
  the ignore line — keep the `loopkit:decided` marker comment and init will not
  put it back (step 3). Everything else under `.loopkit/` is meant to be
  committed.

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
- **Linux** — `tests/selftest.sh` passes on `node:22-bookworm` (Debian 12: Python 3.11, Node 22, bash 5.2). `.github/workflows/selftest.yml` runs it on `ubuntu-latest` for every push and pull request, and that job now also runs `install.sh` (cached on its pinned engine versions) so the runtime model's invariants are **proved on Linux**, not just on the maintainer's laptop. The job fails if the model-invariant pin skips: a green tick that meant "the engines were missing, so nothing was checked" is the failure this repo is built to catch.

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

## Where to run the suite

Run it on Linux, not on macOS, whenever you have the choice.

On macOS the suite reports three failures that have nothing to do with your
change: `/var` is a symlink to `/private/var`, so a temp path compares unequal
to itself and three graph checks fail. A timing check also flakes under load.
Both are known and neither means anything is wrong.

That is the problem. Every run needs a human to say "ignore those four" — and a
suite whose output must be explained away is a suite whose real failures get
explained away too. On 2026-09-07 four failures appeared on macOS and all four
were noise; the same commit was `ALL PASS` on Linux.

```bash
LOOPKIT_REMOTE=user@host bash tools/remote-gate.sh <branch>
```

It stages a local clone, **rsyncs your current working tree over it**, sends
that to the host, and runs the suite in a container there. It prints the
platform, what `TMPDIR` resolves to, and a `SCOPE:` line saying how many
uncommitted changes went with it. Use macOS only to check that the bash 3.2 path
still works, and read its four known failures as noise until they are fixed.

**It gates what is on disk, not what is pushed** — and that is not a detail. A
version that cloned the branch from GitHub inside the container once replaced
this one through a merge conflict. Run from a dirty worktree it printed
`REMOTE GATE: PASS (rc=0)` while the uncommitted work was never sent, and said
nothing about it. `tests/pins/remote-gate-sends-working-tree.sh` now fails if
the staged tree loses a working-tree addition, edit or deletion, or if the run
stops announcing its scope. Narrow scope can be defended; silent scope cannot.

Set `LOOPKIT_REMOTE_DOCKER=0` to run bare on the host instead of in a container,
and then own the three memory failures out loud: `memory.py` finds a mempalace
the host has installed, so the case asserting behaviour "when the palace is
absent" measures a machine where it is present.

`LOOPKIT_GATE_STAGE_DIR=<dir>` builds exactly what would be sent, into `<dir>`,
and stops before any network call — useful when you want to see what the gate
is about to judge.

**Read its exit status, not its last line.** `remote-gate.sh` exits with the
suite's own status and prints `REMOTE GATE: PASS` or `REMOTE GATE: FAIL (rc=N)`
to say which. It did not always: the first version ran
`suite | grep | tail` and then read `$?`, which is *tail's* status, so it exited
0 on a suite printing `FAILURE: 3 checks failed`. The rule that came out of it,
and that `tests/pins/remote-gate-status.sh` now enforces on every run: **never
read `$?` after a pipe.** Send the output to a file, capture the status on the
next line, and filter the file for display. Display and verdict must be
separate paths, and only one of them is allowed to fail.

The runner's second argument replaces the suite command, which is how that pin
proves the runner can still go red — a gate that has only ever been seen say
PASS has not been shown to be able to say anything else.

### What the gate compares against

The stop gate reads the whole branch against its merge base, plus the working
tree. It resolves the trunk from `origin/HEAD` — the remote's own record of its
default branch — so it does not care whether your trunk is called `main`,
`develop` or `trunk`, whether a local `main` exists, or whether HEAD is
detached (it is, in every review worktree and every CI pull-request checkout).

When there genuinely is no merge base — an orphan branch, a repo with no
remote and no trunk-shaped ref — the gate prints a `NOTE: no merge base` line
saying which of those situations it is in, and falls back to the working tree.
That note prints **every** time the gate is not reading a merge base, including
when the suite then runs and the run therefore looks covered. A silent fallback
is how the original bug walks back in.
