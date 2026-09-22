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

A PIN MUST BIND ONE LINE, AND UNTIL 2026-09-08 IT DID NOT

A pin names a document and a path, never a line — the line is supposed to come
from the document's own citation. When a document cites a path exactly once
that works: there is one candidate, and the pin checks it. When a document
cites the same path many times, "the cited line" is a set, and the check was
`any(...)` over that set. The pin then asserted only that SOMEWHERE in the
document a citation of that path lands on a matching line — not that the
sentence making the claim does.

Measured on this repo: rotating all 16 citations of `triage_state.py` in
`specs/blocked-waits-on-what.md` onto each other's lines — every citation
wrong, the cited SET unchanged — still printed
"PASS — every citation points at what it claims." Twelve of fifteen pins sat
over a multiply-cited path, so twelve pins were non-binding. Nothing in the
repository was wrong; the tripwire was simply absent, which is this project's
worst defect class: a gate that cannot go red.

The missing information is the link between a claim and a citation. The config
knows which line a claim is about (the regex); the document knows which lines
it cites; nothing recorded WHICH citation realises WHICH claim. No amount of
set arithmetic recovers it — a rotation that is closed over the cited set
leaves every set-derived fact identical. It has to be written down. So a pin
takes an optional fifth element:

    ANCHOR — a regex matched against the citing document's own prose, within
    ANCHOR_WINDOW lines of a citation. It selects the ONE citation this pin is
    about, and that citation's line is then checked exactly.

The two cases are now distinguished rather than collapsed:

    unambiguous   the document cites the path at one line. No anchor needed.
    anchored      an anchor selects one citation. That line is checked.
    loose         anchor is the literal "*": the pin means "cited somewhere in
                  this document" and keeps the old any-of-them semantics. It
                  must be asked for; it is never the default.
    ambiguous     two or more cited lines and no anchor -> FAIL. This is the
                  tripwire. Such a pin reads as binding and is not, so it is
                  refused rather than quietly weakened.

WHAT A RUN THAT CHECKS NOTHING REPORTS

A citation checker over a project with no citations used to print
"Checked 0 citation(s) ... PASS" — a green line for having verified nothing,
which is the shape this repo names as a defect: a gate that reports success by
running nothing. Two wrong answers were available. Failing on zero would break
a project that legitimately cites no line numbers, and there is nothing wrong
with being such a project. Passing on zero hides a scanned set that no longer
matches where the docs live — the actual bug found here, where `scanned` missed
`specs/` and `docs/` entirely.

So the verdict word tracks what was verified, and the exit code is the
project's decision:

    PASS    at least one citation was checked and every one holds.
    EMPTY   zero citations and zero pins. Printed loudly, never as PASS.
    FAIL    a citation is wrong, or a pin no longer holds.

EMPTY exits 0 by default, so adopting the plugin does not turn a citation-free
repo red on day one. A project that means to keep citations honest sets
`"allow_empty": false` in its config (or CI passes --fail-on-empty) and EMPTY
becomes exit 1 — the day the scan stops seeing the docs, the gate says so.

CONFIG  <project>/.loopkit/citations.json (optional):

    {
      "scanned": ["CLAUDE.md", "docs/*.md", ".claude/agents/*.md"],
      "allow_empty": true,
      "pins": [
        ["CLAUDE.md", ".claude/hooks/block_dangerous.py", "gh\\\\s+pr\\\\s+merge", "the merge refusal"],
        ["CLAUDE.md", ".claude/hooks/block_dangerous.py", "git\\\\s+add\\\\s+-A", "the add-all refusal", "NEVER stage everything"]
      ]

A pin is [citing doc, cited path, regex the cited line must match, what it is
evidence of, and optionally an ANCHOR regex identifying which citation]. The
anchor is only needed when the document cites that path more than once — and
then it is required.
    }

`scanned` entries may be globs. Without a config the defaults below apply and
there are no pins. The defaults cover the instruction files, the docs tree and
`specs/` — a spec is the source of truth in this loop, so a citation there
carries as much weight as one in CLAUDE.md. Dated records (state/, inbox/,
CHANGELOG.md) are deliberately never scanned: they describe what was true when
written, and "correcting" them would falsify the record.

USAGE

    python3 check-citations.py [--root DIR]     # check
    python3 check-citations.py --list           # show every citation found
    python3 check-citations.py --fail-on-empty  # EMPTY is a failure in CI

Exit 0 = all good. Exit 1 = at least one citation is wrong, or the run was
EMPTY and the project asked for that to be a failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Where citations live. The first version of this list named the seven files a
# fresh init creates and nothing else, so on a repo whose docs sit in docs/ and
# whose truth sits in specs/ it scanned seven files and found nothing to check.
# Widened to the whole docs tree, specs/, and the plugin-shaped layout (a repo
# that SHIPS agents and skills keeps them under plugins/<name>/, not .claude/).
DEFAULT_SCANNED = [
    "CLAUDE.md",
    "AGENTS.md",
    "constitution.md",
    "README.md",
    "ROADMAP.md",
    "FILES.md",
    "TOOLS.md",
    "COMMANDS.md",
    "docs/*.md",
    "docs/*/*.md",
    "specs/*.md",
    ".claude/agents/*.md",
    ".claude/skills/*/SKILL.md",
    ".claude/skills/*/references/*.md",
    "plugins/*/agents/*.md",
    "plugins/*/commands/*.md",
    "plugins/*/skills/*/SKILL.md",
    "plugins/*/skills/*/references/*.md",
]

# How far from a citation a pin's anchor may match. Two lines either side,
# because these documents hard-wrap: the sentence naming the mechanism is
# routinely on the line above or below the backticked `path:line` itself.
# Wider would let one anchor select two neighbouring citations, which the
# ambiguity check then refuses — noisily, and with the line numbers, so the
# author narrows the anchor rather than guessing.
ANCHOR_WINDOW = 2

# A citation looks like `path/to/file.ext:123`, usually inside backticks.
# The extension list keeps prose like "step 2:3" and times out of the match.
CITATION = re.compile(
    r"(?P<path>[A-Za-z0-9_./-]+\.(?:py|sh|ts|tsx|js|mjs|cjs|json|ya?ml|md|sql|txt|go|rs|java|rb))"
    r":(?P<line>\d+)\b"
)


# (citing doc, cited path, regex the cited line must match, what it is evidence
# of, anchor regex selecting WHICH citation — None when the doc cites it once,
# "*" to ask for the old any-of-them semantics on purpose).
Pin = tuple[str, str, str, str, "str | None"]

LOOSE = "*"   # not a valid regex on its own, so it cannot collide with one


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


def load_config(root: Path) -> tuple[list[str], list[Pin], bool]:
    scanned = list(DEFAULT_SCANNED)
    pins: list[Pin] = []
    allow_empty = True   # adopting the plugin must not turn a citation-free repo red
    cfg = root / ".loopkit" / "citations.json"
    if cfg.is_file():
        try:
            raw = json.loads(cfg.read_text(encoding="utf-8"))
        except ValueError as e:
            print(f"FAIL: {cfg} is not valid JSON: {e}")
            sys.exit(1)
        if isinstance(raw.get("scanned"), list):
            scanned = [str(s) for s in raw["scanned"]]
        if isinstance(raw.get("allow_empty"), bool):
            allow_empty = raw["allow_empty"]
        for entry in raw.get("pins", []) or []:
            # 4 elements is the original shape and still legal — it is exactly
            # right for a path a document cites once. The 5th is the anchor.
            if isinstance(entry, list) and len(entry) in (4, 5):
                doc, path, pattern, meaning = (str(x) for x in entry[:4])
                anchor = str(entry[4]) if len(entry) == 5 else None
                pins.append((doc, path, pattern, meaning, anchor))
    return scanned, pins, allow_empty


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
    ap.add_argument("--fail-on-empty", action="store_true",
                    help="exit 1 when the run finds no citations and no pins")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else project_root()
    scanned_patterns, pins, allow_empty = load_config(root)
    if args.fail_on_empty:
        allow_empty = False
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
    doc_text: dict[str, list[str]] = {}
    for doc, path, pattern, meaning, anchor in pins:
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

        # Which of those citations is this pin about? See "A PIN MUST BIND ONE
        # LINE" above: without this step the pin checks the SET of cited lines.
        loose = anchor == LOOSE
        if anchor is not None and not loose:
            try:
                arx = re.compile(anchor)
            except re.error as e:
                failures.append(f"PIN: bad anchor regex for {doc} -> {path}: {e}")
                continue
            if doc not in doc_text:
                doc_text[doc] = read_lines(root / doc) or []
            dl = doc_text[doc]
            refs = [
                r for r in refs
                if any(arx.search(t) for t in
                       dl[max(0, r[1] - 1 - ANCHOR_WINDOW):r[1] + ANCHOR_WINDOW])
            ]
            if not refs:
                failures.append(
                    f"PIN: {doc} -> {path} for {meaning}: the anchor "
                    f"/{anchor}/ matches nothing within {ANCHOR_WINDOW} line(s) "
                    f"of any citation of {path} in {doc}. The sentence that "
                    f"made this claim was rewritten or lost its citation. "
                    f"Re-read it and re-point the pin — do not widen the "
                    f"anchor until it matches something again."
                )
                continue

        distinct = sorted({ln for _, _, _, ln in refs})
        if not loose and len(distinct) > 1:
            failures.append(
                f"PIN: {doc} -> {path} for {meaning} IS NOT BOUND TO A LINE. "
                f"{'The anchor selects' if anchor else 'The document cites'} "
                f"{len(distinct)} different lines "
                f"({', '.join(str(d) for d in distinct)}), so the pin passes "
                f"if ANY of them matches — it checks a set of lines, not the "
                f"sentence that makes the claim. Add a 5th element to the pin: "
                f"a regex matching prose within {ANCHOR_WINDOW} line(s) of the "
                f"ONE citation this claim is about. Write \"{LOOSE}\" instead "
                f"only if the pin really does mean 'cited somewhere in this "
                f"document'."
            )
            continue

        if loose:
            hit = any(0 < ln <= len(cited) and rx.search(cited[ln - 1])
                      for _, _, _, ln in refs)
        else:
            ln = distinct[0]
            hit = 0 < ln <= len(cited) and bool(rx.search(cited[ln - 1]))
        if hit:
            continue

        actual = [i for i, t in enumerate(cited, start=1) if rx.search(t)]
        cited_nums = ", ".join(str(d) for d in distinct)
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

    if not citations and not pins:
        print(
            "\nEMPTY — this run verified nothing: no citation and no pin was "
            "found in the scanned set."
        )
        print(
            "  A project with no `file:line` citations is a legitimate project, "
            "so this is not a failure by default. It is not a clean bill of "
            "health either: the same output appears when `scanned` has drifted "
            "away from where the docs actually live, which is the bug that "
            "produced this verdict word."
        )
        print(f"  Scanned: {', '.join(scanned_patterns) or '(nothing)'}")
        if allow_empty:
            print(
                "  To make this bite, set \"allow_empty\": false in "
                ".loopkit/citations.json, or run with --fail-on-empty."
            )
            return 0
        print(
            "\nFAIL — the project set allow_empty=false: it expects citations "
            "here and there are none. Either the docs lost them, or `scanned` "
            "no longer points at the docs."
        )
        return 1

    if not pins:
        print(
            "PASS — every citation resolves (structural only: 0 pins, so no "
            "cited line was checked for CONTENT. A citation used as evidence "
            "that a rule is enforced deserves a pin)."
        )
        return 0

    print("PASS — every citation points at what it claims.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
