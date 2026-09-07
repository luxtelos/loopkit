#!/usr/bin/env python3
"""queue-never-reads-empty.py — the queue must never parse as empty when it isn't.

A header carrying an unknown or renamed column used to yield zero rows, exit 0,
no diagnostic. The loop then reads its own memory as empty, reports itself idle,
and nobody notices. The same shape was fixed once before, on 2026-08-31, for a
padded header — with 40 rows sitting unread in the file — so this is the second
visit and the guard now covers the class rather than the instance.

Both halves matter: the two REFUSE cases prove it bites, and the two
legitimately-empty cases prove it does not bite work that is honestly empty.
Run from the repo root."""
import json, pathlib, subprocess, sys, tempfile

import os, subprocess
_ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=os.path.dirname(os.path.abspath(__file__)),
                       capture_output=True, text=True).stdout.strip()
assert _ROOT, "not inside a git checkout — cannot locate the repo root"
os.chdir(_ROOT)
CLI = "plugins/loopkit/scripts/triage_state.py"
d = pathlib.Path(tempfile.mkdtemp())

cases = {
    "five columns (must parse)": """# Triage

| finding | source | priority | spec | status |
| --- | --- | --- | --- | --- |
| a thing | src/one | high |  | new |
| another | src/two | low |  | blocked |
""",
    "four columns (also accepted)": """# Triage

| finding | source | priority | status |
| --- | --- | --- | --- |
| a thing | src/one | high | new |
""",
    "six columns (must REFUSE, not report empty)": """# Triage

| finding | source | priority | spec | status | waits_on |
| --- | --- | --- | --- | --- | --- |
| a thing | src/one | high |  | new | |
| another | src/two | low |  | blocked | row:src/one=done |
""",
    "renamed column (must REFUSE)": """# Triage

| finding | origin | priority | spec | status |
| --- | --- | --- | --- | --- |
| a thing | src/one | high |  | new |
""",
    "genuinely empty (must parse as empty)": "# Triage\n\nNothing yet.\n",
    "header only, no rows (must parse as empty)": """# Triage

| finding | source | priority | spec | status |
| --- | --- | --- | --- | --- |
""",
}

fails = 0
all_refused = True
for label, body in cases.items():
    f = d / (label.split()[0] + str(abs(hash(label))) + ".md")
    f.write_text(body)
    r = subprocess.run([sys.executable, CLI, "list", "--state", str(f), "--format", "json"],
                       capture_output=True, text=True)
    try:
        n = len(json.loads(r.stdout or "[]"))
    except Exception:
        n = None
    must_refuse = "REFUSE" in label
    refused = r.returncode != 0
    ok = refused if must_refuse else (not refused)
    if not ok:
        fails += 1
    if not refused:
        all_refused = False
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark} {label:46} rows={n} exit={r.returncode}")
    if must_refuse and refused:
        first = (r.stderr.strip().splitlines() or [""])[-1]
        print(f"       said: {first[:110]}")

if fails == 0 and all_refused:
    print("HARNESS BROKEN: every case exited non-zero, including the ones that "
          "should parse — the two 'ok' lines above are a universal failure "
          "satisfying a check that expected failure, not evidence")
    raise SystemExit(1)
print("SILENT-ZERO PINS PASS" if fails == 0 else f"{fails} FAILURE(S)")
raise SystemExit(1 if fails else 0)
