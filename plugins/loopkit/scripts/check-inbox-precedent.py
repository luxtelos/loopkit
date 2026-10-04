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

THE RULE. Every NEW or CHANGED open `## ` section of `inbox/needs-human.md`
carries, on ONE line of its body that the rendered file shows,

    Precedent searched: <queries run> → <result>

The shape is exact, so the plugin and every copy of the rule agree on it:

  * the label is `Precedent searched:`, case and all, at the start of the line
    (a `> ` blockquote or a `- ` list marker in front of it is fine; bold, a
    sentence, or a `###` heading in front of it is not);
  * the arrow is `→` (U+2192). `->`, `-->`, `=>` and lookalikes are refused by
    name, with the expected shape in the message;
  * both sides of the arrow say something. "→ none" is a real result: the
    search ran and found nothing. An empty side, the template's own
    `<placeholders>`, TODO, or "none" where the queries go is a search that did
    not happen, written down as one that did. Real zero is not fabricated zero;
  * it is one line. A label on one line and an arrow on the next is an empty
    label followed by some other sentence, so it is refused;
  * it is visible. A line inside a fenced code block (``` or ~~~), inside an
    HTML comment, or in an indented code block (four spaces or a tab) does not
    count — the owner reads the rendered file, and the whole point of the line
    is that it exists to be read.

WHY IT READS THE FILE AND NOT THE COMMAND. A guard that asks "does this tool
call write the inbox?" is guessing at the outside of a write, and every such
guess in this repo has leaked: a heredoc into an interpreter, a redirect
through a variable, an editor nobody listed. The file is the one thing every
write path produces. So this checks content, and it runs where the paths
converge — `loop-commit.sh` before staging, `stop_gate.sh` before "done" —
both against the SAME base (loop-commit asks the stop gate for it).

WHAT COUNTS AS NEW: IDENTITY. A section is grandfathered only if the base
holds a section with the same heading AND the same body (whitespace and line
endings aside), or if its heading is closed. Everything else is judged:

  * an old section left as it was is never judged — the inbox's history is not
    rewritten to satisfy a rule that came later;
  * an old open section whose body is edited is a changed question and is
    judged. Annotating one means adding the line, or stamping it RESOLVED;
  * a new question under an old heading is new — whether the old one was
    deleted or stamped RESOLVED first. A per-heading count let exactly that
    swap through, which is why identity replaced it;
  * a second identical copy is new; reordering sections is not.

A CLOSED heading (`RESOLVED <date> — …`, struck through, `[resolved]` — the
same `is_closed` the inbox bridge uses, one definition) is a record of a
ruling, not a question waiting on a human, and is not judged. That is what
lets an old section be stamped RESOLVED, with the ruling under it. It also
means a heading that shouts an uppercase marker (`DONE`) mid-title is skipped;
the bridge files no row for it either.

This is a guard against FORGETTING to search, not against lying about it. An
agent that writes a plausible false line passes. The reviewer reads the line.
What the check buys is that the line exists to be read.

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

LABEL_TEXT = "Precedent searched:"
# The exact label at the start of a visible line. In front of it only a
# blockquote marker and/or a list marker. Case-sensitive on purpose.
LABEL = re.compile(r"^[ ]{0,3}(?:>[ ]?)*[ ]{0,3}(?:[-*+][ ]+)?Precedent searched:(.*)$")
# The same words in any other dress: lower case, bold, mid-sentence, no colon.
NEAR_LABEL = re.compile(r"precedent\W{0,4}searched", re.I)
ARROW = "→"  # U+2192, and nothing else
OTHER_ARROW = re.compile(r"-+>|=+>|[⇒⟶⟹⇨➔➜➝↦]|－＞")
FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$")
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
    if ARROW not in text:
        other = OTHER_ARROW.search(text)
        if other:
            return f"'{other.group(0)}' is not the arrow — use → (U+2192): {SHAPE}"
        if text.strip(DRESSING):
            return f"the line has no '→ <result>' on it (the arrow is → (U+2192)): {SHAPE}"
        return "the line is empty"
    queries, result = (part.strip(DRESSING) for part in text.split(ARROW, 1))
    if not queries:
        return "no query is named before the arrow"
    if not result:
        return "the arrow is followed by no result"
    if PLACEHOLDER.match(queries) or PLACEHOLDER.match(result):
        return "the line still holds a placeholder"
    if queries.lower() in NO_SEARCH:
        return f"'{queries}' is not a query — say what was searched"
    return ""


def visible_lines(body: str):
    """Yield (shown, hidden, where) for each line of a section body.

    `shown` is the text the rendered file displays; `hidden` is what it does
    not, and `where` names why ('' when nothing is hidden). Fenced code,
    indented code and HTML comments are hidden. A fence or comment that never
    closes runs to the end of the section, which is how it renders.
    """
    fence = ""
    in_comment = False
    for raw in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if fence:
            m = FENCE.match(raw)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
                fence = ""
            yield "", raw, "inside a fenced code block"
            continue
        if not in_comment:
            m = FENCE.match(raw)
            if m:
                fence = m.group(1)
                yield "", raw, "inside a fenced code block"
                continue
            if raw.startswith("\t") or raw.startswith("    "):
                yield "", raw, "in an indented code block"
                continue
        shown, hidden, rest = "", "", raw
        while rest:
            if in_comment:
                end = rest.find("-->")
                if end < 0:
                    hidden += rest
                    break
                hidden += rest[:end]
                rest, in_comment = rest[end + 3:], False
            else:
                start = rest.find("<!--")
                if start < 0:
                    shown += rest
                    break
                shown += rest[:start]
                rest, in_comment = rest[start + 4:], True
        yield shown, hidden, ("inside an HTML comment" if hidden else "")


def judge_body(body: str) -> str:
    """'' if the section body carries at least one usable line, else why not."""
    reason = f"no '{LABEL_TEXT}' line"
    for shown, hidden, where in visible_lines(body):
        m = LABEL.match(shown)
        if m:
            why = judge_line(m.group(1))
            if not why:
                return ""
            reason = why
        elif NEAR_LABEL.search(shown):
            reason = (f"the label must be exactly '{LABEL_TEXT}' at the start of a line "
                      f"(not bold, not mid-sentence, not a heading): {SHAPE}")
        elif NEAR_LABEL.search(hidden):
            reason = f"the line is {where}, so the rendered file does not show it"
    return reason


def identity(heading: str, body: str) -> tuple[str, str]:
    """What makes two sections the same section: heading AND body."""
    return (heading, body)


def open_sections(text: str) -> list[tuple[str, str, str]]:
    """(heading, normalised body, why it is bare — '' if usable) for open sections."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    matches = list(HEADING.finditer(text))
    for i, m in enumerate(matches):
        heading = " ".join(m.group(1).split())
        if is_closed(heading):
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        out.append((heading, " ".join(body.split()), judge_body(body)))
    return out


def new_bare_sections(current: str, base: str) -> list[tuple[str, str]]:
    """Sections of `current` that are bare and that `base` does not hold verbatim."""
    allowance = Counter(identity(h, b) for h, b, _ in open_sections(base))
    refused = []
    for heading, body, why in open_sections(current):
        key = identity(heading, body)
        if allowance[key] > 0:
            allowance[key] -= 1   # the same section was already there; never judged
            continue
        if why:
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
    print(f"REFUSED: {args.inbox} — {len(refused)} new or changed section(s) without a usable "
          f"'{LABEL_TEXT}' line", file=err)
    for heading, why in refused:
        print(f"  ## {heading}\n       {why}", file=err)
    print(f"""
Every new or changed section records the search made BEFORE asking a human,
on one visible line (not in a code block or a comment), arrow → (U+2192):

  {SHAPE}
  e.g. {EXAMPLE}

Run the search first: the memory adapter, a grep over the code, a grep over
docs/adr/ and specs/, and the inbox's own RESOLVED sections. If it finds the
ruling, the finding is `knowledge`, not `decision` — record it and do not
escalate. If it finds nothing, "→ no ruling found" is a real result; write it.
Sections present unchanged in {base_ref} are not judged; an edited one is.""", file=err)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # a crash must not read as "refused" or as "fine"
        print(f"check-inbox-precedent.py could not run: {exc!r}", file=sys.stderr)
        sys.exit(2)
