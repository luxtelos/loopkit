#!/usr/bin/env python3
"""ticks.py — the ticks ledger: one JSON line per loop event, append-only.

    ticks.py append --event stage|gate|acceptance|transition|merge [--k v ...]
    ticks.py tail [--n N]

Written by loop-next.sh (stage served), stop_gate.sh (gate verdict),
triage_state.py update (row transitions) and anything else that wants to be
counted. Read by loop-metrics.py. Lives at state/ticks.jsonl. Fail-open: a
ledger write can never change a verdict, so every path here exits 0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


def project_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def ledger(root: Path) -> Path:
    return root / "state" / "ticks.jsonl"


def append(root: Path, event: str, **fields) -> dict:
    row = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "event": event}
    row.update({k: v for k, v in fields.items() if v is not None})
    try:
        p = ledger(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"ticks.py: {e}", file=sys.stderr)
    return row


def rows(root: Path) -> list[dict]:
    p = ledger(root)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=None)
    ap = argparse.ArgumentParser(parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append", parents=[common]); a.add_argument("--event", required=True)
    a.add_argument("--k", action="append", default=[], help="key=value, repeatable")
    t = sub.add_parser("tail", parents=[common]); t.add_argument("--n", type=int, default=5)
    args = ap.parse_args()
    root = project_root(args.root)
    if args.cmd == "append":
        fields = {}
        for kv in args.k:
            if "=" in kv:
                k, v = kv.split("=", 1)
                fields[k.strip()] = v.strip()
        print(json.dumps(append(root, args.event, **fields)))
    else:
        for r in rows(root)[-args.n:]:
            print(json.dumps(r))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
