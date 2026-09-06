#!/usr/bin/env python3
"""Pick the next stage for loop-next.sh — a lookup over triage rows, never a judgement.

stdin: JSON rows from `triage_state.py list --format json`.
argv[1]: optional scope — a lane name from <project>/.loopkit/scopes.json
         (the same table loop-scan.py reads) or a comma list of terms.

stdout, tab-separated, read by loop-next.sh:
  line 1  counts  pr-open|fixing|spec-ready|spec-draft|new
  then    <stage>\t<first matching finding in file order>
  then    note\t<scope note>          (only when a scope was given)
  then    blocked\t<n>                (only when blocked rows exist)
  then    target:<stage>\t<finding>\t<source>   every row at each work stage

Lives in its own file because bash 3.2 (macOS default) cannot parse a quoted
heredoc inside $( ) when the body contains an apostrophe.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

WANT = ["pr-open", "fixing", "spec-ready", "spec-draft", "new"]
# Priority rank used ONLY inside a scoped pick. Unscoped stays file order so the
# old behaviour is byte-identical (pinned by the control test). In a lane the
# first row by file order is usually the oldest umbrella ticket — the epic row
# that predates every real finding — which is exactly the row a sprint does
# not want served first. Priority is a column, so ranking by it is still a
# lookup, not a judgement.
PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
MAX_FANOUT = 8


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


def scope_terms(scope: str) -> list[str]:
    """One source of truth for lane vocabulary: loop-scan.py's load_scopes(),
    reading <project>/.loopkit/scopes.json. Falls back to parsing the file
    directly if the scan module cannot be loaded, so a lane still resolves."""
    if not scope:
        return []
    root = project_root()
    table: dict[str, list[str]] = {}
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        spec = importlib.util.spec_from_file_location(
            "loop_scan", os.path.join(here, "loop-scan.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        table = mod.load_scopes(root) or {}
    except Exception:
        try:
            raw = json.loads((root / ".loopkit" / "scopes.json").read_text(encoding="utf-8"))
            table = {str(k).lower(): [str(t).lower() for t in v] for k, v in raw.items()
                     if isinstance(v, list)}
        except Exception:
            table = {}
    return table.get(scope.lower()) or [
        t.strip().lower() for t in scope.split(",") if t.strip()
    ]


def pick(rows: list[dict], scope: str) -> str:
    terms = scope_terms(scope)

    def in_scope(r: dict) -> bool:
        hay = ((r.get("finding") or "") + " " + (r.get("source") or "")).lower()
        return any(t in hay for t in terms)

    matched = [r for r in rows if in_scope(r)] if terms else rows
    note = ""
    scoped = bool(terms) and bool(matched)
    if terms and not matched:
        note = f"SCOPE: {scope} matched 0 rows — falling back to the unscoped pick"
        matched = rows
    elif terms:
        note = f"SCOPE: {scope} ({len(matched)} row(s) match; unscoped rows ignored)"
        # Say how much ACTIONABLE work the lane is hiding. A scoped tick once
        # printed "STAGE: discover / no actionable rows" while twenty `new`
        # rows waited outside the lane. Both lines were true and the pair was
        # misleading: the lane was idle, the loop was not. A filter that cannot
        # report what it filtered reads as an empty queue.
        outside_new = sum(
            1
            for r in rows
            if (r.get("status") or "").strip() == "new" and r not in matched
        )
        if outside_new:
            note += f" — unscoped new={outside_new} still waiting"

    if scoped:
        # stable sort: priority first, file order within a priority
        rows = sorted(
            rows,
            key=lambda r: PRIORITY_RANK.get((r.get("priority") or "").strip().lower(), 9),
        )
    counts = {s: 0 for s in WANT}
    first: dict[str, str] = {}
    for r in rows:
        st = (r.get("status") or "").strip()
        if st not in counts:
            continue
        # pr-open is a cheap poll, never a stage; a PR in flight is checked
        # whatever lane the tick is in, so it is counted from ALL rows.
        if st != "pr-open" and scoped and r not in matched:
            continue
        counts[st] += 1
        first.setdefault(st, (r.get("finding") or "").strip())
    out = ["|".join(str(counts[s]) for s in WANT)]
    out += [f"{s}\t{first.get(s, '')}" for s in WANT]
    if note:
        out.append(f"note\t{note}")
    # `blocked` is deliberately absent from WANT: it is neither a stage to
    # advance nor a row to poll. A row waiting on a human ruling cannot move
    # however often it is checked, so filing it as pr-open spends a poll every
    # tick and overstates what is in flight. Counted here so it stays visible,
    # and nowhere else.
    n_blocked = sum(1 for r in rows if (r.get("status") or "").strip() == "blocked")
    if n_blocked:
        out.append(f"blocked\t{n_blocked}")
    # Every row at each work stage, so a tick can advance the STAGE, not one
    # row of it. Rows are independent findings in independent worktrees;
    # serialising them was a habit, never the pipeline's constraint. Capped so
    # a tick stays bounded; the cap is a dispatch limit, not a hidden filter —
    # the counts line still reports the full stage.
    for st in ("fixing", "spec-ready", "spec-draft", "new"):
        stage_rows = [
            r for r in rows
            if (r.get("status") or "").strip() == st and (not scoped or r in matched)
        ]
        for r in stage_rows[:MAX_FANOUT]:
            out.append(f"target:{st}\t{(r.get('finding') or '').strip()}\t{(r.get('source') or '').strip()}")
    return "\n".join(out)


def main() -> int:
    scope = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        rows = json.load(sys.stdin)
    except Exception:
        print("ERR")
        return 0
    print(pick(rows, scope))
    return 0


if __name__ == "__main__":
    sys.exit(main())
