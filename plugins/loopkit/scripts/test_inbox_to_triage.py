#!/usr/bin/env python3
"""Pins for inbox_to_triage.py — the bridge from findings to the work queue.

WHY THIS EXISTS. Measured 2026-09-06: `inbox/needs-human.md` held 66 findings
and `state/triage.md` knew about 2. Nothing converted one into the other —
`morning-triage.sh` mentions the inbox only in a comment, and discovers from
code. So the loop reported itself idle while sixty-four findings sat in a file
it never read. That is a retrieval failure, not a shortage of work.

THE CONTROL CASE, and the reason this script is create-only: `triage_state.upsert_row`
overwrites `status` unconditionally. A bridge built on it would reset every row
the loop had advanced back to `new` on each run, silently undoing the pipeline.
The first test below is the one that matters.
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec because triage_state defines a @dataclass, and
    # dataclasses resolves annotations via sys.modules[cls.__module__].
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


i2t = _load("i2t", "inbox_to_triage.py")
ts = _load("ts", "triage_state.py")

INBOX = """# needs-human.md

Preamble prose that is not a finding.

## The regression gate is red for 19 pre-existing failures

Body text.

## RESOLVED 2026-09-04 — Post-payment landing: /main or /professional-dashboard?

Already decided; must not become work.

## Flexible pricing needs an ADR before any code

Body.

### A sub-heading is not a finding

Body.
"""


class Bridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.tmp.name) / "needs-human.md"
        self.inbox.write_text(INBOX)
        self.state = Path(self.tmp.name) / "triage.md"
        ts.ensure_schema(self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        return ts.parse_table(self.state).rows

    def test_creates_one_row_per_unresolved_heading(self):
        i2t.bridge(self.inbox, self.state, apply=True)
        findings = [r["finding"] for r in self.rows()]
        self.assertEqual(len(findings), 2)
        self.assertTrue(any("regression gate is red" in f for f in findings))
        self.assertTrue(any("Flexible pricing" in f for f in findings))

    def test_resolved_headings_never_become_work(self):
        i2t.bridge(self.inbox, self.state, apply=True)
        self.assertFalse(
            any("Post-payment landing" in r["finding"] for r in self.rows()),
            "a heading marked RESOLVED is a record, not a task",
        )

    def test_struck_through_heading_is_closed_even_when_the_marker_is_late(self):
        """The real inbox's first line, which the first cut of this got wrong.

        A 48-character lower-cased window missed
        "~~Two constitution SHALLs…~~ — RESOLVED 2026-08-31" because the marker
        sits past the window. Strikethrough alone is now sufficient.
        """
        self.assertTrue(
            i2t.is_closed("~~Two constitution SHALLs fire on undefined conditions~~ — RESOLVED 2026-08-31")
        )

    def test_lowercase_prose_about_resolving_stays_OPEN(self):
        """The other half: a live finding must not be closed by its own wording.

        Erring toward filing is deliberate — a false CLOSED keeps real work off
        the board, which is the bug this script exists to fix; a false OPEN
        costs one cheap loop-assess pass.
        """
        self.assertFalse(
            i2t.is_closed("Grace-lane reactivation — needs a ruling before it can be resolved")
        )

    def test_status_reports_are_records_not_work(self):
        """The first live tick served these seven as `new`. They are the past."""
        for h in (
            "2026-07-09 — Epic #419 Payments sprint-0 COMPLETE: PRs #1575 + #1576 open",
            "2026-07-23 — Epic #419 spec arc COMPLETE: v3 MERGED (PR #1796 → payments fd735228)",
            "[superseded] 2026-07-23 (EOD) — Epic #419 spec v3 SHIPPED: PR #1796 open for review",
            "[superseded] 2026-07-23 (EOD) — Epic #419 spec v3: ONE action pending (owner ratification)",
        ):
            self.assertTrue(i2t.is_closed(h), h)

    def test_lowercase_complete_in_prose_stays_open(self):
        # "…needs to be complete before…" is a live requirement, not a stamp.
        self.assertFalse(i2t.is_closed("Renewal reminder must be complete before the cron exists"))

    def test_uppercase_marker_anywhere_closes(self):
        self.assertTrue(i2t.is_closed("2026-09-04 — RESOLVED: post-payment landing"))
        self.assertTrue(i2t.is_closed("Wipe notice — SUPERSEDED by ADR-064"))

    def test_sub_headings_are_not_findings(self):
        i2t.bridge(self.inbox, self.state, apply=True)
        self.assertFalse(any("sub-heading" in r["finding"] for r in self.rows()))

    def test_rerunning_never_resets_an_advanced_row(self):
        """THE CONTROL CASE. upsert_row overwrites status; this must not."""
        i2t.bridge(self.inbox, self.state, apply=True)
        row = self.rows()[0]
        ts.update_row(
            self.state,
            source=row["source"],
            finding=None,
            priority=None,
            spec=None,
            status="pr-open",
        )
        i2t.bridge(self.inbox, self.state, apply=True)
        after = ts.find_row(self.rows(), row["source"])
        self.assertEqual(
            after["status"], "pr-open",
            "the bridge must create only — re-running it must never undo the pipeline",
        )

    def test_rerunning_creates_no_duplicates(self):
        i2t.bridge(self.inbox, self.state, apply=True)
        first = len(self.rows())
        i2t.bridge(self.inbox, self.state, apply=True)
        self.assertEqual(len(self.rows()), first)

    def test_dry_run_is_the_default_and_writes_nothing(self):
        created = i2t.bridge(self.inbox, self.state)
        self.assertEqual(len(created), 2, "a dry run still REPORTS what it would create")
        self.assertEqual(self.rows(), [], "and writes nothing")

    def test_source_is_stable_across_runs(self):
        """Sources key the upsert, so an unstable key would duplicate forever."""
        a = i2t.bridge(self.inbox, self.state)
        b = i2t.bridge(self.inbox, self.state)
        self.assertEqual([s for _, s in a], [s for _, s in b])

    def test_a_heading_that_truncates_onto_a_SPACE_is_still_idempotent(self):
        """The bug the test above was too short to catch.

        source_for cuts at a fixed 72 characters. When the cut lands on a space,
        triage_state strips it on write, so the key read back differs from the
        key computed and the row is re-filed on every run. Four real rows did
        this on 2026-09-06. The fixture heading below is built so character 73
        is a space.
        """
        head = "2026-07-02 — PR #1299 conflicts resolved and three rulings are"
        # pad so the 72-char cut falls exactly on whitespace
        head = head + " " * max(1, 73 - len(head)) + "needed now"
        self.assertEqual(head[72], " ", "fixture must exercise the space-at-cut case")

        inbox = Path(self.tmp.name) / "spacecut.md"
        inbox.write_text(f"# x\n\n## {head}\n\nbody\n")
        i2t.bridge(inbox, self.state, apply=True)
        before = len(self.rows())
        i2t.bridge(inbox, self.state, apply=True)
        self.assertEqual(len(self.rows()), before, "re-running must add nothing")

    def test_written_source_round_trips_through_triage_state(self):
        """Directly: the key we write must equal the key we read back."""
        heading = "A" * 40 + " " + "B" * 60
        src = i2t.source_for(heading)
        self.assertEqual(src, ts.normalize_cell(src))

    def test_the_board_is_excluded_from_prettier(self):
        """A formatter must never rewrite the key column.

        lint-staged runs `prettier --write` on every *.md at commit time, and
        prettier's table formatting strips spaces around inline code in cells
        ("on `dev` only" → "on`dev`only"). Found 2026-09-06: seven cells
        damaged, two keys no longer matching, the bridge re-filing them every
        run. The fix is an ignore entry; this pins that it stays.
        """
        # In the plugin the ignore entry is laid down by loopkit-init.sh, so
        # the pin is: a fresh init produces it, and a second init keeps it once.
        import subprocess
        with tempfile.TemporaryDirectory() as proj:
            init = os.path.join(HERE, "loopkit-init.sh")
            for _ in range(2):
                subprocess.run(["bash", init, "--project", proj], check=True,
                               capture_output=True, text=True)
            ignore = Path(proj) / ".prettierignore"
            self.assertTrue(ignore.exists(), ".prettierignore is missing after init")
            entries = [
                ln.strip() for ln in ignore.read_text().splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")
            ]
            self.assertEqual(entries.count("state/triage.md"), 1)

    def test_a_missing_inbox_is_not_a_crash(self):
        created = i2t.bridge(Path(self.tmp.name) / "nope.md", self.state)
        self.assertEqual(created, [])


if __name__ == "__main__":
    unittest.main()
