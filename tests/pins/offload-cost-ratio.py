#!/usr/bin/env python3
"""Measure what a 50-pattern offload file costs against a 1-pattern one.

WHY A RATIO. This pin used to assert a hard 200 ms wall clock. It failed twice on
2026-09-07 — 215 ms, then 205 ms — on a busy machine with nothing regressed. A
gate that fails for LOAD teaches one habit: re-run until green. That is
indistinguishable from re-running until a real failure hides, which is the most
expensive thing a gate can teach.

What the pin actually wants is that pattern matching is linear in the pattern
count rather than pathological. A ratio measured in the same run on the same
machine says exactly that, and cannot fail because the laptop is busy: both
halves are equally busy. Best-of-N because load inflates a measurement and never
deflates one.

usage: offload-cost-ratio.py <hook> <one-pattern-file> <many-pattern-file>
prints: "<ratio> <ms> hit|miss"
"""
import json, os, shutil, subprocess, sys, time

PAYLOAD = json.dumps({"tool_name": "Bash", "tool_input": {"command": "seq 1 3"}})


def measure(hook: str, patterns: str, n: int = 5) -> tuple[float, bool]:
    live = os.path.join(os.environ["CLAUDE_PROJECT_DIR"], ".loopkit", "offload-patterns.txt")
    shutil.copyfile(patterns, live)
    best, hit = None, False
    for _ in range(n):
        t = time.perf_counter()
        r = subprocess.run([sys.executable, hook], input=PAYLOAD,
                           capture_output=True, text=True)
        d = time.perf_counter() - t
        best = d if best is None else min(best, d)
        hit = hit or bool(r.stdout)
    return best, hit


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: offload-cost-ratio.py <hook> <one-pattern> <many-pattern>", file=sys.stderr)
        return 2
    hook, one_f, many_f = sys.argv[1], sys.argv[2], sys.argv[3]
    one, _ = measure(hook, one_f)
    many, hit = measure(hook, many_f)
    if one <= 0:
        print("0 0 miss", file=sys.stderr)
        return 1
    label = "hit" if hit else "miss"
    print(f"{many / one:.2f} {int(many * 1000)} {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
