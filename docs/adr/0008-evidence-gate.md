# 0008 — A finding moves forward only on evidence from two systems of record

Status: **proposed** (owner request, 2026-10-15; the three open questions were
ruled by the owner on 2026-10-16 and are built — decisions 2, 5 and Consequences). The EARS wording is a draft at
`state/2026-10-15-spec-draft-evidence-gate.md` and awaits ratification.

## Context

A loop built a fix and had it reviewed on a cause that came from one source: a
response-field name and a URL parameter, read as proof that a user had a
pending invite. Nobody read the identity provider or the user's database rows.
Three later reads — the identity provider's user record, the user's role rows,
the profile row — showed there was no invite, only a stale profile flag that
both of the first two values had been copied from. Merged alone, the fix would
have turned a harmless redirect loop into a consent prompt that deletes data.

Two things were already true and did not help:

- Rules saying "check several sources before a claim about live state" existed
  as context. Nothing read them back at the moment a finding moved.
- The loop's own notes recorded that a plain sign-in did not loop in its
  reproduction. That observation contradicted the cause. Nothing asked for it
  to be reconciled before the conclusion moved on.

At `77203a7` the only gated transition in `triage_state.py` is `pr-open`, and
the gate reads review verdicts, not evidence. `new → spec-draft → spec-ready`
is ungated.

The owner asked for three things (2026-10-15): a sources step before every
assessment that lists the connected tools and maps each noun to where its truth
is kept; a gate in the state tool that refuses to move a finding to spec or PR
when a claim about live state has fewer than two independent sources; and a
contradiction list in which any unexplained observation blocks the conclusion.

## Decisions

### 1. The gate reads a declared value, never a guess about the text

The gate does **not** try to detect "a claim about live state" from free text.
That is a blocklist of phrasings, and it leaks the first time somebody phrases
the claim a new way. Instead every assessment states its position explicitly,
in a `## Sources` section:

- **always at least two `read:` rows on different systems of record**; and
- **one of them from a LIVE system** (identity, data, runtime) — unless the
  assessment declares **`Live state: none — <why>`**, which waives only the
  live requirement, never the two systems.

A missing section, an empty section, a table with no usable rows, or a `none`
with no reason is a *fabricated* declaration and is refused. A real
`Live state: none` is allowed, because it is written down where a reviewer can
disagree with it.

### 2. "Independent" means a different system of record — and the gate decides which system, not the writer

Each `## Sources` row names a **class** (`identity`, `data`, `runtime`, `code`,
`memory`, `tracker`, `derived`). The class maps to a system of record in
`<project>/.loopkit/sources.json` (defaults built in, template laid down by
init). Independence is counted over the **systems**, not the rows:

- Two rows of the same class are one system.
- Two classes mapped to the same system are one system.
- A value the application derived from one system — a response field, a URL
  parameter, a cached flag — is class `derived`, whose system is marked
  `counts: false`. It may be recorded (and should be, so the reviewer sees what
  the claim rested on) but it is worth **zero**. That is the incident's own
  evidence, and under this rule it scores nothing.
- A class not in the configuration is refused, not ignored. A silent ignore is
  how a typo becomes a pass.

The writer cannot raise the count by naming a new system in a cell; the cell is
display, the class is the key. Systems are an allowlist owned by the project.

What the gate **cannot** do is prove that a `read:` happened. It makes the
agent name two systems of record and say what it read, in which environment;
whether that is true stays the reviewer's job.

**Live systems** (owner ruling 2026-10-16 on whether one read must be live:
"both above options are needed"). Classes marked `live: true` — by default
`identity`, `data` (read-only) and `runtime` (logs, analytics) — are where live
state is *kept*. A finding about a real user, firm, row, flag or runtime
behaviour must read at least one of them; `code`, `memory`, `tracker` and
`derived` describe or copy the state and never satisfy this. Two honest reads
of code and memory therefore no longer pass a claim about a user. A finding
that claims nothing live declares `Live state: none — <why>`, and still needs
two systems for what it does claim: **a code-only finding reads code plus one
more system — memory (a trap or ruling about that code) or the tracker (the
report that raised it).**

### 3. Contradictions block until each one is explained

The assessment carries a required `## Does not fit` section. It is either one
item `- none — <what was checked>`, or one item per observation that does not
fit the conclusion, each with an `explained:` line. A missing section, an
empty one, or any item without an `explained:` with content blocks the same
transitions as the sources rule. `none` must stand alone; `none` beside real
items is a contradiction in itself and is refused.

### 4. Which transitions, and where the gate sits

`spec-draft`, `spec-ready` and `pr-open`, through both `update` and `upsert`.
Those are the three moves that commit the loop to a cause: writing acceptance
for it, building it, and asking a human to merge it. `fixing`, `blocked`,
`inbox` and `done` are not gated — routing away from the pipeline must stay
cheap, and `fixing` is reached only from `spec-ready`, which was gated.

The gate is checked **before** the verdict gate and before anything is
written, and it is pure: a refusal leaves no transition event, no row change
and no override record.

The row finds its assessment by a declared line, `Row source: <the row's
source>`, in a markdown file directly under `state/`. Exactly one match is
used. None is refused; more than one is refused and names them, and
`--assessment <path>` picks one explicitly (the file must still declare the
row). The queue keeps its five columns — `specs/blocked-waits-on-what.md`
forbids a sixth.

### 5. One override, approved by a named human, and its reason is committed

`--override-sources "<reason>" --approved-by "<human name>"`. An empty reason
is refused, and so is an override with no approver. **There is no label-based
skip of any kind** — no flag, priority or word in the finding opens the door,
urgent fixes included. Owner ruling, 2026-10-16: "i should always review,
nothing skips a human in loop at least once".

How a human approver is told from an agent, in the order they are checked:

1. **Not the actor.** The name may not equal any identity the running process
   is known by: `LOOPKIT_AGENT`, and `GIT_AUTHOR_NAME` / `GIT_COMMITTER_NAME`
   when a harness sets them for its commits. Checked before the allowlist, so
   an allowlisted name still cannot approve its own run. **Limit:** nothing in
   this plugin sets `LOOPKIT_AGENT` — a harness or operator must export it per
   agent — and `git config user.name` is deliberately not used, because in a
   local session it is usually the human owner's own name and refusing it
   would refuse the human. With none of these set, this rule checks nothing,
   and the record says `run by unset`.
2. **Allowlist.** If `<project>/.loopkit/approvers.txt` exists, the name must
   be on it (case-insensitive). It is trusted only as described in §7, which is
   what makes it the guarantee: adding a name takes a commit a reviewer sees.
3. Without an allowlist, a name is refused when any of its words is a role or
   tool word (`agent`, `bot`, `claude`, `reviewer`, `implementer`, `ci`,
   `runner`, `github`, `noreply`, … — `evidence.AGENT_WORDS`), when it carries
   `[bot]` or an `@` address, when it holds a run of four or more digits, or
   when it has no word of two letters.

Rule 3 is a tripwire against an agent writing its own role, model or CI
identity into the field — the failure that actually happens — not proof of
humanity. A human-looking name typed by an agent passes it; that is why the
name is committed where a human reads it, and why rule 1 exists. An override on a transition the evidence already
allows records nothing.

An earlier review found that override reasons written only to
`state/ticks.jsonl` never reach a commit. So this one is written twice: as a
`sources-override` event in the ledger, and as a line under
`## Evidence-gate overrides` in `state/triage.md` — the file every tick already
commits — naming the date, the row, the target status, the reason, the human
approver and the agent that ran it (or `unset`). It is written only after the row write succeeded, so a
transition that failed for another reason leaves no override behind.

### 7. The gate's own configuration is trusted only as committed

`.loopkit/sources.json` decides what counts as a system of record and
`.loopkit/approvers.txt` decides who may approve an override. An agent that
could rewrite either could make the incident's evidence pass, or approve its
own override. Two layers stop that (review of `0d7b8a7`):

1. `protect_governance.py` lists both files beside `constitution.md` and
   `specs/`, so a Write, an Edit or a shell redirect to either is refused.
   This layer is a tool blocklist: an interpreter can write a file without
   naming it where the hook looks.
2. So the gate does not depend on spotting the write. `triage_state.py`
   trusts each file only when it is **tracked and identical in the working
   tree, the index and HEAD** (`git ls-files --error-unmatch`, then
   `git status --porcelain -- <file>` empty). Untracked, modified, staged but
   uncommitted, or not in a git checkout at all: refused, failing closed. A
   project without the file uses the built-in defaults. Changing who may
   approve, or what counts as a source, is therefore always a commit a
   reviewer sees.

### 6. Fail closed

An unreadable assessment, an invalid `sources.json`, or a helper module that
will not load refuses the transition. This is the opposite of the ledger
*write* (fail-open by design): a missing write is the ledger's problem; a check
that could not run is not a pass.

## Known limits

- **A mislabelled read cannot be detected.** A cached flag written up as a
  `data` read, or a URL parameter written up as `runtime`, looks exactly like a
  real one. The gate does not try to detect it from the text — that would be
  the blocklist decision 1 rejects. The `read:` line says what was read and
  where, and the reviewer judges it.
- **Placeholders are refused by a filler list** (`TODO`, `TBD`, `?`, `...`,
  `<…>`, `n/a`, …): a list that can only make the gate stricter.
- **The actor check depends on an identity being set** (§5 rule 1).

## Consequences

- Every finding at `new` that goes to the pipeline now needs an assessment with
  both sections before `spec-draft`.
- **No grandfathering** (owner ruling 2026-10-16: "Gate them too"). A row
  already past `new` when a project upgrades is checked at its NEXT gated move
  like any other. The way through is adding the evidence, or an override with
  a reason and a named human approver.
- `source-snapshot.py` writes the `## Sources` skeleton: one row per class, the
  finding's nouns matched to classes as a *suggestion*, and a probe of which
  tools answer. A tool it cannot probe — including every MCP server, which a
  script cannot see — is reported `unknown`, never `connected`.
- The gate is a floor, not a judge. It turns "check several sources" from a
  sentence an agent can skip into a section it cannot omit, and leaves the
  question "were these the right sources, honestly read?" where it belongs:
  with a reviewer that is not the author.
