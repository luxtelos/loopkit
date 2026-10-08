#!/usr/bin/env python3
"""Exercise block_mcp_merge_approve.py against cases held as data.

The cases live in this file, never on a command line: the session's own
block_dangerous hook reads Bash command text, and a fixture that says "merge"
or "approve" on a command line would get the test harness refused. Same lesson
as test_block_dangerous.py.

usage: python3 hooks/test_block_mcp_merge_approve.py [path-to-hook]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("block_mcp_merge_approve.py"))

UUID = "mcp__1a59c906-04da-521d-bda7-7f71b9f9e01c__"
PR = {"owner": "o", "repo": "r", "pullNumber": 12}

# (label, tool_name, tool_input)
MUST_BLOCK = [
    ("github merge tool", "mcp__github__merge_pull_request", dict(PR, merge_method="squash")),
    ("uuid-server merge tool", UUID + "merge_pull_request", dict(PR)),
    ("gitlab merge_merge_request", "mcp__gitlab__merge_merge_request", {"project_id": "1", "merge_request_iid": 3}),
    ("gitlab accept_merge_request (the API's word for merge)", "mcp__gitlab__accept_merge_request", {"merge_request_iid": 3}),
    ("a PR-context *_merge name", "mcp__forge__pull_request_merge", dict(PR)),
    ("camelCase mergePullRequest", "mcp__forge__mergePullRequest", dict(PR)),
    ("review_write create APPROVE", "mcp__github__pull_request_review_write",
     dict(PR, method="create", event="APPROVE", body="lgtm")),
    ("review_write submit_pending APPROVE", "mcp__github__pull_request_review_write",
     dict(PR, method="submit_pending", event="APPROVE")),
    ("lowercase approve event", "mcp__github__pull_request_review_write",
     dict(PR, method="create", event="approve")),
    ("submit_pending_pull_request_review APPROVE", "mcp__github__submit_pending_pull_request_review",
     dict(PR, event="APPROVE")),
    ("create_pull_request_review on a uuid server, state APPROVED", UUID + "create_pull_request_review",
     dict(PR, state="APPROVED")),
    ("gitlab approve_merge_request", "mcp__gitlab__approve_merge_request", {"merge_request_iid": 3}),
    ("desktop auto-merge enable", "mcp__ccd_pr__set_auto_merge",
     {"enabled": True, "url": "https://github.com/o/r/pull/12"}),
    ("auto-merge enable as a string", "mcp__ccd_pr__set_auto_merge", {"enabled": "true", "url": "u"}),
    ("auto-merge with no on/off field (cannot prove it is off)", "mcp__ccd_pr__set_auto_merge", {"url": "u"}),
    ("enable_auto_merge", "mcp__github__enable_auto_merge", dict(PR)),
    ("enable_pull_request_auto_merge", "mcp__github__enable_pull_request_auto_merge", dict(PR)),
]

MUST_ALLOW = [
    ("review_write COMMENT", "mcp__github__pull_request_review_write",
     dict(PR, method="create", event="COMMENT", body="FAIL: see findings")),
    ("review_write REQUEST_CHANGES", "mcp__github__pull_request_review_write",
     dict(PR, method="submit_pending", event="REQUEST_CHANGES")),
    ("review_write pending (no event)", "mcp__github__pull_request_review_write", dict(PR, method="create")),
    ("review body that mentions the word approve", "mcp__github__pull_request_review_write",
     dict(PR, method="create", event="COMMENT", body="I would approve once the merge test passes")),
    ("add_issue_comment saying merge and approve", "mcp__github__add_issue_comment",
     {"owner": "o", "repo": "r", "issue_number": 12, "body": "ready to merge? a human must approve"}),
    ("update_pull_request_branch (not a merge)", "mcp__github__update_pull_request_branch", dict(PR)),
    ("pull_request_read", "mcp__github__pull_request_read", dict(PR, method="get")),
    ("list_pull_requests", "mcp__github__list_pull_requests", {"owner": "o", "repo": "r"}),
    ("gitlab unapprove_merge_request", "mcp__gitlab__unapprove_merge_request", {"merge_request_iid": 3}),
    ("auto-merge disable (bool)", "mcp__ccd_pr__set_auto_merge", {"enabled": False, "url": "u"}),
    ("auto-merge disable (string)", "mcp__ccd_pr__set_auto_merge", {"enabled": "false", "url": "u"}),
    ("disable_auto_merge", "mcp__github__disable_auto_merge", dict(PR)),
    ("ccd_pr get_status", "mcp__ccd_pr__get_status", {"url": "u"}),
    ("a non-MCP tool name", "merge_pull_request", dict(PR)),
    ("a built-in tool with an APPROVE-looking field", "Bash", {"command": "ls", "event": "APPROVE"}),
    ("an unrelated MCP tool", "mcp__mempalace__mempalace_search", {"query": "merge approve"}),
]


def run(payload_text: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, HOOK], input=payload_text, capture_output=True, text=True, env=env)


def rc(tool: str, tool_input, env: dict) -> int:
    return run(json.dumps({"tool_name": tool, "tool_input": tool_input}), env).returncode


fails = 0
base_env = dict(os.environ)
base_env.pop("CLAUDE_PROJECT_DIR", None)


def check(ok: bool, line: str) -> None:
    global fails
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  {line}")


if not Path(HOOK).is_file():
    print(f"FAIL  hook not found: {HOOK}")
    sys.exit(1)

with tempfile.TemporaryDirectory() as empty_root:
    env = dict(base_env, CLAUDE_PROJECT_DIR=empty_root)

    print("MUST BLOCK (rc must be 2):")
    for label, tool, ti in MUST_BLOCK:
        p = run(json.dumps({"tool_name": tool, "tool_input": ti}), env)
        # The refusal must name a rule, so a false positive can be reported by name.
        ok = p.returncode == 2 and "BLOCKED [mcp-" in p.stderr
        check(ok, f"rc={p.returncode}  {label}")

    print("MUST ALLOW (rc must be 0):")
    for label, tool, ti in MUST_ALLOW:
        got = rc(tool, ti, env)
        check(got == 0, f"rc={got}  {label}")

    print("MUST NEVER CRASH (rc must be 0 on junk input, fail-open on a parse error):")
    for label, raw in [
        ("malformed json", "{not json"),
        ("empty stdin", ""),
        ("json that is not an object", "[1, 2]"),
        ("no tool_name", '{"tool_input": {}}'),
        ("tool_input is a string", '{"tool_name": "mcp__github__pull_request_review_write", "tool_input": "x"}'),
    ]:
        got = run(raw, env).returncode
        check(got == 0, f"rc={got}  {label}")

# Switchable off by rule name, the same reviewed way as block_dangerous.
with tempfile.TemporaryDirectory() as cfg_root:
    lk = Path(cfg_root) / ".loopkit"
    lk.mkdir()
    (lk / "block-disabled.txt").write_text("# a project opted out deliberately\nmcp-approve\n")
    env = dict(base_env, CLAUDE_PROJECT_DIR=cfg_root)
    print("PROJECT CONFIG (.loopkit/block-disabled.txt):")
    for label, tool, ti, want in [
        ("mcp-approve off: APPROVE allowed", "mcp__github__pull_request_review_write",
         dict(PR, method="create", event="APPROVE"), 0),
        ("mcp-approve off: gitlab approve allowed", "mcp__gitlab__approve_merge_request", {}, 0),
        ("mcp-merge still blocks", "mcp__github__merge_pull_request", dict(PR), 2),
        ("mcp-auto-merge still blocks", "mcp__ccd_pr__set_auto_merge", {"enabled": True, "url": "u"}, 2),
    ]:
        got = rc(tool, ti, env)
        check(got == want, f"rc={got} (want {want})  {label}")

# A crash after the payload parsed must not wave a merge through: exit 1 is a
# non-blocking error, so the tool would RUN. Faults are forced by patching `re`
# before the hook loads, as test_block_dangerous.py does.
_BREAK_RE = (
    "import re\n"
    "def _boom(*a, **k):\n"
    "    raise RuntimeError('forced by test')\n"
    "re.search = _boom\n"
    "re.match = _boom\n"
    "re.fullmatch = _boom\n"
    "re.sub = _boom\n"
)


def run_with_fault(tool: str, tool_input, env: dict) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    code = _BREAK_RE + f"\nimport runpy\nrunpy.run_path({HOOK!r}, run_name='__main__')\n"
    return subprocess.run([sys.executable, "-c", code], input=payload, capture_output=True, text=True, env=env)


with tempfile.TemporaryDirectory() as fault_root:
    env = dict(base_env, CLAUDE_PROJECT_DIR=fault_root)
    print("FORCED FAULTS (a crash never fails open on a merge or approve tool):")
    for label, tool, ti, want in [
        ("re raises on a merge tool -> block", "mcp__github__merge_pull_request", dict(PR), 2),
        ("re raises on a review tool -> block", "mcp__github__pull_request_review_write",
         dict(PR, method="create", event="APPROVE"), 2),
        ("re raises on an unrelated tool -> allow (a crash is an allow)", "mcp__github__get_me", {}, 0),
    ]:
        p = run_with_fault(tool, ti, env)
        # A block must come from the crash path, or the fault never fired and
        # this section proves nothing.
        ok = p.returncode == want and (want == 0 or "safety hook failed" in p.stderr)
        check(ok, f"rc={p.returncode} want={want}  {label}")

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
