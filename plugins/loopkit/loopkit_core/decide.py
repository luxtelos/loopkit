#!/usr/bin/env python3
"""decide.py — the Stage precedence and the CONTINUE/WAIT/IDLE split, as one
pure function.

Until M2 this logic lived in two inline `if/elif` chains at the bottom of
`plugins/loopkit/scripts/loop-next.sh`. Bash could not be called from any other
language, could not be unit-tested without spawning a shell and a git repo, and
could not be held to `spec/fixtures/*.json` — so the fixtures asserted a
three-way split that no code the fixtures could reach actually computed.

`specs/loopkit-runtime.md` criteria 4, 5, 6, 7 and 8 are this file:

  4  Stage precedence is `fixing > spec-ready > spec-draft > new`, and
     `discover` when none of those four is present.
  5  a `pr-open` row is COUNTED and POLLED, never served as a Stage.
  6  a `blocked` row is COUNTED, never polled and never served as a Stage.
  7  Stage != discover -> CONTINUE. Stage == discover with any `pr-open` or
     `blocked` row -> WAIT. Otherwise IDLE.
  8  `decide(counts) -> (Stage, Next)` is PURE: the same counts yield the same
     pair, with no filesystem, clock or network read.

Criterion 8 is why `decide` takes a Counts mapping rather than a path, a root
or an argv. Everything impure — parsing `state/triage.md`, resolving a lane,
tallying rows — already happens in `triage_state.py` and `loop_next_pick.py`
before this function is reached.

The ACTION and NEXT sentences live here too, beside the branch that selects
them, because a decision whose explanation is stored somewhere else drifts from
it. They are the exact strings `loop-next.sh` printed before M2; the parity pin
`tests/pins/loop-next-output-parity.sh` diffs old against new to prove it.

CLI, used by `loop-next.sh`:

    python3 -m loopkit_core.decide --pr N --fixing N --spec-ready N \\
        --spec-draft N --new N --blocked N

prints four tab-separated lines: `stage`, `next`, `action`, `next_line`.
"""
from __future__ import annotations

import argparse
import sys
from typing import Mapping

# The six keys of Counts (`specs/loopkit-runtime.md` §Nouns). All six are
# ALWAYS present in the structured form, `0` when no row holds one; the
# tab-separated stdout of `loop_next_pick.pick()` omits a zero `blocked` line,
# which is a rendering detail of that transport and not a difference in the
# tally.
COUNT_KEYS = ("pr-open", "fixing", "spec-ready", "spec-draft", "new", "blocked")

# Highest first. `pr-open` is deliberately NOT in this tuple: making it a stage
# is what starved the backlog — a real loop once spent ~40 consecutive ticks on
# one `pr-open` row while 40 rows sat unclassified at `new`. `blocked` is not
# here either: a row waiting on a human ruling cannot move however often it is
# served.
STAGE_PRECEDENCE = ("fixing", "spec-ready", "spec-draft", "new")

# The Stage served when no work stage has a row.
DISCOVER = "discover"

STAGES = STAGE_PRECEDENCE + (DISCOVER,)
NEXTS = ("CONTINUE", "WAIT", "IDLE")

# What a tick should DO once the Stage is known. Verbatim from the `A=` arms of
# the pre-M2 `loop-next.sh`.
ACTIONS = {
    "fixing": "run the reviewer agent + the stop gate on the in-flight change. PASS -> open PR, set pr-open. FAIL -> cite the criterion.",
    "spec-ready": "hand to the implementer in its own worktree and branch. Set fixing.",
    "spec-draft": "run the spec-writer skill to produce EARS criteria. Carry the control case in. Set spec-ready.",
    "new": "run loop-assess Mode A: baseline, classify, route. code -> spec-draft; anything else -> inbox and set the row inbox.",
    DISCOVER: "no actionable rows. Run morning-triage to find work.",
}

# Why the loop should or should not sleep. Verbatim from the pre-M2
# `loop-next.sh`; WAIT interpolates the blocked count.
CONTINUE_TEXT = (
    "CONTINUE — after this stage there is still work here; run the next tick NOW. "
    "Do not schedule a wakeup; nothing external is being waited on."
)
WAIT_TEXT = (
    "WAIT — nothing here can move without an outside event (PR review / CI / a human "
    "ruling on {blocked} blocked row(s)). A wakeup is appropriate: 30 min working hours, "
    "60 otherwise."
)
IDLE_TEXT = (
    "IDLE — no rows and nothing in flight. Run morning-triage now; a wakeup here would "
    "sleep on an empty queue."
)


def normalize_counts(counts: Mapping[str, object] | None) -> dict[str, int]:
    """Every key of COUNT_KEYS present as an int, missing ones as 0.

    Pure, and total: a Counts mapping that omits `blocked` (the shape
    `loop_next_pick.pick()` renders for bash) means zero blocked rows, not an
    error. A value that is not an integer is a caller bug and raises, because
    coercing it silently is how a miscount comes to look like an empty backlog.
    """
    src = counts or {}
    out: dict[str, int] = {}
    for key in COUNT_KEYS:
        raw = src.get(key, 0)
        if raw is None or raw == "":
            raw = 0
        out[key] = int(raw)
    return out


def decide(counts: Mapping[str, object] | None) -> tuple[str, str]:
    """(Stage, Next) from Counts alone. Criteria 4-8.

    PURE by construction: no import here touches the filesystem, the clock or
    the network, and the body reads nothing but its argument. Pinned by
    `tests/pins/decide-pure.py`, which calls it twice under a frozen
    environment and compares, and by `tests/pins/decide-from-fixtures.py`,
    which drives the three-way split from `spec/fixtures/*.json` rather than
    from hand-written counts.
    """
    n = normalize_counts(counts)

    stage = DISCOVER
    for candidate in STAGE_PRECEDENCE:
        if n[candidate] > 0:
            stage = candidate
            break

    if stage != DISCOVER:
        nxt = "CONTINUE"
    elif n["pr-open"] > 0 or n["blocked"] > 0:
        nxt = "WAIT"
    else:
        nxt = "IDLE"
    return stage, nxt


def action_for(stage: str) -> str:
    """The ACTION sentence for a Stage. Pure lookup."""
    return ACTIONS.get(stage, ACTIONS[DISCOVER])


def next_line(counts: Mapping[str, object] | None) -> str:
    """The full NEXT sentence, blocked count interpolated. Pure."""
    n = normalize_counts(counts)
    _, nxt = decide(n)
    if nxt == "CONTINUE":
        return CONTINUE_TEXT
    if nxt == "WAIT":
        return WAIT_TEXT.format(blocked=n["blocked"])
    return IDLE_TEXT


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage + Next from Counts (pure lookup).")
    ap.add_argument("--pr", "--pr-open", dest="pr_open", type=int, default=0)
    ap.add_argument("--fixing", type=int, default=0)
    ap.add_argument("--spec-ready", dest="spec_ready", type=int, default=0)
    ap.add_argument("--spec-draft", dest="spec_draft", type=int, default=0)
    ap.add_argument("--new", dest="new", type=int, default=0)
    ap.add_argument("--blocked", type=int, default=0)
    a = ap.parse_args(argv)
    counts = {
        "pr-open": a.pr_open,
        "fixing": a.fixing,
        "spec-ready": a.spec_ready,
        "spec-draft": a.spec_draft,
        "new": a.new,
        "blocked": a.blocked,
    }
    stage, nxt = decide(counts)
    # Tab-separated so bash 3.2 can read it with awk -F'\t'. One key per line,
    # never positional: a caller that reads the wrong field of a positional
    # line fails silently, and this output selects the whole tick.
    print(f"stage\t{stage}")
    print(f"next\t{nxt}")
    print(f"action\t{action_for(stage)}")
    print(f"next_line\t{next_line(counts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
