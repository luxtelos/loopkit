#!/usr/bin/env python3
"""loop-scan.py — one deterministic answer to "what is in flight, and what needs me?"

WHY A SCRIPT AND NOT A RULE IN CLAUDE.md
----------------------------------------
"Which PRs are open, which are green, which are blocked" is a lookup, not a
judgement. CLAUDE.md is *context*: it can say "check the PRs" but it cannot run
`gh`, and every line in it spends compliance budget on every turn — including
the thousands that are not about pull requests. A derived value the prompt
should never compute belongs in a tool, so the model asks this script and
spends its attention on the rows that actually need a decision.

WHAT IT DELIBERATELY DOES
-------------------------
Two API calls total — one `gh pr list`, one `gh issue list` — never one call per
item. Per-item detail endpoints are what make a status sweep slow, and a sweep
nobody runs because it is slow is worse than a coarse one they run daily.

It reports CI from `statusCheckRollup` with a caveat: green means *nothing
reported failure*, not *everything ran*. Steps inside a job are sequential and
fail fast, so a green rollup is consistent with later steps never executing.

It also prints the local `state/triage.md` counts, which no GitHub query can
see. A status answer that covers only GitHub is how forty unclassified findings
stayed invisible while the loop reported itself busy.

Always exits 0. Read the table, not the exit code — a scan that failed closed
would block a tick over a network blip. Read-only: nothing here changes state.

usage:
    python3 loop-scan.py
    python3 loop-scan.py --prs-only
    python3 loop-scan.py --repo owner/name --limit 60 --scope <lane|a,b,c>

config:
    LOOPKIT_REPO           owner/name (default: `gh repo view`)
    LOOPKIT_ISSUE_LABEL    the label the loop puts on issues it owns (default: loopkit)
    <project>/.loopkit/scopes.json   {"lane": ["term", ...], ...} — named lanes
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLOCKED_STATES = ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED")
PENDING_STATES = ("", "PENDING", "IN_PROGRESS", "QUEUED", "EXPECTED")

# Named scopes, so a lane is one flag and not a remembered list of keywords.
# Loaded from <project>/.loopkit/scopes.json; empty until a project defines its
# lanes. Kept as a module global so tests and loop_next_pick.py share one table.
SCOPES: dict[str, list[str]] = {}


def project_root() -> Path:
    env = os.environ.get("LOOPKIT_PROJECT_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def load_scopes(root: Path) -> dict[str, list[str]]:
    try:
        raw = json.loads((root / ".loopkit" / "scopes.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for lane, terms in raw.items():
            if isinstance(terms, list):
                out[str(lane).lower()] = [str(t).lower() for t in terms if str(t).strip()]
    return out


def resolve_terms(scope: str | None, scopes: dict[str, list[str]]) -> list[str]:
    """A lane name, or a comma list of terms. Empty means unscoped."""
    if not scope:
        return []
    return scopes.get(scope.lower()) or [
        t.strip().lower() for t in scope.split(",") if t.strip()
    ]


def detect_repo() -> str | None:
    env = os.environ.get("LOOPKIT_REPO")
    if env:
        return env
    try:
        p = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                           capture_output=True, text=True, timeout=30)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except Exception:
        pass
    return None


def gh_json(args: list[str]) -> list | None:
    """Run a gh command and parse JSON. None means 'could not read', which is
    deliberately different from an empty list — 'no PRs' and 'I could not look'
    must never render the same way."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def scope_rank(item: dict, terms: list[str]) -> str:
    """'strong', 'weak' or 'out' — ranked, never silently filtered.

    Filtering on one match set gets this wrong in both directions: a docs PR
    carrying the epic LABEL is genuinely tagged work even if nobody doing the
    sprint wants it in their list, and a PR whose title merely mentions the
    lane ("payments fixed by #2220") is a passing mention, not the subject.

    So: labels and branch names are declarations and rank STRONG. A title
    keyword alone is a hint and ranks WEAK. The caller shows both, separately.
    """
    if not terms:
        return "strong"

    labels = " ".join(lbl.get("name", "") for lbl in item.get("labels", []) or []).lower()
    branches = f'{item.get("baseRefName", "")} {item.get("headRefName", "")}'.lower()
    title = str(item.get("title", "")).lower()

    if any(t in labels for t in terms) or any(t in branches for t in terms):
        return "strong"
    if any(t in title for t in terms):
        return "weak"
    return "out"


def is_docs_only(item: dict) -> bool:
    """Docs PRs ride along on an epic label without being that epic's work. Worth
    marking rather than dropping — a sprint handoff cares about code."""
    labels = [lbl.get("name", "").lower() for lbl in item.get("labels", []) or []]
    return "documentation" in labels or str(item.get("title", "")).startswith("docs(")


def ci_state(pr: dict) -> str:
    checks = pr.get("statusCheckRollup") or []
    if not checks:
        return "none"
    states = [c.get("conclusion") or c.get("state") or "" for c in checks]
    if any(s in BLOCKED_STATES for s in states):
        return "FAILING"
    if any(s in PENDING_STATES for s in states):
        return "pending"
    return "green"


def blocker(pr: dict, ci: str) -> str:
    """What is actually stopping this PR, in the order it stops it."""
    if pr.get("isDraft"):
        return "draft"
    if pr.get("mergeable") == "CONFLICTING":
        return "CONFLICTS - merge base in"
    if ci == "FAILING":
        return "CI failing"
    if ci == "pending":
        return "CI running"
    decision = pr.get("reviewDecision")
    if decision == "CHANGES_REQUESTED":
        return "changes requested"
    if decision == "APPROVED":
        return "approved - awaiting human merge"
    return "needs a reviewer"


def mergeable_now(rows: list) -> list[int]:
    """PRs a human could merge right now, with nothing else to do first.

    Approved is NOT the same as mergeable — a PR can be approved with red CI or
    a conflicted base, and reporting those together is how a PR once showed up
    as ready to merge while its CI was failing. This is the only summary line
    that should ever be acted on without opening the PR.
    """
    return [
        pr["number"]
        for pr in rows
        if pr.get("reviewDecision") == "APPROVED"
        and not pr.get("isDraft")
        and pr.get("mergeable") != "CONFLICTING"
        and ci_state(pr) in ("green", "none")
    ]


def render_prs(rows: list | None) -> None:
    if rows is None:
        print("PRS: could not read (auth or network).")
        print("     That is NOT the same as zero open PRs. Do not read it as all clear.")
        return
    if not rows:
        print("PRS: none open.")
        return

    print(f"PRS: {len(rows)} open\n")
    print(f'  {"#":>5}  {"CI":<8} {"BASE":<14} {"BLOCKED ON":<32} TITLE')
    print(f'  {"-" * 5}  {"-" * 8} {"-" * 14} {"-" * 32} {"-" * 30}')
    for pr in sorted(rows, key=lambda r: r["number"], reverse=True):
        ci = ci_state(pr)
        title = pr.get("title", "")[:54]
        print(
            f'  {pr["number"]:>5}  {ci:<8} {pr.get("baseRefName", "?"):<14} '
            f"{blocker(pr, ci):<32} {title}"
        )

    failing = [p["number"] for p in rows if ci_state(p) == "FAILING"]
    conflicting = [p["number"] for p in rows if p.get("mergeable") == "CONFLICTING"]
    ready = sorted(mergeable_now(rows), reverse=True)
    approved_but_not = sorted(
        p["number"] for p in rows
        if p.get("reviewDecision") == "APPROVED" and p["number"] not in ready
    )

    print()
    # "Approved" and "mergeable" are different questions. READY is the only
    # line here a human can act on without opening anything.
    if ready:
        print(f"  READY TO MERGE (approved, green, no conflicts) : {ready}")
    else:
        print("  READY TO MERGE                                 : none")
    if approved_but_not:
        print(f"  approved but NOT mergeable (see table)         : {approved_but_not}")
    if failing:
        print(f"  CI FAILING                                     : {failing}")
    if conflicting:
        print(f"  CONFLICTING                                    : {conflicting}")

    print()
    print("  Caveat: green means nothing REPORTED failure, not that everything ran.")
    print("  Steps inside a job are sequential and fail fast, so a green rollup is")
    print("  consistent with later steps never executing. Read the job before")
    print("  trusting the tick.")


def render_issues(rows: list | None, label: str) -> None:
    if rows is None:
        print(f"ISSUES ({label}): could not read (auth or network).")
        return
    if not rows:
        print(f"ISSUES ({label}): none open.")
        return
    print(f"ISSUES ({label}): {len(rows)} open\n")
    for issue in sorted(rows, key=lambda r: r["number"], reverse=True):
        labels = ",".join(
            lbl["name"] for lbl in issue.get("labels", []) if lbl["name"] != label
        )
        print(f'  {issue["number"]:>5}  {issue.get("title","")[:62]:<62} {labels}')


def render_local_backlog(root: Path) -> None:
    """The half GitHub cannot see. A status answer that covers only GitHub is how
    forty unclassified findings stayed invisible while the loop looked busy."""
    state = root / "state" / "triage.md"
    if not state.exists():
        print("LOCAL BACKLOG: no state/triage.md yet (run /loopkit:init, then morning-triage).")
        return
    try:
        p = subprocess.run(
            [sys.executable, str(HERE / "triage_state.py"),
             "list", "--state", str(state), "--format", "json"],
            capture_output=True, text=True, timeout=60,
        )
        rows = json.loads(p.stdout)
    except Exception:
        print("LOCAL BACKLOG: could not parse state/triage.md")
        return

    counts = collections.Counter(r.get("status", "?") for r in rows)
    print("LOCAL BACKLOG (state/triage.md — invisible to any GitHub query):")
    print("  " + ("  ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty"))
    if counts.get("new"):
        print(f"  -> {counts['new']} unclassified. Next stage: bash {HERE}/loop-next.sh")


def main() -> int:
    root = project_root()
    SCOPES.update(load_scopes(root))
    label = os.environ.get("LOOPKIT_ISSUE_LABEL", "loopkit")

    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--repo", default=None, help="owner/name (default: LOOPKIT_REPO or `gh repo view`)")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--prs-only", action="store_true")
    ap.add_argument(
        "--scope",
        help="Restrict to one lane: " + (", ".join(SCOPES) or "(none defined in .loopkit/scopes.json)")
             + ". Or pass your own comma-separated terms.",
    )
    args = ap.parse_args()

    repo = args.repo or detect_repo()
    if not repo:
        print("SCAN: no repository. Set LOOPKIT_REPO=owner/name, pass --repo, or run inside a gh-authenticated clone.")
        print()
        render_local_backlog(root)
        print("\nSCAN: done — read-only, nothing changed.")
        return 0

    terms = resolve_terms(args.scope, SCOPES)

    header = f"SCAN: {repo}"
    if terms:
        header += f"   scope={args.scope}"
    print(header + "\n")

    prs = gh_json([
        "gh", "pr", "list", "--repo", repo, "--state", "open",
        "--limit", str(args.limit),
        "--json", "number,title,isDraft,mergeable,reviewDecision,"
                  "statusCheckRollup,headRefName,baseRefName,labels",
    ])
    weak_prs: list[dict] = []
    if prs is not None and terms:
        total = len(prs)
        ranked = [(scope_rank(p, terms), p) for p in prs]
        prs = [p for r, p in ranked if r == "strong"]
        weak_prs = [p for r, p in ranked if r == "weak"]
        docs = [p["number"] for p in prs if is_docs_only(p)]
        print(f"  {len(prs)} of {total} open PRs are in this lane "
              f"(labelled or branched into it).")
        if docs:
            print(f"  Of those, {docs} are docs-only — tagged with the epic, "
                  f"not code work for it.")
        print()
    render_prs(prs)
    if weak_prs:
        print()
        print("  Mentions only — the term appears in the title but the PR is not")
        print("  labelled or branched into this lane. Probably not yours:")
        for p in sorted(weak_prs, key=lambda r: r["number"], reverse=True):
            print(f'    {p["number"]:>5}  {p.get("title","")[:66]}')

    if not args.prs_only:
        print()
        issues = gh_json([
            "gh", "issue", "list", "--repo", repo, "--state", "open",
            "--limit", str(args.limit), "--label", label,
            "--json", "number,title,labels,updatedAt",
        ])
        weak_issues: list[dict] = []
        if issues is not None and terms:
            ranked_i = [(scope_rank(i, terms), i) for i in issues]
            issues = [i for r, i in ranked_i if r == "strong"]
            weak_issues = [i for r, i in ranked_i if r == "weak"]
        render_issues(issues, label)
        if weak_issues:
            print()
            print("  Mentions only (title matched, no label) — probably not yours:")
            for i in sorted(weak_issues, key=lambda r: r["number"], reverse=True):
                print(f'    {i["number"]:>5}  {i.get("title","")[:66]}')
        print()
        render_local_backlog(root)

    print("\nSCAN: done — read-only, nothing changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
