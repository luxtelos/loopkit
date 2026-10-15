#!/usr/bin/env python3
"""block_dangerous.py on MCP tools that run shell commands.

An MCP tool can run a shell line just as Bash does: the desktop terminal's
`run_in_terminal {command}`, desktop-commander's `start_process {command}` and
`interact_with_process {input}`, any server's `execute_command`. Until this
file, block_dangerous.py was wired to the `Bash` matcher only, so
`gh pr merge 1` typed through such a tool met no LoopKit hook at all
(independent review FAIL of PR #61, 2026-10-08).

Three halves, each of which must be able to fail on its own:

  1. EXTRACTION. Fed an MCP payload, the hook finds the command and refuses it
     exactly as it would on Bash: a shell-named tool has EVERY string checked;
     any other MCP tool has its command-like keys checked (command, cmd,
     script, shell_command, commandLine, an argv list), so an unknown tool that
     carries a command is checked, not skipped.
  2. NO FALSE REFUSALS. Ordinary terminal lines, and prose about merging in a
     non-shell tool's body, still pass.
  3. WIRING. hooks.json is read the way Claude Code reads it: every PreToolUse
     entry whose matcher matches the tool name runs, with the payload on stdin.
     A terminal-tool merge must come out blocked. This half fails if the
     matcher goes back to `Bash` alone, whatever the hook's own code says.

Cases live in this file, never on a command line: the session's own
block_dangerous hook reads Bash command text and would refuse the harness.

usage: python3 hooks/test_block_dangerous_mcp.py [path-to-block_dangerous.py]
       LOOPKIT_HOOKS_JSON=<path> overrides the hooks.json the wiring half reads.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = sys.argv[1] if len(sys.argv) > 1 else str(HERE / "block_dangerous.py")
HOOKS_JSON = Path(os.environ.get("LOOPKIT_HOOKS_JSON") or (Path(HOOK).resolve().parent / "hooks.json"))

UUID = "mcp__1a59c906-04da-521d-bda7-7f71b9f9e01c__"
_GHO = "gho_" + "V" * 36  # synthetic, assembled: never a real value in a tracked file

# Assembled so this file's own text never holds a command-position literal.
MERGE = "gh" " pr merge 1 --squash"
APPROVE = "gh" " pr review 1 --approve"
API_MERGE = "gh" " api -X PUT repos/o/r/pulls/1/merge"

# (label, tool_name, tool_input, rule the refusal must name)
MUST_BLOCK = [
    ("desktop terminal run_in_terminal: merge", "mcp__terminal__run_in_terminal",
     {"command": MERGE, "title": "t"}, "gh-pr-merge"),
    ("desktop terminal, chained after cd", "mcp__terminal__run_in_terminal",
     {"command": "cd repo && " + MERGE}, "gh-pr-merge"),
    ("desktop-commander start_process: approve", "mcp__desktop-commander__start_process",
     {"command": APPROVE, "timeout_ms": 5000}, "gh-pr-approve"),
    ("interact_with_process sends the line as `input`", "mcp__desktop-commander__interact_with_process",
     {"pid": 4242, "input": MERGE}, "gh-pr-merge"),
    ("execute_command on a uuid server", UUID + "execute_command", {"command": API_MERGE}, "gh-api-merge"),
    ("camelCase runShellCommand", "mcp__forge__runShellCommand", {"commandLine": MERGE}, "gh-pr-merge"),
    ("unknown tool with a `cmd` key", "mcp__forge__do_thing", {"cmd": MERGE}, "gh-pr-merge"),
    ("unknown tool with a `script` key", "mcp__forge__job_create", {"name": "x", "script": MERGE}, "gh-pr-merge"),
    ("unknown tool, command nested one level down", "mcp__forge__job_create",
     {"step": {"shell_command": MERGE}}, "gh-pr-merge"),
    ("argv form: command + args list", "mcp__forge__spawn",
     {"command": "gh", "args": ["pr", "merge", "1"]}, "gh-pr-merge"),
    ("a list of commands, the bad one second", "mcp__forge__batch",
     {"commands": ["ls", MERGE]}, "gh-pr-merge"),
    ("the rm -rf floor through a terminal tool", "mcp__terminal__run_in_terminal",
     {"command": "rm -rf build"}, "rm-recursive-force"),
    ("force push through a terminal tool", "mcp__terminal__run_in_terminal",
     {"command": "git push --force origin main"}, "git-force-push"),
    ("a secret literal through a terminal tool", "mcp__terminal__run_in_terminal",
     {"command": f"GH_TOKEN={_GHO} gh pr list"}, "secret-on-command-line"),
]

MUST_ALLOW = [
    ("terminal: npm test", "mcp__terminal__run_in_terminal", {"command": "npm test", "title": "tests"}),
    ("terminal: read-only gh pr view", "mcp__terminal__run_in_terminal", {"command": "gh pr view 1 --json state"}),
    ("terminal: prose that mentions merge", "mcp__terminal__run_in_terminal",
     {"command": 'echo "the loop never runs gh pr merge"'}),
    ("open_terminal_tab, no command at all", "mcp__terminal__open_terminal_tab", {"cwd": "."}),
    ("start_process: a dev server", "mcp__desktop-commander__start_process", {"command": "npm run dev"}),
    ("argv form, harmless", "mcp__forge__spawn", {"command": "gh", "args": ["pr", "view", "1"]}),
    ("non-shell tool, merge prose in a body", "mcp__github__add_issue_comment",
     {"owner": "o", "repo": "r", "issue_number": 1, "body": MERGE + " is for a human"}),
    ("non-shell tool, rm -rf prose in a query", "mcp__mempalace__mempalace_search", {"query": "rm -rf build"}),
    ("non-shell tool, a `title` that reads like a command", "mcp__github__create_pull_request",
     {"owner": "o", "repo": "r", "title": "docs: why " + MERGE + " is refused", "head": "a", "base": "b"}),
    ("Bash still reads `command` as before", "Bash", {"command": "ls -la"}),
]


def run(payload: dict, env: dict, hook: str = HOOK) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, hook], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


fails = 0


def check(ok: bool, line: str) -> None:
    global fails
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  {line}")


if not Path(HOOK).is_file():
    print(f"FAIL  hook not found: {HOOK}")
    sys.exit(1)

base_env = dict(os.environ)
base_env.pop("CLAUDE_PROJECT_DIR", None)

with tempfile.TemporaryDirectory() as root:
    env = dict(base_env, CLAUDE_PROJECT_DIR=root)

    print("EXTRACTION: block_dangerous.py refuses the command an MCP tool would run (rc must be 2):")
    for label, tool, ti, rule in MUST_BLOCK:
        p = run({"tool_name": tool, "tool_input": ti}, env)
        ok = p.returncode == 2 and f"BLOCKED [{rule}]" in p.stderr
        if rule == "secret-on-command-line":
            ok = ok and _GHO not in p.stderr  # report the shape, never the value
        check(ok, f"rc={p.returncode}  {label}")

    print("NO FALSE REFUSALS (rc must be 0):")
    for label, tool, ti in MUST_ALLOW:
        p = run({"tool_name": tool, "tool_input": ti}, env)
        check(p.returncode == 0, f"rc={p.returncode}  {label}" + (f"  [{p.stderr.splitlines()[0]}]" if p.stderr else ""))

    print("JUNK INPUT on an MCP shell tool never crashes the hook into a block (rc must be 0):")
    for label, raw in [
        ("tool_input is a string", json.dumps({"tool_name": "mcp__terminal__run_in_terminal", "tool_input": "x"})),
        ("command is a number", json.dumps({"tool_name": "mcp__terminal__run_in_terminal", "tool_input": {"command": 7}})),
        ("deeply nested junk", json.dumps({"tool_name": "mcp__forge__x", "tool_input": {"a": [[[[[[{"b": None}]]]]]]}})),
    ]:
        p = subprocess.run([sys.executable, HOOK], input=raw, capture_output=True, text=True, env=env)
        check(p.returncode == 0, f"rc={p.returncode}  {label}")


# ---- WIRING: dispatch real payloads through hooks.json the way Claude Code does ----

def _matches(matcher: str | None, tool: str) -> bool:
    # A missing matcher or `*` matches every tool. Otherwise the matcher is a
    # regex over the tool name. fullmatch is the STRICTER reading: a hook that
    # fires under fullmatch fires under search too, so a pass here holds
    # whichever way the harness anchors it.
    if not matcher or matcher == "*":
        return True
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return False


def guards_for(tool: str) -> list[str]:
    """The merge/approve guard commands hooks.json would run for this tool."""
    hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    out = []
    for entry in hooks:
        if not _matches(entry.get("matcher"), tool):
            continue
        for h in entry.get("hooks", []):
            cmd = h.get("command", "")
            if "block_dangerous.py" in cmd or "block_mcp_merge_approve.py" in cmd:
                out.append(cmd)
    return out


def dispatch(tool: str, tool_input: dict, env: dict) -> tuple[int, list[str]]:
    """Run every guard hooks.json wires for this tool, payload on stdin.
    Returns (2 if any guard blocked else 0, the hook files that ran)."""
    plugin_root = str(HOOKS_JSON.resolve().parent.parent)
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input})
    blocked, ran = 0, []
    for cmd in guards_for(tool):
        line = cmd.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root)
        ran.append(re.search(r"hooks/(\w+\.py)", line).group(1))
        p = subprocess.run(line, shell=True, input=payload, capture_output=True, text=True, env=env)
        if p.returncode == 2:
            blocked = 2
    return blocked, ran


with tempfile.TemporaryDirectory() as root:
    env = dict(base_env, CLAUDE_PROJECT_DIR=root)
    print(f"WIRING ({HOOKS_JSON.name}): what Claude Code would run, fed the payload on stdin:")
    try:
        ran_terminal = guards_for("mcp__terminal__run_in_terminal")
    except Exception as exc:  # unreadable hooks.json is a failure, not a skip
        ran_terminal = []
        check(False, f"hooks.json unreadable: {type(exc).__name__}")
    check(any("block_dangerous.py" in c for c in ran_terminal),
          "block_dangerous.py is wired for mcp__terminal__run_in_terminal")
    check(any("block_dangerous.py" in c for c in guards_for(UUID + "execute_command")),
          "block_dangerous.py is wired for a uuid server's tool")
    check(any("block_dangerous.py" in c for c in guards_for("Bash")), "block_dangerous.py is still wired for Bash")
    for label, tool, ti, want in [
        ("terminal merge is refused end to end", "mcp__terminal__run_in_terminal", {"command": MERGE}, 2),
        ("desktop-commander approve is refused end to end", "mcp__desktop-commander__start_process",
         {"command": APPROVE}, 2),
        ("unknown tool with a cmd key is refused end to end", "mcp__forge__do_thing", {"cmd": MERGE}, 2),
        ("the github merge tool is refused end to end", "mcp__github__merge_pull_request",
         {"owner": "o", "repo": "r", "pullNumber": 1}, 2),
        ("Bash merge is still refused end to end", "Bash", {"command": MERGE}, 2),
        ("terminal npm test runs end to end", "mcp__terminal__run_in_terminal", {"command": "npm test"}, 0),
        ("a github read runs end to end", "mcp__github__pull_request_read",
         {"owner": "o", "repo": "r", "pullNumber": 1, "method": "get"}, 0),
    ]:
        got, ran = dispatch(tool, ti, env)
        check(got == want, f"rc={got} want={want}  {label}  (ran: {', '.join(ran) or 'nothing'})")

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
