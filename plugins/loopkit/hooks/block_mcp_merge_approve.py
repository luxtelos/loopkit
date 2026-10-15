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

  read         first word a read verb (get, list, search, read, ...), or a
               trailing read/get/list/search with no write verb
               (`pull_request_read`): ALLOWED, whatever its parameters say.
               A read cannot merge.
  mcp-auto-merge  a name carrying auto-merge or auto-complete: refused unless
               a negate verb names it (disable_, cancel_) or every toggle
               field present says OFF. No toggle at all is ON.
  mcp-merge    any other name with a merge word (merge, merged, merging, ...)
               not next to a negate verb; or accept_ / complete_ on a PR noun.
  mcp-approve  any name with an approve word (approve, approval, ...) not next
               to a negate verb (unapprove_, revoke_, dismiss_).
  parameters   on the PR / review surface (a PR noun or review/vote word in
               the name or the input keys), at ANY depth, lists included:
               a verdict key (event, state, status, action, decision, ...)
               equal to APPROVE / APPROVED in any case -> mcp-approve;
               `vote` > 0 or "approved" -> mcp-approve (ADO: 10, 5);
               `approve`/`approved`/`auto_approve` truthy -> mcp-approve;
               status/state/state_event equal to completed / merged / merge
               -> mcp-merge; a `merge*` key with a value -> mcp-merge; an
               auto-merge / auto-complete / merge_when_* key that is not OFF
               -> mcp-auto-merge.
  mcp-pr-unknown-action  a PR tool whose name has no verb this hook knows to
               be safe (create, update, add, comment, request, close, set,
               write, ...): land_, squash_, rebase_, submit_ without review.
               It might merge, so it is refused until someone names it.

COMMENT and REQUEST_CHANGES reviews, creating, updating and closing PRs,
reviewer requests, comments, `update_pull_request_branch` (it brings a PR up
to date; it does not merge it) and switching auto-merge OFF stay allowed.

A merge word OUTSIDE the PR surface is refused too (a `merge_records` tool on a
database server): fail closed costs a switch-off line; fail open costs a
merge. NOT covered by this hook, said plainly: a tool whose name and keys carry
no merge, approve or PR word at all (a `ship_it {number}`), and a direct push
to a protected branch. Branch protection is the floor for those.

Failure policy, the same shape as block_dangerous.py:

  * a parse error (empty or malformed stdin, no tool name) is an ALLOW;
  * a crash AFTER the call parsed is not. Exit 1 is a non-blocking error, so
    a hook that crashed on `merge_pull_request` would wave it through. The
    fallback uses plain substring tests (the regex engine may be what failed)
    and is WIDER than the main path: it blocks a non-read tool whose name says
    merge, approve, accept, complete or vote, and a PR/review tool whose input
    mentions any of those. Any other tool is allowed.

Switch-off, per project: name the rule (`mcp-merge`, `mcp-auto-merge`,
`mcp-approve`, `mcp-pr-unknown-action`) in <project>/.loopkit/block-disabled.txt — the same file
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
RULES = (RULE_MERGE, RULE_AUTO, RULE_APPROVE, RULE_UNKNOWN)

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
_MAX_DEPTH = 8


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


def _walk(node: object, depth: int = 0, key: str = ""):
    """Every (folded key, scalar value) pair at any depth. A list's elements
    inherit the list's key, so `event: ["APPROVE"]` yields (event, APPROVE)."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            fk = _key(k)
            if isinstance(v, (dict, list)):
                if v:
                    yield fk, v  # the container itself, for truthiness tests
                yield from _walk(v, depth + 1, fk)
            else:
                yield fk, v
    elif isinstance(node, list):
        for v in node:
            if isinstance(v, (dict, list)):
                yield from _walk(v, depth + 1, key)
            else:
                yield key, v


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


def _context(ts: list[str], tool_input: dict) -> bool:
    """Is this the pull/merge-request or review surface?"""
    if PR in ts or set(ts) & _REVIEW_WORDS:
        return True
    for k in tool_input:
        kt = tokens(str(k))
        if PR in kt or "pull" in kt or set(kt) & _REVIEW_WORDS:
            return True
    return False


def _says_off(tool_input: dict) -> bool:
    """True only when a toggle field says OFF in so many words."""
    d = {snake(str(k)): v for k, v in tool_input.items()}
    present = [d[key] for key in TOGGLE_KEYS if key in d]
    # OFF only when there is a toggle and EVERY toggle field says off:
    # `{enabled: false, enable: true}` is not a clear OFF, so it is ON.
    return bool(present) and not any(_truthy(v) for v in present)


def classify(tool_name: str, tool_input: dict) -> str | None:
    """The rule this call trips, ignoring the switch-off file. A SHAPE rule:
    merge-, auto-merge- and approve-shaped names and parameters are refused
    unless the call is clearly a read or a negation."""
    if not tool_name.startswith("mcp__"):
        return None
    ts = tokens(tool_name.rsplit("__", 1)[-1])
    if not ts:
        return None
    words = set(ts)

    # A read only looks: get_auto_merge_status, list_pull_request_reviews
    # {state: APPROVED}, pull_request_read. Nothing it is given can merge.
    if ts[0] in READ_VERBS or (ts[-1] in TRAILING_READ_VERBS and not words & (SAFE_PR_VERBS - {"review"})):
        return None

    negated = bool(words & NEGATE_VERBS)

    # Auto-merge / auto-complete by name: refused unless it is switched OFF.
    if _has_auto(ts):
        if negated:
            return None
        if "enable" in words or not _says_off(tool_input):
            return RULE_AUTO
        return None

    ctx = _context(ts, tool_input)
    if not negated:
        # merge, merged, merging, mergePullRequestByNumber: any merge word that
        # is not the noun `merge_request` (collapsed to PR by tokens()).
        if any(t.startswith("merg") for t in ts):
            return RULE_MERGE
        if any(t.startswith("approv") for t in ts):
            return RULE_APPROVE
        # GitLab's accept_merge_request, ADO's complete_pull_request.
        if PR in words and any(t.startswith(("accept", "complet")) for t in ts):
            return RULE_MERGE

    if ctx:
        rule = _param_rule(tool_input)
        if rule:
            return rule

    # A PR tool doing something this hook cannot name: land_pull_request,
    # submit_pull_request (Gerrit's word for merge). `submit` with `review`
    # submits a review, whose verdict the parameters above already read.
    if PR in words and not negated:
        known = words & (SAFE_PR_VERBS | READ_VERBS | KNOWN_PR_NOUNS)
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
    (unless it plainly reads, disables or un-approves), and any PR/review tool
    whose input mentions approve, merge, complete or vote."""
    try:
        if not tool_name.startswith("mcp__"):
            return None
        name = tool_name.rsplit("__", 1)[-1].lower()
        try:
            text = json.dumps(tool_input).lower()
        except Exception:
            text = str(tool_input).lower()
        reads = name.startswith(("get", "list", "search", "read", "fetch", "query", "find", "view"))
        undoes = "disable" in name or "unapprov" in name or "cancel" in name
        rule = None
        if not reads and not undoes:
            if "auto" in name and "merg" in name:
                rule = RULE_AUTO
            elif "merg" in name.replace("merge_request", "").replace("mergerequest", ""):
                rule = RULE_MERGE
            elif "approv" in name or "vote" in name:
                rule = RULE_APPROVE
            elif "accept" in name or "complet" in name:
                rule = RULE_MERGE
            elif any(w in name for w in ("pull", "review", "merge_request", "_pr", "pr_", "_mr", "mr_")) and \
                    any(w in text for w in ("approv", "merg", "complet", "vote")):
                rule = RULE_MERGE
        if rule is None:
            return None
        return None if rule in disabled_rules() else rule
    except Exception:
        return None


WHY = {
    RULE_MERGE: "the loop never merges; a human merges",
    RULE_UNKNOWN: ("an action on a pull/merge request this hook cannot classify; "
                   "it might merge, so it is refused until it is named"),
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
    except (ValueError, TypeError):
        return 0  # a parse error is an allow
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
