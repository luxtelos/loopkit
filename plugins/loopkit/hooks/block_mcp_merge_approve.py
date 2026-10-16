#!/usr/bin/env python3
"""block_mcp_merge_approve.py — the loop never merges and never approves, on MCP tools too.

block_dangerous.py refuses `gh pr merge`, `gh pr review --approve` and
`gh api .../pulls/N/merge`, but until 0.2.5 it was wired to Bash only. An MCP
server reached the same actions with no hook in front of it:
`mcp__github__merge_pull_request`, `mcp__github__pull_request_review_write`
with event APPROVE, a desktop helper that switches on auto-merge (a merge by
proxy: the code lands with no further look), or the same tools under another
server's name (`mcp__<uuid>__merge_pull_request`, gitlab's
`merge_merge_request`). Until this hook, "nobody can merge or approve" was
true on the gh CLI and nowhere else (reviewer finding, 2026-10-07).

Claude Code runs this as a PreToolUse hook with matcher `mcp__.*` and pipes
the call on STDIN. A shell line typed through an MCP tool (`run_in_terminal
{command: "gh pr merge 1"}`) is NOT this hook's job: block_dangerous.py is
wired on `Bash|mcp__.*` and reads that command with the same rules it applies
to Bash. This hook judges the MCP tool's own name and parameters.

    {"tool_name": "mcp__github__merge_pull_request", "tool_input": {...}}

Exit 0 allows. Exit 2 blocks and shows stderr to the model.

What it decides. The first version enumerated known-bad names
(merge_pull_request, approve_merge_request, ...) and allowed everything else,
so a merge it had not heard of ran: merge_pull_request_by_number, a bare
`merge`, an ADO-style `status: completed`, `autoComplete: true`, `vote: 10`, an
APPROVE inside a list (independent review FAIL, 2026-10-08). It is now a SHAPE
rule that refuses unless the call is clearly safe.

The tool's SHORT name (after the last `__`, camelCase folded) is split into
words; `pull_request` / `merge_request` / pr / mr collapse to one noun, so
`merge` in `list_merge_requests` is never read as a verb.

  context      a merge or approve word counts only on the code-review surface:
               the short or server name carries pull / pr / review /
               merge_request, the server is a code host (git, github,
               gitlab, ...), or the input keys reference a pull request.
               `merge_cells` on a spreadsheet and `approve_expense` are not
               merges of code and run. Auto-merge / auto-complete counts
               everywhere.
  names        a name carrying a merge, approve or auto-merge word in
               context (merge, merged, approve, approval, accept_ / complete_
               on a PR noun) is REFUSED unless the WHOLE short name is on the
               pinned SAFE_NAMES list (disable_auto_merge, cancel_auto_merge,
               get_auto_merge_status, set_auto_merge when every toggle says
               OFF, ...). No word elsewhere in a name exempts it: the first
               version let a negate or read word anywhere skip every check,
               and merge_pull_request_and_delete_branch and
               check_and_merge_pull_request ran (review of 5514e4d).
               -> mcp-merge, mcp-approve, mcp-auto-merge.
  read         after that: a plain read — one read verb leading (get, list,
               search, ...) or trailing (`pull_request_read`), and no `and`
               — is ALLOWED whatever its parameters say. A read cannot merge.
  parameters   on the PR / review surface (a PR noun or review/vote word in
               the name or the input keys), to depth 64 and 20000 values,
               lists included:
               a verdict key (event, state, status, action, decision, ...)
               equal to APPROVE / APPROVED in any case -> mcp-approve;
               `vote` > 0 or "approved" -> mcp-approve (ADO: 10, 5);
               `approve`/`approved`/`auto_approve` truthy -> mcp-approve;
               status/state/state_event equal to completed / merged / merge
               -> mcp-merge; a `merge*` key with a value -> mcp-merge; an
               auto-merge / auto-complete / merge_when_* key that is not OFF
               -> mcp-auto-merge. Past either limit -> mcp-unreadable-input:
               an unread remainder is refused, never trusted.
  mcp-pr-unknown-action  a PR tool whose name has no verb this hook knows
               (create, update, add, comment, request, close, set, write,
               delete, ...): land_, squash_, rebase_, submit_ without review.
               It might merge, so it is refused until someone names it.

COMMENT and REQUEST_CHANGES reviews, creating, updating and closing PRs,
reviewer requests, comments, `update_pull_request_branch` (it brings a PR up
to date; it does not merge it) and switching auto-merge OFF stay allowed.

NOT covered by this hook, said plainly: a tool whose name and keys carry no
merge, approve, auto-merge or PR word at all (a `ship_it {number}`); a merge
word on a server this hook does not know as a code host, with no PR key in
its input; and a direct push to a protected branch. Branch protection is the
floor for those.

Failure policy, the same shape as block_dangerous.py:

  * a parse error (empty or malformed stdin, no tool name) is an ALLOW —
    except when the tool name, found by plain string search, is on the PR /
    review surface: then it is mcp-unreadable-input. A 100000-deep payload
    made json.loads raise RecursionError and the hook allow a merge tool.
  * a crash AFTER the call parsed is not. Exit 1 is a non-blocking error, so
    a hook that crashed on `merge_pull_request` would wave it through. The
    fallback uses plain substring tests (the regex engine may be what failed)
    and is WIDER than the main path: it blocks a tool whose name says merge,
    approve, accept, complete or vote unless the name is exactly on
    SAFE_NAMES, and a PR/review tool whose input mentions any of those. Any
    other tool is allowed.

Switch-off, per project: name the rule (`mcp-merge`, `mcp-auto-merge`,
`mcp-approve`, `mcp-pr-unknown-action`, `mcp-unreadable-input`) in <project>/.loopkit/block-disabled.txt — the same file
block_dangerous.py reads. That is a reviewed change to a tracked file, which
is the only alternative to people working around the guard.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

RULE_MERGE = "mcp-merge"
RULE_AUTO = "mcp-auto-merge"
RULE_APPROVE = "mcp-approve"
RULE_UNKNOWN = "mcp-pr-unknown-action"
RULE_UNREADABLE = "mcp-unreadable-input"
RULES = (RULE_MERGE, RULE_AUTO, RULE_APPROVE, RULE_UNKNOWN, RULE_UNREADABLE)

# The ONLY names that may carry a merge, approve or auto-merge word and still
# run. Exact short names, nothing fuzzy: a negate or read word somewhere else
# in a name proved nothing (`merge_pull_request_and_delete_branch`,
# `check_and_merge_pull_request` passed the first version of this rule,
# independent review FAIL of 5514e4d). Each entry says when it may run:
#   "always" — it cannot merge or approve by itself;
#   "off"    — only when every toggle field present says OFF.
# Pinned by test_block_mcp_merge_approve.py, so adding a name is a reviewed
# change to this file and its pin, never a quiet widening.
SAFE_NAMES: dict[str, str] = {
    "disable_auto_merge": "always",
    "disable_pull_request_auto_merge": "always",
    "cancel_auto_merge": "always",
    "get_auto_merge_status": "always",
    "set_auto_merge": "off",
}

# Code servers: a merge or approve word in a tool on one of these is about
# code, whatever else the name says (a git server's `git_merge` merges
# branches; that is the loop merging).
FORGE_SERVERS = frozenset({"git", "github", "gitlab", "gitea", "forgejo", "bitbucket", "gerrit",
                           "ado", "azure", "devops", "codeberg"})

# ---- Name side. A tool's short name is split into tokens; the noun phrases
# `pull_request` and `merge_request` (and pr / mr) collapse to one token, PR,
# so `merge` in `list_merge_requests` is a noun and never reads as a verb.
PR = "<pr>"
_PR_WORDS = frozenset({"pr", "prs", "mr", "mrs", "pullrequest", "pullrequests",
                       "mergerequest", "mergerequests"})
# The call only looks. First token (or last, for `pull_request_read`).
READ_VERBS = frozenset({
    "get", "list", "search", "read", "fetch", "query", "find", "view", "show",
    "describe", "check", "count", "inspect", "diff", "download", "export",
    "compare", "preview", "is", "has", "can",
})
# A trailing read verb marks a read only from this smaller set:
# `pull_request_read`. `status` is deliberately NOT a read verb — a tool named
# `pull_request_status` may SET the status — but it counts as a known word for
# the unknown-action rule, so its parameters decide.
TRAILING_READ_VERBS = frozenset({"read", "get", "list", "search", "view", "show", "fetch"})
KNOWN_PR_NOUNS = frozenset({"status", "diff", "files", "commits", "comments", "info", "details"})
# The call undoes or declines. A merge/approve word next to one of these is
# its object, not its action: disable_auto_merge, unapprove_merge_request.
NEGATE_VERBS = frozenset({
    "disable", "cancel", "unapprove", "revoke", "remove", "withdraw", "dismiss",
    "delete", "abort", "stop", "reject", "decline", "abandon", "unset", "clear",
})
# Actions on a pull/merge request that are known not to merge or approve by
# themselves. Their PARAMETERS are still read (update_pull_request {state:
# merged} is refused). A PR tool whose name carries none of these, and no read
# or negate verb, is an action this hook cannot classify: refused, not allowed.
SAFE_PR_VERBS = frozenset({
    "create", "open", "update", "edit", "add", "comment", "reply", "request",
    "resolve", "unresolve", "close", "reopen", "lock", "unlock", "assign",
    "unassign", "label", "unlabel", "bind", "unbind", "set", "write", "mark",
    "convert", "draft", "rerun", "retry", "subscribe", "unsubscribe", "watch",
    "unwatch", "monitor", "link", "unlink", "rename", "review", "fork", "copilot",
})
# Names of the code-review surface: a tool whose short name, or whose input
# keys, carry one of these has its parameters read for a verdict.
_REVIEW_WORDS = frozenset({"review", "reviews", "reviewer", "reviewers", "vote", "votes"})

# ---- Parameter side, after camelCase folding and a trailing `s` dropped
# (`events` reads as `event`).
VERDICT_KEYS = frozenset({
    "event", "state", "status", "action", "review_event", "review_state",
    "review_status", "review_decision", "decision", "verdict", "type",
    "outcome", "result", "approval_status",
})
APPROVE_VALUES = frozenset({"approve", "approved", "approving", "approval",
                            "approved_with_suggestions", "approvedwithsuggestions"})
# A PR whose status/state says this has been merged by the call.
MERGED_VALUES = frozenset({"completed", "complete", "merged", "merge"})
STATUS_KEYS = frozenset({"status", "state", "state_event", "pull_request_status", "pr_status", "merge_status"})
# Input keys that switch auto-merge on or off, and the values that mean OFF.
TOGGLE_KEYS = ("enabled", "enable", "auto_merge", "automerge", "on", "value", "state")
OFF_VALUES = frozenset({"false", "off", "0", "no", "disable", "disabled", "none", "null", ""})
# How far the parameter reader goes. Past either limit a call on the PR /
# review surface is REFUSED (mcp-unreadable-input), never allowed: the first
# version stopped silently at depth 8, and `{event: APPROVE}` at depth 10 ran.
_MAX_DEPTH = 64
_MAX_ITEMS = 20000


class Unreadable(Exception):
    """The input is deeper or larger than this hook will read."""


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def disabled_rules(root: Path | None = None) -> set[str]:
    """Rule names listed in .loopkit/block-disabled.txt. Plain string ops only,
    so the crash fallback can still honour the switch-off."""
    try:
        root = root or project_root()
        lines = (root / ".loopkit" / "block-disabled.txt").read_text(encoding="utf-8").splitlines()
    except Exception:
        return set()
    return {l.strip() for l in lines if l.strip() and not l.strip().startswith("#")}


def snake(name: str) -> str:
    """`mergePullRequest` -> `merge_pull_request`; dashes read as underscores."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower().replace("-", "_")


def short_name(tool_name: str) -> str:
    return snake(tool_name.rsplit("__", 1)[-1])


def tokens(name: str) -> list[str]:
    """Split a snake/camel name into words, collapsing the PR/MR noun phrases."""
    raw = [t for t in snake(name).split("_") if t]
    out: list[str] = []
    i = 0
    while i < len(raw):
        t = raw[i]
        nxt = raw[i + 1] if i + 1 < len(raw) else ""
        if t in ("pull", "merge") and nxt in ("request", "requests"):
            out.append(PR)
            i += 2
            continue
        out.append(PR if t in _PR_WORDS else t)
        i += 1
    return out


def _key(k: object) -> str:
    """A parameter key, folded: camelCase -> snake, a trailing plural `s` off
    (so `events` reads as `event`, `reviews` as `review`)."""
    k = snake(str(k))
    return k[:-1] if len(k) > 3 and k.endswith("s") and not k.endswith(("ss", "us", "is")) else k


def _walk(node: object, depth: int = 0, key: str = "", budget: list | None = None):
    """Every (folded key, scalar value) pair at any depth up to _MAX_DEPTH. A
    list's elements inherit the list's key, so `event: ["APPROVE"]` yields
    (event, APPROVE). Raises Unreadable past _MAX_DEPTH or _MAX_ITEMS: the
    caller REFUSES, it never treats an unread remainder as clean."""
    if budget is None:
        budget = [0]
    if depth > _MAX_DEPTH:
        raise Unreadable(f"nested deeper than {_MAX_DEPTH}")
    if isinstance(node, dict):
        items = [(_key(k), v) for k, v in node.items()]
    elif isinstance(node, list):
        items = [(key, v) for v in node]
    else:
        return
    for fk, v in items:
        budget[0] += 1
        if budget[0] > _MAX_ITEMS:
            raise Unreadable(f"more than {_MAX_ITEMS} values")
        if isinstance(v, (dict, list)):
            if v and isinstance(node, dict):
                yield fk, v  # the container itself, for truthiness tests
            yield from _walk(v, depth + 1, fk, budget)
        else:
            yield fk, v


def _truthy(v: object) -> bool:
    """ON unless it says OFF in so many words. A non-empty object (ADO's
    `autoCompleteSetBy: {id}`) is ON."""
    if v is None or v is False:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() not in OFF_VALUES
    return bool(v)


def _number(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _key_tokens(k: str) -> list[str]:
    return tokens(k)


def _is_auto_key(k: str) -> bool:
    flat = k.replace("_", "")
    return ("automerge" in flat or "autocomplete" in flat or flat.startswith("mergewhen")
            or flat.startswith("autosubmit"))


def _param_rule(tool_input: dict) -> str | None:
    """The rule a PR/review tool's PARAMETERS trip, at any depth."""
    for k, v in _walk(tool_input):
        if _is_auto_key(k):
            if _truthy(v):
                return RULE_AUTO
            continue
        kt = _key_tokens(k)
        # vote: ADO's 10 = approved, 5 = approved with suggestions.
        if "vote" in kt:
            n = _number(v)
            if (n is not None and n > 0) or (isinstance(v, str) and v.strip().lower() in APPROVE_VALUES):
                return RULE_APPROVE
            continue
        if isinstance(v, str):
            val = v.strip().lower()
            if k in VERDICT_KEYS and val in APPROVE_VALUES:
                return RULE_APPROVE
            if k in STATUS_KEYS and val in MERGED_VALUES:
                return RULE_MERGE
        # approve: true, approved: "yes", is_approved: 1
        if kt and (kt[-1] in ("approve", "approved") or kt == ["approval"]) \
                and not isinstance(v, (dict, list)) and _truthy(v):
            return RULE_APPROVE
        # merge: true, merge_now: 1, merge_strategy: squash — a merge asked for
        # through a parameter. `merge_request_iid` collapsed to PR above.
        if kt and kt[0].startswith("merg") and not isinstance(v, (dict, list)) and _truthy(v):
            return RULE_MERGE
    return None


def _has_auto(ts: list[str]) -> bool:
    for i, t in enumerate(ts):
        if t.startswith("automerg") or t.startswith("autocomplet"):
            return True
        if t == "auto" and i + 1 < len(ts) and (ts[i + 1].startswith("merg") or ts[i + 1].startswith("complet")):
            return True
    return False


def _key_context(tool_input: dict) -> bool:
    """Do the top-level input keys reference a pull/merge request or review?"""
    for k in tool_input:
        kt = tokens(str(k))
        if PR in kt or "pull" in kt or set(kt) & _REVIEW_WORDS:
            return True
    return False


def _context(ts: list[str], tool_input: dict) -> bool:
    """Is this the pull/merge-request or review surface, for PARAMETERS?"""
    return PR in ts or bool(set(ts) & _REVIEW_WORDS) or _key_context(tool_input)


def _name_context(tool_name: str, ts: list[str], tool_input: dict) -> bool:
    """Is a merge/approve word in this NAME about code? The short name or the
    server name carries pull / pr / review / merge_request, the server is a
    forge, or the input keys reference a pull request. `merge_cells` on a
    spreadsheet server and `approve_expense` are not, and refusing them only
    gets the guard switched off (independent review, 5514e4d)."""
    full = [t for t in tokens(tool_name) if t != "mcp"]
    words = set(full)
    return (PR in words or "pull" in words or bool(words & _REVIEW_WORDS)
            or bool(words & FORGE_SERVERS) or _key_context(tool_input))


def _says_off(tool_input: dict) -> bool:
    """True only when a toggle field says OFF in so many words."""
    d = {snake(str(k)): v for k, v in tool_input.items()}
    present = [d[key] for key in TOGGLE_KEYS if key in d]
    # OFF only when there is a toggle and EVERY toggle field says off:
    # `{enabled: false, enable: true}` is not a clear OFF, so it is ON.
    return bool(present) and not any(_truthy(v) for v in present)


def _risky_name_rule(ts: list[str], ctx: bool) -> str | None:
    """The rule a NAME trips by the words in it, before any allowlist."""
    if _has_auto(ts):
        return RULE_AUTO  # auto-merge / auto-complete is about code anywhere
    if not ctx:
        return None
    if any(t.startswith("merg") for t in ts):
        return RULE_MERGE  # merge, merged, merging; never the noun merge_request
    if any(t.startswith("approv") for t in ts):
        return RULE_APPROVE
    if PR in ts and any(t.startswith(("accept", "complet")) for t in ts):
        return RULE_MERGE  # GitLab's accept_merge_request, ADO's complete_pull_request
    return None


def classify(tool_name: str, tool_input: dict) -> str | None:
    """The rule this call trips, ignoring the switch-off file. A SHAPE rule:
    merge-, auto-merge- and approve-shaped names and parameters are refused
    unless the name is EXACTLY on SAFE_NAMES or the call is a plain read."""
    if not tool_name.startswith("mcp__"):
        return None
    short = short_name(tool_name)
    ts = tokens(short)
    if not ts:
        return None
    words = set(ts)

    # 1. Names. A merge / approve / auto-merge word refuses unless the whole
    # short name is pinned safe. No word elsewhere in the name exempts it.
    rule = _risky_name_rule(ts, _name_context(tool_name, ts, tool_input))
    if rule:
        when = SAFE_NAMES.get(short)
        if when == "always":
            return None
        if when == "off" and _says_off(tool_input):
            return None
        return rule

    # 2. A plain read only looks: `list_pull_request_reviews {state: APPROVED}`,
    # `pull_request_read`. Plain means one read verb leading (or trailing) and
    # no `and` joining a second action onto it.
    plain = "and" not in words
    if plain and (ts[0] in READ_VERBS
                  or (ts[-1] in TRAILING_READ_VERBS and not words & (SAFE_PR_VERBS - {"review"}))):
        return None

    # 3. Parameters, on the PR / review surface, to _MAX_DEPTH. Past a limit
    # this REFUSES: an unread remainder is not a clean one.
    if _context(ts, tool_input):
        try:
            rule = _param_rule(tool_input)
        except Unreadable:
            return RULE_UNREADABLE
        if rule:
            return rule

    # 4. A PR tool doing something this hook cannot name: land_pull_request,
    # squash_pull_request, submit_pull_request (Gerrit's word for merge).
    # `submit` with `review` submits a review, whose verdict step 3 read.
    if PR in words:
        known = words & (SAFE_PR_VERBS | READ_VERBS | KNOWN_PR_NOUNS | NEGATE_VERBS)
        if "submit" in words and "review" in words:
            known = known | {"submit"}
        if not known:
            return RULE_UNKNOWN
    return None


def decide(tool_name: str, tool_input: dict, disabled: set[str] | None = None) -> str | None:
    """The rule name that refuses this call, or None to allow."""
    rule = classify(tool_name, tool_input if isinstance(tool_input, dict) else {})
    if not rule:
        return None
    disabled = disabled if disabled is not None else disabled_rules()
    return None if rule in disabled else rule


def fallback(tool_name: str, tool_input: object) -> str | None:
    """Crash path: plain substring tests, no regex, and wider than decide().
    Blocks a tool whose name says merge, approve, accept, complete or vote
    unless the name is exactly on SAFE_NAMES, and any PR/review tool whose
    input mentions any of those. Any other tool is allowed."""
    try:
        if not tool_name.startswith("mcp__"):
            return None
        name = tool_name.rsplit("__", 1)[-1].lower()
        if SAFE_NAMES.get(name) == "always":
            return None
        try:
            text = json.dumps(tool_input).lower()
        except Exception:
            text = str(tool_input).lower()
        rule = _risky_by_substring(tool_name)
        if rule is None and _pr_ish(tool_name) and any(w in text for w in ("approv", "merg", "complet", "vote")):
            rule = RULE_MERGE
        if rule is None:
            return None
        return None if rule in disabled_rules() else rule
    except Exception:
        return None


def _pr_ish(tool_name: str) -> bool:
    n = tool_name.lower()
    return any(w in n for w in ("pull", "review", "merge_request", "mergerequest", "_pr", "pr_",
                                "_mr", "mr_", "github", "gitlab", "gitea", "bitbucket", "gerrit"))


def _risky_by_substring(tool_name: str) -> str | None:
    """Plain substring test of a tool NAME, for paths where nothing else can
    be trusted (a crash, an unparseable payload)."""
    name = tool_name.rsplit("__", 1)[-1].lower()
    if "auto" in name and "merg" in name:
        return RULE_AUTO
    if "merg" in name.replace("merge_request", "").replace("mergerequest", ""):
        return RULE_MERGE
    if "approv" in name or "vote" in name:
        return RULE_APPROVE
    if "accept" in name or "complet" in name:
        return RULE_MERGE
    return None


def _name_from_raw(raw: str) -> str:
    """The tool name from a payload json.loads could not read, by plain string
    search. "" when there is none."""
    i = raw.find('"tool_name"')
    if i < 0:
        return ""
    j = raw.find('"', raw.find(":", i) + 1)
    k = raw.find('"', j + 1) if j >= 0 else -1
    return raw[j + 1:k] if j >= 0 and k > j else ""


WHY = {
    RULE_MERGE: "the loop never merges; a human merges",
    RULE_UNKNOWN: ("an action on a pull/merge request this hook cannot classify; "
                   "it might merge, so it is refused until it is named"),
    RULE_UNREADABLE: ("its input is too deep, too large or unparseable to read in full; "
                      "on the pull/merge-request surface an unread remainder is refused, not trusted"),
    RULE_AUTO: "switching auto-merge on is a merge by proxy; a human merges",
    RULE_APPROVE: "the loop never approves; post the verdict as a COMMENT and a human approves",
}


def main() -> int:
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    try:
        payload = json.loads(raw) if raw.strip() else None
    except (ValueError, TypeError, RecursionError, MemoryError):
        # A parse error is an allow — EXCEPT on the PR / review surface. A
        # 100000-deep payload made json.loads raise RecursionError and the
        # hook allow a merge tool (independent review, 5514e4d). Plain string
        # search for the name: nothing parsed can be trusted here.
        name = _name_from_raw(raw)
        if name.startswith("mcp__") and (_risky_by_substring(name) or _pr_ish(name)) \
                and RULE_UNREADABLE not in disabled_rules():
            print(f"BLOCKED [{RULE_UNREADABLE}]: {name}: {WHY[RULE_UNREADABLE]}.", file=sys.stderr)
            return 2
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str) or not tool_name:
        return 0
    if not isinstance(tool_input, dict):
        tool_input = {}

    try:
        rule = decide(tool_name, tool_input)
    except Exception as exc:
        rule = fallback(tool_name, tool_input)
        if rule:
            print(
                f"BLOCKED [{rule}]: the safety hook failed ({type(exc).__name__}) while "
                f"reading {tool_name}; refusing rather than letting a merge or "
                "approve run unchecked.",
                file=sys.stderr,
            )
            return 2
        return 0

    if not rule:
        return 0
    # The tool name only, never the input: an input may carry a token.
    print(
        f"BLOCKED [{rule}]: {tool_name}: {WHY[rule]}.\n"
        "Merge and approve are human acts for every agent, on the gh CLI "
        "(block_dangerous.py) and on MCP tools (this hook). If this is a false "
        "positive, name the rule in .loopkit/block-disabled.txt (a reviewed "
        "change); do not reach for another tool that does the same thing.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        code = 0  # never raise; main() already failed closed where it matters
    sys.exit(code)
