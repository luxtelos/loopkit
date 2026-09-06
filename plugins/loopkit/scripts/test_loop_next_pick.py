#!/usr/bin/env python3
"""Pins for loop_next_pick.py — the --scope pick and its fallback.

Control case first: with no scope the pick must be byte-identical to the old
behaviour (first row per status in file order). Then the things --scope adds:
a matching lane picks the first MATCHING row by priority, a lane that matches
nothing says so and falls back instead of serving an empty stage, and a scope
must report how much actionable work it is hiding.

Lanes come from <project>/.loopkit/scopes.json; the tests build one in a
temp dir and point LOOPKIT_PROJECT_ROOT at it.

usage: python3 scripts/test_loop_next_pick.py
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("lnp", os.path.join(HERE, "loop_next_pick.py"))
lnp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lnp)

ROWS = [
    {"finding": "Auth invited-user visibility RCA", "source": "GitHub #1282", "status": "new", "priority": "high"},
    {"finding": "Report bundle schema validation", "source": "GitHub #1460", "status": "pr-open", "priority": "high"},
    {"finding": "Epic — Pricing and Billing", "source": "GitHub #419", "status": "new", "priority": "medium"},
    {"finding": "Billing activation writes NULL period_end", "source": "state/audit", "status": "new", "priority": "critical"},
    {"finding": "Auth role drift", "source": "GitHub #687", "status": "spec-draft", "priority": "medium"},
]

SCOPES = {
    "billing": ["billing", "invoice", "period_end", "419"],
    "auth": ["auth", "role", "687"],
}

_TMP = tempfile.TemporaryDirectory()
_LK = Path(_TMP.name) / ".loopkit"
_LK.mkdir()
(_LK / "scopes.json").write_text(json.dumps(SCOPES))
os.environ["LOOPKIT_PROJECT_ROOT"] = _TMP.name


def lines(out: str) -> dict:
    parts = out.split("\n")
    d = {"counts": parts[0]}
    for ln in parts[1:]:
        k, _, v = ln.partition("\t")
        d[k] = v
    return d


class Pick(unittest.TestCase):
    def test_control_no_scope_is_file_order(self):
        d = lines(lnp.pick(ROWS, ""))
        self.assertEqual(d["counts"], "1|0|0|1|3")
        self.assertEqual(d["new"], "Auth invited-user visibility RCA")
        self.assertNotIn("note", d)

    def test_scope_ranks_critical_above_the_older_umbrella_row(self):
        # The epic row is EARLIER in the file and matches the lane too; file
        # order alone would serve it first.
        d = lines(lnp.pick(ROWS, "billing"))
        self.assertEqual(d["new"], "Billing activation writes NULL period_end")
        self.assertEqual(d["counts"], "1|0|0|0|2")  # pr-open polled in every lane
        self.assertIn("2 row(s) match", d["note"])

    def test_unscoped_ignores_priority_and_keeps_file_order(self):
        d = lines(lnp.pick(ROWS, ""))
        self.assertEqual(d["new"], "Auth invited-user visibility RCA")  # high, but first in file

    def test_named_lane_uses_the_scopes_table(self):
        d = lines(lnp.pick(ROWS, "auth"))
        self.assertEqual(d["new"], "Auth invited-user visibility RCA")
        self.assertEqual(d["spec-draft"], "Auth role drift")

    def test_lane_names_are_case_insensitive(self):
        self.assertEqual(lnp.scope_terms("Billing"), SCOPES["billing"])

    def test_unknown_scope_falls_back_and_says_so(self):
        d = lines(lnp.pick(ROWS, "zzz-nolane"))
        self.assertEqual(d["new"], "Auth invited-user visibility RCA")
        self.assertIn("matched 0 rows", d["note"])

    def test_targets_list_every_row_at_a_stage_in_priority_order_when_scoped(self):
        out = lnp.pick(ROWS, "billing")
        targets = [ln.split("\t") for ln in out.split("\n") if ln.startswith("target:new\t")]
        self.assertEqual([t[1] for t in targets], [
            "Billing activation writes NULL period_end",
            "Epic — Pricing and Billing",
        ])
        self.assertEqual(targets[0][2], "state/audit")  # source travels with the row

    def test_targets_unscoped_include_every_lane_in_file_order(self):
        out = lnp.pick(ROWS, "")
        targets = [ln.split("\t")[1] for ln in out.split("\n") if ln.startswith("target:new\t")]
        self.assertEqual(targets[0], "Auth invited-user visibility RCA")
        self.assertEqual(len(targets), 3)

    def test_fanout_is_capped(self):
        many = [{"finding": f"row {i}", "source": f"src {i}", "status": "new", "priority": "low"} for i in range(20)]
        out = lnp.pick(many, "")
        self.assertEqual(sum(1 for ln in out.split("\n") if ln.startswith("target:new\t")), lnp.MAX_FANOUT)
        self.assertTrue(out.startswith("0|0|0|0|20"))  # the count is never hidden by the cap

    def test_comma_terms_work_without_a_named_lane(self):
        d = lines(lnp.pick(ROWS, "period_end,nothing"))
        self.assertEqual(d["new"], "Billing activation writes NULL period_end")


class NoScopesFile(unittest.TestCase):
    """A project with no .loopkit/scopes.json still gets comma-term scopes."""

    def test_comma_terms_without_a_table(self):
        with tempfile.TemporaryDirectory() as bare:
            saved = os.environ["LOOPKIT_PROJECT_ROOT"]
            os.environ["LOOPKIT_PROJECT_ROOT"] = bare
            try:
                self.assertEqual(lnp.scope_terms("Billing"), ["billing"])
                d = lines(lnp.pick(ROWS, "period_end"))
                self.assertEqual(d["new"], "Billing activation writes NULL period_end")
            finally:
                os.environ["LOOPKIT_PROJECT_ROOT"] = saved


class ScopeHidesNothing(unittest.TestCase):
    """A scoped pick must SAY how much actionable work it is ignoring.

    A scoped tick once printed "STAGE: discover / no actionable rows" while
    twenty `new` rows sat in the unscoped backlog. Both statements were true
    and the pair was misleading — the lane was idle, the loop was not.
    """

    def test_scope_note_reports_actionable_rows_left_outside_the_lane(self):
        d = lines(lnp.pick(ROWS, "billing"))
        self.assertIn("unscoped new=", d["note"])
        # ROWS holds three `new` rows; the billing lane matches the epic row
        # and the period_end row, leaving the auth one outside.
        self.assertIn("unscoped new=1", d["note"])

    def test_no_note_noise_when_the_lane_holds_everything(self):
        only_new = [r for r in ROWS if r["status"] == "new"]
        d = lines(lnp.pick(only_new, "period_end,419,auth"))
        self.assertNotIn("unscoped new=", d.get("note", ""))

    def test_control_unscoped_pick_emits_no_note_at_all(self):
        d = lines(lnp.pick(ROWS, ""))
        self.assertNotIn("note", d)


class BlockedIsNotPrOpen(unittest.TestCase):
    """`blocked` is a real status, not a `pr-open` row nobody polls twice.

    Rows waiting on a human ruling cannot advance however often they are
    checked. Filed as pr-open they spend a poll every tick and the POLL line
    overstates what is in flight. Blocked rows are counted, never served as a
    stage, and never polled.
    """

    BLOCKED = ROWS + [
        {"finding": "Purge job under the wipe role", "source": "GitHub #1447",
         "status": "blocked", "priority": "high"},
        {"finding": "Grace-lane reactivation mechanics", "source": "GitHub #1448",
         "status": "blocked", "priority": "high"},
    ]

    def test_blocked_rows_are_reported_on_their_own_line(self):
        d = lines(lnp.pick(self.BLOCKED, ""))
        self.assertEqual(d["blocked"], "2")

    def test_blocked_rows_never_inflate_the_pr_open_poll(self):
        before = lines(lnp.pick(ROWS, ""))["counts"].split("|")[0]
        after = lines(lnp.pick(self.BLOCKED, ""))["counts"].split("|")[0]
        self.assertEqual(before, after, "blocked rows must not be counted as pr-open")

    def test_blocked_rows_are_never_dispatched_as_a_target(self):
        out = lnp.pick(self.BLOCKED, "")
        self.assertNotIn("target:blocked", out)
        for ln in out.split("\n"):
            self.assertNotIn("Grace-lane reactivation", ln.replace("blocked\t2", ""))

    def test_no_blocked_line_when_there_are_none(self):
        d = lines(lnp.pick(ROWS, ""))
        self.assertNotIn("blocked", d)


if __name__ == "__main__":
    unittest.main()
