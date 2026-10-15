#!/usr/bin/env python3
"""Mutation harness for the two MCP guards: each pin must be able to fail.

A test that passes against the fix proves nothing until it is shown to FAIL
against the code it guards with one piece taken out. This file copies the
plugin's hooks into a scratch tree, applies ONE textual mutation at a time to
block_dangerous.py, block_mcp_merge_approve.py or hooks.json, and runs the
pin file for that half against the mutant. Every mutant must be caught
(the pin exits non-zero); the unmutated copy must pass (the control).

A mutation whose text is no longer in the source is itself a failure, so the
harness cannot rot into "0 mutants applied, 0 survived".

usage: python3 hooks/test_mcp_guard_mutants.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
PLUGIN = HOOKS.parent
SHELL_PIN = "test_block_dangerous_mcp.py"
NAME_PIN = "test_block_mcp_merge_approve.py"

# (label, file to mutate, old text, new text, pin that must catch it)
MUTANTS = [
    # ---- shell half: block_dangerous.py on MCP tools that run commands
    ("wiring: block_dangerous back on Bash only", "hooks.json",
     '"matcher": "Bash|mcp__.*"', '"matcher": "Bash"', SHELL_PIN),
    ("shell-named tools no longer have every string read", "block_dangerous.py",
     "every = is_shell_tool(tool_name)", "every = False", SHELL_PIN),
    ("command-like keys narrowed to `command` alone", "block_dangerous.py",
     "return bool(_tokens(key) & COMMAND_KEY_TOKENS)", 'return key == "command"', SHELL_PIN),
    ("argv lists no longer joined into one line", "block_dangerous.py",
     'add(v + " " + _join(av))', "pass", SHELL_PIN),
    ("only the first extracted line is checked", "block_dangerous.py",
     "for one in cmds:", "for one in cmds[:1]:", SHELL_PIN),
    # ---- name/parameter half: block_mcp_merge_approve.py
    ("merge word only when it is the exact old name", "block_mcp_merge_approve.py",
     'if any(t.startswith("merg") for t in ts):', "if ts == ['merge', PR]:", NAME_PIN),
    ("approve word in a name no longer refused", "block_mcp_merge_approve.py",
     'if any(t.startswith("approv") for t in ts):', "if False:", NAME_PIN),
    ("list elements no longer walked", "block_mcp_merge_approve.py",
     "                yield key, v", "                pass", NAME_PIN),
    ("vote no longer read", "block_mcp_merge_approve.py",
     'if "vote" in kt:', "if False:", NAME_PIN),
    ("status completed/merged no longer read", "block_mcp_merge_approve.py",
     "if k in STATUS_KEYS and val in MERGED_VALUES:", "if False:", NAME_PIN),
    ("auto-merge/auto-complete keys no longer read", "block_mcp_merge_approve.py",
     "if _is_auto_key(k):", "if False:", NAME_PIN),
    ("parameters never read", "block_mcp_merge_approve.py",
     "    if ctx:\n", "    if False:\n", NAME_PIN),
    ("unknown PR action allowed", "block_mcp_merge_approve.py",
     "return RULE_UNKNOWN", "return None", NAME_PIN),
    # The ALLOW half must catch too: without the read exemption a read that
    # filters on APPROVED would be refused, and the pin must say so.
    ("read exemption removed (false refusals)", "block_mcp_merge_approve.py",
     "        return None\n\n    negated = ", "        pass\n\n    negated = ", NAME_PIN),
    ("toggle OFF no longer honoured (false refusal on disable)", "block_mcp_merge_approve.py",
     "return bool(present) and not any(_truthy(v) for v in present)", "return False", NAME_PIN),
]


def build_tree(root: Path) -> Path:
    """A scratch plugin: hooks copied, loopkit_core linked. Returns its hooks dir."""
    plug = root / "plugins" / "loopkit"
    shutil.copytree(HOOKS, plug / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
    os.symlink(PLUGIN / "loopkit_core", plug / "loopkit_core")
    return plug / "hooks"


def run_pin(hooks: Path, pin: str) -> int:
    target = "block_dangerous.py" if pin == SHELL_PIN else "block_mcp_merge_approve.py"
    env = dict(os.environ, LOOPKIT_HOOKS_JSON=str(hooks / "hooks.json"))
    env.pop("CLAUDE_PROJECT_DIR", None)
    p = subprocess.run([sys.executable, str(hooks / pin), str(hooks / target)],
                       capture_output=True, text=True, env=env)
    return p.returncode


fails = 0


def check(ok: bool, line: str) -> None:
    global fails
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  {line}", flush=True)


print("CONTROL (unmutated copy, every pin must pass):")
with tempfile.TemporaryDirectory() as d:
    hooks = build_tree(Path(d))
    for pin in (SHELL_PIN, NAME_PIN):
        rc = run_pin(hooks, pin)
        check(rc == 0, f"rc={rc}  {pin}")

print("MUTANTS (each must be caught: pin rc != 0):")
for label, fname, old, new, pin in MUTANTS:
    with tempfile.TemporaryDirectory() as d:
        hooks = build_tree(Path(d))
        f = hooks / fname
        src = f.read_text(encoding="utf-8")
        n = src.count(old)
        if n != 1:
            check(False, f"mutation text found {n} times (want 1), cannot apply: {label}")
            continue
        f.write_text(src.replace(old, new), encoding="utf-8")
        rc = run_pin(hooks, pin)
        check(rc != 0, f"{'caught' if rc else 'SURVIVED'} rc={rc}  {label}  [{pin}]")

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
