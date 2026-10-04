# Assessment — an inbox section can ask a human a question nobody searched for (2026-10-04)

Source: owner ruling, 2026-10-04. The coordinator offered "a check that refuses
any `inbox/needs-human.md` entry without a 'Precedent searched' line, and flags
owner questions in my briefs"; the owner said yes. loop-assess Mode A.

## Observed

A new `## ` section with no record of any search commits to
`inbox/needs-human.md` through `loop-commit.sh`, and the stop gate passes the
turn that wrote it. In the session that produced this ruling an agent used that
path to ask the owner a question whose answer was already a ruling encoded in
code.

## Evidence

`python3 tests/pins/inbox-precedent-gate.py`, written before any fix, on main
at `c598d5d`: **8 ok, 25 FAIL**, exit 1. The lines that matter:

- `[commit][new]` — `loop-commit.sh -m pin -- inbox/needs-human.md` on a scratch
  repo whose working inbox gained a bare section printed `committed <sha>` and
  moved HEAD.
- `[gate][new]` — `stop_gate.sh` on the same shape printed
  `SKIP: no changed code files` then `PASS: gate short-circuited`, both for an
  uncommitted section and for one that reached the branch by a bare commit.

Could this have failed? Yes, and it shows it: the 8 that passed on the unfixed
tree are the control cases (a commit that does not name the inbox, an entry
with the line, a project with no inbox, a branch holding only old sections), so
the pin separates "the gate is missing" from "everything is red". The same pin
run against a copy of `origin/main`'s `plugins/` after the fix was written
gives 8 ok / 25 FAIL again — only the fix differs between the two runs.

What was read, not run: `skills/loop-assess/SKILL.md` §A4. The `decision`
route says to state the cost of both options and give the whole context. It
never says to search. The paragraph above Mode A does say "confirm nobody has
already ruled", but it names three places (inbox, ticket thread, `state/`) and
leaves out the two where an implemented ruling actually lives — the code and
the memory adapter — and nothing records that the check happened.

## Control case

Three paths a fix must not break, each pinned:

1. An ordinary inbox entry WITH the line still commits
   (`[commit][control] an ordinary entry WITH the line commits`).
2. An old entry without the line still passes — the inbox's history is not
   rewritten for a rule that came later (`[check][grandfather]`,
   `[commit][grandfather]`, `[gate][grandfather]`). This repo's own inbox has
   ten such sections on main.
3. A commit that does not name the inbox is untouched, even while the inbox is
   dirty with a bare section, and so is a project with no inbox at all
   (`[commit][control]` ×2, `[gate][control]`).

## Classification

`process` — a missing gate on a write path, plus a `prompt` half (the skill's
`decision` route never asked for the search).

The reflex resisted: a PreToolUse guard that spots "this command writes the
inbox". That is the shape this repo keeps getting wrong. A guard on the
command is guessing at the outside of a write, and each such guess here has
leaked to a spelling nobody listed (a heredoc into an interpreter walks past
`protect_governance.py`). The file is the one thing every write path produces,
so the check reads the file, at the two places the paths converge.

Not `decision`: the owner ruled. Not `code` in the spec-writer sense: no
product behaviour changes, so no `specs/` entry is required by the route
(proposed EARS wording is drafted in
`state/2026-10-04-spec-draft-inbox-precedent-gate.md` for a human to ratify,
because `specs/`, `constitution.md` and `CLAUDE.md` are governance-guarded and
were not edited).

## Route

Script + gate wiring + instruction edit:

- `scripts/check-inbox-precedent.py` — the rule, over file content.
- `scripts/loop-commit.sh` — runs it before staging; refuses with exit 65.
- `hooks/stop_gate.sh` — runs it before the no-code short-circuit, against the
  gate's own merge base, so a bare commit does not get round it.
- `skills/loop-assess/SKILL.md` §A4 — the `decision` route searches first and
  records the line; a hit reroutes to `knowledge`.
- `templates/needs-human.md`, `templates/brief.md`, the contracts and the guide
  — so a new user learns the line exists.

Grandfathering rule chosen: per open heading, the number of sections WITHOUT a
usable line may not grow relative to the base (HEAD for the commit wrapper, the
merge base for the stop gate). Closed headings (`RESOLVED …`, the bridge's own
`is_closed`) are records, not questions, and are not judged.

## Not a code problem

- **Owner questions in briefs, statuses and PR bodies.** Those are prompts and
  chat, not files on a converging write path; no script reads them. The skill
  and `templates/brief.md` now say to flag them and carry the line, and that is
  a rule an agent keeps, not a gate that stops it. Said plainly so nobody cites
  it as a control.
- **A false line.** The check proves the line exists and says something. It
  cannot prove the search was run or was the right search. That stays with the
  reviewer, who now has a line to read.
- **A PreToolUse fast path on Write/Edit.** Deliberately not built. It would be
  the command-guessing detector described above, it could only see Write/Edit
  payloads and not the file that results, and a refusal at commit time already
  arrives in the same turn. Recommendation: leave it out unless refusals at
  commit time turn out to be too late in practice.
- **Consumers.** This reaches nobody until the plugin version is bumped; the
  bump is not part of this change. On first run after the bump, a consumer's
  in-flight branch that added a bare section will be stopped by the gate until
  the line is added. That is the rule working, and it costs one search.
