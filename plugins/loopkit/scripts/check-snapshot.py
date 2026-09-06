#!/usr/bin/env python3
"""check-snapshot.py — validate commerce snapshot evals for shape and honesty.

    check-snapshot.py [--root DIR] [--dir evals/commerce] [--strict]

Each file must carry: id, transcript (list of role/content), state_before,
expected_state_after, and either cap or must_not. It must grade END STATE:
`expected_state_after` may not contain a `path`, `steps` or `tool_calls` key.
A snapshot that names a live key literal is an ERROR. Exit 1 with --strict on
errors. n=0 when the directory is absent — nothing is invented.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REQUIRED = ("id", "transcript", "state_before", "expected_state_after")
PATH_KEYS = ("path", "steps", "tool_calls", "trajectory")
LIVE = re.compile(r"\bsk_live_[A-Za-z0-9]{8,}")


def project_root(explicit: str | None) -> Path:
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


def check(path: Path) -> list[tuple[str, str]]:
    f: list[tuple[str, str]] = []
    try:
        raw = path.read_text(encoding="utf-8")
        d = json.loads(raw)
    except Exception as e:
        return [("ERROR", f"not valid JSON: {e}")]
    for k in REQUIRED:
        if k not in d:
            f.append(("ERROR", f"missing `{k}`"))
    if not isinstance(d.get("transcript"), list) or not all(isinstance(t, dict) and {"role", "content"} <= set(t) for t in d.get("transcript") or []):
        f.append(("ERROR", "`transcript` must be a list of {role, content}"))
    exp = d.get("expected_state_after") or {}
    if any(k in exp for k in PATH_KEYS):
        f.append(("ERROR", "expected_state_after grades a PATH (path/steps/tool_calls); grade the end state only"))
    if "cap" not in d and "must_not" not in d:
        f.append(("WARN", "no `cap` and no `must_not` — what would this snapshot catch?"))
    if LIVE.search(raw):
        f.append(("ERROR", "a live-mode key literal is in the snapshot"))
    if not d.get("source"):
        f.append(("WARN", "no `source` — snapshots come from real conversations; say which"))
    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root"); ap.add_argument("--dir", default="evals/commerce"); ap.add_argument("--strict", action="store_true")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    root = project_root(a.root)
    files = [Path(x) for x in a.files] or sorted((root / a.dir).glob("*.json"))
    if not files:
        print(f"SNAPSHOTS: n=0 (no files under {a.dir})"); return 0
    errors = 0
    for p in files:
        fs = check(p)
        errors += sum(1 for lvl, _ in fs if lvl == "ERROR")
        print(f"  {'ok  ' if not fs else 'note'} {p.name}")
        for lvl, msg in fs:
            print(f"        {lvl}: {msg}")
    print(f"SNAPSHOTS: {len(files)} checked, {errors} error(s)")
    if errors and a.strict:
        print("VERDICT: FAIL"); return 1
    print("VERDICT: PASS"); return 0


if __name__ == "__main__":
    sys.exit(main())
