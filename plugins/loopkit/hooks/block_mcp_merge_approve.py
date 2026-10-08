#!/usr/bin/env python3
"""block_mcp_merge_approve.py — the loop never merges and never approves, on MCP tools too.

block_dangerous.py refuses `gh pr merge`, `gh pr review --approve` and
`gh api .../pulls/N/merge`, but it is wired to the Bash tool only. An MCP
server reaches the same actions with no hook in front of it:
`mcp__github__merge_pull_request`, `mcp__github__pull_request_review_write`
with event APPROVE, a desktop helper that switches on auto-merge (a merge by
proxy: the code lands with no further look), or the same tools under another
server's name (`mcp__<uuid>__merge_pull_request`, gitlab's
`merge_merge_request`). Until this hook, "nobody can merge or approve" was
true on the gh CLI and nowhere else (reviewer finding, 2026-10-07).

Claude Code runs this as a PreToolUse hook with matcher `mcp__.*` and pipes
the call on STDIN:

    {"tool_name": "mcp__github__merge_pull_request", "tool_input": {...}}

Exit 0 allows. Exit 2 blocks and shows stderr to the model.

What it decides, on the tool's SHORT name (the part after the last `__`,
camelCase folded to snake_case so `mergePullRequest` reads as
`merge_pull_request`):

  mcp-merge        merge_pull_request, merge_merge_request,
                   accept_merge_request (GitLab's API word for merge), and a
                   PR/MR-context `*_merge` such as pull_request_merge.
  mcp-auto-merge   enable_*auto_merge always; set/toggle/update auto-merge
                   unless the input says OFF in so many words. A call that
                   does not say OFF is treated as ON: auto-merge is a merge.
                   disable_*auto_merge stays allowed.
  mcp-approve      approve_pull_request / approve_merge_request, and any
                   review tool (`review` in the name) whose input carries
                   event/state/action/decision == APPROVE or APPROVED, any
                   case. COMMENT and REQUEST_CHANGES stay allowed: reviewers
                   post their verdicts as comments.

Everything else is allowed. `update_pull_request_branch` brings a PR up to
date with its base; it does not merge the PR, so it is not blocked.

Failure policy, the same shape as block_dangerous.py:

  * a parse error (empty or malformed stdin, no tool name) is an ALLOW;
  * a crash AFTER the call parsed is not. Exit 1 is a non-blocking error, so
    a hook that crashed on `merge_pull_request` would wave it through. The
    fallback uses plain substring tests (the regex engine may be what failed)
    and blocks a tool whose name says merge or approve, or a review tool whose
    input mentions approve. Any other tool is allowed.

Switch-off, per project: name the rule (`mcp-merge`, `mcp-auto-merge`,
`mcp-approve`) in <project>/.loopkit/block-disabled.txt — the same file
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
RULES = (RULE_MERGE, RULE_AUTO, RULE_APPROVE)

_TARGET = r"(?:pull_request|merge_request|pr|mr)s?"
MERGE_RE = re.compile(
    rf"(?:^|_)(?:merge|accept)_{_TARGET}$"      # merge_pull_request, merge_merge_request, accept_merge_request
    rf"|(?:^|_){_TARGET}_merge$"                # pull_request_merge, pr_merge
)
AUTO_RE = re.compile(r"auto_?merge")
APPROVE_NAME_RE = re.compile(rf"(?:^|_)approve_{_TARGET}$")  # not unapprove_*: no `_` before it

# Input keys that carry a review's verdict, after camelCase folding.
VERDICT_KEYS = ("event", "state", "action", "review_event", "decision", "verdict", "type")
APPROVE_VALUES = ("approve", "approved")
# Input keys that switch auto-merge on or off, and the values that mean OFF.
TOGGLE_KEYS = ("enabled", "enable", "auto_merge", "automerge", "on", "value", "state")
OFF_VALUES = ("false", "off", "0", "no", "disable", "disabled")


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


def _folded(d: dict) -> dict:
    return {snake(str(k)): v for k, v in d.items()}


def _says_approve(tool_input: dict) -> bool:
    """A verdict field equal to APPROVE / APPROVED, top level or one level down
    (a `review: {event: ...}` shape)."""
    layers = [_folded(tool_input)]
    for v in list(layers[0].values()):
        if isinstance(v, dict):
            layers.append(_folded(v))
    for layer in layers:
        for key in VERDICT_KEYS:
            v = layer.get(key)
            if isinstance(v, str) and v.strip().lower() in APPROVE_VALUES:
                return True
    return False


def _says_off(tool_input: dict) -> bool:
    """True only when a toggle field says OFF in so many words."""
    d = _folded(tool_input)
    for key in TOGGLE_KEYS:
        if key not in d:
            continue
        v = d[key]
        if v is False or (isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0):
            return True
        if isinstance(v, str) and v.strip().lower() in OFF_VALUES:
            return True
        return False  # the first toggle field present decides; anything else is ON
    return False


def decide(tool_name: str, tool_input: dict, disabled: set[str] | None = None) -> str | None:
    """The rule name that refuses this call, or None to allow."""
    if not tool_name.startswith("mcp__"):
        return None
    disabled = disabled if disabled is not None else disabled_rules()
    name = short_name(tool_name)

    if AUTO_RE.search(name):
        if "disable" in name:
            return None
        if "enable" in name or not _says_off(tool_input):
            return None if RULE_AUTO in disabled else RULE_AUTO
        return None

    if MERGE_RE.search(name):
        return None if RULE_MERGE in disabled else RULE_MERGE

    if APPROVE_NAME_RE.search(name) or ("review" in name and _says_approve(tool_input)):
        return None if RULE_APPROVE in disabled else RULE_APPROVE

    return None


def fallback(tool_name: str, tool_input: object) -> str | None:
    """Crash path: plain substring tests, no regex. Blocks only a tool that
    names merge or approve, or a review tool whose input mentions approve."""
    try:
        if not tool_name.startswith("mcp__"):
            return None
        name = tool_name.rsplit("__", 1)[-1].lower()
        try:
            text = json.dumps(tool_input).lower()
        except Exception:
            text = str(tool_input).lower()
        if "merge" in name and "disable" not in name:
            rule = RULE_AUTO if "auto" in name else RULE_MERGE
        elif "approve" in name and "unapprove" not in name:
            rule = RULE_APPROVE
        elif "review" in name and "approve" in text:
            rule = RULE_APPROVE
        else:
            return None
        return None if rule in disabled_rules() else rule
    except Exception:
        return None


WHY = {
    RULE_MERGE: "the loop never merges; a human merges",
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
