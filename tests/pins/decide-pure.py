#!/usr/bin/env python3
"""decide-pure.py — `loopkit_core.decide()` is a pure function.

`specs/loopkit-runtime.md` criterion 8: "`decide(counts) -> (Stage, Next)`
SHALL be a pure function: the same counts SHALL yield the same pair with no
filesystem, clock or network read."

Calling it twice and comparing is NOT enough on its own — a function that reads
the clock still returns the same pair twice within a second, so that check
passes on impure code and would be a gate that cannot fail. Three checks
together, each able to fail alone:

  P1 STRUCTURAL. `loopkit_core.decide`'s namespace holds no module capable of
     touching the world: no `os`, `time`, `datetime`, `socket`, `subprocess`,
     `pathlib`, `random`, `urllib`, `json`. You cannot read a clock you did not
     import.
  P2 FROZEN ENVIRONMENT. `open`, `os.stat`, `os.listdir`, `time.time`,
     `time.monotonic`, `socket.socket` and `subprocess.run` are replaced with
     traps that RAISE. `decide` is then driven over every combination of counts
     in {0,1,2} for all six keys — 729 calls — and any world-read blows up.
  P3 DETERMINISM. The pair from inside the frozen environment equals the pair
     from outside it, and a second call inside equals the first.

usage: python3 tests/pins/decide-pure.py
"""
from __future__ import annotations

import builtins
import itertools
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "plugins" / "loopkit"))

from loopkit_core import decide as decide_mod  # noqa: E402
from loopkit_core.decide import COUNT_KEYS, decide, next_line  # noqa: E402

# Anything in this list can read the clock, the disk or the network. A pure
# lookup needs none of them.
FORBIDDEN_IMPORTS = (
    "os", "time", "datetime", "socket", "subprocess", "pathlib", "Path",
    "random", "urllib", "json", "importlib", "shutil", "tempfile",
)

fails: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    fails.append(msg)


class Trap(Exception):
    pass


def main() -> int:
    print("== P1 structural: decide.py imports nothing that can touch the world")
    leaked = [n for n in FORBIDDEN_IMPORTS if n in vars(decide_mod)]
    if leaked:
        fail(f"loopkit_core.decide imports {leaked} — a pure lookup needs none of them")
    else:
        ok("no os / time / datetime / socket / subprocess / pathlib / random in the namespace")

    # All 3**6 count combinations. Small, exhaustive, and it exercises every
    # precedence branch and both discover branches.
    combos = [dict(zip(COUNT_KEYS, v)) for v in itertools.product((0, 1, 2), repeat=len(COUNT_KEYS))]

    print("== P3a the same counts give the same pair (outside the freeze)")
    before = {tuple(sorted(c.items())): decide(c) for c in combos}
    ok(f"{len(before)} distinct count vectors evaluated")

    print("== P2 frozen environment: every world-read is a trap that raises")

    def trap(*_a, **_k):
        raise Trap("decide() read the world")

    saved = {
        "open": builtins.open,
        "os.stat": os.stat,
        "os.listdir": os.listdir,
        "os.getenv": os.getenv,
        "time.time": time.time,
        "time.monotonic": time.monotonic,
        "socket.socket": socket.socket,
        "subprocess.run": subprocess.run,
    }
    trapped = 0
    mismatched = 0
    err: Exception | None = None
    builtins.open = trap
    os.stat = trap
    os.listdir = trap
    os.getenv = trap
    time.time = trap
    time.monotonic = trap
    socket.socket = trap
    subprocess.run = trap
    try:
        for c in combos:
            try:
                first = decide(c)
                second = decide(dict(c))
            except Exception as exc:  # noqa: BLE001 - the trap is the signal
                trapped += 1
                err = exc
                break
            if first != second or first != before[tuple(sorted(c.items()))]:
                mismatched += 1
            # next_line() is the other half loop-next.sh consumes; freeze it too.
            try:
                next_line(c)
            except Exception as exc:  # noqa: BLE001
                trapped += 1
                err = exc
                break
    finally:
        builtins.open = saved["open"]
        os.stat = saved["os.stat"]
        os.listdir = saved["os.listdir"]
        os.getenv = saved["os.getenv"]
        time.time = saved["time.time"]
        time.monotonic = saved["time.monotonic"]
        socket.socket = saved["socket.socket"]
        subprocess.run = saved["subprocess.run"]

    if trapped:
        fail(f"decide()/next_line() touched the world under the freeze: {err!r}")
    else:
        ok(f"{len(combos)} count vectors, {len(combos) * 3} calls, zero world-reads")

    print("== P3b determinism: inside the freeze equals outside it, twice over")
    if mismatched:
        fail(f"{mismatched} count vector(s) gave a different pair on a second call")
    else:
        ok("every pair identical across the two calls and across the freeze boundary")

    print("== the three-way split is reachable at all (a control on the control)")
    seen = {n for _s, n in (decide(c) for c in combos)}
    if seen == {"CONTINUE", "WAIT", "IDLE"}:
        ok("CONTINUE, WAIT and IDLE all occur across the count space")
    else:
        fail(f"decide never returns some of CONTINUE/WAIT/IDLE: saw {sorted(seen)}")

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S)")
        return 1
    print("decide() purity PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
