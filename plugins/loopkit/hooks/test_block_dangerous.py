#!/usr/bin/env python3
"""Exercise block_dangerous.py against cases held as data, not as shell literals.

The cases must live in a file: putting them on a command line means the
session's own PreToolUse hook inspects them and refuses to run the test — the
guard blocks its own test harness. Same class as the guard blocking the commit
that described it.

usage: python3 hooks/test_block_dangerous.py [path-to-hook]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("block_dangerous.py"))

MUST_ALLOW = [
    ("named add then commit -F", "git" " add foo.txt && git commit -F msg.txt"),
    ("chained, -F flag", "git" " add a.md b.md && git commit -q -F /tmp/m.txt"),
    ("dotted path", "git" " add .claude/hooks/x.py"),
    ("./ prefix", "git" " add ./scripts/x.sh"),
    (".gitignore by name", "git" " add .gitignore"),
    ("read-only PR view", "gh" " pr view 2404 --json state"),
    ("prose mentioning it", 'echo "do not use git add -A"'),
    ("prose mentioning merge", 'echo "the loop never runs gh pr merge"'),
]
MUST_BLOCK = [
    ("real force-add", "git" " add -f secret.env"),
    ("--force", "git" " add --force x"),
    ("add-all -A", "git" " add -A"),
    ("add-all --all", "git" " add --all"),
    ("add-all dot", "git" " add ."),
    ("add-all dot-slash", "git" " add ./"),
    ("add-all dot then semicolon", "git" " add .;"),
    ("add-all dot-slash then chain", "git" " add ./ && echo done"),
    ("add-all dot then pipe", "git" " add .| cat"),
    ("pr merge", "gh" " pr merge 2404"),
    ("self-approve", "gh" " pr review 2404 --approve"),
    ("api merge", "gh" " api -X PUT repos/o/r/pulls/12/merge"),
    ("force push", "git" " push --force origin main"),
    ("hard reset", "git" " reset --hard HEAD~3"),
    ("rm -rf", "rm" " -rf build/"),
    ("tool cache add", "git" " add .mempalace/"),
]


def rc(cmd: str, env: dict | None = None) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    p = subprocess.run(
        [sys.executable, HOOK], input=payload, capture_output=True, text=True, env=env
    )
    return p.returncode


fails = 0
base_env = dict(os.environ)
base_env.pop("CLAUDE_PROJECT_DIR", None)

# An empty project root: no .loopkit/, so only the built-ins apply.
with tempfile.TemporaryDirectory() as empty_root:
    env = dict(base_env, CLAUDE_PROJECT_DIR=empty_root)

    print("MUST ALLOW (rc must be 0):")
    for name, cmd in MUST_ALLOW:
        got = rc(cmd, env)
        ok = got == 0
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {name}")

    print("MUST BLOCK (rc must be 2):")
    for name, cmd in MUST_BLOCK:
        got = rc(cmd, env)
        ok = got == 2
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {name}")

    print("MUST NEVER CRASH (rc must be 0 on junk input):")
    for name, raw in [("malformed json", "{not json"), ("empty stdin", ""), ("no tool_input", '{"tool_name":"Bash"}')]:
        p = subprocess.run([sys.executable, HOOK], input=raw, capture_output=True, text=True, env=env)
        ok = p.returncode == 0
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={p.returncode}  {name}")

# A configured project root: extras block, disabled built-ins allow.
with tempfile.TemporaryDirectory() as cfg_root:
    lk = Path(cfg_root) / ".loopkit"
    lk.mkdir()
    (lk / "block-patterns.txt").write_text("# project extras\n\\bterraform\\s+destroy\\b\n(unbalanced\n")
    (lk / "block-disabled.txt").write_text("git-rebase\n")
    env = dict(base_env, CLAUDE_PROJECT_DIR=cfg_root)

    print("PROJECT CONFIG (.loopkit/):")
    cases = [
        ("extra pattern blocks", "terraform" " destroy -auto-approve", 2),
        ("broken extra pattern is skipped, gate still works", "git" " add -A", 2),
        ("disabled built-in allows", "git" " rebase main", 0),
        ("other built-ins still block", "git" " reset --hard", 2),
    ]
    for name, cmd, want in cases:
        got = rc(cmd, env)
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got} (want {want})  {name}")

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
