#!/usr/bin/env python3
"""Cases for loop-scan.py's decision functions.

These three functions decide what a human is told to act on, so they are the
part that must not be wrong. The network calls around them are not tested here
— they are one `gh` invocation each and fail visibly.

The case that motivated this file: a PR was reported as "approved, waiting
on a human merge" while its CI was failing, because the summary asked
"is it approved?" when the useful question is "could I merge it right now?"

usage: python3 scripts/test_loop_scan.py
"""

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("loop_scan", HERE / "loop-scan.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)


def pr(num=1, ci=None, draft=False, mergeable="MERGEABLE", review=None):
    """A PR shaped like gh's JSON. `ci` is a list of conclusions, or None."""
    checks = None if ci is None else [{"conclusion": c} for c in ci]
    return {
        "number": num, "title": f"pr {num}", "isDraft": draft,
        "mergeable": mergeable, "reviewDecision": review,
        "statusCheckRollup": checks, "baseRefName": "main",
    }


fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))
    if not ok:
        fails += 1


print("ci_state:")
check("all success is green", ls.ci_state(pr(ci=["SUCCESS", "SUCCESS"])), "green")
check("any failure wins", ls.ci_state(pr(ci=["SUCCESS", "FAILURE"])), "FAILING")
check("timed out counts as failing", ls.ci_state(pr(ci=["TIMED_OUT"])), "FAILING")
check("cancelled counts as failing", ls.ci_state(pr(ci=["CANCELLED"])), "FAILING")
check("pending is pending", ls.ci_state(pr(ci=["SUCCESS", "IN_PROGRESS"])), "pending")
check("failure beats pending", ls.ci_state(pr(ci=["IN_PROGRESS", "FAILURE"])), "FAILING")
check("no checks is 'none', not green", ls.ci_state(pr(ci=None)), "none")

print("\nblocker — reports the FIRST thing stopping it:")
check("draft outranks all", ls.blocker(pr(draft=True, ci=["FAILURE"]), "FAILING"), "draft")
check("conflicts outrank CI", ls.blocker(pr(mergeable="CONFLICTING", ci=["FAILURE"]), "FAILING"),
      "CONFLICTS - merge base in")
check("failing CI", ls.blocker(pr(ci=["FAILURE"]), "FAILING"), "CI failing")
check("running CI", ls.blocker(pr(ci=["QUEUED"]), "pending"), "CI running")
check("changes requested", ls.blocker(pr(ci=["SUCCESS"], review="CHANGES_REQUESTED"), "green"),
      "changes requested")
check("approved and green", ls.blocker(pr(ci=["SUCCESS"], review="APPROVED"), "green"),
      "approved - awaiting human merge")
check("no review yet", ls.blocker(pr(ci=["SUCCESS"]), "green"), "needs a reviewer")

print("\nmergeable_now — the only line a human should act on unread:")
check("approved + green", ls.mergeable_now([pr(1, ci=["SUCCESS"], review="APPROVED")]), [1])
check("approved + no checks at all", ls.mergeable_now([pr(2, ci=None, review="APPROVED")]), [2])
# The approved-but-red regression, pinned:
check("approved but CI failing is NOT ready",
      ls.mergeable_now([pr(1248, ci=["FAILURE"], review="APPROVED")]), [])
check("approved but conflicting is NOT ready",
      ls.mergeable_now([pr(3, ci=["SUCCESS"], review="APPROVED", mergeable="CONFLICTING")]), [])
check("approved but draft is NOT ready",
      ls.mergeable_now([pr(4, ci=["SUCCESS"], review="APPROVED", draft=True)]), [])
check("approved but CI still running is NOT ready",
      ls.mergeable_now([pr(5, ci=["IN_PROGRESS"], review="APPROVED")]), [])
check("green but unreviewed is NOT ready", ls.mergeable_now([pr(6, ci=["SUCCESS"])]), [])
check("changes requested is NOT ready",
      ls.mergeable_now([pr(7, ci=["SUCCESS"], review="CHANGES_REQUESTED")]), [])
check("picks only the ready one out of a mixed set",
      ls.mergeable_now([
          pr(10, ci=["SUCCESS"], review="APPROVED"),
          pr(11, ci=["FAILURE"], review="APPROVED"),
          pr(12, ci=["SUCCESS"]),
      ]), [10])

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)
