#!/usr/bin/env python3
"""loop-metrics.py — the numbers the knowledge-layer thesis is judged by.

Reads state/ticks.jsonl (written by ticks.py) and state/triage.md. Prints
each figure with its n=, and prints "n=0" rather than a number when there is
nothing to count — a metric that is invented is worse than one that is
absent.

    loop-metrics.py [--root DIR] [--since YYYY-MM-DD] [--json]

  ticks            stage events served, by stage
  gate pass rate   gate PASS / all gate verdicts
  verified success gate PASS events later followed by a `done` transition,
                   over gate PASS events (the falsification instrument —
                   "verified" here means gate green AND the row reached done;
                   a human merge is what moves a row to done)
  re-asks          `transition` events into `inbox` whose source was already
                   at inbox before (a question asked twice)
  blocked rows     rows currently at `blocked`
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# M2: this module moved into `loopkit_core`, so `ticks` is a sibling in the
# package. The fallback keeps the file runnable as a bare script (`python3
# loopkit_core/loop_metrics.py`), where there is no package to be relative to.
try:
    from . import ticks  # noqa: E402
except ImportError:  # pragma: no cover - only when run as a loose script
    sys.path.insert(0, str(HERE))
    import ticks  # noqa: E402


def triage_rows(root: Path) -> list[dict]:
    state = root / "state" / "triage.md"
    if not state.exists():
        return []
    try:
        p = subprocess.run([sys.executable, str(HERE / "triage_state.py"), "list", "--state", str(state), "--format", "json"],
                           capture_output=True, text=True, timeout=30)
        return json.loads(p.stdout) if p.returncode == 0 else []
    except Exception:
        return []


def compute(root: Path, since: str | None = None) -> dict:
    ev = [r for r in ticks.rows(root) if not since or r.get("at", "") >= since]
    stages = collections.Counter(r.get("stage", "?") for r in ev if r.get("event") == "stage")
    gates = [r for r in ev if r.get("event") == "gate"]
    passes = [r for r in gates if r.get("result") == "PASS"]
    transitions = [r for r in ev if r.get("event") == "transition"]
    done_after: set[str] = set()
    for i, g in enumerate(passes):
        for t in transitions:
            if t.get("status") == "done" and t.get("at", "") >= g.get("at", ""):
                done_after.add(g.get("at", ""))
                break
    seen_inbox: set[str] = set()
    reasks = 0
    for t in transitions:
        if t.get("status") == "inbox":
            src = t.get("source", "")
            if src in seen_inbox:
                reasks += 1
            seen_inbox.add(src)
    rows = triage_rows(root)
    blocked = sum(1 for r in rows if r.get("status") == "blocked")
    return {
        "ticks": {"n": sum(stages.values()), "by_stage": dict(stages)},
        "gate_pass_rate": {"n": len(gates), "value": (len(passes) / len(gates)) if gates else None},
        "verified_success": {"n": len(passes), "value": (len(done_after) / len(passes)) if passes else None},
        "reasks": {"n": len([t for t in transitions if t.get("status") == "inbox"]), "value": reasks},
        "blocked_rows": {"n": len(rows), "value": blocked},
    }


def fmt(name: str, m: dict) -> str:
    v = m.get("value")
    if isinstance(v, float):
        v = f"{v:.0%}"
    if m.get("n", 0) == 0:
        return f"{name:<18} n=0 (nothing to count yet)"
    return f"{name:<18} {v if v is not None else m.get('by_stage')}  n={m['n']}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root"); ap.add_argument("--since"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = ticks.project_root(a.root)
    m = compute(root, a.since)
    if a.json:
        print(json.dumps(m, indent=1)); return 0
    print(f"LOOP METRICS ({root.name}{', since ' + a.since if a.since else ''})")
    for k in ("ticks", "gate_pass_rate", "verified_success", "reasks", "blocked_rows"):
        print("  " + fmt(k, m[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
