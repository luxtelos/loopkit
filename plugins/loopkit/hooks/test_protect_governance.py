#!/usr/bin/env python3
"""Prove protect_governance.py guards by absolute path, across worktrees.

The case that matters: the SAME file reached through a different worktree
path. That is what a settings.json deny rule misses.

usage: python3 hooks/test_protect_governance.py [path-to-hook]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("protect_governance.py"))

MUST_BLOCK = [
    ("constitution in this worktree", "Edit", "/repo/app/constitution.md"),
    ("constitution via ANOTHER worktree", "Edit",
     "/repo/app/.claude/worktrees/other/constitution.md"),
    ("constitution via a temp clone", "Edit", "/tmp/wt-x/constitution.md"),
    ("a spec file", "Edit", "/repo/app/specs/billing-lifecycle.md"),
    ("a spec via another worktree", "Write",
     "/repo/app/.claude/worktrees/other/specs/foo.md"),
    ("relative path to constitution", "Edit", "constitution.md"),
    ("notebook edit on a spec", "NotebookEdit", "/repo/app/specs/model.ipynb"),
]
MUST_ALLOW = [
    ("an ordinary source file", "Edit", "/repo/app/lib/db/repos/usage.ts"),
    ("a state file", "Write", "/repo/app/state/2026-01-01-notes.md"),
    ("the inbox", "Edit", "/repo/app/inbox/needs-human.md"),
    ("a doc that merely mentions specs", "Edit", "/repo/app/docs/specs-guide.md"),
    ("a non-edit tool", "Bash", "/repo/app/constitution.md"),
]


def rc(tool: str, path: str, env_ok: bool = False, root: str | None = None) -> int:
    env = dict(os.environ)
    env.pop("GOVERNANCE_EDIT_OK", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if env_ok:
        env["GOVERNANCE_EDIT_OK"] = "1"
    if root:
        env["CLAUDE_PROJECT_DIR"] = root
    payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": path}})
    p = subprocess.run(
        [sys.executable, HOOK], input=payload, capture_output=True, text=True, env=env
    )
    return p.returncode


fails = 0
with tempfile.TemporaryDirectory() as empty_root:
    print("MUST BLOCK (rc must be 2):")
    for name, tool, path in MUST_BLOCK:
        got = rc(tool, path, root=empty_root)
        ok = got == 2
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {name}")

    print("MUST ALLOW (rc must be 0):")
    for name, tool, path in MUST_ALLOW:
        got = rc(tool, path, root=empty_root)
        ok = got == 0
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {name}")

    print("OVERRIDE (ratified edit, rc must be 0):")
    got = rc("Edit", "/repo/app/constitution.md", env_ok=True, root=empty_root)
    ok = got == 0
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  GOVERNANCE_EDIT_OK=1")

    print("JUNK INPUT (rc must be 0):")
    p = subprocess.run([sys.executable, HOOK], input="{nope", capture_output=True, text=True)
    ok = p.returncode == 0
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  rc={p.returncode}  malformed json")

with tempfile.TemporaryDirectory() as cfg_root:
    lk = Path(cfg_root) / ".loopkit"
    lk.mkdir()
    (lk / "protected.txt").write_text("# project extras\n(?:^|/)docs/adr/\n")
    print("PROJECT CONFIG (.loopkit/protected.txt):")
    for name, tool, path, want in [
        ("an ADR is protected", "Edit", "/repo/app/docs/adr/ADR-001.md", 2),
        ("other docs still open", "Edit", "/repo/app/docs/guide.md", 0),
    ]:
        got = rc(tool, path, root=cfg_root)
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got} (want {want})  {name}")

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
