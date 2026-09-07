#!/usr/bin/env python3
"""Deterministic helpers for Loop Kit triage state."""

from __future__ import annotations

import argparse
import contextlib
import json
import re
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


def normalize_cell(value: str) -> str:
    return " ".join(value.strip().split())


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
    if not path.exists():
        return TriageTable(prefix=DEFAULT_PREFIX, rows=[], suffix="")

    lines = path.read_text(encoding="utf-8").splitlines()
    header_index = None
    # Match the header STRUCTURALLY, by its cells, not by an exact string.
    # Bug found 2026-08-31: the comparison was against the single-space form
    # "| finding | source | priority | spec | status |", but state/triage.md pads
    # its header for column alignment. So header_index stayed None and this
    # returned rows=[] with exit 0 — indistinguishable from an empty queue. The
    # real file held 40 rows at `new` and scripts/run_loop.sh, which depends on
    # this helper, would have found no work at all.
    accepted_headers = (
        ["finding", "source", "priority", "status"],
        ["finding", "source", "priority", "spec", "status"],
    )
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip().lower() for cell in split_markdown_row(stripped)]
        if list(cells) in [list(h) for h in accepted_headers]:
            header_index = index
            break

    if header_index is None:
        prefix = path.read_text(encoding="utf-8")
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        return TriageTable(prefix=prefix or DEFAULT_PREFIX, rows=[], suffix="")

    prefix = "\n".join(lines[:header_index])
    if prefix:
        prefix += "\n"

    header_cells = split_markdown_row(lines[header_index])
    row_start = header_index + 2
    row_end = row_start
    while row_end < len(lines) and lines[row_end].strip().startswith("|"):
        row_end += 1

    rows: list[dict[str, str]] = []
    for raw_line in lines[row_start:row_end]:
        cells = split_markdown_row(raw_line)
        if len(cells) != len(header_cells):
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

    suffix = "\n".join(lines[row_end:])
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

    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("--state", required=True)
    update_parser.add_argument("--source", required=True)
    update_parser.add_argument("--finding")
    update_parser.add_argument("--priority")
    update_parser.add_argument("--spec")
    update_parser.add_argument("--status")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--state", required=True)
    list_parser.add_argument("--status", action="append", default=[])
    list_parser.add_argument("--format", choices=["tsv", "json", "usv"], default="tsv")

    return parser


def _root_for(state: Path) -> Path:
    """The worktree root that owns this state file — `state/triage.md` sits one
    level under it. The lock is per worktree because the index is per worktree."""
    resolved = state.resolve()
    return resolved.parent.parent if resolved.parent.name == "state" else resolved.parent


def write_lock(state: Path):
    """The driver lock, around a read-modify-write of the queue.

    Every mutating command here is parse-then-write: two drivers interleaving
    between the parse and the write lose one of the two edits silently, and the
    "state file is the lock" guard rail that was supposed to prevent it enforced
    nothing (2026-09-07). Re-entrant, so a driver that already holds the lock —
    loop-commit.sh, say — does not deadlock against itself.

    Fail-open: if the helper cannot be loaded at all, an unlocked write is still
    better than a triage command that refuses to run.
    """
    try:
        import importlib.util
        here = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location("driver_lock", here / "driver_lock.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod.held(_root_for(state), label="triage_state")
    except Exception:
        import contextlib
        return contextlib.nullcontext()


def _record_transition(state: Path, source: str, status: str | None) -> None:
    """Append to state/ticks.jsonl beside the state file. Never raises."""
    if not status:
        return
    try:
        import importlib.util
        here = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location("ticks", here / "ticks.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        root = state.resolve().parent.parent if state.resolve().parent.name == "state" else state.resolve().parent
        mod.append(root, "transition", source=source, status=status)
    except Exception:
        pass


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    state_path = Path(args.state)

    # Only the writers take the lock. `list` is a read and must never block on
    # a driver that is mid-commit — a status question that hangs gets replaced
    # by a status question nobody asks.
    mutating = args.command in {"ensure-schema", "upsert", "update"}

    try:
        with (write_lock(state_path) if mutating else contextlib.nullcontext()):
            if args.command == "ensure-schema":
                ensure_schema(state_path)
                return 0

            if args.command == "upsert":
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
                _record_transition(Path(args.state), args.source, args.status)
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
