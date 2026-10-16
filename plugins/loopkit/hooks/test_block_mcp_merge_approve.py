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
    # ---- The fail-open holes the independent review of 8920c72 found. Each
    # is a name or a parameter the old blocklist did not enumerate. The rule is
    # now a shape: merge/approve-shaped names and parameters are refused
    # unless the tool is clearly a read or a negation.
    ("hole 1: merge_pull_request_by_number", "mcp__forge__merge_pull_request_by_number", dict(PR)),
    ("hole 2: a bare `merge`", "mcp__forge__merge", dict(PR)),
    ("hole 2b: a bare `approve`", "mcp__forge__approve", dict(PR)),
    ("hole 3: ADO-style status completed", "mcp__ado__update_pull_request_status",
     {"repositoryId": "r", "pullRequestId": 7, "status": "completed"}),
    ("hole 3b: status Completed, any case", "mcp__ado__repo_update_pull_request",
     {"repositoryId": "r", "pullRequestId": 7, "status": "Completed"}),
    ("hole 3c: state merged", "mcp__github__update_pull_request", dict(PR, state="merged")),
    ("hole 3d: complete_pull_request", "mcp__ado__repo_complete_pull_request", {"pullRequestId": 7}),
    ("hole 4: autoComplete true", "mcp__ado__repo_update_pull_request",
     {"repositoryId": "r", "pullRequestId": 7, "autoComplete": True}),
    ("hole 4b: autoCompleteSetBy an identity", "mcp__ado__repo_update_pull_request",
     {"pullRequestId": 7, "autoCompleteSetBy": {"id": "abc"}}),
    ("hole 4c: gitlab merge_when_pipeline_succeeds", "mcp__gitlab__update_merge_request",
     {"merge_request_iid": 3, "merge_when_pipeline_succeeds": True}),
    ("hole 5: vote 10 (approved)", "mcp__ado__repo_update_pull_request_reviewers",
     {"pullRequestId": 7, "vote": 10}),
    ("hole 5b: vote 5 (approved with suggestions)", "mcp__ado__repo_vote_pull_request",
     {"pullRequestId": 7, "vote": 5}),
    ("hole 5c: vote as a string", "mcp__ado__repo_update_pull_request_reviewers",
     {"pullRequestId": 7, "reviewers": [{"id": "me", "vote": "10"}]}),
    ("hole 6: APPROVE inside a list", "mcp__github__pull_request_review_write",
     dict(PR, method="create", event=["APPROVE"])),
    ("hole 6b: APPROVE in a list of review objects", "mcp__forge__pull_request_reviews_write",
     dict(PR, reviews=[{"body": "ok", "event": "APPROVE"}])),
    ("hole 6c: approved: true", "mcp__forge__update_pull_request", dict(PR, approved=True)),
    ("unknown action on a PR tool (land)", "mcp__forge__land_pull_request", dict(PR)),
    ("unknown action on a PR tool (submit, no review)", "mcp__forge__submit_pull_request", dict(PR)),
    ("unknown action on a PR tool (squash)", "mcp__forge__squash_pull_request", dict(PR)),
    ("a `*_status` PR tool that sets completed", "mcp__forge__pull_request_status", dict(PR, status="completed")),
    ("gitlab state_event merge", "mcp__gitlab__update_merge_request", {"merge_request_iid": 3, "state_event": "merge"}),
    ("auto-merge toggles that disagree are not a clear OFF", "mcp__ccd_pr__set_auto_merge",
     {"enabled": False, "enable": True, "url": "u"}),
    ("verdict nested in an object", "mcp__forge__pull_request_review_write", dict(PR, review={"event": {"type": "APPROVE"}})),
    ("auto_approve: true", "mcp__forge__update_pull_request", dict(PR, autoApprove=True)),
    # ---- Round 2 (review of 5514e4d): a negate or read word ANYWHERE in the
    # name exempted it. "Delete the branch" and "remove the source branch" are
    # standard options on a real merge. A merge/approve/auto-merge word now
    # refuses unless the WHOLE short name is on the pinned SAFE_NAMES list.
    ("negate word elsewhere: merge_pull_request_and_delete_branch",
     "mcp__forge__merge_pull_request_and_delete_branch", dict(PR)),
    ("negate word elsewhere: merge_and_remove_source_branch (gitlab)",
     "mcp__gitlab__merge_and_remove_source_branch", {"project_id": "1", "merge_request_iid": 3}),
    ("negate word elsewhere: merge_and_cancel_review", "mcp__forge__merge_and_cancel_review", dict(PR)),
    ("negate word elsewhere: approve_pr_and_dismiss_stale_reviews",
     "mcp__forge__approve_pr_and_dismiss_stale_reviews", dict(PR)),
    ("negate word elsewhere: enable_auto_merge_and_clear_queue",
     "mcp__forge__enable_auto_merge_and_clear_queue", dict(PR)),
    ("leading read word: check_and_merge_pull_request", "mcp__forge__check_and_merge_pull_request", dict(PR)),
    ("leading read word: get_and_approve_pull_request", "mcp__forge__get_and_approve_pull_request", dict(PR)),
    ("a PR-keyed bare merge on a non-forge server name", "mcp__tools__merge", {"pull_request_id": 7}),
    ("a git server's git_merge (merging branches is the loop merging)", "mcp__git__git_merge",
     {"repo_path": ".", "branch": "fix/x"}),
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
    # ---- No false refusals on what a reviewer does every day. The shape rule
    # above must not cost these: reading, searching, commenting, creating,
    # reviewer requests, closing, and posting a non-APPROVE verdict.
    ("search_issues with review:approved in the query", "mcp__github__search_issues",
     {"query": "repo:o/r is:pr review:approved"}),
    ("search_pull_requests is:merged", "mcp__github__search_pull_requests", {"query": "repo:o/r is:merged"}),
    ("pull_request_read get_diff", "mcp__github__pull_request_read", dict(PR, method="get_diff")),
    ("pull_request_read get_reviews", "mcp__github__pull_request_read", dict(PR, method="get_reviews")),
    ("list_pull_request_reviews filtered to APPROVED (a read)", "mcp__forge__list_pull_request_reviews",
     dict(PR, state="APPROVED")),
    ("get_auto_merge_status (a read)", "mcp__forge__get_auto_merge_status", dict(PR)),
    ("gitlab list_merge_requests state merged (a read)", "mcp__gitlab__list_merge_requests",
     {"project_id": "1", "state": "merged"}),
    ("create_pull_request", "mcp__github__create_pull_request",
     {"owner": "o", "repo": "r", "title": "fix: merge sort", "head": "a", "base": "b", "draft": True}),
    ("gitlab create_merge_request", "mcp__gitlab__create_merge_request",
     {"project_id": "1", "source_branch": "a", "target_branch": "b", "title": "t"}),
    ("update_pull_request reviewers", "mcp__github__update_pull_request", dict(PR, reviewers=["alice"])),
    ("update_pull_request close", "mcp__github__update_pull_request", dict(PR, state="closed")),
    ("ADO abandon via status", "mcp__ado__repo_update_pull_request", {"pullRequestId": 7, "status": "Abandoned"}),
    ("ADO reject vote -10", "mcp__ado__repo_update_pull_request_reviewers", {"pullRequestId": 7, "vote": -10}),
    ("request_copilot_review", "mcp__github__request_copilot_review", dict(PR)),
    ("add_comment_to_pending_review", "mcp__github__add_comment_to_pending_review",
     dict(PR, path="a.py", body="nit", line=3, side="RIGHT", subjectType="LINE")),
    ("add_reply_to_pull_request_comment", "mcp__github__add_reply_to_pull_request_comment",
     dict(PR, commentId=9, body="fixed")),
    ("review_write delete_pending", "mcp__github__pull_request_review_write", dict(PR, method="delete_pending")),
    ("review_write resolve_thread", "mcp__github__pull_request_review_write",
     dict(PR, method="resolve_thread", threadId="T1")),
    ("auto-merge disable with a merge_method", "mcp__ccd_pr__set_auto_merge",
     {"enabled": False, "merge_method": "squash", "url": "u"}),
    ("ccd_pr bind_pr", "mcp__ccd_pr__bind_pr", {"url": "u"}),
    ("ccd_pr unbind_pr", "mcp__ccd_pr__unbind_pr", {"url": "u"}),
    ("ccd_pr set_monitor", "mcp__ccd_pr__set_monitor", {"url": "u", "enabled": True}),
    ("issue_write close as completed (an issue, not a PR)", "mcp__github__issue_write",
     {"owner": "o", "repo": "r", "issue_number": 4, "method": "update", "state": "closed",
      "state_reason": "completed"}),
    ("get_commit", "mcp__github__get_commit", {"owner": "o", "repo": "r", "sha": "abc"}),
    ("uuid server: list_pull_requests", UUID + "list_pull_requests", {"owner": "o", "repo": "r"}),
    ("an unrelated tool with a status field", "mcp__scheduled-tasks__update_scheduled_task",
     {"id": "t", "status": "completed"}),
    # ---- Round 2: merge/approve words OUTSIDE the PR / review / merge-request
    # surface are not merges of code. Refusing them gets the guard switched off.
    ("spreadsheet merge_cells", "mcp__sheets__merge_cells", {"spreadsheetId": "s", "range": "A1:B2"}),
    ("pdf merge_pdfs", "mcp__pdf__merge_pdfs", {"files": ["a.pdf", "b.pdf"], "output": "c.pdf"}),
    ("expenses approve_expense", "mcp__expenses__approve_expense", {"expense_id": "e1"}),
    ("pinned safe name: cancel_auto_merge", "mcp__github__cancel_auto_merge", dict(PR)),
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

# ---- Round 2: the readers have limits, and a limit is a REFUSAL on the
# PR / review surface. 8 levels and a silent stop let `{event: APPROVE}` at
# depth 10 through, and a 100000-deep payload crashed the parser into an allow.

def nest(leaf, depth: int):
    for _ in range(depth):
        leaf = {"x": leaf}
    return leaf


def deep_raw(tool: str, depth: int) -> str:
    """A payload too deep for json.loads: built as text, never parsed here."""
    return ('{"tool_name": "%s", "tool_input": {"owner": "o", "pullNumber": 1, "x": ' % tool
            + "[" * depth + "]" * depth + "}}")


with tempfile.TemporaryDirectory() as lim_root:
    env = dict(base_env, CLAUDE_PROJECT_DIR=lim_root)
    print("LIMITS (risky tools refuse past a limit; ordinary tools are not walked):")
    for label, raw, want in [
        ("review tool: APPROVE at depth 10 is read and refused",
         json.dumps({"tool_name": "mcp__github__pull_request_review_write",
                     "tool_input": dict(PR, method="create", review=nest({"event": "APPROVE"}, 10))}), 2),
        ("review tool: past the depth limit, harmless leaf -> refused as unreadable",
         json.dumps({"tool_name": "mcp__github__pull_request_review_write",
                     "tool_input": dict(PR, method="create", review=nest({"body": "ok"}, 80))}), 2),
        ("review tool: past the size limit -> refused as unreadable",
         json.dumps({"tool_name": "mcp__github__pull_request_review_write",
                     "tool_input": dict(PR, method="create", comments=[{"body": "x"}] * 30000)}), 2),
        ("merge-shaped tool: 100000-deep payload the parser cannot read -> refused",
         deep_raw("mcp__forge__update_pull_request", 100000), 2),
        ("review tool: 100000-deep payload -> refused",
         deep_raw("mcp__github__pull_request_review_write", 100000), 2),
        ("ordinary tool: 100000-deep payload -> allowed (documented: not this hook's surface)",
         deep_raw("mcp__notion__create_page", 100000), 0),
        ("ordinary tool: past the depth limit -> allowed (its parameters are never walked)",
         json.dumps({"tool_name": "mcp__notion__create_page", "tool_input": {"blocks": nest({"t": "x"}, 80)}}), 0),
    ]:
        p = run(raw, env)
        ok = p.returncode == want and (want == 0 or "BLOCKED [mcp-" in p.stderr)
        check(ok, f"rc={p.returncode} want={want}  {label}")

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
        ("mcp-pr-unknown-action still blocks", "mcp__forge__land_pull_request", dict(PR), 2),
        ("vote 10 is an approve: allowed when mcp-approve is off", "mcp__ado__repo_update_pull_request_reviewers",
         {"pullRequestId": 7, "vote": 10}, 0),
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
        ("re raises on merge_pull_request_by_number -> block", "mcp__forge__merge_pull_request_by_number", dict(PR), 2),
        ("re raises on an ADO status completed -> block", "mcp__ado__update_pull_request_status",
         {"pullRequestId": 7, "status": "completed"}, 2),
        ("re raises on a vote tool -> block", "mcp__ado__repo_vote_pull_request", {"pullRequestId": 7, "vote": 10}, 2),
        ("re raises on an unrelated tool -> allow (a crash is an allow)", "mcp__github__get_me", {}, 0),
        ("re raises on a PR read -> allow", "mcp__github__pull_request_read", dict(PR, method="get"), 0),
    ]:
        p = run_with_fault(tool, ti, env)
        # A block must come from the crash path, or the fault never fired and
        # this section proves nothing.
        ok = p.returncode == want and (want == 0 or "safety hook failed" in p.stderr)
        check(ok, f"rc={p.returncode} want={want}  {label}")

# The allowlist of merge/approve-worded names that may run is itself pinned:
# widening it is a reviewed change to the hook AND to this list.
EXPECTED_SAFE_NAMES = {
    "disable_auto_merge": "always",
    "disable_pull_request_auto_merge": "always",
    "cancel_auto_merge": "always",
    "get_auto_merge_status": "always",
    "set_auto_merge": "off",
}
print("PINNED ALLOWLIST (SAFE_NAMES must equal this list exactly):")
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("hook_under_test", HOOK)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    got_safe = dict(getattr(_mod, "SAFE_NAMES", {}))
except Exception as exc:  # an unloadable hook is a failure, not a skip
    got_safe = {"<load failed>": type(exc).__name__}
check(got_safe == EXPECTED_SAFE_NAMES, f"SAFE_NAMES = {sorted(got_safe.items())}")
with tempfile.TemporaryDirectory() as al_root:
    env = dict(base_env, CLAUDE_PROJECT_DIR=al_root)
    for label, tool, ti, want in [
        ("set_auto_merge OFF runs (its 'off' condition)", "mcp__ccd_pr__set_auto_merge", {"enabled": False, "url": "u"}, 0),
        ("set_auto_merge ON is refused (same name, condition fails)", "mcp__ccd_pr__set_auto_merge",
         {"enabled": True, "url": "u"}, 2),
        ("a safe name with a suffix is not the safe name", "mcp__github__disable_auto_merge_then_merge", dict(PR), 2),
    ]:
        got = rc(tool, ti, env)
        check(got == want, f"rc={got} want={want}  {label}")

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
