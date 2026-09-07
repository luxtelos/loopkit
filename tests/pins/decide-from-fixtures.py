#!/usr/bin/env python3
"""decide-from-fixtures.py — `decide()` reproduces the split the FIXTURES assert.

`spec/fixtures/README.md`: "These files are the contract." So this pin reads
`spec/fixtures/*.json` and drives `loopkit_core.decide()` from what is in them,
never from counts typed into this file. Hand-written counts would let the code
and the fixtures drift apart while both looked green — the fixture would say
WAIT, the code would say IDLE, and no command would notice.

For each fixture it:

  1. tallies `input.queue` by `status`, deduplicating by `source`
     (`specs/loopkit-runtime.md` criterion 9: identity is `source`);
  2. compares that tally to `expected.counts` when the fixture asserts one —
     so a wrong tally fails HERE rather than silently feeding `decide`;
  3. calls `decide(counts)` and compares the pair to
     `(expected.stage, expected.next)`.

Then it requires the three-way split to be COVERED: fixture 01 CONTINUE,
02 WAIT, 03 IDLE. Criterion 7's note — "the three-way split is the whole point,
so all three are required" — is a property of the fixture SET, and a pin that
only iterates whatever files happen to exist would go quietly green if two of
the three were deleted.

usage: python3 tests/pins/decide-from-fixtures.py
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "loopkit"))

from loopkit_core.decide import COUNT_KEYS, decide  # noqa: E402

# The fixture that must exercise each Next, by criterion 7's own naming.
REQUIRED_SPLIT = {
    "CONTINUE": "01-stage-precedence",
    "WAIT": "02-blocked-only",
    "IDLE": "03-empty-queue",
}

fails: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    fails.append(msg)


def counts_from_queue(queue: list[dict]) -> dict[str, int]:
    """Criterion 9: a `source` that appears twice is ONE row."""
    seen: set[str] = set()
    counts = {k: 0 for k in COUNT_KEYS}
    for row in queue:
        source = (row.get("source") or "").strip()
        if source and source in seen:
            continue
        seen.add(source)
        status = (row.get("status") or "").strip()
        if status in counts:
            counts[status] += 1
    return counts


def main() -> int:
    files = sorted(glob.glob(str(REPO / "spec" / "fixtures" / "*.json")))
    if not files:
        fail("no fixtures under spec/fixtures/ — the contract is missing")
        print("\n1 FAILURE(S)")
        return 1

    print("== decide() against every fixture, driven from the fixture files")
    observed: dict[str, set[str]] = {}
    for path in files:
        name = Path(path).stem
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        expected = data.get("expected", {})
        if "stage" not in expected or "next" not in expected:
            continue

        counts = counts_from_queue(data.get("input", {}).get("queue", []))

        if "counts" in expected and counts != expected["counts"]:
            fail(f"{name}: tally {counts} != expected.counts {expected['counts']}")
            continue

        got = decide(counts)
        want = (expected["stage"], expected["next"])
        if got == want:
            ok(f"{name}: {counts} -> {got[0]} / {got[1]}")
            observed.setdefault(want[1], set()).add(name)
        else:
            fail(f"{name}: decide{counts} = {got}, fixture says {want}")

    print("== the three-way split is covered by the fixture set, not just reachable")
    for nxt, fixture in REQUIRED_SPLIT.items():
        # Several fixtures may land on one Next (04 and 05 both CONTINUE).
        # What must hold is that the NAMED one still does: deleting 01 and
        # keeping 05 would leave CONTINUE "covered" while the precedence case
        # it was there to pin had gone.
        if fixture in observed.get(nxt, set()):
            ok(f"{nxt} is asserted by {fixture}")
        elif nxt in observed:
            fail(f"{nxt} comes only from {sorted(observed[nxt])}, not the required {fixture}")
        else:
            fail(f"no fixture asserts {nxt} — {fixture}.json is missing or changed")

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S)")
        return 1
    print("decide() matches the fixtures PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
