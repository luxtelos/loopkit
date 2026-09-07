#!/usr/bin/env python3
"""inbox_to_triage.py — put the inbox's findings on the board.

    python3 scripts/inbox_to_triage.py            # dry run: says what it would add
    python3 scripts/inbox_to_triage.py --apply    # adds the rows

(Underscored, not dashed like loop-scan.py: the test imports it as a module,
and a dash is not a legal identifier.)

WHY. Measured 2026-09-06: `inbox/needs-human.md` held 66 findings and
`state/triage.md` knew about 2. Nothing bridged them — `morning-triage.sh`
mentions the inbox only in a comment and discovers from CODE, so anything
written down as prose was invisible to the loop that was meant to work it. The
loop then reported itself idle in front of sixty-four open findings. That is a
retrieval failure, not a shortage of work, and it is the "no consolidation
strategy" anti-pattern: memory that only grows and is never routed back.

WHY IT IS A SCRIPT. "Which inbox headings have no triage row?" is a set
difference over two files — a lookup, not a judgement. A model re-deriving it
gets a different answer each time and nobody can check the result. Same reason
loop-next.sh does not let the agent choose its own stage.

CREATE-ONLY, DELIBERATELY. `triage_state.upsert_row` overwrites `status`
without asking, so a bridge built on it would reset every advanced row back to
`new` on each run and silently undo the pipeline. This one checks for an
existing row by source and skips it — the row's status belongs to the loop, not
to the inbox. Pinned by `test_rerunning_never_resets_an_advanced_row`.

WHAT IT DOES NOT DO. It does not classify, prioritise beyond a default, or
decide whether a finding is real. Rows land at `new`, which is precisely the
stage whose job is to answer those questions (loop-assess Mode A). Turning a
finding into a task is the lookup; deciding what the task IS remains work.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "triage_state", os.path.join(HERE, "triage_state.py")
)
ts = importlib.util.module_from_spec(_spec)
# Register BEFORE exec: triage_state defines a @dataclass, and dataclasses
# resolves annotations through sys.modules[cls.__module__]. Without this line
# it raises "'NoneType' object has no attribute '__dict__'" at import time —
# a failure that looks like a bug in the dataclass rather than in the loader.
sys.modules["triage_state"] = ts
_spec.loader.exec_module(ts)

DEFAULT_INBOX = "inbox/needs-human.md"
DEFAULT_STATE = "state/triage.md"

# Level-2 headings only. A `###` is a subsection of a finding, not a finding —
# promoting one would file the same work twice under two names.
HEADING = re.compile(r"^##\s+(?!#)(.+?)\s*$", re.M)

# A heading that announces its own outcome is a record, not a task.
#
# Two signals, both matching how this repo actually writes them:
#   ~~struck through~~   — the markdown convention for a retired item
#   an UPPERCASE marker  — "RESOLVED 2026-09-04 — …", anywhere in the heading
#
# Uppercase is load-bearing. An earlier cut lower-cased the heading and only
# looked at its first 48 characters, which missed
# "~~Two constitution SHALLs…~~ — RESOLVED 2026-08-31" (the marker sits past the
# window) and would equally have closed a live finding whose title merely said
# "…before it can be resolved". Requiring the shouted form separates a status
# stamp from ordinary prose, and the repo writes stamps in caps.
#
# The asymmetry is deliberate. A false CLOSED silently keeps real work off the
# board — the exact bug this script exists to fix. A false OPEN just files a row
# that loop-assess closes in one cheap pass. So this errs toward filing.
CLOSED_MARKERS = (
    "RESOLVED", "CLOSED", "ANSWERED", "SUPERSEDED", "WITHDRAWN", "DONE",
    # Added 2026-09-06 after the first live tick served seven July STATUS
    # REPORTS as work — "sprint-0 COMPLETE", "spec v3 MERGED", "v3 SHIPPED".
    # A heading announcing that something finished is a record of the past,
    # not a task. Uppercase whole-word, same reasoning as the rest.
    "COMPLETE", "COMPLETED", "MERGED", "SHIPPED", "LANDED",
)
STRUCK = re.compile(r"~~.+?~~")
# A bracketed tag at the START is this repo's other stamp style:
# "[superseded] 2026-07-23 (EOD) — …". Lowercase inside brackets is fine
# here because the brackets themselves are the signal.
TAGGED_CLOSED = re.compile(r"^\s*\[(superseded|resolved|closed|done|withdrawn)\]", re.I)


def is_closed(heading: str) -> bool:
    if STRUCK.search(heading) or TAGGED_CLOSED.search(heading):
        return True
    return any(re.search(rf"\b{m}\b", heading) for m in CLOSED_MARKERS)


def source_for(heading: str) -> str:
    """Stable key. Unstable keys duplicate forever, since upsert keys on source.

    Truncated because the source column is read in a terminal, but truncated at
    a fixed width rather than at a word boundary — a boundary moves when the
    heading is edited, and a key that moves is a new row.

    The trailing .strip() is not tidiness, it is the whole contract. Cutting at
    a fixed width lands on a space roughly one time in six; `triage_state`
    normalises cells on write and drops it, so the key READ BACK differs from
    the key COMPUTED, no row ever matches, and the finding is re-filed on every
    run. Four rows did exactly that on 2026-09-06 before this line existed —
    and the "sources are stable" test missed it because its fixture headings
    were shorter than the cut. Any key written through triage_state must be
    normalised the same way triage_state will normalise it.
    """
    flat = " ".join(heading.split())
    return f"{DEFAULT_INBOX} § {flat[:72].strip()}"


def bridge(
    inbox: Path, state: Path, *, apply: bool = False, priority: str = "medium"
) -> list[tuple[str, str]]:
    """Return the (finding, source) pairs missing from the board; add them if apply."""
    try:
        text = Path(inbox).read_text()
    except OSError:
        # A missing inbox is an empty inbox, not a crash. This runs inside a
        # tick and must never be the reason a tick fails.
        return []

    ts.ensure_schema(Path(state))
    existing = {r["source"] for r in ts.parse_table(Path(state)).rows}

    created: list[tuple[str, str]] = []
    for heading in HEADING.findall(text):
        if is_closed(heading):
            continue
        source = source_for(heading)
        if source in existing:
            continue
        created.append((heading, source))
        existing.add(source)
        if apply:
            ts.upsert_row(
                Path(state),
                finding=heading,
                source=source,
                priority=priority,
                spec="",
                status="new",
            )
    return created


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inbox", default=DEFAULT_INBOX)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--priority", default="medium")
    ap.add_argument(
        "--apply", action="store_true", help="write the rows (default is a dry run)"
    )
    args = ap.parse_args()

    created = bridge(
        Path(args.inbox), Path(args.state), apply=args.apply, priority=args.priority
    )
    verb = "ADDED" if args.apply else "WOULD ADD"
    for finding, _ in created:
        print(f"{verb}\t{finding}")
    print(f"VERDICT: {len(created)} finding(s) not on the board" + ("" if args.apply else " — dry run, nothing written"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
