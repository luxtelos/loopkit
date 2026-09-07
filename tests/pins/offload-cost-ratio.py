#!/usr/bin/env python3
"""offload-cost-ratio.py — pattern matching must stay LINEAR in the pattern count.

WHAT THIS PIN HAS BEEN, TWICE, AND WHY BOTH WERE WRONG.

v1 asserted a hard 200 ms wall clock on one subprocess run of the hook. It
failed twice on 2026-09-07 at 215 ms and 205 ms on a busy machine with nothing
regressed, and a reviewer reproduced 307 ms. A gate that fails for load teaches
one habit — re-run until green — which is indistinguishable from re-running
until a real failure hides.

v2 replaced it with a ratio between two subprocess runs, 50 patterns against 1,
and claimed it "cannot fail for load, because both halves are equally loaded".
The 2026-09-07 review measured that claim and it is false. The halves run
sequentially, so load hits them asymmetrically: idle it scored 0.45 to 1.81, and
under load it reached 2.75 against a 3.0 threshold — 9% from a false alarm. It
also MISSED injected quadratic work at +55 ms, only tripping at +150 ms. Both
failures have one cause: about 59 ms of every measurement is bare interpreter
startup, a constant sitting in both halves that dilutes the ratio until the
noise band and the detection band overlap. A pin that can both false-alarm and
miss is worse than no pin, because it teaches people to ignore it.

WHAT THIS PIN IS NOW. The same property, measured where the constant does not
exist: `decide()` is imported and called IN-PROCESS, so there is no interpreter
startup in either half at all. The measured quantity is nanoseconds per pattern
examined, at 20 patterns and at 2000, interleaved and best-of-K so a load spike
lands on both halves rather than on one. A linear matcher holds that quantity
flat; a pathological one does not.

Measured on this design, 2026-09-07:
  linear (the real hook), idle      0.75 - 1.19    8 runs
  linear, under 12-way CPU load     up to 1.43
  O(n^1.75) injected                    11.5
  O(n^2)   injected                     96.6
So 3.0 now sits in a gap of nearly two orders of magnitude instead of 9% above
observed noise, and the quadratic work v2 missed is caught by a factor of 30.

WHAT IS DELIBERATELY NOT PINNED, SAID PLAINLY. The end-to-end wall clock of one
hook invocation is NOT pinned by anything, here or elsewhere. It is roughly
59 ms of interpreter startup plus a few microseconds of matching, so it is a
measure of Python's startup and of how busy the machine is, and almost nothing
to do with this code. Any gate over it is the v1 flake rebuilt. If that cost
ever needs a bound, it needs a different mechanism — a persistent hook process,
or a budget measured once at install time — not a threshold in this file.

usage:  offload-cost-ratio.py <path to offload_rewrite.py>
prints: "<growth> <ns per pattern> hit|miss"
"""
import importlib.util
import pathlib
import re
import sys
import time

# 100x apart. v2 used 50x1, where the signal was swamped; a wide spread is what
# turns "is it linear" into a question the clock can actually answer.
N_SMALL, N_LARGE = 20, 2000
REPS_SMALL, REPS_LARGE = 400, 20
ROUNDS = 5

# Between a noise band that tops out near 1.5 and injected quadratic work at 96.
GROWTH_MAX = 3.0

# A matcher can also get slower without changing shape — a linear matcher 100x
# slower is still linear. This is the ceiling for that, set with ~50x headroom
# over the slowest measurement seen (290 ns/pattern on a loaded laptop,
# and slower again inside a container), so it catches a catastrophe and never
# a busy afternoon.
NS_PER_PATTERN_MAX = 15000

# A MISS, on purpose: every pattern is examined, which is the worst case and the
# only case whose cost is proportional to the pattern count.
MISS_CMD = "git status --porcelain"
HIT_CMD = "seq 1 3"


def load_hook(path: str):
    spec = importlib.util.spec_from_file_location("offload_rewrite_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def noisy(n: int) -> list:
    return [(s, re.compile(s)) for s in ("^noisy%d\\b" % i for i in range(n))]


def measure(decide, wrapper) -> tuple[float, float]:
    """Nanoseconds per pattern at both sizes, interleaved best-of-ROUNDS.

    INTERLEAVED is the whole point. v2 measured one size to completion and then
    the other, so a load spike inflated exactly one of them and moved the ratio.
    Alternating them means a spike lands on both, and best-of takes the round
    the spike missed. Load can only ever ADD time, so the minimum is the
    measurement least contaminated by it.
    """
    small, large = noisy(N_SMALL), noisy(N_LARGE)
    best_small = best_large = None
    for _ in range(ROUNDS):
        start = time.perf_counter()
        for _ in range(REPS_SMALL):
            decide(MISS_CMD, small, wrapper)
        per = (time.perf_counter() - start) / REPS_SMALL / N_SMALL
        best_small = per if best_small is None else min(best_small, per)

        start = time.perf_counter()
        for _ in range(REPS_LARGE):
            decide(MISS_CMD, large, wrapper)
        per = (time.perf_counter() - start) / REPS_LARGE / N_LARGE
        best_large = per if best_large is None else min(best_large, per)
    return best_small, best_large


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: offload-cost-ratio.py <path to offload_rewrite.py>", file=sys.stderr)
        return 2
    hook = load_hook(sys.argv[1])
    wrapper = pathlib.Path("/nonexistent/run-capped.sh")

    # Correctness, not timing: the LAST pattern in a 50-line file must still
    # match. v2 got this from whether the subprocess wrote to stdout; asking
    # `decide` directly tests the same thing without a clock in the way.
    fifty = noisy(49) + [("^seq\\b", re.compile("^seq\\b"))]
    hit = hook.decide(HIT_CMD, fifty, wrapper) is not None

    small, large = measure(hook.decide, wrapper)
    if small <= 0 or large <= 0:
        print("0 0 miss", file=sys.stderr)
        return 1

    growth = large / small
    ns = large * 1e9
    print(f"{growth:.2f} {int(ns)} {'hit' if hit else 'miss'}")

    failures = []
    if not hit:
        failures.append("the last pattern of a 50-pattern file did not match")
    if growth > GROWTH_MAX:
        failures.append(
            f"per-pattern cost grew {growth:.2f}x between {N_SMALL} and {N_LARGE} "
            f"patterns (limit {GROWTH_MAX}) — the matcher is not linear"
        )
    if ns > NS_PER_PATTERN_MAX:
        failures.append(
            f"{int(ns)} ns per pattern exceeds {NS_PER_PATTERN_MAX} — still "
            f"linear, but far slower than it was"
        )
    for f in failures:
        print(f"offload-cost-ratio: {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
