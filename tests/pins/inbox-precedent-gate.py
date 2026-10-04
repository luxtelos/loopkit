#!/usr/bin/env python3
"""inbox-precedent-gate.py — a new inbox section says what was searched first.

THE FAILURE THIS PINS. An agent escalated a question to the human when the
repository already held the answer: an earlier ruling, already encoded in
code. The `decision` route in loop-assess told the agent to write the whole
context and the cost of both options. It never asked whether anybody had
looked, and nothing read the resulting section to find out. So the human was
asked a settled question, which is the most expensive noise a loop makes.

THE RULE. Every NEW open section in `inbox/needs-human.md` carries

    Precedent searched: <queries run> → <result>

WHAT IS CHECKED, AND WHERE. The CONTENT of the file, never the command that
wrote it. A detector that guesses "does this command write the inbox" leaks
every round (a heredoc into an interpreter walks past it), so the check is a
script over the resulting file, run where every write path converges:

    [check]        scripts/check-inbox-precedent.py — the rule itself
    [commit]       scripts/loop-commit.sh           — refuses the commit
    [gate]         hooks/stop_gate.sh               — refuses "done", and is
                                                      the layer that catches a
                                                      bare `git commit`

Each case below is tagged with the half it exercises, so
`inbox-precedent-prove-red.sh` can break one half and require exactly that tag
to go red.

    [new]          a new section without the line is refused
    [empty]        a line that is present but says nothing is refused
    [grandfather]  a section that was already there is never judged
    [control]      the ordinary paths still work

usage: inbox-precedent-gate.py [<plugin-root>]     (default: this repo's plugin)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO / "plugins" / "loopkit"
CHECK = PLUGIN / "scripts" / "check-inbox-precedent.py"
COMMIT = PLUGIN / "scripts" / "loop-commit.sh"
GATE = PLUGIN / "hooks" / "stop_gate.sh"
PY = sys.executable
INBOX = "inbox/needs-human.md"

fails = 0


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def fail(msg: str) -> None:
    global fails
    fails += 1
    print(f"  FAIL {msg}")


def expect(cond: bool, msg: str, detail: str = "") -> None:
    if cond:
        ok(msg)
    else:
        fail(msg + (f" :: {' | '.join(l.strip() for l in detail.splitlines() if l.strip())[-300:]}" if detail else ""))


def clean_env(root: Path) -> dict:
    """An environment that belongs to the scratch repo and nothing else.

    A suite run from inside a worktree, a hook, or another loop-commit carries
    GIT_* variables and LOOPKIT_DRIVER_LOCK. Any of them would point the code
    under test at the caller's repository instead of the scratch one.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and not k.startswith("LOOP")
           and k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
    env["TMPDIR"] = str(root.parent)
    return env


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          env=clean_env(root))


PREAMBLE = "# needs-human.md — the open door\n\nPreamble prose.\n\n---\n"
OLD = "\n## An old question nobody stamped (2026-01-01)\n\nAsked before the rule existed.\n"
GOOD_LINE = 'Precedent searched: memory recall "refund window"; grep -rn refund specs docs/adr → no ruling found\n'


def section(title: str, line: str = "") -> str:
    return f"\n## {title} (2026-02-02)\n\nBlocks: a row.\n\nThe question, with both costs.\n\n{line}"


def new_repo(tmp: Path, name: str, inbox: str | None = PREAMBLE + OLD) -> Path:
    """A repo on `main` whose first commit holds `inbox` (or no inbox at all)."""
    root = tmp / name
    (root / "inbox").mkdir(parents=True)
    (root / ".loopkit").mkdir()
    git(tmp, "init", "-q", "-b", "main", name)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)
    (root / "seed.txt").write_text("seed\n")
    git(root, "add", "seed.txt")
    if inbox is not None:
        (root / INBOX).write_text(inbox, encoding="utf-8")
        git(root, "add", INBOX)
    git(root, "commit", "-q", "-m", "init")
    return root


def write_inbox(root: Path, text: str) -> None:
    (root / INBOX).write_text(text, encoding="utf-8")


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").stdout.strip()


def run_check(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(CHECK), "--root", str(root), *args], cwd=root,
                          capture_output=True, text=True, env=clean_env(root))


def run_commit(root: Path, *paths: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(COMMIT), "-m", "pin", "--", *paths], cwd=cwd or root,
                          capture_output=True, text=True, env=clean_env(root),
                          stdin=subprocess.DEVNULL)


def run_gate(root: Path) -> subprocess.CompletedProcess:
    env = clean_env(root)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    return subprocess.run(["bash", str(GATE)], cwd=root, capture_output=True, text=True,
                          env=env, stdin=subprocess.DEVNULL)


def both(p: subprocess.CompletedProcess) -> str:
    return (p.stdout or "") + (p.stderr or "")


with tempfile.TemporaryDirectory(prefix="inbox-precedent.") as t:
    tmp = Path(t).resolve()

    # ------------------------------------------------------------------ check
    print("== the rule, over the file's content")
    r = new_repo(tmp, "c1")
    write_inbox(r, PREAMBLE + section("Decide the refund window") + OLD)
    p = run_check(r)
    expect(p.returncode == 1 and "Decide the refund window" in both(p),
           "[check][new] a new section with no line is refused, and named", both(p))

    write_inbox(r, PREAMBLE + section("Decide the refund window", GOOD_LINE) + OLD)
    p = run_check(r)
    expect(p.returncode == 0, "[check][control] a new section WITH the line passes", both(p))

    write_inbox(r, PREAMBLE + OLD + "\nA later note under the old section.\n")
    p = run_check(r)
    expect(p.returncode == 0,
           "[check][grandfather] an old section without the line still passes when the file is edited", both(p))

    write_inbox(r, PREAMBLE + section("Decide the refund window", GOOD_LINE)
                + OLD.replace("## An old", "## RESOLVED 2026-03-03 — An old") + "\nRuling: keep it.\n")
    p = run_check(r)
    expect(p.returncode == 0,
           "[check][grandfather] stamping an old section RESOLVED does not make it a new entry", both(p))

    write_inbox(r, PREAMBLE + section("First new one", GOOD_LINE) + section("Second new one") + OLD)
    p = run_check(r)
    expect(p.returncode == 1 and "Second new one" in both(p) and "## First new one" not in both(p),
           "[check][new] a line in one section does not cover the section next to it", both(p))

    write_inbox(r, PREAMBLE.replace("Preamble prose.", GOOD_LINE) + section("Decide the refund window") + OLD)
    p = run_check(r)
    expect(p.returncode == 1,
           "[check][new] a line in the preamble covers no section", both(p))

    wrapped = ('Precedent searched: memory recall "refund window"; grep -rn refund specs\n'
               "docs/adr\n→ no ruling found\n")
    write_inbox(r, PREAMBLE + section("Decide the refund window", wrapped) + OLD)
    p = run_check(r)
    expect(p.returncode == 0,
           "[check][control] a line wrapped over the arrow by a formatter still passes", both(p))

    write_inbox(r, PREAMBLE + section("Decide the refund window",
                                      "- **Precedent searched:** grep -rn refund specs -> nothing matched\n") + OLD)
    p = run_check(r)
    expect(p.returncode == 0,
           "[check][control] a bulleted, bold line with an ASCII arrow passes", both(p))

    print("== real zero is not fabricated zero")
    for label, line in (
        ("the label with nothing after it", "Precedent searched:\n"),
        ("queries with no arrow and no result", "Precedent searched: grep -rn refund specs\n"),
        ("an arrow with no result", "Precedent searched: grep -rn refund specs →\n"),
        ("an arrow with no query", "Precedent searched: → no ruling found\n"),
        ("the template's own placeholders", "Precedent searched: <queries run> → <result>\n"),
        ("a TODO for a result", "Precedent searched: grep -rn refund specs → TODO\n"),
        ("'none' where the queries go", "Precedent searched: none → none\n"),
    ):
        write_inbox(r, PREAMBLE + section("Decide the refund window", line) + OLD)
        p = run_check(r)
        expect(p.returncode == 1, f"[check][empty] refused: {label}", both(p))

    write_inbox(r, PREAMBLE + section("Decide the refund window",
                                      "Precedent searched: grep -rn refund specs docs/adr → none\n") + OLD)
    p = run_check(r)
    expect(p.returncode == 0,
           "[check][control] a search that found nothing is a real result and passes", both(p))

    print("== the edges of 'new'")
    r = new_repo(tmp, "c2", inbox=None)
    p = run_check(r)
    expect(p.returncode == 0, "[check][control] a project with no inbox file passes", both(p))
    (r / "inbox").mkdir(exist_ok=True)
    write_inbox(r, PREAMBLE + section("Decide the refund window"))
    p = run_check(r)
    expect(p.returncode == 1,
           "[check][new] an inbox file the base never had: every open section is new", both(p))

    r = new_repo(tmp, "c3")
    write_inbox(r, PREAMBLE + OLD + OLD)
    p = run_check(r)
    expect(p.returncode == 1,
           "[check][new] a second section under an old heading is new, not grandfathered", both(p))

    # ----------------------------------------------------------------- commit
    print("== loop-commit.sh refuses the commit")
    r = new_repo(tmp, "k1")
    before = head(r)
    write_inbox(r, PREAMBLE + section("Decide the refund window") + OLD)
    p = run_commit(r, INBOX)
    staged = git(r, "diff", "--cached", "--name-only").stdout.strip()
    expect(p.returncode != 0 and head(r) == before and "Precedent searched" in both(p),
           "[commit][new] a new section with no line is not committed, and the refusal says why", both(p))
    expect(staged == "", "[commit][new] the refused file is not left staged", staged)

    p = run_commit(r, "inbox")
    expect(p.returncode != 0 and head(r) == before,
           "[commit][new] naming the directory instead of the file changes nothing", both(p))

    p = run_commit(r, "needs-human.md", cwd=r / "inbox")
    expect(p.returncode != 0 and head(r) == before,
           "[commit][new] committing from inside inbox/ changes nothing", both(p))

    (r / "notes.txt").write_text("unrelated\n")
    p = run_commit(r, "notes.txt")
    expect(p.returncode == 0 and head(r) != before,
           "[commit][control] a commit that does not name the inbox goes through while the inbox is dirty", both(p))

    before = head(r)
    write_inbox(r, PREAMBLE + section("Decide the refund window", "Precedent searched: →\n") + OLD)
    p = run_commit(r, INBOX)
    expect(p.returncode != 0 and head(r) == before,
           "[commit][empty] an empty line is not committed", both(p))

    write_inbox(r, PREAMBLE + section("Decide the refund window", GOOD_LINE) + OLD)
    p = run_commit(r, INBOX)
    expect(p.returncode == 0 and head(r) != before,
           "[commit][control] an ordinary entry WITH the line commits", both(p))

    before = head(r)
    write_inbox(r, (r / INBOX).read_text(encoding="utf-8") + "\nA note added under the old section.\n")
    p = run_commit(r, INBOX)
    expect(p.returncode == 0 and head(r) != before,
           "[commit][grandfather] editing a file that holds an old bare section commits", both(p))

    r = new_repo(tmp, "k2", inbox=None)
    before = head(r)
    (r / "a.txt").write_text("a\n")
    p = run_commit(r, "a.txt")
    expect(p.returncode == 0 and head(r) != before,
           "[commit][control] a project with no inbox commits as before", both(p))

    # ------------------------------------------------------------------- gate
    print("== stop_gate.sh refuses 'done', including what a bare commit let in")
    r = new_repo(tmp, "g1")
    git(r, "checkout", "-q", "-b", "feat")
    p = run_gate(r)
    expect(p.returncode == 0 and "PASS" in both(p),
           "[gate][grandfather] a branch whose inbox holds only old sections passes", both(p))

    write_inbox(r, PREAMBLE + section("Decide the refund window") + OLD)
    p = run_gate(r)
    expect(p.returncode != 0 and "Precedent searched" in both(p),
           "[gate][new] an uncommitted new section with no line blocks the stop", both(p))

    # The write path loop-commit.sh never sees: a bare commit. The gate reads
    # the branch against its merge base, so the section is still new to it.
    git(r, "add", INBOX)
    git(r, "commit", "-q", "-m", "bare commit, no wrapper")
    clean = git(r, "status", "--porcelain", "--", INBOX).stdout.strip()
    p = run_gate(r)
    expect(clean == "" and p.returncode != 0 and "Precedent searched" in both(p),
           "[gate][new] a section that reached the branch through a bare commit still blocks the stop", both(p))

    write_inbox(r, PREAMBLE + section("Decide the refund window", GOOD_LINE) + OLD)
    p = run_gate(r)
    expect(p.returncode == 0 and "PASS" in both(p),
           "[gate][control] the same section WITH the line passes", both(p))

    r = new_repo(tmp, "g2", inbox=None)
    p = run_gate(r)
    expect(p.returncode == 0 and "PASS" in both(p),
           "[gate][control] a project with no inbox passes as before", both(p))

print()
print("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)")
sys.exit(0 if fails == 0 else 1)
