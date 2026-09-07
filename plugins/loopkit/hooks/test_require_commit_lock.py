#!/usr/bin/env python3
"""Exercise require_commit_lock.py.

Cases are held as data and fed to the hook on stdin, never put on a command
line: the session's own PreToolUse hooks inspect command lines, and a guard
that blocks its own test harness is the same class of bug as a guard that
blocks the commit describing it.

usage: python3 hooks/test_require_commit_lock.py [path-to-hook]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("require_commit_lock.py"))

# Split literals so this file's own content never reads as a runnable command.
GIT = "git"
COMMIT = " commit"

MUST_BLOCK = [
    ("bare commit", f"{GIT}{COMMIT} -m 'x'"),
    ("add then commit", f"{GIT} add a.md && {GIT}{COMMIT} -m 'x'"),
    ("pathspec form is still two calls on a shared index",
     f"{GIT} add a.md && {GIT}{COMMIT} -m 'x' -- a.md"),
    ("after a cd", f"cd /tmp/x; {GIT}{COMMIT} -m 'x'"),
    ("with -C", f"{GIT} -C /tmp/x{COMMIT} -m 'x'"),
    ("with -c config", f"{GIT} -c user.name=t{COMMIT} -m 'x'"),
    ("newline separated", f"echo hi\n{GIT}{COMMIT} -F m.txt"),
    ("inside a subshell", f"({GIT}{COMMIT} -m 'x')"),
]

MUST_ALLOW = [
    ("through the wrapper", 'bash "$P/scripts/loop-commit.sh" -m "x" -- a.md'),
    ("through the lock directly",
     f'python3 "$P/scripts/driver_lock.py" run -- {GIT}{COMMIT} -m "x"'),
    ("staging alone is harmless", f"{GIT} add a.md b.md"),
    ("reads pass", f"{GIT} log --oneline -5"),
    ("status passes", f"{GIT} status --short"),
    ("show passes", f"{GIT} show --name-only HEAD"),
    # The guard must not block writing ABOUT the guard.
    ("prose mentioning it", f'echo "never run a bare {GIT}{COMMIT} here"'),
    ("a path that merely contains the word",
     f"{GIT} add docs/commit-policy.md"),
    ("inline door", f"LOOPKIT_COMMIT_UNLOCKED=1 {GIT}{COMMIT} -m 'x'"),
    ("inline door after another assignment",
     f"FOO=1 LOOPKIT_COMMIT_UNLOCKED=1 {GIT}{COMMIT} -m 'x'"),
]


def rc(command: str, env: dict) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    p = subprocess.run(
        [sys.executable, HOOK], input=payload, text=True,
        capture_output=True, env=env,
    )
    return p.returncode


fails = 0
base_env = {k: v for k, v in os.environ.items() if k != "LOOPKIT_COMMIT_UNLOCKED"}

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "proj"
    (root / ".loopkit").mkdir(parents=True)
    env = dict(base_env, CLAUDE_PROJECT_DIR=str(root))

    print("MUST BLOCK (exit 2):")
    for name, cmd in MUST_BLOCK:
        got = rc(cmd, env)
        ok = got == 2
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {name}")

    print("MUST ALLOW (exit 0):")
    for name, cmd in MUST_ALLOW:
        got = rc(cmd, env)
        ok = got == 0
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {name}")

    print("DOORS AND SCOPE:")
    checks = [
        ("env door opens it",
         rc(f"{GIT}{COMMIT} -m 'x'", dict(env, LOOPKIT_COMMIT_UNLOCKED="1")), 0),
    ]
    # A project that names the pattern in block-disabled.txt has decided.
    (root / ".loopkit" / "block-disabled.txt").write_text("commit-without-lock\n")
    checks.append(("block-disabled.txt switches it off", rc(f"{GIT}{COMMIT} -m 'x'", env), 0))
    (root / ".loopkit" / "block-disabled.txt").write_text("git-rebase\n")
    checks.append(("an unrelated disabled name leaves it on", rc(f"{GIT}{COMMIT} -m 'x'", env), 2))

    # No .loopkit/ means this is not a LoopKit project: stay entirely out of it.
    bare = Path(tmp) / "bare"
    bare.mkdir()
    checks.append(("passive in a non-LoopKit repo",
                   rc(f"{GIT}{COMMIT} -m 'x'", dict(base_env, CLAUDE_PROJECT_DIR=str(bare))), 0))

    for name, got, want in checks:
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got} (want {want})  {name}")

    print("FAIL-OPEN:")
    p = subprocess.run([sys.executable, HOOK], input="{nope", text=True,
                       capture_output=True, env=env)
    ok = p.returncode == 0
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  rc={p.returncode}  junk stdin allows")

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
