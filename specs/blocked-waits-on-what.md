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
one. That is exactly right for a row awaiting a ruling: polling it would nag the
owner every tick, and the quiet is the point.

It is wrong for a row awaiting an event. On 2026-09-07 two rows sat blocked long
after their preconditions passed — M2 waited on M1 merging, which had happened;
the marketplace submission waited on 0.2.0 being tagged, which had happened three
releases earlier. Neither was ever going to be noticed, because nothing looks at
a blocked row. The recurrence rate is not "sometimes"; it is once per
event-blocked row, every time.

## Scope

In: a way for a blocked row to record what it waits on; a cheap per-tick check
for the event kind; the transition to `new` when the condition holds. Out: any
change to how ruling-blocked rows behave; any new status value that would
invalidate an existing queue; anything that makes the tick guess.

## Constraints

- **The check is a lookup, never a judgement.** The moment a tick decides for
  itself whether a condition "probably" holds, the queue stops being memory and
  becomes an opinion. A condition it cannot evaluate leaves the row alone.
- **Backwards compatible.** An existing `blocked` row with no recorded condition
  keeps today's behaviour exactly: counted, never polled.
- **Cheap.** The check runs on every tick, so it may not cost an API call per
  row. Conditions that need the network are evaluated from data a tick already
  fetches, or not at all.

## Acceptance (EARS)

1. WHEN a blocked row records no condition, the runtime SHALL count it, SHALL NOT
   poll it and SHALL NOT serve it — today's behaviour, unchanged. Check
   `[today]`: a fixture queue of ruling-blocked rows yields the same
   `BACKLOG`/`BLOCKED`/`STAGE`/`NEXT` lines before and after this change.
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
   shape, each yielding tick exit 0, zero transitions and exactly one
   diagnostic naming the row.
5. The condition check SHALL be a pure function of data the tick already holds:
   same inputs, same verdict, no clock read beyond a date comparison, no network
   call of its own. Check: call it twice with identical inputs under a frozen
   clock and compare.
6. WHEN a condition names another row (for example, blocked until row X reaches
   `done`), the runtime SHALL evaluate it against the queue in the same read that
   produced the counts, SHALL NOT re-read the queue, and SHALL NOT follow a chain
   more than one row deep in a single tick. A row unblocked this tick is served
   the next one, which keeps each transition attributable.
7. IF two rows name each other as conditions, THEN both SHALL stay blocked and
   the runtime SHALL print one line naming the cycle. Check: a fixture with a
   two-row cycle produces no transition and one diagnostic.
8. The status vocabulary SHALL NOT gain a new value. A queue written before this
   change SHALL parse unchanged afterwards, and a queue written after it SHALL
   parse in a runtime that predates it. Check `[today]`:
   `python3 -c "import triage_state; print(sorted(triage_state.VALID_STATUSES))"`
   before and after are equal. Check `[M2b]`: write a queue with conditions,
   parse it with the merge-base `triage_state`, write it back, compare bytes.

### What is checkable today

Criteria 1 and 8's first check run against the current runtime. Everything
tagged `[M2b]` needs the condition evaluator that does not exist yet; those are
tagged, not hidden. No criterion here cites a check that does not exist — the
defect a reviewer found in the runtime spec's criterion 33 two hours ago, where
a proxy was named as though it were a command.

## Edge cases

- **Boundary.** Zero blocked rows: no check runs, no output line. One blocked row
  with a satisfied condition: exactly one transition. Every blocked row satisfied
  at once: all transition, and the tick reports each — a silent mass-unblock is
  indistinguishable from a bug.
- **Error.** A condition referencing a row that no longer exists → stays blocked,
  one diagnostic. A condition whose recorded form is from a newer runtime →
  stays blocked, one diagnostic. Both are criterion 4.
- **Concurrency.** Two ticks running against one queue must not double-transition
  a row; the transition goes through the existing writer, which already keys on
  `source`. Not applicable beyond that: a tick is a single process.

## Control case

The twelve rows currently waiting on the owner's rulings must stay exactly as
they are — counted, unpolled, silent. If this change makes the owner's queue
noisier, it has failed regardless of what else it fixes. Pin that with a fixture
of ruling-blocked rows whose tick output is byte-identical before and after.
