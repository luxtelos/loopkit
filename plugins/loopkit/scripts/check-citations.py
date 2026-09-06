#!/usr/bin/env python3
"""check-citations.py — assert that `file:line` citations still point at what they claim.

WHY THIS EXISTS

Instruction files that say "hook-enforced" should cite the line, so the claim
can be checked rather than believed. That is the right instinct — an
enforcement claim nobody can verify is how "hook-enforced" survives in a file
after the hook stops matching. But a line number is a derived value, and
derived values in prose rot the moment anyone edits the cited file. A rotted
citation is worse than none: it reads as rigour and points at the wrong line.
In the repo this was written for, two citations had drifted within a day of
being written — both by someone being careful.

HOW IT WORKS

Two passes, deliberately different in strictness:

  1. STRUCTURAL (automatic, every citation) — every `path:NN` found in the
     scanned docs must name a file that exists and actually has NN lines.
     Cheap, needs no maintenance, and catches deletions, renames and truncation.

  2. PINNED (registry) — for citations that carry real weight, the cited line
     must match a regex describing what is supposed to be there. When it fails,
     the script searches the file for that regex and prints the line number the
     citation should now say, so fixing it is a one-token edit.

Pins are the load-bearing half. Add one whenever a doc cites a line as evidence
that a rule is enforced; skip it for incidental pointers, or this becomes
busywork nobody runs.

CONFIG  <project>/.loopkit/citations.json (optional):

    {
      "scanned": ["CLAUDE.md", "docs/*.md", ".claude/agents/*.md"],
      "pins": [
        ["CLAUDE.md", ".claude/hooks/block_dangerous.py", "gh\\\\s+pr\\\\s+merge", "the merge refusal"]
      ]
    }

`scanned` entries may be globs. Without a config the defaults below apply and
there are no pins. Dated records (state/, inbox/) are deliberately never
scanned: they describe what was true when written, and "correcting" them
would falsify the record.

USAGE

    python3 check-citations.py [--root DIR]     # check
    python3 check-citations.py --list           # show every citation found

Exit 0 = all good. Exit 1 = at least one citation is wrong.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_SCANNED = [
    "CLAUDE.md",
    "constitution.md",
    "README.md",
    "FILES.md",
    "TOOLS.md",
    "COMMANDS.md",
    "docs/MUTATION_POLICY.md",
    ".claude/agents/*.md",
    ".claude/skills/*/SKILL.md",
    ".claude/skills/*/references/*.md",
]

# A citation looks like `path/to/file.ext:123`, usually inside backticks.
# The extension list keeps prose like "step 2:3" and times out of the match.
CITATION = re.compile(
    r"(?P<path>[A-Za-z0-9_./-]+\.(?:py|sh|ts|tsx|js|mjs|cjs|json|ya?ml|md|sql|txt|go|rs|java|rb))"
    r":(?P<line>\d+)\b"
)


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
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


def load_config(root: Path) -> tuple[list[str], list[tuple[str, str, str, str]]]:
    scanned = list(DEFAULT_SCANNED)
    pins: list[tuple[str, str, str, str]] = []
    cfg = root / ".loopkit" / "citations.json"
    if cfg.is_file():
        try:
            raw = json.loads(cfg.read_text(encoding="utf-8"))
        except ValueError as e:
            print(f"FAIL: {cfg} is not valid JSON: {e}")
            sys.exit(1)
        if isinstance(raw.get("scanned"), list):
            scanned = [str(s) for s in raw["scanned"]]
        for entry in raw.get("pins", []) or []:
            if isinstance(entry, list) and len(entry) == 4:
                pins.append(tuple(str(x) for x in entry))  # type: ignore[arg-type]
    return scanned, pins


def expand(root: Path, patterns: list[str]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        if any(ch in pat for ch in "*?["):
            out.extend(sorted(str(p.relative_to(root)) for p in root.glob(pat) if p.is_file()))
        else:
            out.append(pat)
    seen: set[str] = set()
    return [d for d in out if not (d in seen or seen.add(d))]


def read_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None


def find_citations(root: Path, docs: list[str]) -> list[tuple[str, int, str, int]]:
    """Return (doc, doc_line_no, cited_path, cited_line_no) for every citation."""
    out = []
    for doc in docs:
        lines = read_lines(root / doc)
        if lines is None:
            continue
        for i, text in enumerate(lines, start=1):
            for m in CITATION.finditer(text):
                out.append((doc, i, m.group("path"), int(m.group("line"))))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else project_root()
    scanned_patterns, pins = load_config(root)
    docs = expand(root, scanned_patterns)
    citations = find_citations(root, docs)

    if args.list:
        for doc, doc_line, path, line in citations:
            print(f"{doc}:{doc_line}  ->  {path}:{line}")
        print(f"\n{len(citations)} citation(s) found across {len(docs)} file(s).")
        return 0

    failures: list[str] = []

    # ── Pass 1: structural ────────────────────────────────────────────
    for doc, doc_line, path, line in citations:
        cited = read_lines(root / path)
        if cited is None:
            failures.append(
                f"{doc}:{doc_line} cites {path}:{line}, but that file does not "
                f"exist or is not readable."
            )
        elif line > len(cited):
            failures.append(
                f"{doc}:{doc_line} cites {path}:{line}, but {path} has only "
                f"{len(cited)} lines."
            )

    # ── Pass 2: pinned content ────────────────────────────────────────
    for doc, path, pattern, meaning in pins:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            failures.append(f"PIN: bad regex for {doc} -> {path}: {e}")
            continue
        cited = read_lines(root / path)
        if cited is None:
            failures.append(f"PIN: {doc} cites {path}, which is unreadable.")
            continue

        refs = [c for c in citations if c[0] == doc and c[2] == path]
        if not refs:
            failures.append(
                f"PIN: expected {doc} to cite {path} for {meaning}, but it "
                f"cites it nowhere. Either the citation was dropped, or this "
                f"pin is stale and should be removed."
            )
            continue

        if any(0 < ln <= len(cited) and rx.search(cited[ln - 1]) for _, _, _, ln in refs):
            continue

        actual = [i for i, t in enumerate(cited, start=1) if rx.search(t)]
        cited_nums = ", ".join(str(ln) for _, _, _, ln in refs)
        if actual:
            where = ", ".join(str(a) for a in actual[:5])
            failures.append(
                f"PIN: {doc} cites {path}:{cited_nums} for {meaning}, but that "
                f"line does not contain it. It is now on line {where}. "
                f"Update the citation."
            )
        else:
            failures.append(
                f"PIN: {doc} cites {path}:{cited_nums} for {meaning}, but "
                f"nothing in {path} matches /{pattern}/ any more. The mechanism "
                f"may have been removed — if so the claim in {doc} is now false "
                f"and must be rewritten, not re-pointed."
            )

    print(f"Checked {len(citations)} citation(s) across {len(docs)} file(s); "
          f"{len(pins)} pinned.")
    if failures:
        print(f"\nFAIL — {len(failures)} problem(s):\n")
        for f in failures:
            print(f"  - {f}")
        print(
            "\nA citation that points at the wrong line is worse than no citation: "
            "it reads as rigour. Fix the number, or rewrite the claim if the "
            "mechanism is gone."
        )
        return 1

    print("PASS — every citation points at what it claims.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
