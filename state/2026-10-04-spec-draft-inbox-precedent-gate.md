# Spec draft — inbox precedent gate (2026-10-04)

DRAFT, for a human to ratify. `specs/`, `constitution.md` and `CLAUDE.md` are
governance-guarded, so none of them was edited and no override was used. The
behaviour below is what `tests/pins/inbox-precedent-gate.py` already pins; this
file is only the wording those three documents would carry if a human wants it
there. Assessment: `state/2026-10-04-inbox-precedent-gate-assessment.md`.

## Outcome

A human is never asked, through `inbox/needs-human.md`, a question that the
agent did not first search the repository for.

## Scope

In: new open sections of `inbox/needs-human.md`; `loop-commit.sh`;
`stop_gate.sh`; the loop-assess `decision` route.
Out: questions asked in briefs, statuses, PR bodies or chat (no file on a
converging write path); whether the recorded search was the right one.

## Proposed `specs/inbox-precedent-gate.md` — EARS criteria

1. WHEN a commit made through `loop-commit.sh` would publish
   `inbox/needs-human.md` AND the working file holds an open `## ` section that
   HEAD does not account for AND that section has no usable
   `Precedent searched: <queries run> → <result>` line, the wrapper SHALL exit
   65, SHALL stage nothing, SHALL create no commit, and SHALL name the section.
2. `loop-commit.sh` SHALL judge against the base the stop gate prints with
   `stop_gate.sh --print-base` (HEAD when it prints none), so the two give the
   same answer for the same inbox.
2a. WHEN the stop gate runs AND `inbox/needs-human.md` holds such a section
   relative to the gate's merge base (HEAD when there is none), the gate SHALL
   reject the stop, whether or not any code file changed and however the
   section reached the branch.
3. A line SHALL be usable only if it is ONE line of the section body that the
   rendered file shows (not inside a fenced code block, an indented code block
   or an HTML comment), starts with exactly `Precedent searched:` (optionally
   after a blockquote or list marker; case-sensitive), names at least one query
   before the arrow `→` (U+2192) and a result after it on the same line, each
   side holding at least one Unicode letter or digit, and
   neither side is a placeholder (`<…>`, `TODO`, `TBD`, `…`) and the query side
   is not a bare "none". A result of "none" / "no ruling found" SHALL be
   accepted. Any other arrow (`->`, `-->`, `=>`, …) SHALL be refused with a
   message naming the expected shape.
4. WHERE the base holds a section with the same heading AND the same body
   (whitespace and line endings aside), the check SHALL NOT judge it. Any open
   section without such a twin — new, edited, or a new question under an old
   heading — SHALL be judged.
5. WHERE a heading is closed by the inbox bridge's own rule (`is_closed`), the
   check SHALL NOT judge the section.
6. WHEN the named paths of a `loop-commit.sh` call would not publish
   `inbox/needs-human.md`, the wrapper SHALL behave exactly as before,
   whatever the state of the inbox file. (Control case.)
7. IF the check itself cannot run, THEN both callers SHALL refuse rather than
   pass.
8. WHEN loop-assess classifies a finding `decision`, it SHALL search the
   memory adapter, the code, and `docs/adr/` + `specs/` before writing the
   section; IF the search returns the ruling, THEN the finding SHALL be routed
   `knowledge` and no section SHALL be written.

## Proposed `constitution.md` line (and `templates/constitution.template.md`)

> WHEN the loop asks a human to decide, it SHALL first search memory, the code
> and the paper trail for an existing ruling, and SHALL record the search in
> the escalation as `Precedent searched: <queries run> → <result>`.

## Proposed `CLAUDE.md` / `templates/CLAUDE.snippet.md` wording

Amend the existing inbox bullet, adding one sentence and no new bullet (the
file has a compliance budget):

> Anything you are less than confident about goes to `inbox/needs-human.md`
> with the whole context and the cost of both options — never into a PR.
> Search for an existing ruling first and record it in the section as
> `Precedent searched: <queries run> → <result>`; a new section without that
> line does not commit.

## Open for the ratifier

- Whether the constitution line is wanted at all, given the gate already
  enforces the file half. Cost of adding: one more standing instruction against
  the ceiling. Cost of not adding: the brief/status half stays a skill-level
  rule only.
  Precedent searched: grep -n "inbox\|precedent\|already ruled" constitution.md CLAUDE.md plugins/loopkit/templates/CLAUDE.snippet.md → the inbox bullet exists; no line about searching first
