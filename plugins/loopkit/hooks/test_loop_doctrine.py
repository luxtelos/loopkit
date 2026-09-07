#!/usr/bin/env python3
"""Cases for loop_doctrine.py.

They live in a Python file rather than shell arguments for the same reason the
block_dangerous cases do: passing them on a command line makes the session's
own hooks inspect them, and a test harness that trips the thing it is testing
is not a test harness.

usage: python3 hooks/test_loop_doctrine.py [path-to-hook]
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("loop_doctrine.py"))

MUST_INJECT = [
    ("bare /loop", "/loop"),
    ("the short form", "/loop work the loopkit backlog, one stage per tick, then stop"),
    ("the long form", "/loop Run the loop-tick skill. First loop-watch.sh --pr 2404"),
    ("names the skill", "please run the loop-tick skill now"),
    ("backlog phrasing, no slash", "work the backlog for me"),
    ("pipeline phrasing", "advance the pipeline one stage"),
    ("triage phrasing", "process the triage findings"),
    ("plain ask", "run the loop"),
    ("leading whitespace", "   /loop coordinate on the billing epic"),
    # The plugin's own canonical skill invocations. `^\s*/loop\b` cannot match
    # these — `\b` fails between `p` and `k` — so the doctrine was silent on the
    # exact prompt the skill list advertises. Found by the 2026-09-07 review.
    ("the plugin's own tick skill", "/loopkit:tick"),
    ("the plugin's own scan skill", "/loopkit:scan"),
]

MUST_STAY_QUIET = [
    ("a for loop", "the for loop in parser.ts is off by one"),
    ("a retry loop", "the retry loop never terminates on 429"),
    ("an event loop", "the event loop is blocked during import"),
    ("loop counter", "rename the loop variable to idx"),
    ("stopping the loop", "/loop stop"),
    ("unrelated work", "fix the failing test in tests/unit/token-refresh.test.ts"),
    ("empty", ""),
]


def run(prompt: str) -> str:
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
    p = subprocess.run(
        [sys.executable, HOOK], input=payload, capture_output=True, text=True
    )
    if p.returncode != 0:
        return f"__NONZERO_EXIT_{p.returncode}__"
    return p.stdout


fails = 0

print("MUST INJECT the doctrine:")
for name, prompt in MUST_INJECT:
    out = run(prompt)
    ok = "LOOP-KIT TICK DISCIPLINE" in out and "loop-next.sh" in out
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    fails += 0 if ok else 1

print("\nMUST STAY QUIET (a hook that fires on everything gets switched off):")
for name, prompt in MUST_STAY_QUIET:
    out = run(prompt)
    ok = "LOOP-KIT TICK DISCIPLINE" not in out
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    fails += 0 if ok else 1

print("\nMUST NEVER BLOCK (exit 0 on anything, including junk):")
for name, raw in [("malformed json", "{not json"), ("empty stdin", "")]:
    p = subprocess.run([sys.executable, HOOK], input=raw, capture_output=True, text=True)
    ok = p.returncode == 0
    print(f"  {'ok  ' if ok else 'FAIL'} {name} (exit {p.returncode})")
    fails += 0 if ok else 1

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)
