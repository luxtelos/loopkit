# DRAFT spec — the evidence gate (awaits owner ratification)

> This is a draft. `specs/` is governance-guarded and the owner ratifies the
> wording; the implementation in the same PR is built against this text. When
> ratified it moves to `specs/evidence-gate.md` unchanged or as amended.

- Row: `owner request 2026-10-15 §evidence-gate`
- Assessment: `state/2026-10-15-evidence-gate-assessment.md`
- ADR: `docs/adr/0008-evidence-gate.md`
- Outcome: a finding cannot move to `spec-draft`, `spec-ready` or `pr-open`
  unless its assessment names at least two independent systems of record it
  read — one of them live, unless it declares in writing that it makes no
  claim about live state — and
  every observation that does not fit the conclusion has been explained.

## Why

A fix was built and reviewed on a one-source cause (a response-field name and a
URL parameter, both copied from one stale flag). The rule "check several
sources" existed as context and nothing enforced it. The observation that
contradicted the cause was already written down and nothing asked for it to be
reconciled.

## Scope

In: a `## Sources` section and a `## Does not fit` section in every
assessment; a per-project map from class to system of record; a snapshot
script that writes the sources skeleton and probes tools; a refusal in
`triage_state.py`; one reasoned override whose reason is committed.

Out: judging whether a `read:` line is true; choosing which systems a given
finding *should* have read; any new queue column or status.

## Definitions

- **Assessment** — a markdown file directly under `state/` carrying the line
  `Row source: <row source>` outside code fences and HTML comments.
- **Class** — a key of `classes` in `<project>/.loopkit/sources.json`, or of
  the built-in defaults when that file is absent: `identity`, `data`,
  `runtime`, `code`, `memory`, `tracker`, `derived`.
- **System of record** — the `system` value of a class. **Independent
  systems** — distinct `system` values among usable `read:` rows whose class
  does not set `counts: false`.
- **Usable** — the text after `read:` (or `n/a:`, `none —`, `explained:`)
  contains at least two consecutive letters or digits once `<…>` placeholders
  are removed.
- **Gated transition** — `update` or `upsert` with `--status` `spec-draft`,
  `spec-ready` or `pr-open`.

## Acceptance criteria (EARS)

**Sources**

- **E1** WHEN a gated transition is requested and the row's assessment has no
  `## Sources` section, THE recorder SHALL refuse it and leave the row, the
  ledger and `state/triage.md` unchanged.
- **E2** WHEN the `## Sources` section has neither a usable
  `Live state: none — <why>` line nor a sources table with at least one row,
  THE recorder SHALL refuse the transition (an empty section is a fabricated
  zero).
- **E3** WHEN the usable `read:` rows name fewer than two independent systems,
  THE recorder SHALL refuse the transition and name the systems it counted —
  whether or not `Live state: none` is declared (owner, 2026-10-16: "both
  above options are needed").
- **E4** WHEN two `read:` rows are of the same class, or of classes mapped to
  the same system, THE recorder SHALL count them as one system.
- **E5** WHEN a `read:` row's class sets `counts: false` (the default
  `derived` class: response fields, URL parameters, cached flags), THE
  recorder SHALL count it as zero systems.
- **E6** WHEN a sources row names a class that is not configured, or its
  evidence is neither a usable `read:` nor a usable `n/a:`, THE recorder SHALL
  refuse the transition and name the row.
- **E7** WHEN the section carries a usable `Live state: none — <why>` line,
  THE recorder SHALL waive the live requirement (E23) and SHALL still require
  two independent systems (E3); a code-only finding reads code plus memory or
  the tracker.
- **E23** WHEN no usable `Live state: none` line is present, THE recorder
  SHALL refuse unless at least one counted `read:` comes from a class with
  `live: true` (defaults: `identity`, `data`, `runtime`); `code`, `memory`,
  `tracker` and `derived` SHALL never satisfy it.
- **E24** WHEN a row that was already past `new` before the gate existed makes
  its next gated move, THE recorder SHALL check it like any other row (owner,
  2026-10-16: "Gate them too"); there is no grandfathering.

**Contradictions**

- **E8** WHEN the assessment has no `## Does not fit` section, or the section
  has no list item, THE recorder SHALL refuse the transition.
- **E9** WHEN any `## Does not fit` item lacks a usable `explained:`, THE
  recorder SHALL refuse the transition and quote the item.
- **E10** WHEN the section's only item is a usable `none — <what was
  checked>`, THE recorder SHALL accept it; WHEN `none` appears beside other
  items, THE recorder SHALL refuse.

**Locating the assessment**

- **E11** WHEN no file under `state/` declares the row's source, THE recorder
  SHALL refuse; WHEN more than one does and `--assessment` is not given, THE
  recorder SHALL refuse and list them; WHEN `--assessment <path>` is given,
  THE recorder SHALL use that file only if it declares the row's source.

**Override**

- **E12** WHEN `--override-sources "<reason>"` is given with a usable reason
  and an acceptable approver (E20, E21), and the evidence would refuse, THE recorder SHALL allow the transition, say
  so on stderr, append a `sources-override` event to `state/ticks.jsonl`, AND
  append a line naming date, row, status, reason, approver and agent under
  `## Evidence-gate overrides` in `state/triage.md`.
- **E13** WHEN the override reason is empty or not usable, THE recorder SHALL
  refuse the transition.
- **E14** WHEN the transition fails for any other reason (unknown source,
  verdict gate), THE recorder SHALL record no override.

**Approver** (owner ruling 2026-10-16: "nothing skips a human in loop at least once")

- **E20** WHEN `--override-sources` is given without a usable `--approved-by
  <human name>`, THE recorder SHALL refuse the transition; WHEN
  `--approved-by` is given without `--override-sources`, THE recorder SHALL
  refuse; WHEN an override is allowed, THE approver SHALL appear in both the
  `state/triage.md` line and the ledger event.
- **E21** IF `.loopkit/approvers.txt` exists, THEN THE approver SHALL be on it;
  OTHERWISE THE recorder SHALL refuse an approver equal to `LOOPKIT_AGENT`, or
  one with a role/tool word (`evidence.AGENT_WORDS`), a `[bot]` tag, an `@`
  address or a run of 4+ digits.
- **E22** THE recorder SHALL offer no label-, priority- or flag-based skip of
  the gate; a row labelled as an urgent fix with no evidence SHALL be refused
  like any other.

**Control**

- **E15** WHEN the status is `new`, `fixing`, `blocked`, `inbox` or `done`,
  THE recorder SHALL NOT read any assessment.
- **E16** WHILE the evidence gate passes, THE `pr-open` verdict gate SHALL
  behave exactly as before (refuse without a PASS, refuse a self-PASS when
  agents are named, accept `--override-verdict` with a reason).
- **E17** IF `sources.json` is not valid JSON or the assessment cannot be read,
  THEN THE recorder SHALL refuse the gated transition (fail closed).

**Snapshot**

- **E18** WHEN `source-snapshot.py` runs, IT SHALL emit a `## Sources` table
  with one row per configured class whose evidence cell is a placeholder the
  gate refuses, and a tools table in which a tool that cannot be probed —
  including every MCP server named in `.mcp.json` — is `unknown`, never
  `connected`; it SHALL print MCP server names only, never their
  configuration.
- **E19** WHEN given `--finding <text>`, IT SHALL mark the classes whose nouns
  appear in the text, as a suggestion the gate does not read.

## Edge-case families

- Placeholders left in (`read: <what, env>`) — not usable (E6).
- Rows inside a code fence or an HTML comment — invisible to the gate.
- `Live state: none` with no reason, or `n/a` as the reason — not usable (E2).
- A reason containing `|` — escaped in `state/triage.md` so the queue still
  parses.
- A project with no `.loopkit/sources.json` — built-in defaults, identical to
  the template (pinned).

## Pins

`tests/pins/evidence-gate.py` (cases per criterion) and
`tests/pins/evidence-gate-prove-red.sh` (each half of the gate mutated in a
scratch copy; each mutation must turn its own case red).
