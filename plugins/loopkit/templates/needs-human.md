# needs-human.md — the open door

Work the loop is not confident enough to ship waits here for a human. One file,
newest section at the top, never a file per finding. Every edit to this file
pings the webhook in `LOOPKIT_NEEDS_HUMAN_WEBHOOK` (if set).

A section is answerable only if it carries all of:

1. A dated heading: `## <what is undecided> (YYYY-MM-DD)`.
2. What it blocks (spec slug, ticket, triage row source).
3. The question, stated so it can be answered yes/no or by picking an option —
   with the WHOLE context in the section: the facts, the figures, the files.
   The reader must not need a lookup.
4. The cost of EACH option, not one recommendation.
5. What was searched before asking, on one plain line of the section:
   `Precedent searched: <queries run> → <result>` — the memory adapter, a grep
   over the code, a grep over `docs/adr/` and `specs/`. If the search finds the
   ruling, do not write the section at all. The label is exact, the arrow is
   `→` (not `->`), and the line must be visible — not in a code block or an
   HTML comment. `→ no ruling found` is a real result; an empty line is not.
   `loop-commit.sh` and the stop gate refuse a NEW or EDITED section without a
   usable line (`scripts/check-inbox-precedent.py`); a section left exactly as
   it was is never judged. To annotate an old open question, add the line or
   stamp it RESOLVED.

When the human rules, do not delete the section: prefix the heading with
`RESOLVED <date> —` (uppercase, so `inbox_to_triage.py` stops filing it) and
record the ruling under it. Then land the ruling as a durable artifact — a rule,
a hook pattern, a runbook, a test pin — in the same session.

---
