#!/usr/bin/env python3
"""check-inbox-precedent.py — a new inbox section says what was searched first.

    python3 scripts/check-inbox-precedent.py                    # working file vs HEAD
    python3 scripts/check-inbox-precedent.py --base <ref>       # vs a merge base
    python3 scripts/check-inbox-precedent.py -- <pathspec>...   # only if those paths touch the inbox

WHY. An escalation costs a human's attention, and the most expensive one is the
question somebody already answered. The `decision` route used to say "write the
whole context and the cost of both options" and stop there. Nothing asked
whether the agent had LOOKED before asking, so a question whose answer was
already a ruling encoded in code went to the human anyway.

THE RULE. Every NEW open `## ` section of `inbox/needs-human.md` carries

    Precedent searched: <queries run> → <result>

Both halves are required and both must say something. "→ none" is a real
result: the search ran and found nothing. An empty line, a missing arrow, the
template's own `<placeholders>`, or "none" where the queries go is a search
that did not happen, written down as one that did, and it is refused. Real
zero is not fabricated zero.

WHY IT READS THE FILE AND NOT THE COMMAND. A guard that asks "does this tool
call write the inbox?" is guessing at the outside of a write, and every such
guess in this repo has leaked: a heredoc into an interpreter, a redirect
through a variable, an editor nobody listed. The file is the one thing every
write path produces. So this checks content, and it runs where the paths
converge — `loop-commit.sh` before staging, `stop_gate.sh` before "done".

WHAT COUNTS AS NEW. A section is judged only if the base does not already hold
it. Stated exactly: for each open heading, the number of sections under that
heading WITHOUT a usable line may not grow relative to the base. So

  * a section the base already had is never judged, line or no line — the
    inbox's history is not rewritten to satisfy a rule that came later;
  * a second section under an old heading is new;
  * renaming an OPEN heading makes a new section. Add the line; it is one
    search.

A CLOSED heading (`RESOLVED <date> — …`, struck through, `[resolved]` — the
same `is_closed` the inbox bridge uses, one definition) is a record of a
ruling, not a question waiting on a human, and is not judged. That is also
what lets an old section be stamped RESOLVED without becoming "new".

This is a guard against FORGETTING to search, not against lying about it. An
agent that writes a plausible false line passes; so does one that shouts DONE
in a heading. The reviewer reads the line. What the check buys is that the
line exists to be read.

EXIT: 0 nothing to refuse; 1 refused (each section named on stderr);
2 the check itself could not run — callers treat that as a refusal too.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# `plugins/loopkit`, the directory that CONTAINS the package. The heading
# grammar and the closed-heading rule are the inbox bridge's; importing them is
# what keeps "a section" meaning one thing in both places.
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from loopkit_core.inbox_to_triage import DEFAULT_INBOX, HEADING, is_closed  # noqa: E402

# The label at the start of a line, after an optional list marker, blockquote
# or bold. Case-insensitive: the rule is that the line exists and says
# something, not that it was typed in one casing.
LABEL = re.compile(
    r"^[ \t>*+\-]*(?:\*\*|__)?precedent searched(?:\*\*|__)?[ \t]*:(?:\*\*|__)?[ \t]*(.*)$",
    re.I,
)
ARROW = re.compile(r"→|->")
# Markdown dressing that carries no content of its own.
DRESSING = " \t*_`.;,"
# What a template or an unfinished draft leaves behind.
PLACEHOLDER = re.compile(r"^(?:<[^>]*>|todo|tbd|fixme|xxx|\?+|\.{2,}|…+)$", re.I)
# Fine as a RESULT ("→ none": searched, found nothing). Not fine as the
# QUERIES: "none → none" says no search was run.
NO_SEARCH = {"none", "n/a", "na", "nothing", "no", "nil", "skipped", "-", "—", "–"}

SHAPE = "Precedent searched: <queries run> → <result>"
EXAMPLE = ('Precedent searched: memory recall "refund window"; '
           'grep -rn "refund" specs/ docs/adr/ → no ruling found')


def judge_line(text: str) -> str:
    """'' if `text` (everything after the label) is a usable record, else why not."""
    m = ARROW.search(text)
    if not m:
        return "the line has no '→ <result>'" if text.strip(DRESSING) else "the line is empty"
    queries = text[:m.start()].strip(DRESSING)
    result = text[m.end():].strip(DRESSING)
    if not queries:
        return "no query is named before the arrow"
    if not result:
        return "the arrow is followed by no result"
    if PLACEHOLDER.match(queries) or PLACEHOLDER.match(result):
        return "the line still holds a placeholder"
    if queries.lower() in NO_SEARCH:
        return f"'{queries}' is not a query — say what was searched"
    return ""


def judge_body(body: str) -> str:
    """'' if the section body carries at least one usable line, else why not."""
    lines = body.splitlines()
    reason = "no 'Precedent searched:' line"
    for i, line in enumerate(lines):
        m = LABEL.match(line)
        if not m:
            continue
        # A formatter wraps long lines, and the arrow is usually what lands on
        # the next one. Read to the end of the paragraph, not the end of the line.
        parts = [m.group(1)]
        for nxt in lines[i + 1:]:
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                break
            parts.append(nxt.strip())
        why = judge_line(" ".join(parts))
        if not why:
            return ""
        reason = why
    return reason


def open_sections(text: str) -> list[tuple[str, str]]:
    """(heading key, why it is bare — '' if it carries a usable line), open sections only."""
    out = []
    matches = list(HEADING.finditer(text))
    for i, m in enumerate(matches):
        heading = " ".join(m.group(1).split())
        if is_closed(heading):
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((heading, judge_body(text[m.end():end])))
    return out


def new_bare_sections(current: str, base: str) -> list[tuple[str, str]]:
    """Sections of `current` that are bare and that `base` does not account for."""
    allowance = Counter(h for h, why in open_sections(base) if why)
    refused = []
    for heading, why in open_sections(current):
        if not why:
            continue
        if allowance[heading] > 0:
            allowance[heading] -= 1   # already there before; never judged
            continue
        refused.append((heading, why))
    return refused


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, timeout=30)


def resolve_root(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    p = git(Path.cwd(), "rev-parse", "--show-toplevel")
    if p.returncode == 0 and p.stdout.strip():
        return Path(p.stdout.decode().strip())
    return Path.cwd()


def touched(inbox: str, pathspecs: list[str]) -> bool:
    """Would a commit of `pathspecs` (relative to the cwd) publish the inbox file?

    Asked of git, not worked out from the spelling: `inbox`, `./inbox/x`,
    `needs-human.md` from inside inbox/ and a glob all name the same file.
    Any doubt answers True — checking a file that was not named costs a
    refusal that says why; skipping one that was named is the leak.
    """
    p = git(Path.cwd(), "status", "--porcelain", "-z", "--untracked-files=all", "--", *pathspecs)
    if p.returncode != 0:
        return True
    for entry in p.stdout.decode("utf-8", "replace").split("\0"):
        # `XY <path>`; the source half of a rename arrives as a bare path.
        if entry == inbox or entry[3:] == inbox:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", help="project root (default: CLAUDE_PROJECT_DIR, else the git toplevel)")
    ap.add_argument("--base", default="HEAD",
                    help="the commit whose inbox sections are already there (default: HEAD)")
    ap.add_argument("--inbox", default=DEFAULT_INBOX, help="inbox path, relative to the root")
    ap.add_argument("pathspecs", nargs="*",
                    help="after `--`: check only if a commit of these paths would publish the inbox")
    args = ap.parse_args(argv)

    root = resolve_root(args.root)
    inbox_file = root / args.inbox
    if not inbox_file.is_file():
        return 0  # no inbox, or it is being deleted: nothing is being asked of anyone
    if args.pathspecs and not touched(args.inbox, args.pathspecs):
        return 0

    current = inbox_file.read_text(encoding="utf-8", errors="replace")
    base_ref = args.base or "HEAD"
    shown = git(root, "show", f"{base_ref}:{args.inbox}")
    # No such commit, or the commit has no inbox: there is nothing to
    # grandfather, so every open section is new. That is the strict direction.
    base = shown.stdout.decode("utf-8", "replace") if shown.returncode == 0 else ""

    refused = new_bare_sections(current, base)
    if not refused:
        return 0

    err = sys.stderr
    print(f"REFUSED: {args.inbox} — {len(refused)} new section(s) without a usable "
          f"'Precedent searched:' line", file=err)
    for heading, why in refused:
        print(f"  ## {heading}\n       {why}", file=err)
    print(f"""
Every new section records the search made BEFORE asking a human:

  {SHAPE}
  e.g. {EXAMPLE}

Run the search first: the memory adapter, a grep over the code, a grep over
docs/adr/ and specs/, and the inbox's own RESOLVED sections. If it finds the
ruling, the finding is `knowledge`, not `decision` — record it and do not
escalate. If it finds nothing, "→ no ruling found" is a real result; write it.
Sections already present in {base_ref} are not judged.""", file=err)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # a crash must not read as "refused" or as "fine"
        print(f"check-inbox-precedent.py could not run: {exc!r}", file=sys.stderr)
        sys.exit(2)
