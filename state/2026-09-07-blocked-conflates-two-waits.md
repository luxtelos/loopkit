# Assessment — `blocked` conflates two different waits (2026-09-07)

## Observed

`docs/research/runtime-plan.md §M2` and `plan §distribution
official-marketplace` were both `blocked`. Neither was waiting on a person. M2
waited on M1 merging, which happened; the submission waited on 0.2.0 being
tagged, which happened three releases ago. Both sat unserved.

## Evidence

`loop-next.sh` prints `BLOCKED: n row(s) awaiting a human ruling — not polled,
not a stage`, and `loop_next_pick.pick()` counts blocked rows without ever
emitting a target for one. So an event-blocked row is invisible to every tick
until a human re-reads the table. Could this check have failed? Yes — if either
precondition were still unmet, the rows would have been correctly parked and
nothing would have surfaced.

## Control case

A row genuinely waiting on a person must STILL never be polled. Whatever fixes
this must not turn the eleven ruling-blocked rows into a poll on every tick;
that behaviour is deliberate and is what keeps the owner's queue quiet.

## Classification

`code`, narrowly. The status vocabulary has one value for two meanings, and the
tick cannot tell them apart. The reflex to resist is "re-read the blocked rows
each morning" — that is a habit, and it failed here for the full life of both
preconditions.

## Route

`spec-writer`. The shape of the fix, for the spec to make precise: a blocked row
carries WHAT it waits on. A row waiting on a person stays exactly as it is —
counted, never polled. A row waiting on an observable event (a merge, a tag, a
release) is checked cheaply on each tick, and becomes `new` when its condition
holds. The check must be a lookup, not a judgement, or the tick starts guessing.

## Not a code problem

Which rows are currently mis-parked is a one-off correction, done in this same
commit. The eleven genuinely waiting on the owner stay `blocked` and stay
unpolled.
