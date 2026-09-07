#!/usr/bin/env python3
"""Deterministic helpers for Loop Kit triage state."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


COLUMNS = ["finding", "source", "priority", "spec", "status"]
VALID_STATUSES = {
    "new",
    "spec-draft",
    "spec-ready",
    "fixing",
    "pr-open",
    # Waiting on a decision only a human can make. Distinct from `inbox`
    # (which means "routed to a human, off the pipeline") and emphatically
    # distinct from `pr-open`: on 2026-09-06 two rows sat at pr-open reading
    # "BLOCKED on owner ruling", so every tick spent a cheap poll on rows that
    # could not advance, and the POLL line overstated what was in flight.
    # A blocked row is counted and shown, never polled, never served as a stage.
    "blocked",
    "inbox",
    "done",
}
DEFAULT_PREFIX = """# triage.md — the work queue

Items land here after discovery. The loop only implements unattended work when a
spec exists and the row is marked `spec-ready`. Status values:
`new, spec-draft, spec-ready, fixing, pr-open, inbox, done`.

"""


@dataclass
class TriageTable:
    prefix: str
    rows: list[dict[str, str]]
    suffix: str


# The headers a queue file may legitimately carry. Kept module-level because
# three separate places need to recognise a header line, and a second copy is a
# second place to forget a column.
ACCEPTED_HEADERS: tuple[tuple[str, ...], ...] = (
    ("finding", "source", "priority", "status"),
    ("finding", "source", "priority", "spec", "status"),
)

# A row of the narrowest accepted table has this many column separators, so a
# line with at least this many unescaped pipes is table-shaped even without the
# optional outer pipes that GitHub-flavoured markdown does not require.
MIN_TABLE_PIPES = min(len(h) for h in ACCEPTED_HEADERS) - 1

_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_FENCE = re.compile(r"^\s*(?:```|~~~)")


def normalize_cell(value: str) -> str:
    return " ".join(value.strip().split())


def is_separator_row(stripped: str) -> bool:
    """`|---|---|` in any of its spellings, including the padded ones."""
    return bool(stripped) and set(stripped) <= set("-:| ")


def is_table_shaped(stripped: str) -> bool:
    """Would a markdown reader take this line for part of a table?

    GitHub-flavoured markdown does NOT require the outer pipes, so
    `a thing | src/x | high |  | new` is a perfectly legal row. The old row scan
    tested `startswith("|")` and therefore read such rows as prose — shape C of
    the 2026-09-07 review, where two real rows parsed as an empty queue.
    """
    if not stripped:
        return False
    if stripped.startswith("|"):
        return True
    return len(_UNESCAPED_PIPE.findall(stripped)) >= MIN_TABLE_PIPES


def is_blank_row(stripped: str) -> bool:
    return all(not cell.strip() for cell in split_markdown_row(stripped))


def header_cells_if_header(stripped: str) -> list[str] | None:
    """The header's cells if this line IS a header, else None. Structural, not
    a string compare: state/triage.md pads its header for column alignment."""
    cells = [cell.strip().lower() for cell in split_markdown_row(stripped)]
    return cells if any(cells == list(h) for h in ACCEPTED_HEADERS) else None


def fenced_flags(lines: list[str]) -> list[bool]:
    """True for every line inside (or opening/closing) a fenced code block.

    A triage.md that SHOWS a specimen table in a fence — GETTING-STARTED does
    exactly this — must not have its specimen counted as unread work.
    """
    flags: list[bool] = []
    inside = False
    for line in lines:
        if _FENCE.match(line):
            flags.append(True)
            inside = not inside
            continue
        flags.append(inside)
    return flags


def split_markdown_row(line: str) -> list[str]:
    # Split on pipes that are NOT escaped as "\|", then turn the escaped pipes
    # back into real ones. Without this, a finding text that contains a literal
    # "|" (e.g. a GitHub title "fix A | B") would explode into extra columns,
    # the row would fail the column-count check, and it would be silently
    # dropped — then re-ingested as a duplicate forever.
    inner = line.strip().strip("|")
    parts = re.split(r"(?<!\\)\|", inner)
    return [part.strip().replace("\\|", "|") for part in parts]


def format_row(row: dict[str, str]) -> str:
    # Escape any literal pipe in a cell so it can't be mistaken for a column
    # separator on the next parse. Pairs with the unescaping in split_markdown_row.
    values = [row.get(column, "").strip().replace("|", "\\|") for column in COLUMNS]
    return "| " + " | ".join(values) + " |"


def validate_status(status: str) -> None:
    if status and status not in VALID_STATUSES:
        raise ValueError(
            f"invalid status '{status}'; expected one of: {', '.join(sorted(VALID_STATUSES))}",
        )


def parse_table(path: Path) -> TriageTable:
    """Read the queue, or REFUSE — but never quietly return fewer rows than the
    file holds.

    THE INVARIANT, and it is the whole design: if the file plainly contains N
    table rows and this function can only turn M < N of them into rows, it
    raises. The cause does not matter and is deliberately not enumerated,
    because enumerating causes is exactly how this bug kept coming back.

    Three visits now. 2026-08-31: a padded header failed an exact string
    compare, so 40 rows read as an empty queue. 2026-09-07 (a): an unknown or
    renamed column did the same, and the fix guarded only the case where NO
    header was found. 2026-09-07 (b): a review built nine shapes and four still
    read as empty — rows narrower or wider than the header, rows written
    without the optional outer pipes, a blank line after the separator — plus a
    duplicated header that lost one row of two. Every one of them was a
    different CAUSE reaching the same EFFECT, and each fix named the cause. So
    this one names the effect instead: short read, therefore refuse.

    A loop that reads its own memory as empty reports itself idle and nobody
    notices, which is why this is the one place in the parser that raises
    rather than degrading. Degrading is a lie the caller cannot detect.
    """
    if not path.exists():
        return TriageTable(prefix=DEFAULT_PREFIX, rows=[], suffix="")

    lines = path.read_text(encoding="utf-8").splitlines()
    fenced = fenced_flags(lines)
    accepted_display = [" | ".join(h) for h in ACCEPTED_HEADERS]

    def is_data_line(index: int) -> bool:
        """A line that carries queue data — not a separator, not a header, not
        an empty row, not documentation inside a code fence."""
        stripped = lines[index].strip()
        if fenced[index] or not is_table_shaped(stripped):
            return False
        if is_separator_row(stripped) or is_blank_row(stripped):
            return False
        return header_cells_if_header(stripped) is None

    # Match the header STRUCTURALLY, by its cells, not by an exact string:
    # state/triage.md pads its header for column alignment (the 2026-08-31 bug).
    header_indices = [
        i for i, line in enumerate(lines)
        if not fenced[i] and header_cells_if_header(line.strip()) is not None
    ]

    if len(header_indices) > 1:
        # Shape D of the review. The old scan took the FIRST header and stopped
        # reading at the blank line above the second one, so a file holding two
        # rows returned one — a partial loss, which is worse than an empty read
        # because the count still looks plausible.
        raise ValueError(
            f"{path}: {len(header_indices)} header rows found, at lines "
            f"{', '.join(str(i + 1) for i in header_indices)}. A queue file has "
            f"exactly one table; with two, every row under the second header is "
            f"silently dropped. Merge them into one table, or move the extra one "
            f"out of this file."
        )

    header_index = header_indices[0] if header_indices else None

    if header_index is None:
        candidates = [i for i in range(len(lines))
                      if not fenced[i] and is_table_shaped(lines[i].strip())
                      and not is_separator_row(lines[i].strip())
                      and not is_blank_row(lines[i].strip())]
        if len(candidates) > 1:  # an unrecognised header line plus data
            raise ValueError(
                f"{path}: no recognisable header, but the file holds "
                f"{len(candidates) - 1} row-shaped line(s). An unknown, renamed "
                f"or reordered column is the usual cause. Accepted headers: "
                f"{accepted_display}. Refusing to report an empty queue — a loop "
                f"that reads its own memory as empty reports itself idle and "
                f"nobody notices."
            )
        prefix = path.read_text(encoding="utf-8")
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        return TriageTable(prefix=prefix or DEFAULT_PREFIX, rows=[], suffix="")

    prefix = "\n".join(lines[:header_index])
    if prefix:
        prefix += "\n"

    header_cells = split_markdown_row(lines[header_index])

    # THE TABLE REGION. Everything from the separator down to the first line
    # that is neither blank nor table-shaped. Blank lines are CROSSED rather
    # than treated as the end: the old scan stopped at the first line without a
    # leading `|`, so a blank line immediately after the separator (shape G)
    # ended it before a single row was read, and rows written without outer
    # pipes (shape C) were never in it at all.
    region_end = header_index + 1
    while region_end < len(lines):
        stripped = lines[region_end].strip()
        if stripped and not is_table_shaped(stripped):
            break
        region_end += 1
    # Trailing blanks belong to the suffix, not the table.
    while region_end > header_index + 1 and not lines[region_end - 1].strip():
        region_end -= 1

    data_indices = [i for i in range(header_index + 1, region_end) if is_data_line(i)]

    rows: list[dict[str, str]] = []
    dropped: list[str] = []
    for index in data_indices:
        cells = split_markdown_row(lines[index])
        if len(cells) != len(header_cells):
            dropped.append(
                f"line {index + 1}: {len(cells)} cells against a "
                f"{len(header_cells)}-column header"
            )
            continue
        raw_row = dict(zip(header_cells, cells, strict=True))
        row = {column: "" for column in COLUMNS}
        row["finding"] = normalize_cell(raw_row.get("finding", ""))
        row["source"] = normalize_cell(raw_row.get("source", ""))
        row["priority"] = normalize_cell(raw_row.get("priority", ""))
        row["spec"] = normalize_cell(raw_row.get("spec", ""))
        row["status"] = normalize_cell(raw_row.get("status", ""))
        validate_status(row["status"])
        if row["finding"] or row["source"]:
            rows.append(row)
        else:
            dropped.append(f"line {index + 1}: neither finding nor source is set")

    # THE INVARIANT. Not "did a known bad shape occur" but "did anything the
    # file plainly holds fail to come out". Anything that lands in `dropped`
    # after this point — including a cause nobody has met yet — is refused.
    if len(rows) < len(data_indices):
        detail = "; ".join(dropped[:5]) or "cause not identified"
        raise ValueError(
            f"{path}: the table holds {len(data_indices)} data row(s) but only "
            f"{len(rows)} parsed. {detail}. Header is "
            f"'{' | '.join(header_cells)}'; accepted headers: {accepted_display}. "
            f"Refusing a short read — a queue that reports fewer rows than it "
            f"holds is not a degraded read, it is a lie the caller cannot detect."
        )

    suffix = "\n".join(lines[region_end:])
    if suffix:
        suffix = "\n" + suffix
    return TriageTable(prefix=prefix or DEFAULT_PREFIX, rows=rows, suffix=suffix)


def write_table(path: Path, table: TriageTable) -> None:
    header = "| finding | source | priority | spec | status |"
    separator = "|---|---|---|---|---|"
    body = [format_row(row) for row in table.rows]
    parts = [table.prefix.rstrip("\n"), header, separator, *body]
    text = "\n".join(part for part in parts if part != "")
    if table.suffix:
        text += table.suffix
    if not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ensure_schema(path: Path) -> None:
    table = parse_table(path)
    write_table(path, table)


# ---------------------------------------------------------------------------
# THE pr-open GATE
#
# The doctrine says "a row reaches pr-open only on a recorded PASS from a
# different agent". Until 2026-09-07 that sentence was prose and nothing else:
# `triage_state.py update --status pr-open` succeeded with no verdict recorded
# anywhere, so the rule could be violated by the very tool that records rows.
# A rule the recorder cannot enforce is a rule the recorder will be blamed for.
#
# WHERE A VERDICT LIVES: state/ticks.jsonl, the append-only ledger this module
# already writes transitions to. No new file, no new format, and loop-metrics.py
# can already read it. A verdict is one line:
#   {"event":"verdict","source":"<row source>","result":"PASS","by":"<agent>"}
#
# HOW IT IS FOUND: the LAST verdict line for that source wins. Last and not
# any, so a PASS followed by a FAIL closes the door again rather than leaving
# it propped open by history.
#
# WHAT IS ENFORCED, exactly, and no more than this:
#   - a verdict exists for the source                     (always)
#   - the latest one is a PASS                            (always)
#   - it names who gave it                                (always)
#   - that name differs from the agent that did the work  (only when both are
#     known, i.e. when LOOPKIT_AGENT was set on both runs)
# The last one is the honest limit. With LOOPKIT_AGENT unset — which is the
# default — this gate cannot tell one agent from another and does not pretend
# to. It stops "no verdict at all", which is the failure the owner actually hit.
# ---------------------------------------------------------------------------

VERDICT_EVENT = "verdict"
GATED_STATUSES = {"pr-open"}


def project_root_for(state: Path) -> Path:
    """The project root implied by a state file path. Identical to the rule
    _record_transition writes with, so the reader and the writer cannot drift."""
    resolved = state.resolve()
    return resolved.parent.parent if resolved.parent.name == "state" else resolved.parent


def _driver_lock_path() -> Path | None:
    """Locate `driver_lock.py`, which lives in `scripts/` beside this package.

    Relocation hazard, and the reason this is a named function with a pin
    (`tests/pins/queue-lock-held.py`): `write_lock` used to load the helper from
    its OWN directory. That was correct while this module lived in `scripts/`.
    After M2 moved it into `loopkit_core/`, the same expression resolves to
    `loopkit_core/driver_lock.py`, which does not exist — and because the caller
    fails open, the lock would have degraded to a no-op that still returns a
    perfectly good context manager. A guard that silently stops guarding is
    worse than no guard, so the path is resolved explicitly and pinned.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "scripts" / "driver_lock.py", here / "driver_lock.py"):
        if candidate.is_file():
            return candidate
    return None


def write_lock(state: Path):
    """The driver lock, around a read-modify-write of the queue.

    Every mutating command here is parse-then-write: two drivers interleaving
    between the parse and the write lose one of the two edits silently, and the
    "state file is the lock" guard rail that was supposed to prevent it enforced
    nothing (2026-09-07). Re-entrant, so a driver that already holds the lock —
    loop-commit.sh, say — does not deadlock against itself.

    The root is per worktree, because the git index is per worktree; that is
    what `project_root_for` computes, and it is deliberately the same rule the
    ledger writer uses so the two cannot drift.

    Fail-open: if the helper cannot be loaded at all, an unlocked write is still
    better than a triage command that refuses to run.
    """
    try:
        import importlib.util
        path = _driver_lock_path()
        if path is None:
            return contextlib.nullcontext()
        spec = importlib.util.spec_from_file_location("driver_lock", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.held(project_root_for(state), label="triage_state")
    except Exception:
        return contextlib.nullcontext()


def ledger_path(state: Path) -> Path:
    return project_root_for(state) / "state" / "ticks.jsonl"


def read_events(state: Path) -> list[dict]:
    """Every parseable line of the ledger. A corrupt line is skipped, not fatal:
    the ledger is append-only and fail-open on write, so a torn last line is a
    normal thing to meet and never a reason to block the pipeline."""
    path = ledger_path(state)
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            events.append(row)
    return events


def latest_verdict(state: Path, source: str) -> dict | None:
    wanted = normalize_cell(source)
    hits = [
        e for e in read_events(state)
        if e.get("event") == VERDICT_EVENT
        and normalize_cell(str(e.get("source", ""))) == wanted
    ]
    return hits[-1] if hits else None


def last_worker(state: Path, source: str) -> str:
    """Who last moved this row to `fixing` — the agent that wrote the code."""
    wanted = normalize_cell(source)
    for event in reversed(read_events(state)):
        if (event.get("event") == "transition"
                and normalize_cell(str(event.get("source", ""))) == wanted
                and event.get("status") == "fixing"):
            return normalize_cell(str(event.get("by", "")))
    return ""


def record_verdict(
    state: Path,
    *,
    source: str,
    result: str,
    by: str,
    evidence: str = "",
) -> dict:
    result = result.strip().upper()
    if result not in {"PASS", "FAIL", "BLOCKED"}:
        raise ValueError(f"verdict result must be PASS, FAIL or BLOCKED, not '{result}'")
    if not normalize_cell(by):
        raise ValueError("a verdict must name who gave it: pass --by <agent>")
    return _append_event(
        state,
        VERDICT_EVENT,
        source=normalize_cell(source),
        result=result,
        by=normalize_cell(by),
        evidence=normalize_cell(evidence) or None,
    )


def check_gate(state: Path, source: str, status: str, override: str | None) -> str:
    """Empty string when the transition is allowed, having possibly recorded an
    override. Raises ValueError when it is refused."""
    if normalize_cell(status or "") not in GATED_STATUSES:
        return ""

    if override is not None:
        if not normalize_cell(override):
            raise ValueError(
                "--override-verdict needs a reason. An override with no reason is "
                "not a decision, it is the gate switched off."
            )
        _append_event(state, "verdict-override", source=normalize_cell(source),
                      status=status, reason=normalize_cell(override),
                      by=os.environ.get("LOOPKIT_AGENT") or None)
        return (
            f"OVERRIDE: '{source}' moved to {status} with NO reviewer PASS on record.\n"
            f"  reason: {normalize_cell(override)}\n"
            f"  recorded in {ledger_path(state)} as a verdict-override event."
        )

    verdict = latest_verdict(state, source)
    how = (
        f"Record one with:\n"
        f"  triage_state.py verdict --state {state} --source \"{source}\" "
        f"--result PASS --by <reviewing agent> --evidence \"<what proved it>\"\n"
        f"or, if a human is deciding otherwise, say so where it can be read later:\n"
        f"  triage_state.py update ... --status {status} --override-verdict \"<reason>\""
    )
    if verdict is None:
        raise ValueError(
            f"refusing to set '{source}' to {status}: no verdict is recorded in "
            f"{ledger_path(state)}. Opening a pull request was never the "
            f"transition — a reviewed one is. {how}"
        )

    result = str(verdict.get("result", "")).upper()
    if result != "PASS":
        raise ValueError(
            f"refusing to set '{source}' to {status}: the latest recorded verdict "
            f"is {result or 'unreadable'}, not PASS. Fix the finding and have it "
            f"re-reviewed. {how}"
        )

    reviewer = normalize_cell(str(verdict.get("by", "")))
    if not reviewer:
        raise ValueError(
            f"refusing to set '{source}' to {status}: the recorded PASS does not "
            f"name who gave it, so it cannot be shown to come from a different "
            f"agent than the one that wrote the code. {how}"
        )

    worker = last_worker(state, source)
    if worker and worker == reviewer:
        raise ValueError(
            f"refusing to set '{source}' to {status}: '{reviewer}' both did the "
            f"work and passed it. The agent that wrote the code is never the "
            f"agent that approves it. Hand it to a reviewer agent. {how}"
        )
    return ""


def find_row(rows: list[dict[str, str]], source: str) -> dict[str, str] | None:
    for row in rows:
        if row["source"] == source:
            return row
    return None


def upsert_row(
    path: Path,
    *,
    finding: str,
    source: str,
    priority: str,
    spec: str,
    status: str,
) -> bool:
    validate_status(status)
    table = parse_table(path)
    row = find_row(table.rows, source)
    created = row is None
    if row is None:
        row = {column: "" for column in COLUMNS}
        table.rows.append(row)
    row.update(
        {
            "finding": normalize_cell(finding),
            "source": normalize_cell(source),
            "priority": normalize_cell(priority),
            "spec": normalize_cell(spec),
            "status": normalize_cell(status),
        },
    )
    write_table(path, table)
    return created


def update_row(
    path: Path,
    *,
    source: str,
    finding: str | None,
    priority: str | None,
    spec: str | None,
    status: str | None,
) -> None:
    if status is not None:
        validate_status(status)
    table = parse_table(path)
    row = find_row(table.rows, normalize_cell(source))
    if row is None:
        raise ValueError(f"source not found: {source}")

    if finding is not None:
        row["finding"] = normalize_cell(finding)
    if priority is not None:
        row["priority"] = normalize_cell(priority)
    if spec is not None:
        row["spec"] = normalize_cell(spec)
    if status is not None:
        row["status"] = normalize_cell(status)

    write_table(path, table)


def iter_rows(
    path: Path,
    *,
    statuses: Iterable[str] | None,
) -> list[dict[str, str]]:
    table = parse_table(path)
    wanted = set(statuses or [])
    if not wanted:
        return table.rows
    return [row for row in table.rows if row["status"] in wanted]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure_parser = subparsers.add_parser("ensure-schema")
    ensure_parser.add_argument("--state", required=True)

    upsert_parser = subparsers.add_parser("upsert")
    upsert_parser.add_argument("--state", required=True)
    upsert_parser.add_argument("--finding", required=True)
    upsert_parser.add_argument("--source", required=True)
    upsert_parser.add_argument("--priority", required=True)
    upsert_parser.add_argument("--spec", default="")
    upsert_parser.add_argument("--status", default="new")
    upsert_parser.add_argument("--override-verdict", default=None, metavar="REASON")

    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("--state", required=True)
    update_parser.add_argument("--source", required=True)
    update_parser.add_argument("--finding")
    update_parser.add_argument("--priority")
    update_parser.add_argument("--spec")
    update_parser.add_argument("--status")
    update_parser.add_argument(
        "--override-verdict", default=None, metavar="REASON",
        help="move to pr-open without a recorded PASS. The reason is written to "
             "state/ticks.jsonl as a verdict-override event and printed loudly. "
             "For a human who has decided otherwise — not for an agent in a hurry.",
    )

    verdict_parser = subparsers.add_parser(
        "verdict", help="record a review verdict for a row in state/ticks.jsonl")
    verdict_parser.add_argument("--state", required=True)
    verdict_parser.add_argument("--source", required=True)
    verdict_parser.add_argument("--result", required=True, choices=["PASS", "FAIL", "BLOCKED"])
    verdict_parser.add_argument("--by", required=True, help="the reviewing agent")
    verdict_parser.add_argument("--evidence", default="", help="what proved it")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--state", required=True)
    list_parser.add_argument("--status", action="append", default=[])
    list_parser.add_argument("--format", choices=["tsv", "json", "usv"], default="tsv")

    return parser


def _append_event(state: Path, event: str, **fields) -> dict:
    """Append one line to state/ticks.jsonl beside the state file.

    Fail-open on WRITE, on purpose: a ledger that cannot be written must never
    change a verdict or block a transition. Note the asymmetry with reading —
    check_gate treats a missing verdict as a refusal. That is deliberate. A
    write we could not make is the ledger's problem; a PASS we cannot find is
    the pipeline's.
    """
    try:
        import importlib.util
        here = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location("ticks", here / "ticks.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.append(project_root_for(state), event, **fields)
    except Exception:
        return {}


def _record_transition(state: Path, source: str, status: str | None) -> None:
    if not status:
        return
    # `by` is what makes the different-agent half of the rule checkable later.
    # Unset by default, and check_gate says so rather than pretending.
    _append_event(state, "transition", source=source, status=status,
                  by=os.environ.get("LOOPKIT_AGENT") or None)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    state_path = Path(args.state)

    # Only the writers take the lock. `list` is a read and must never block on
    # a driver that is mid-commit — a status question that hangs gets replaced
    # by a status question nobody asks. `verdict` is outside it too: it appends
    # to the ledger and never parses or rewrites the queue table, so it is not
    # part of the read-modify-write the lock exists to serialise.
    mutating = args.command in {"ensure-schema", "upsert", "update"}

    try:
        with (write_lock(state_path) if mutating else contextlib.nullcontext()):
            if args.command == "ensure-schema":
                ensure_schema(state_path)
                return 0

            if args.command == "upsert":
                # Gated as well as `update`, because a gate on one verb and not
                # the other is not a gate, it is a speed bump with a signposted
                # detour.
                note = check_gate(state_path, args.source, args.status, args.override_verdict)
                if note:
                    print(note, file=sys.stderr)
                created = upsert_row(
                    state_path,
                    finding=args.finding,
                    source=args.source,
                    priority=args.priority,
                    spec=args.spec,
                    status=args.status,
                )
                print("created" if created else "updated")
                return 0

            if args.command == "update":
                # BEFORE the transition is recorded and before the row is
                # written. A refusal must leave no trace of the transition it
                # refused.
                note = check_gate(state_path, args.source, args.status, args.override_verdict)
                if note:
                    print(note, file=sys.stderr)
                _record_transition(state_path, args.source, args.status)
                update_row(
                    state_path,
                    source=args.source,
                    finding=args.finding,
                    priority=args.priority,
                    spec=args.spec,
                    status=args.status,
                )
                print("updated")
                return 0

        if args.command == "verdict":
            row = record_verdict(
                state_path,
                source=args.source,
                result=args.result,
                by=args.by,
                evidence=args.evidence,
            )
            print(f"recorded {args.result} for '{args.source}' by {args.by}"
                  if row else "verdict NOT recorded: the ledger could not be written")
            return 0 if row else 1

        if args.command == "list":
            rows = iter_rows(state_path, statuses=args.status)
            if args.format == "json":
                print(json.dumps(rows, indent=2))
                return 0
            if args.format == "usv":
                for row in rows:
                    print("\x1f".join(row.get(column, "") for column in COLUMNS))
                return 0
            for row in rows:
                print(
                    "\t".join(
                        row.get(column, "") for column in COLUMNS
                    ),
                )
            return 0
    except ValueError as exc:
        parser.error(str(exc))

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
