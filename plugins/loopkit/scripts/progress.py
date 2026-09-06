#!/usr/bin/env python3
"""progress.py — the deterministic half of session memory.

Compaction and session-resume summaries are weakest at exactly one thing:
which files were touched. A model reconstructs that list from memory and gets
it wrong; git already knows. So this script owns "Files Modified" and a small
append-only progress log, and the hooks read from it instead of asking the
model.

    progress.py files-modified [--root R] [--limit N]   sorted tracked-modified ∪ untracked
    progress.py append --text "<what happened>" [--root R]   one dated line → state/progress.md
    progress.py last [--root R] [--n N]                  the last N progress lines
    progress.py snapshot [--root R]                      the five compaction sections, filled deterministically

Everything is fail-open: outside a git repo it prints nothing and exits 0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

IGNORED_PREFIXES = (".loopkit/scratch/", ".loopkit/session/", "node_modules/")
SECTIONS = ("Session Intent", "Files Modified", "Decisions Made", "Current State", "Next Steps")


def project_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def _git(root: Path, *args: str) -> list[str]:
    try:
        p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    if p.returncode != 0:
        return []
    return [l for l in p.stdout.splitlines() if l.strip()]


def files_modified(root: Path) -> list[str]:
    """Tracked files changed vs HEAD plus untracked files, sorted, harness litter removed."""
    changed = set(_git(root, "diff", "--name-only", "--diff-filter=ACMRTUXB", "HEAD"))
    changed |= set(_git(root, "ls-files", "--others", "--exclude-standard"))
    files = [f for f in changed if not f.startswith(IGNORED_PREFIXES)]
    # Source files first, dot-paths (config, harness) last: a resume line that
    # shows eight `.loopkit/*` entries and hides `app.ts` tells you nothing.
    return sorted(files, key=lambda f: (f.startswith("."), f))


def progress_path(root: Path) -> Path:
    return root / "state" / "progress.md"


def append(root: Path, text: str) -> str:
    path = progress_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# progress.md — append-only session log\n\n"
            "One line per event, written by the stop gate and the loop, read at\n"
            "session start and before compaction. Never edited by hand.\n\n",
            encoding="utf-8",
        )
    files = files_modified(root)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    line = f"- {stamp} — {' '.join(text.split())}"
    if files:
        shown = ", ".join(files[:12]) + (f" (+{len(files) - 12} more)" if len(files) > 12 else "")
        line += f" — files: {shown}"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return line


def last(root: Path, n: int = 1) -> list[str]:
    path = progress_path(root)
    if not path.exists():
        return []
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    return lines[-n:]


def snapshot(root: Path) -> str:
    """The five compaction sections. Deterministic parts are filled from git and
    the progress log; the model fills Session Intent in its own summary."""
    files = files_modified(root)
    branch = (_git(root, "branch", "--show-current") or ["?"])[0]
    log = _git(root, "log", "--oneline", "-3")
    decisions = last(root, 5)
    next_steps: list[str] = []
    state = root / "state" / "triage.md"
    if state.exists():
        here = Path(__file__).resolve().parent
        try:
            p = subprocess.run(["bash", str(here / "loop-next.sh")], cwd=root, capture_output=True, text=True, timeout=30)
            next_steps = [l for l in p.stdout.splitlines() if l.startswith(("STAGE:", "TARGET:", "BLOCKED:"))]
        except Exception:
            next_steps = []
    out = ["## Session Intent", "", "(model fills this from the conversation)", ""]
    out += ["## Files Modified", ""] + ([f"- {f}" for f in files] or ["- none"]) + [""]
    out += ["## Decisions Made", ""] + (decisions or ["- none recorded in state/progress.md"]) + [""]
    out += ["## Current State", "", f"- branch: {branch}"] + [f"- {l}" for l in log] + [""]
    out += ["## Next Steps", ""] + ([f"- {l}" for l in next_steps] or ["- (no state/triage.md — run the loop's morning-triage)"]) + [""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("files-modified"); a.add_argument("--root"); a.add_argument("--limit", type=int, default=0)
    b = sub.add_parser("append"); b.add_argument("--root"); b.add_argument("--text", required=True)
    c = sub.add_parser("last"); c.add_argument("--root"); c.add_argument("--n", type=int, default=1)
    d = sub.add_parser("snapshot"); d.add_argument("--root")
    args = ap.parse_args()
    root = project_root(args.root)
    try:
        if args.cmd == "files-modified":
            files = files_modified(root)
            if args.limit:
                files = files[: args.limit]
            print("\n".join(files))
        elif args.cmd == "append":
            print(append(root, args.text))
        elif args.cmd == "last":
            print("\n".join(last(root, args.n)))
        elif args.cmd == "snapshot":
            print(snapshot(root))
    except Exception as e:  # fail-open: a memory helper must never break a hook
        print(f"progress.py: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
