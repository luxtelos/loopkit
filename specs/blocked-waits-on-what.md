# A blocked row records WHAT it waits on

- Row: `loop 2026-09-07 §blocked-conflates-two-waits`
- Assessment: `state/2026-09-07-blocked-conflates-two-waits.md`
- Outcome: a row waiting on an observable event is re-checked cheaply on every
  tick and becomes actionable by itself; a row waiting on a person is still
  never polled.

## Citations in this spec

Every `file:line` below is written **`path:line as of <sha>`**. A bare line
number is a claim about whichever tree the reader happens to have, and it stops
being true the moment `main` moves; the same number qualified by a sha is a
claim about one tree, and that claim never rots.

This spec learned it the expensive way. #27 moved
`plugins/loopkit/loopkit_core/triage_state.py` several hundred lines under it
and nine citations went stale at once — red the first day
`check-citations.py` was pointed at this repository, in #39.

The sha is `66abc4d`, the branch head every number below was re-measured on. To
re-verify one: `git diff 66abc4d..HEAD -- <path>`; an empty diff means the
number still holds. `check-citations.py` matches on `path:line` and ignores the
suffix, so the convention costs the gate nothing and is still checked by it.

This is documentation style, not governance, and it needs no ruling. The open
question in `inbox/needs-human.md` is about who may write to `specs/` at all,
which is a different thing from how a citation is spelled.

## Why

`blocked` means two different things today and the tick cannot tell them apart.
`loop-next.sh` prints "awaiting a human ruling — not polled, not a stage", and
`loop_next_pick.pick()` counts blocked rows without ever emitting a target for
one — `blocked` is absent from `WANT` at
`plugins/loopkit/loopkit_core/loop_next_pick.py:26 as of 66abc4d`. That is
exactly right for a
row awaiting a ruling: polling it would nag the owner every tick, and the quiet
is the point.

It is wrong for a row awaiting an event. On 2026-09-07 two rows sat blocked long
after their preconditions passed — M2 waited on M1 merging, which had happened;
the marketplace submission waited on 0.2.0 being tagged, which had happened three
releases earlier. Neither was ever going to be noticed, because nothing looks at
a blocked row. The recurrence rate is not "sometimes"; it is once per
event-blocked row, every time.

## Scope

In: a way for a blocked row to record what it waits on; a cheap per-tick check
for the event kind; the transition to `new` when the condition holds. Out: any
change to how ruling-blocked rows behave; any new status value or new column
that would invalidate an existing queue; anything that makes the tick guess.

## Constraints

- **The check is a lookup, never a judgement.** The moment a tick decides for
  itself whether a condition "probably" holds, the queue stops being memory and
  becomes an opinion. A condition it cannot evaluate leaves the row alone.
- **Backwards compatible.** An existing `blocked` row with no recorded condition
  keeps today's behaviour exactly: counted, never polled.
- **Cheap.** The check runs on every tick, so it may not cost an API call per
  row. Conditions that need the network are evaluated from data a tick already
  fetches, or not at all.

## Where the condition lives, and in what syntax

This section is normative. It exists because the first draft of this spec
permitted five different homes for the condition, and two implementers would
have picked differently.

### The cell

**The condition is recorded in the `spec` cell of the blocked row.** Not a sixth
column, not `source`, not `finding`, not a sidecar file.

Three reasons, each checked against the tree rather than assumed:

1. **Nothing reads `spec` to make a decision.** Of the five columns at
   `plugins/loopkit/loopkit_core/triage_state.py:17 as of 66abc4d`, `source` is
   the identity key (`find_row`,
   `plugins/loopkit/loopkit_core/triage_state.py:526 as of 66abc4d`) and
   `upsert_row` matches on it; `status` and `priority` drive the pick; and
   `finding` **plus** `source` are the haystack for scope matching at
   `plugins/loopkit/loopkit_core/loop_next_pick.py:89 as of 66abc4d`, so a
   condition written
   into either would silently change which rows a `--scope` lane matches. `spec`
   is parsed, normalised and written back, and no code branches on it.
2. **It is empty on every blocked row that exists.** All 12 rows at `blocked` in
   `state/triage.md` today carry an empty `spec` cell. A blocked row has no spec
   yet by definition of being blocked — that is what it is waiting to earn.
3. **It already round-trips.** `spec` is one of `COLUMNS`, so `parse_table`
   copies it (`plugins/loopkit/loopkit_core/triage_state.py:264 as of 66abc4d`)
   and
   `format_row` writes it. No parser change is needed to *carry* a condition;
   only the evaluator is new. A sixth column would need both.

**While a row's status is `blocked`, its `spec` cell SHALL hold either the empty
string or exactly one condition — never a spec path, and never both. While its
status is anything else, the cell SHALL NOT hold a condition.** A blocked row
that has earned a spec path is no longer blocked. This keeps the cell
single-valued, so no delimiter between "spec path" and "condition" has to be
invented, and no such delimiter can be got wrong. The second sentence was
missing until round three; criterion 10 says who enforces it, and what the
runtime does when they do not.

### The grammar

```
condition = "waits:" kind ":" argument
kind      = 1*( ALPHA / "-" )              ; from the closed set below
argument  = safe-char *( [ SP ] safe-char )
SP        = %x20
safe-char = any character C for which C.isspace() is false, and C is not "|"
```

In words: an argument is one or more non-whitespace, non-pipe characters, with
**at most one `U+0020` between any two of them and none at either end**. `a b`
is legal. `a  b` is not, ` a` is not, `a ` is not, and a tab, an NBSP or an em
space anywhere is not.

`safe-char` is given as a predicate rather than a code-point range on purpose.
The mechanism being defended against is `str.split()`, and `str.split()` splits
on exactly the characters for which `str.isspace()` is true. Enumerated over the
whole of Unicode, the two sets are **identical** — 29 characters, with nothing
in either that is absent from the other. A range-based exclusion would be wider
or narrower than the mechanism. This one *is* the mechanism, so it cannot drift
from it.

The closed set of kinds:

| kind | argument | meaning | evaluable today? |
|---|---|---|---|
| `row-done` | the `source` cell of another row, verbatim | that row has reached `done` | yes |

`row-done` is the whole set, and that is deliberate rather than an omission.
Criterion 5 requires the evaluator to be a closed function of the data the tick
already holds, and the parsed rows are the only such data. A kind that needs
`git tag`, the GitHub API or the filesystem is not evaluable under that
constraint, so it is not defined here; writing one (`waits:tag-exists:v0.2.0`,
say) is well-formed-looking but an **unknown kind**, which is criterion 4's
third failure shape — the row stays blocked and says why. Adding a kind is a
spec change, which is the point: the tick may not invent one.

Because both sides of a `row-done` comparison pass through `normalize_cell`, the
argument is compared to the target's `source` after normalisation, with no
trimming or casefolding of its own. That is also why a leading or trailing space
is illegal rather than merely untidy: no normalised `source` begins or ends with
one, so such an argument could never match any row. Admitting it would buy a
condition that looks well-formed and is permanently unsatisfiable, which is the
quietest failure on offer. A `source` containing a literal `|` is unnameable
too, for the different reason given below — the write-side escape changes the
bytes — and that is a known limit rather than an oversight.

### What the syntax may not contain, and why

Three rewrites happen between what an author types and what the next tick reads.
All three were run rather than reasoned about, and the third is worse than a
rewrite:

- `normalize_cell` is `" ".join(value.strip().split())`
  (`plugins/loopkit/loopkit_core/triage_state.py:67 as of 66abc4d`). It
  collapses any run of
  whitespace — all 29 characters, not only `U+0020` — to a single space, and
  strips the ends. **A cell carrying a double space, a tab, an NBSP, or a
  leading or trailing space is silently rewritten on read.**
- `format_row` writes `.replace("|", "\\|")`
  (`plugins/loopkit/loopkit_core/triage_state.py:133 as of 66abc4d`). **A cell
  carrying a
  literal pipe is silently rewritten on write.** The in-memory value survives
  the round trip because `split_markdown_row` unescapes it, but the bytes on
  disk change — `waits:row-done:a|b` is written as
  `| f | row-A | P1 | waits:row-done:a\|b | blocked |` — so a byte-comparison
  pin against a hand-written queue fails.
- **`write_table` writes each row as one line and `parse_table` reads it back
  through `str.splitlines()`.** Ten characters break a line: `\n \v \f \r` and
  `\x1c \x1d \x1e \x85 \u2028 \u2029`. A cell containing any of them is written
  as one line and read back as two, and both halves fail the cell-count check at
  `plugins/loopkit/loopkit_core/triage_state.py:253 as of 66abc4d`. **That used
  to delete the row silently at exit 0. Since `ea6e320` it does not:** the
  short-read invariant at
  `plugins/loopkit/loopkit_core/triage_state.py:275 as of 66abc4d` refuses the
  whole parse, and `triage_state.py list` exits 2 naming the offending line. The
  three line-breaking candidates in the table below are annotated `PARSE
  REFUSED` for that reason; an earlier draft of this spec recorded them as `ROW
  DELETED`, and what changed is the queue-never-reads-empty fix landing
  underneath this spec, not anything about the grammar. The grammar is unmoved
  by it — a cell that stops the tick is no more admissible than one that
  vanishes — but the sentence had to move, because the sentence was the part
  that went false. Neither of the first two reviews named this hazard at all.
  All ten are `isspace()`-true, so the `safe-char`
  exclusion above already covers them — which is the second reason to key it on
  `isspace()` rather than on a hand-picked list of "the whitespace that
  matters". A hand-picked list would have had to know about `\x1d`.

Measured by writing each candidate into a real queue with `write_table`, parsing
it back with `parse_table`, and writing it again:

```
legal?   survives  bytes  candidate                                         note
LEGAL    True      True   'waits:row-done:row-A'                            plain
LEGAL    True      True   'waits:row-done:docs/research/runtime-plan.md §M3' a real source cell, non-ASCII
LEGAL    True      True   'waits:row-done:a b'                              one interior space
LEGAL    True      True   'waits:row-done:a b c d'                          several single spaces
LEGAL    True      True   'waits:row-done:a\u200bb'                         ZWSP - not isspace(), not split
illegal  False     False  'waits:row-done:a  b'                             DOUBLE space
illegal  False     True   'waits:row-done: '                                argument is one space
illegal  False     True   'waits:row-done:  '                               argument is two spaces
illegal  False     True   'waits:row-done:trailing '                        trailing space
illegal  True      True   'waits:row-done: leading'                         leading space
illegal  False     False  'waits:row-done:tab\there'                        TAB
illegal  False     False  'waits:row-done:nbsp\xa0here'                     NBSP
illegal  False     False  'waits:row-done:em\u2003here'                     EM SPACE
illegal  False     False  'waits:row-done:vt\x0bhere'                       VERTICAL TAB     -> PARSE REFUSED
illegal  False     False  'waits:row-done:fs\x1chere'                       FILE SEPARATOR   -> PARSE REFUSED
illegal  False     False  'waits:row-done:cr\rhere'                         CARRIAGE RETURN  -> PARSE REFUSED
illegal  True      True   'waits:row-done:a|b'                              literal PIPE (disk bytes differ)
illegal  True      True   'waits:row-done:'                                 empty argument
illegal  True      True   'waits::row-A'                                    empty kind
illegal  True      True   'waits:row-done'                                  no separator
illegal  False     True   ' waits:row-done:a'                               leading space on the whole cell

LEGAL strings the pipeline rewrites: 0
```

Re-measured at `66abc4d`, after #27 moved the parser. Every `legal?`,
`survives` and `bytes` verdict is unchanged; only the three annotations moved,
from `ROW DELETED` to `PARSE REFUSED`. The grammar this table exists to justify
is therefore the same grammar, and that is worth stating explicitly rather than
leaving a reader to diff two drafts.

`survives` is "the value parsed back equals the value authored"; `bytes` is
"writing that parse back reproduces the file byte for byte". **Zero of the legal
strings are rewritten.** That is the property this grammar exists to have, and
it is measured across the boundary rather than argued from the source of
`normalize_cell`.

The other half of the property matters as much: **no rewrite can turn a legal
condition into an illegal one any more**, because no legal condition is
rewritten at all. Round two's sharpest finding was `waits:row-done: ` — an
argument of a single space, legal under the old rule `1*( any character except
"|" )`, silently rewritten into `waits:row-done:`, which criterion 4 names as
the *malformed* shape. A well-formed condition became a malformed one on write,
with no diagnostic at the point of the rewrite. Under this grammar the
single-space argument is malformed to begin with, and it is still malformed
afterwards. Both spellings land in the same criterion 4 bucket, so the row stays
blocked and says why, whichever one the tick happens to see. The mutation is
still there — nothing in this spec can stop `normalize_cell` running — but it
can no longer change a verdict, and that is the reachable property.

The separator `:` is untouched by all three rewrites. A full queue written
through `write_table`, re-parsed and written again is byte-identical, and a
legal condition cell reads back exactly as authored.

### No sixth column

**The queue SHALL NOT gain a sixth column, for this or any other purpose.**

**The reason this section gave until 2026-09-07 has expired. The SHALL
survives on a different one.** Both halves are set out below, because a rule
kept on a reason that stopped being true is a rule nobody can argue with, and
this spec is not entitled to one of those.

#### What changed underneath it

The header is matched against a closed list of accepted shapes — four-column
and five-column only — at
`plugins/loopkit/loopkit_core/triage_state.py:53 as of 66abc4d`, read by
`header_cells_if_header` at
`plugins/loopkit/loopkit_core/triage_state.py:95 as of 66abc4d`. A six-column
header matches neither, and until `ea6e320` that produced `rows parsed: 0` at
exit 0. This section argued from exactly that sentence. It can no longer.

Taking the real `state/triage.md`, appending a sixth cell to its header,
separator and every row, and parsing it with this branch's runtime:

```
ValueError: <path>: no recognisable header, but the file holds 39 row-shaped
line(s). An unknown, renamed or reordered column is the usual cause. Accepted
headers: ['finding | source | priority | status',
'finding | source | priority | spec | status']. Refusing to report an empty
queue — a loop that reads its own memory as empty reports itself idle and
nobody notices.

rc=2
```

The refusal is at `plugins/loopkit/loopkit_core/triage_state.py:213 as of
66abc4d`. Driven through the actual tick rather than the parser alone,
`loop-next.sh --state <six-column queue>` prints one line and stops:

```
STAGE: error — could not parse <path> via triage_state.py. Do not guess; fix
the state file.
```

**A loudly refused sixth column is a much smaller hazard than a silently zeroed
one, and this spec says so plainly rather than leaving the old sentence
standing.** The loop halts and names the file. Nobody is misled.

#### Why the SHALL survives: the queue is a cross-version contract

The runtime that reads `state/triage.md` is not necessarily the runtime that
wrote it. LoopKit ships as a marketplace plugin and consumers run a pinned,
cached copy, so "an older runtime" is the ordinary case rather than a corner.
Every released version predates the fix. The same six-column queue, driven
through v0.2.2's own `loop-next.sh`:

```
BACKLOG: new=0 spec-draft=0 spec-ready=0 fixing=0 pr-open=0
STAGE: discover
ACTION: no actionable rows. Run morning-triage to find work.
NEXT: IDLE — no rows and nothing in flight.
rc=0
```

Thirty-nine rows of work in the file, twelve of them blocked, and the tick
reports itself idle at exit 0 with no BLOCKED line at all. Writing that parse
back replaces the whole table with an empty one. Measured across the boundary
where it moves:

```
runtime                            six-column queue
v0.2.1  (released)                 rows parsed: 0, exit 0, table emptied on write
v0.2.2  (released)                 rows parsed: 0, exit 0, table emptied on write
687ccad (parent of the fix)        rows parsed: 0, exit 0, table emptied on write
ea6e320 (the fix)                  ValueError, rc=2
66abc4d (this branch)              ValueError, rc=2
```

Reproduce any row with `git show <sha>:plugins/loopkit/loopkit_core/triage_state.py`;
no plugin cache is needed, and `git merge-base --is-ancestor ea6e320 v0.2.2`
fails, which is the one-command form of the first three rows.

So a sixth column written by a fixed runtime is still a silent total loss in
every runtime anybody has installed. That is not a separate worry from
criterion 8 — it *is* criterion 8's contract, "a queue written after this
change SHALL parse in a runtime that predates it", and a sixth column breaks it
in the worst available way. The mixed fleet is not hypothetical: it has already
cost this loop a session, recorded in `inbox/needs-human.md`, where a
governance hook from the cached 0.2.1 fired against a repo whose `main` carried
the fix.

Two smaller reasons survive alongside it. On the fixed runtime the queue is
unreadable until a human repairs it — better than a lie, but still total
unavailability of the loop's memory. And `write_table` hardcodes the
five-column header at
`plugins/loopkit/loopkit_core/triage_state.py:292 as of 66abc4d`, so a sixth
column could not round-trip even if the parser admitted it.

#### One more claim in this section that rotted

Cell-count mismatches were described here as rows being "skipped". They are
not, any more: the row is recorded as dropped at
`plugins/loopkit/loopkit_core/triage_state.py:253 as of 66abc4d`, and the
short-read invariant at
`plugins/loopkit/loopkit_core/triage_state.py:275 as of 66abc4d` then refuses
the whole parse. The parser's own docstring at
`plugins/loopkit/loopkit_core/triage_state.py:153 as of 66abc4d` now records
three visits from this family of bug — 2026-08-31, and twice on 2026-09-07 —
and the fix that ended it. The old wording pointed at that comment as evidence
the hazard was live; it is now evidence the hazard was closed, which is the
opposite claim from the same citation.

Criterion 8's SHALL forbids only a new *status value*, so a sixth column would
satisfy it. Criterion 9 exists to close that gap, and its check runs today.

## Acceptance (EARS)

1. WHEN a blocked row records no condition, the runtime SHALL count it, SHALL NOT
   poll it and SHALL NOT serve it — today's behaviour, unchanged. Check
   `[today]`: a fixture queue of ruling-blocked rows yields the same
   `BACKLOG`/`BLOCKED`/`STAGE`/`NEXT` lines before and after this change, after
   the path scrubbing described in the control case.
2. WHEN a blocked row records a condition that is not yet satisfied, the runtime
   SHALL leave the row blocked and SHALL NOT serve it. Check `[M2b]`: a fixture
   whose condition names an unmerged row yields the same `BACKLOG`/`STAGE` lines
   as the same queue with the condition removed.
3. WHEN a blocked row records a condition that IS satisfied, the runtime SHALL
   move that row to `new`, SHALL clear the row's `spec` cell to the empty string
   in the same write, and SHALL report the transition on the tick's output,
   naming the row and the condition that fired. Check `[M2b]`: a fixture whose
   condition names a row already at `done` produces exactly one transition, one
   output line carrying both the row's source and the condition, and a `spec`
   cell that is empty afterwards. The clearing is criterion 10's rule; it is
   restated here because this is the transition that would otherwise leave a
   spent condition behind, and a rule stated only where it is enforced is a rule
   the reader of criterion 3 never sees.
4. IF a condition cannot be evaluated — a malformed expression, an unreachable
   source, an unknown condition kind — THEN the runtime SHALL leave the row
   blocked, SHALL print one line saying which row and why, and SHALL NOT fail the
   tick. A blocked row that cannot be checked is not an error; silently treating
   it as satisfied would be. Check `[M2b]`: three fixtures, one per failure
   shape, each yielding tick exit 0, zero transitions and exactly one diagnostic
   naming the row. The three shapes are writable because the grammar above
   defines well-formed:
   - malformed expression — `waits:row-done:` (empty argument), or `waits:` with
     no kind, or any cell starting with `waits:` that does not match the ABNF;
   - unreachable source — `waits:row-done:no such row`, where no row's `source`
     equals the argument after `normalize_cell`;
   - unknown kind — `waits:tag-exists:v0.2.0`, well-formed but not in the closed
     kind table.
5. The condition check SHALL be a **closed function of its arguments**: it
   receives the rows this tick has already parsed and the condition to test, it
   reads nothing else, and it returns a verdict. No clock, no network, no
   filesystem, no module-level state — and, the change in this round, no
   carve-out for any of them.

   **The "date comparison" this criterion used to allow is withdrawn.** That
   allowance is what forced the previous check into the wrong shape. Permitting
   *part* of a module obliges the check to enumerate the forbidden members of a
   permitted one, and an enumeration of forbidden members is a deny-list by
   construction: there is no way to say "`date` yes, `date.today` no" without
   naming `today`, then `utcnow`, then whatever the next reader has not thought
   of. Nothing in the closed kind table needs a date — `row-done` compares one
   string to another. If a future kind ever needs today's date, the date arrives
   as an **argument**: the tick already knows it, the evaluator stays closed,
   and the check stays total. Withdrawing the allowance costs no capability. It
   moves one value across the function boundary.

   **Check `[M2b]`: a static allow-list over the evaluator's code object.** Not
   a runtime guard that patches forbidden names into raising. A guard like that
   admits by default, and enumerating the known-bad while permitting the unknown
   is the most-repeated defect in this codebase. Five parts, all decidable
   without running the evaluator at all:

   a. **It is a plain function at all.** `isinstance(evaluator,
      types.FunctionType)`. A callable class instance has no inspectable
      `__code__`, so every part below would raise rather than judge — and a
      check that raises where it meant to refuse is a check whose verdict
      depends on how the caller wrote its `try`. Refuse explicitly.
   b. **No defaulted parameters.** `evaluator.__defaults__` and
      `evaluator.__kwdefaults__` are both empty. A default is evaluated once, at
      `def` time, in the module — so `def evaluate(rows, arg, _now=time.time())`
      hides a clock read that no name in the function body records.
   c. **No closure.** `evaluator.__closure__` is `None`. A free variable is
      non-argument state by definition, and it is also how a decorator hides the
      real evaluator behind a `functools.wraps` wrapper.
   d. **Every reachable name is on the allow-list.** Take the union of
      `co_names` over `evaluator.__code__` and, recursively, every code object
      in its `co_consts`. That is every global, every imported name and every
      attribute the function can reach, including from a nested `def` or
      `lambda`. It must be a subset of the declared list. Everything else
      fails: an unlisted module, an unlisted builtin, an unlisted attribute,
      and — this is the point — a name nobody has thought of yet.
   e. **No allow-listed builtin is shadowed.** For each reachable name that is
      also a builtin, assert `evaluator.__globals__` either does not bind it or
      binds it to the same object as `builtins` does. Without this, a module can
      rebind `len` to something that reads the clock and its evaluator passes
      part (d) with every name legal.

   The allow-list is the builtins the evaluator uses plus the attribute names it
   calls on its arguments — about twenty entries, all of them `str`, `dict` and
   `list` members. Attribute names are included deliberately, although `dis`
   could tell them apart from globals. It costs one line in the list, and it
   earned itself: the only evaluator that reaches dangerous state while naming
   **no module global whatsoever** does it through an argument, as
   `rows.__class__.__base__.__subclasses__()`, and the names that refuse it are
   `__class__`, `__base__` and `__subclasses__` — three attributes. A check that
   scanned globals alone would let it through. Cheap over-strictness is the
   right trade in a check whose entire job is to fail closed.

   **Measured against seventeen stand-in evaluators** — two legal shapes, the
   six spellings the round-two review used to defeat the deny-list, four the
   deny-list did catch, and five more written to attack this check rather than
   the old one:

   ```
   evaluator                            intent     deny-list  audit-hook  allow-list
   pure                                 allowed    allowed    allowed     allowed
   date comparison, dates passed in     allowed    allowed    allowed     allowed
   from time import time                forbidden  ALLOWED!   ALLOWED!    detected
   from datetime import datetime        forbidden  ALLOWED!   ALLOWED!    detected
   subprocess.run(["date", "+%s"])      forbidden  ALLOWED!   detected    detected
   time.perf_counter_ns()               forbidden  ALLOWED!   ALLOWED!    detected
   time.clock_gettime()                 forbidden  ALLOWED!   ALLOWED!    detected
   os.stat().st_mtime                   forbidden  ALLOWED!   ALLOWED!    detected
   time.time()                          forbidden  detected   ALLOWED!    detected
   datetime.datetime.now()              forbidden  detected   ALLOWED!    detected
   datetime.date.today()                forbidden  detected   ALLOWED!    detected
   socket.getaddrinfo()                 forbidden  ALLOWED!   detected    detected
   clock laundered via a module const   forbidden  ALLOWED!   ALLOWED!    detected
   __import__("time") in a nested def   forbidden  detected   ALLOWED!    detected
   open() on a file                     forbidden  detected   detected    detected
   a shadowed builtin len()             forbidden  detected   ALLOWED!    detected
   clock read in a parameter default    forbidden  ALLOWED!   ALLOWED!    detected

   correct:    deny-list 8/17      audit-hook 5/17      allow-list 17/17
   ```

   Three rows are worth reading twice.

   **`clock laundered via a module const`** is `_BOOT = time.time()` at module
   level with the function returning `_BOOT`. The word `time` appears nowhere
   inside the function. Every check that hunts for forbidden module names in the
   function body — the deny-list, an audit hook, a grep, a reviewer reading it —
   is blind to it. The allow-list refuses it because `_BOOT` is a name and
   `_BOOT` is not on the list. That is the whole difference between the two
   shapes: an allow-list does not have to recognise the danger, only to fail to
   recognise the name.

   **`clock read in a parameter default`** defeats parts (c), (d) and (e) on
   their own; part (b) exists solely because of it. It was found by writing this
   check, not by reading the old one, which is the argument for writing checks
   that try to break themselves.

   **The audit hook the round-two review proposed scores worse than the
   deny-list it was meant to replace — and it would have looked like progress.**
   Measured by arming a hook that trips on *any* audit event whatsoever, which
   is the most generous construction available rather than a list of event
   prefixes, twelve of the seventeen evaluators raise **no audit event at all**:

   ```
   time.time()             -> NONE     subprocess.run(...)   -> subprocess.Popen, fork_exec, open
   time.perf_counter_ns()  -> NONE     socket.getaddrinfo()  -> socket.getaddrinfo, import, open
   time.clock_gettime()    -> NONE     open("/etc/hostname") -> open
   datetime.now()          -> NONE
   datetime.date.today()   -> NONE
   os.stat().st_mtime      -> NONE
   ```

   CPython raises audit events for imports, subprocess, sockets and `open`. It
   raises none for reading a clock, which is most of what this criterion
   forbids. So an audit hook is a deny-list too — its list is merely written in
   C, inside the interpreter, by somebody who was not thinking about this
   criterion. It is recorded here because it is the plausible next thing a
   reader reaches for, and because "it is a hook, so it must be sound" is how
   the shape gets bought a third time.

   The same review offered a second suggestion — "assert on the evaluator's
   imports" — and that one was right, in the direction if not the unit. The
   check above is that idea generalised from imports to **names**, and the
   generalisation is load-bearing rather than tidy-minded: `_BOOT =
   time.time()` at module level imports nothing inside the function, and
   `_now=time.time()` in a parameter default imports nothing at all. Both are
   invisible to an import assertion and both are caught by a name assertion.
   Imports are a subset of names; the two evaluators that live in the gap are
   the two nobody had written yet.

   **Six more, written to attack this check rather than the old one.** The
   seventeen above were mostly inherited from two rounds of review, which makes
   them a test of the previous check as much as this one. These were chosen by
   asking where an allow-list over `co_names` would be weakest:

   ```
   adversarial evaluator     intent     verdict   refused because
   clock read in a genexp    forbidden  detected  unlisted name: time
   clock read in a lambda    forbidden  detected  unlisted name: time
   rows.__class__.__base__   forbidden  detected  unlisted: __class__, __base__, __subclasses__
     .__subclasses__()
   a callable class instance forbidden  detected  not a plain function
   functools.wraps wrapper   forbidden  detected  closure over non-argument state
   str/dict methods only     allowed    allowed   -

   wrong: 0/6
   ```

   The third and fourth are the ones that changed this criterion. The
   attribute-chain evaluator names no module global at all — it climbs from an
   *argument* to `object.__subclasses__`, and only the attribute half of part
   (d) refuses it. The callable instance produced no verdict at all in the first
   draft of this check; it raised, because a class instance has no `__code__`.
   Part (a) exists because of it. Both were found by writing evaluators against
   the check rather than by re-reading it, which is the method this criterion
   recommends to whoever implements it: the twenty-third evaluator is the one
   worth writing.

   **What this check does not prove, said plainly so nobody mistakes it for
   total.** It does not prove the evaluator is a function of *honest* inputs: if
   the caller hands it a clock reading as an argument, the evaluator is still
   pure with respect to what it was given. That hole is closed by criterion 6,
   which fixes the argument list to the already-parsed rows and no path — the
   two criteria only hold together, and weakening either reopens it. Separately,
   `co_names` is a CPython implementation detail; a runtime that is not CPython
   needs a different reading of the same rule. Both limits are named here rather
   than left for the next reviewer to find.

   **If you are reading this because the check refused something, do not add a
   name to make it pass.** There is no deny-list here to lengthen. The list is
   of what is permitted, and it is short because the evaluator's job is small. A
   refusal means one of two things, and the refused name tells you which: either
   the evaluator is reaching for state it should have been handed, or the
   permitted list genuinely wants one more `str` method. `datetime` is the first
   of those. `casefold` is the second.

   **Determinism, the weaker half, kept.** Call the evaluator twice with
   identical inputs and compare. It adds almost nothing on top of the
   allow-list, and it costs one line.

   The draft before last named only "call it twice under a frozen clock", which
   cannot fail: freezing the clock removes the variable it exists to detect. The
   draft after that replaced it with the deny-list in the table above, which
   gets nine of seventeen wrong. Both are recorded because the *shape* of the
   mistake survived a rewrite that made the check genuinely stronger, and that
   is the thing to watch for rather than either individual check.
6. WHEN a condition names another row (for example, blocked until row X reaches
   `done`), the runtime SHALL evaluate it against the queue in the same read that
   produced the counts, SHALL NOT re-read the queue, and SHALL NOT follow a chain
   more than one row deep in a single tick.

   **"One row deep" means: evaluate each condition against the statuses as read
   at the top of this tick, and do not resolve transitively.** If A waits on B
   and B waits on C, and C is `done`, then this tick unblocks B only — it does
   not reason "therefore B will be done, therefore A". A three-row chain does not
   stall; it **serialises**, one link per tick, each link unblocking once its own
   predecessor is actually at `done`. The cost is latency measured in ticks; the
   purchase is that every transition is attributable to one observed fact, so a
   wrong unblock names the row that caused it.

   Check `[M2b]`: a fixture with the chain A→B→C where C is at `done`. Tick once
   — exactly one transition (B→`new`), A still `blocked`. Advance B to `done` and
   tick again — exactly one transition (A→`new`). A single tick against that
   fixture SHALL NOT produce two transitions, and the evaluator SHALL read the
   queue exactly once per tick. Assert the second by giving the evaluator the
   already-parsed rows and no path argument at all, rather than by counting
   `parse_table` calls: a function with no way to re-read cannot.
7. IF a set of rows name each other as conditions in a closed loop **of any
   length**, THEN every row in that loop SHALL stay blocked and the runtime SHALL
   print one line naming the cycle.

   Two rows is not the only shape, and it was not even the smallest. Treat the
   conditions as a directed graph — one edge per blocked row carrying a
   `waits:row-done:` condition, from that row's `source` to the argument — and
   report every closed walk. Iterate the edge map in sorted key order so the
   reported set is deterministic. Verified against eight shapes:

   ```
   two-row cycle A<->B                -> [['A', 'B']]
   three-row cycle A->B->C->A         -> [['A', 'B', 'C']]
   four-row cycle                     -> [['A', 'B', 'C', 'D']]
   self-loop A->A                     -> [['A']]
   chain, no cycle A->B->C(done)      -> []
   cycle + innocent chain             -> [['A', 'B']]
   dangling target (criterion 4)      -> []
   no blocked rows                    -> []
   ```

   The previous draft covered only the first line. A→B→C→A would have sat blocked
   forever with no output line — silence with nobody noticing, which is the exact
   failure this whole spec exists to end, recreated one row further out. The
   self-loop, a row naming itself, was missing too.

   Check `[M2b]`: the eight fixtures above, each yielding zero transitions for
   rows inside a cycle, and exactly one diagnostic per cycle naming every row in
   it. The `chain, no cycle` and `dangling target` fixtures are the control
   cases: a detector that reports a cycle for a plain chain is worse than none,
   so a check that only feeds it cycles cannot fail.

   This diagnostic is a read-only pass over the graph and does not weaken
   criterion 6: detecting a cycle is not the same as resolving a chain
   transitively, and no transition is decided by it.
8. The status vocabulary SHALL NOT gain a new value. A queue written before this
   change SHALL parse unchanged afterwards, and a queue written after it SHALL
   parse in a runtime that predates it. Check `[today]`, from the repo root, with
   no `cd`:

   ```
   python3 -c "import sys; sys.path.insert(0, 'plugins/loopkit'); \
       from loopkit_core import triage_state; print(sorted(triage_state.VALID_STATUSES))"
   ```

   Before and after are equal. The implementation is
   `plugins/loopkit/loopkit_core/triage_state.py:17 as of 66abc4d`; a bare
   `import triage_state`
   raises `ModuleNotFoundError` from the repo root, and the path-shim spelling
   (`cd plugins/loopkit/scripts && python3 -c "import triage_state; ..."`) works
   but reads the shim at
   `plugins/loopkit/scripts/triage_state.py:2 as of 66abc4d` rather than
   the implementation, so prefer the form above. Check `[M2b]`: write a queue
   with conditions, parse it with the merge-base `triage_state`, write it back,
   compare bytes.
9. The queue SHALL NOT gain a sixth column. A five-column queue and a runtime
   that expects five columns are the compatibility contract, and the runtime
   that reads the queue is not necessarily the one that wrote it.

   This criterion's check was rewritten on 2026-09-07 because the old one
   asserted a fact that had become false — it required `rows parsed: 0` at exit
   0, and since `ea6e320` the parse refuses instead. **The SHALL was re-argued
   rather than the citation re-pointed.** Making the old check pass again would
   have meant pointing it at whatever still produced a silent zero, which is
   precisely the move `check-citations.py` exists to catch.

   Check `[today]`, three parts. Take the real `state/triage.md`, append a sixth
   cell to its header, separator and every row, and:

   a. **This runtime refuses it.** `parse_table` raises `ValueError` and
      `triage_state.py list` exits **2**. `loop-next.sh --state <that file>`
      prints `STAGE: error — could not parse …` and serves no stage. Zero rows
      at exit 0 is a FAILURE of this check now, not the expected result.
   b. **A released runtime still loses it silently.** The same file through
      `git show 687ccad:plugins/loopkit/loopkit_core/triage_state.py` — the
      parent of the fix, and the shape of both released tags — parses **0 rows
      at exit 0**, and v0.2.2's `loop-next.sh` reports `STAGE: discover` /
      `NEXT: IDLE` with no BLOCKED line. This is the part that keeps the SHALL
      standing, so it is the part that must not be dropped as redundant.
   c. **Nobody took the sixth-column route.** The implementation still reads the
      real queue as **39 rows** (12 `blocked`), and
      `python3 -c "...; print(triage_state.ACCEPTED_HEADERS)"` still lists
      exactly the four- and five-column shapes.

   The row count in (c) is a moving number by design — it was 37 when this spec
   was first written and 39 at `66abc4d`. Assert it against a freshly parsed
   count of the same file, never against a literal copied out of this
   paragraph; a check that hardcodes 39 goes red on the next triage run and
   teaches whoever fixes it to distrust the criterion.
10. WHEN a row's status changes from `blocked` to any other value, the `spec`
    cell SHALL be cleared to the empty string in the same write.

    Criterion 3 moves a satisfied row to `new`, and until this criterion the
    spec said nothing about what happened to the cell. The normative rule above
    is scoped "while a row's status is `blocked`", so the instant the row
    becomes `new` the invariant stops applying and a spent `waits:…` sits in the
    cell the pipeline later writes a real spec path into. Nothing reads it
    today, so there is no bug today. The hazard is that rows bounce: a row that
    returns to `blocked` carries a condition authored against a queue that no
    longer exists, and the next tick evaluates it against whatever is there now.
    The unblock that follows is attributable to nothing anybody decided, which
    is the failure this whole spec exists to end, arriving by the back door.

    **Who clears it: whatever performs the status change, in the same write.**
    For criterion 3's automatic transition that is the runtime. For a hand edit
    or a skill it is the caller, and the default is against them — `update_row`
    leaves `spec` untouched when the argument is `None`
    (`plugins/loopkit/loopkit_core/triage_state.py:582 as of 66abc4d`), so a
    status change
    that simply does not mention `spec` preserves the stale condition. The
    caller must pass `spec=""` explicitly. A rule whose observance depends on
    remembering to pass an optional argument will be broken.

    So the runtime SHALL also **repair** what it finds. On any tick, a row whose
    status is not `blocked` and whose `spec` cell begins `waits:` has that cell
    cleared, and one diagnostic line is printed naming the row. Clearing is safe
    because the prefix is unambiguous, and that was measured rather than
    assumed: of the 39 rows in the real `state/triage.md` at `66abc4d`, three
    carry a non-empty `spec` cell and all three begin `specs/`. `waits:` is not a path,
    so the repair cannot destroy a spec reference. The diagnostic is what stops
    the repair being a silent overwrite — the value goes, the evidence that it
    was there does not.

    Check `[today]`: a detector over the real `state/triage.md` reporting every
    non-`blocked` row whose `spec` cell begins `waits:`. It runs against the
    current runtime and needs no evaluator. Run on this branch it reports zero,
    against 39 rows of which 12 are `blocked` and none of the 12 carries a
    condition yet. Re-measured at `66abc4d`: the row total moved from 37 as the
    queue grew, the blocked count and the three `specs/` cells did not, and the
    detector still reports zero. Assert the zero, not the totals. That zero is the baseline a regression is measured against: a
    detector first run *after* the change could not tell a clean queue from a
    detector that never matches anything.

    Check `[M2b]`: the bounce, in two halves. Build a fixture whose condition is
    satisfied; tick once; assert the row is at `new` **and** its `spec` cell is
    exactly `""`. Then set that row back to `blocked` without writing any
    condition, and tick again; assert zero transitions. The second half is the
    one that bites. A stale condition would fire there and move the row a second
    time on no new fact, and the first half alone cannot see it: asserting the
    cell is empty passes both against an implementation that clears it and
    against one that never wrote anything into it in the first place.

### What is checkable today

Criteria 1, 8's first check, 9, and 10's first check run against the current
runtime. **Criteria 2 through 7, 8's second check, and 10's second check are all
tagged `[M2b]`** — every one of them needs the condition evaluator that does not
exist yet. The tagged set and the not-yet-runnable set are the same set; that is
the claim, and it is true by counting. The first draft left 5, 6 and 7 untagged
while they needed the same unbuilt evaluator, so a reader who took the untagged
set for the runnable set found three that were not.

Criterion 5 is the exception worth naming. Its check is tagged `[M2b]` because
there is no evaluator to inspect, but the check itself needs no evaluator to be
*written*: it is thirty lines of `inspect` over a code object, and it was run
against seventeen stand-in evaluators before this text was written. Against an
evaluator that does not exist it fails closed, which is the correct verdict.

Every criterion names at least one check clause. Counted mechanically over this
section, that is ten of ten; the first draft was seven of eight, criterion 6
having carried none.

No criterion here cites a check that does not exist.

## Edge cases

- **Boundary.** Zero blocked rows: no check runs, no output line. One blocked row
  with a satisfied condition: exactly one transition. Every blocked row satisfied
  at once: all transition, and the tick reports each — a silent mass-unblock is
  indistinguishable from a bug.
- **Error.** A condition referencing a row that no longer exists → stays blocked,
  one diagnostic. A condition whose recorded form is from a newer runtime (an
  unknown kind) → stays blocked, one diagnostic. Both are criterion 4.
- **Grammar.** A `spec` cell on a blocked row that does not start with `waits:`
  is not a condition at all — it is a stray spec path, which the normative rule
  above forbids. Treat it as criterion 4's malformed shape and say so, rather
  than as "no condition"; the two are indistinguishable to a reader otherwise,
  and the quiet one is how a recorded intent gets lost.
- **Transition.** A row that leaves `blocked` and comes back later: the departure
  clears the cell (criterion 10), so the returning row records a fresh condition
  or none at all. A row found at any status other than `blocked` while carrying a
  `waits:` cell is repaired and reported, never evaluated — evaluating it is
  precisely the stale re-fire criterion 10 exists to prevent.
- **Concurrency.** Two ticks running against one queue must not double-transition
  a row; the transition goes through the existing writer, which already keys on
  `source`. Not applicable beyond that: a tick is a single process.

## Control case

The twelve rows currently waiting on the owner's rulings must stay exactly as
they are — counted, unpolled, silent. If this change makes the owner's queue
noisier, it has failed regardless of what else it fixes. Pin that with a fixture
of ruling-blocked rows whose tick output is identical before and after.

**Identical after scrubbing two absolute paths, not byte-identical raw.** The
tick's output embeds the scripts directory and the project directory, both of
which differ between any two checkouts. `tests/pins/loop-next-output-parity.py`
already carries the machinery — it replaces them with `<SCRIPTS>` and `<PROJECT>`
at `tests/pins/loop-next-output-parity.py:119 as of 66abc4d`, and fixture
`02-blocked-only` is
already a ruling-blocked queue. Extend that pin rather than writing a second
comparison; a fixture whose comparison forgets the scrubbing fails on every
machine but the one that recorded it, and gets disabled rather than fixed.

The "before" golden can be captured today, against the current runtime, before
any evaluator exists. Do that first: a control case captured after the change is
not a control case.
