# A blocked row records WHAT it waits on

- Row: `loop 2026-09-07 §blocked-conflates-two-waits`
- Assessment: `state/2026-09-07-blocked-conflates-two-waits.md`
- Outcome: a row waiting on an observable event is re-checked cheaply on every
  tick and becomes actionable by itself; a row waiting on a person is still
  never polled.

## Why

`blocked` means two different things today and the tick cannot tell them apart.
`loop-next.sh` prints "awaiting a human ruling — not polled, not a stage", and
`loop_next_pick.pick()` counts blocked rows without ever emitting a target for
one — `blocked` is absent from `WANT` at
`plugins/loopkit/loopkit_core/loop_next_pick.py:26`. That is exactly right for a
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
   `plugins/loopkit/loopkit_core/triage_state.py:14`, `source` is the identity
   key (`find_row`, `plugins/loopkit/loopkit_core/triage_state.py:159`) and
   `upsert_row` matches on it; `status` and `priority` drive the pick; and
   `finding` **plus** `source` are the haystack for scope matching at
   `plugins/loopkit/loopkit_core/loop_next_pick.py:89`, so a condition written
   into either would silently change which rows a `--scope` lane matches. `spec`
   is parsed, normalised and written back, and no code branches on it.
2. **It is empty on every blocked row that exists.** All 12 rows at `blocked` in
   `state/triage.md` today carry an empty `spec` cell. A blocked row has no spec
   yet by definition of being blocked — that is what it is waiting to earn.
3. **It already round-trips.** `spec` is one of `COLUMNS`, so `parse_table`
   copies it (`plugins/loopkit/loopkit_core/triage_state.py:128`) and
   `format_row` writes it. No parser change is needed to *carry* a condition;
   only the evaluator is new. A sixth column would need both.

**While a row's status is `blocked`, its `spec` cell SHALL hold either the empty
string or exactly one condition — never a spec path, and never both.** A blocked
row that has earned a spec path is no longer blocked. This keeps the cell
single-valued, so no delimiter between "spec path" and "condition" has to be
invented, and no such delimiter can be got wrong.

### The grammar

```
condition = "waits:" kind ":" argument
kind      = 1*( ALPHA / "-" )          ; from the closed set below
argument  = 1*( any character except "|" ), inner spaces single only
```

The closed set of kinds:

| kind | argument | meaning | evaluable today? |
|---|---|---|---|
| `row-done` | the `source` cell of another row, verbatim | that row has reached `done` | yes |

`row-done` is the whole set, and that is deliberate rather than an omission.
Criterion 5 requires the evaluator to be a pure function of data the tick already
holds, and the parsed rows are the only such data. A kind that needs `git tag`,
the GitHub API or the filesystem is not evaluable under that constraint, so it is
not defined here; writing one (`waits:tag-exists:v0.2.0`, say) is
well-formed-looking but an **unknown kind**, which is criterion 4's third failure
shape — the row stays blocked and says why. Adding a kind is a spec change, which
is the point: the tick may not invent one.

Because both sides of a `row-done` comparison pass through `normalize_cell`, the
argument is compared to the target's `source` after normalisation, with no
trimming or casefolding of its own.

### What the syntax may not contain, and why

Two rewrites happen between what an author types and what the next tick reads.
Both were run, not reasoned about:

- `normalize_cell` is `" ".join(value.strip().split())`
  (`plugins/loopkit/loopkit_core/triage_state.py:47`). It collapses any run of
  whitespace to one space and strips the ends. **A grammar carrying a double
  space or a leading space is silently rewritten on read.**
- `format_row` writes `.replace("|", "\\|")`
  (`plugins/loopkit/loopkit_core/triage_state.py:65`). **A grammar carrying a
  literal pipe is silently rewritten on write** — the in-memory value survives
  the round trip because `split_markdown_row` unescapes it, but the bytes on
  disk change, so a byte-comparison pin against a hand-written queue fails.

Measured, writing each candidate through both functions:

```
verdict  norm==in  ondisk==in  candidate
OK       True      True        'waits:row-done:docs/research/runtime-plan.md §M3'
OK       True      True        'waits:tag-exists:v0.2.0'
OK       True      True        'waits:row-done:'
REWRIT   True      False       'waits:row-done:a|b'    -> disk 'waits:row-done:a\|b'
REWRIT   False     True        'waits:row-done:a  b'   -> read 'waits:row-done:a b'
REWRIT   False     False       ' waits:row-done:a'     -> read 'waits:row-done:a'
```

Note that the two rewrites bite at different stages: the pipe survives reading
and is mangled on writing, the double space survives writing and is mangled on
reading. A check that exercises only one direction misses one of them.

The chosen grammar therefore uses `:` as its separator, which neither function
touches. A full queue written through `write_table`, re-parsed and written again
is byte-identical, and the condition cell reads back exactly as authored.

### No sixth column

**The queue SHALL NOT gain a sixth column, for this or any other purpose.**

This is not a style preference; a six-column queue destroys the file. The header
is matched against a closed list of accepted shapes at
`plugins/loopkit/loopkit_core/triage_state.py:89` (four-column and five-column
only), and any row whose cell count differs from the header's is skipped at
`plugins/loopkit/loopkit_core/triage_state.py:121`. Adding a sixth column to the
real `state/triage.md` and parsing it with today's runtime:

```
rows parsed: 0
round-trip identical: False
EXIT=0
```

Zero rows, **exit 0** — indistinguishable from an empty queue, and writing that
parse back replaces all 37 rows with an empty table. This is precisely the
2026-08-31 bug the parser's own comment at
`plugins/loopkit/loopkit_core/triage_state.py:82` memorialises, where a header
that failed to match returned `rows=[]` with exit 0 while the real file held 40
rows.

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
   move that row to `new` and SHALL report the transition on the tick's output,
   naming the row and the condition that fired. Check `[M2b]`: a fixture whose
   condition names a row already at `done` produces exactly one transition and
   one output line carrying both the row's source and the condition.
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
5. The condition check SHALL be a pure function of data the tick already holds:
   same inputs, same verdict, no clock read beyond a date comparison, no network
   call of its own. Check `[M2b]`, in two parts, and the first is the one that
   bites:
   - **Purity.** Install a guard that makes every forbidden call *raise*, then
     call the evaluator once. It must return a verdict, not raise. The guard
     replaces `time.time`, `time.monotonic`, `time.perf_counter`, `time.time_ns`,
     `time.monotonic_ns`, `time.gmtime` and `time.localtime` with a function that
     raises; replaces `socket.socket`, `socket.create_connection` and
     `socket.getaddrinfo` likewise; and rebinds `datetime.datetime` and
     `datetime.date` to subclasses whose `now`, `today` and `utcnow` raise. The
     `datetime` half is not belt-and-braces: a `time`-module guard alone was
     measured passing an evaluator that called `datetime.datetime.now()`.
     Constructing and comparing a `datetime.date` still works, so the date
     comparison this criterion explicitly allows is not caught by mistake.
   - **Determinism.** Call it twice with identical inputs and compare. This is
     the weaker half, kept only for what a second call adds.

   The previous draft named only "call it twice under a frozen clock", which
   cannot fail: freezing the clock removes the variable it is meant to detect.
   Measured against six evaluators, the two checks separate like this:

   ```
                              proposed guard      frozen-clock (old)
   pure evaluator             PASS                PASS
   date comparison (allowed)  PASS                PASS
   reads time.time()          FAIL <- detected    PASS
   reads datetime.now()       FAIL <- detected    PASS
   reads date.today()         FAIL <- detected    PASS
   reads the network          FAIL <- detected    PASS
   ```

   The old check passes all six. It is not a check.
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
   `plugins/loopkit/loopkit_core/triage_state.py:14`; a bare `import triage_state`
   raises `ModuleNotFoundError` from the repo root, and the path-shim spelling
   (`cd plugins/loopkit/scripts && python3 -c "import triage_state; ..."`) works
   but reads the shim at `plugins/loopkit/scripts/triage_state.py:2` rather than
   the implementation, so prefer the form above. Check `[M2b]`: write a queue
   with conditions, parse it with the merge-base `triage_state`, write it back,
   compare bytes.
9. The queue SHALL NOT gain a sixth column. A five-column queue and a runtime
   that expects five columns are the compatibility contract; six columns is a
   silent total loss, not a degraded read. Check `[today]`: take the real
   `state/triage.md`, append a sixth cell to its header, separator and every row,
   and parse it with today's `parse_table`. It reports `rows parsed: 0` at
   **exit 0**, and writing that parse back is not byte-identical to the input —
   all 37 rows are gone. Run and confirmed on this branch. The check passes when
   the implementation still reads the real queue as 37 rows, i.e. when nobody
   took the sixth-column route.

### What is checkable today

Criteria 1, 8's first check, and 9 run against the current runtime. **Criteria 2
through 7, and 8's second check, are all tagged `[M2b]`** — every one of them
needs the condition evaluator that does not exist yet. The tagged set and the
not-yet-runnable set are the same set; that is the claim, and it is now true by
counting. The first draft left 5, 6 and 7 untagged while they needed the same
unbuilt evaluator, so a reader who took the untagged set for the runnable set
found three that were not.

Every criterion names at least one check clause. Counted mechanically over this
section, that is nine of nine; the first draft was seven of eight, criterion 6
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
at `tests/pins/loop-next-output-parity.py:109`, and fixture `02-blocked-only` is
already a ruling-blocked queue. Extend that pin rather than writing a second
comparison; a fixture whose comparison forgets the scrubbing fails on every
machine but the one that recorded it, and gets disabled rather than fixed.

The "before" golden can be captured today, against the current runtime, before
any evaluator exists. Do that first: a control case captured after the change is
not a control case.
