#!/usr/bin/env python3
"""inbox-precedent-gate.py — a new inbox section says what was searched first.

THE FAILURE THIS PINS. An agent escalated a question to the human when the
repository already held the answer: an earlier ruling, already encoded in
code. The `decision` route in loop-assess told the agent to write the whole
context and the cost of both options. It never asked whether anybody had
looked, and nothing read the resulting section to find out. So the human was
asked a settled question, which is the most expensive noise a loop makes.

THE RULE. Every NEW or CHANGED open section in `inbox/needs-human.md` carries,
on one visible line of its body,

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
    [identity]     a section is old only if heading AND body are unchanged —
                   reusing an old heading for a new question is new
    [empty]        a line that is present but says nothing is refused
    [fence] [comment] [indent]
                   a line the rendered file does not show does not count
    [nextline]     the line is ONE line; a result on the next line is not it
    [arrow]        only → (U+2192) is the arrow
    [label]        the label is exactly `Precedent searched:`
    [grandfather]  a section that was already there, unchanged, is never judged
    [agree]        loop-commit.sh and the stop gate judge against the same base
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
    under test at the caller's repository instead of the scratch one. Commit
    signing is switched off through the environment as well as in each repo's
    config, so a machine whose global config signs (and whose signer can refuse)
    cannot fail a fixture commit.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and not k.startswith("LOOP")
           and k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
    env["TMPDIR"] = str(root.parent)
    env.update({"GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false",
                "GIT_CONFIG_KEY_1": "tag.gpgsign", "GIT_CONFIG_VALUE_1": "false"})
    return env


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          env=clean_env(root))


def configure(root: Path) -> None:
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)


PREAMBLE = "# needs-human.md — the open door\n\nPreamble prose.\n\n---\n"
OLD = "\n## Old A (2026-01-01)\n\nAsked before the rule existed.\n"
OLD_B = "\n## Old B (2026-01-01)\n\nAlso asked before the rule existed.\n"
LINE = 'Precedent searched: memory recall "refund window"; grep -rn refund specs docs/adr → no ruling found\n'


def section(title: str, line: str = "", body: str = "The question, with both costs.") -> str:
    return f"\n## {title} (2026-02-02)\n\nBlocks: a row.\n\n{body}\n\n{line}"


def new_repo(tmp: Path, name: str, inbox: str | None = PREAMBLE + OLD) -> Path:
    """A repo on `main` whose first commit holds `inbox` (or no inbox at all)."""
    root = tmp / name
    (root / "inbox").mkdir(parents=True)
    (root / ".loopkit").mkdir()
    git(tmp, "init", "-q", "-b", "main", name)
    configure(root)
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
    r = new_repo(tmp, "c1", inbox=PREAMBLE + OLD + OLD_B)

    def case(text: str, want: int, label: str, must_say: str = "") -> None:
        write_inbox(r, text)
        p = run_check(r)
        said = (not must_say) or (must_say in both(p))
        expect(p.returncode == want and said, label, f"rc={p.returncode} " + both(p))

    case(PREAMBLE + section("Decide the refund window") + OLD + OLD_B, 1,
         "[check][new] a new section with no line is refused, and named", "Decide the refund window")
    case(PREAMBLE + section("Decide the refund window", LINE) + OLD + OLD_B, 0,
         "[check][control] a new section WITH the line passes")
    case(PREAMBLE.replace("Preamble prose.", "Preamble prose, edited.") + OLD + OLD_B, 0,
         "[check][grandfather] old bare sections still pass when the rest of the file is edited")
    case(PREAMBLE + OLD_B + OLD, 0,
         "[check][grandfather] reordering old sections, unchanged, is not new")
    case(PREAMBLE + OLD.replace("Asked before", "Asked   before") + OLD_B, 0,
         "[check][grandfather] a whitespace-only change to an old section is not new")
    case((PREAMBLE + OLD + OLD_B).replace("\n", "\r\n"), 0,
         "[check][grandfather] a CRLF copy of the base is not new")
    case(PREAMBLE + section("Decide the refund window", LINE)
         + OLD.replace("## Old A", "## RESOLVED 2026-03-03 — Old A") + "\nRuling: keep it.\n" + OLD_B, 0,
         "[check][grandfather] stamping an old section RESOLVED does not make it a new entry")
    case(PREAMBLE + section("First new one", LINE) + section("Second new one") + OLD + OLD_B, 1,
         "[check][new] a line in one section does not cover the section next to it", "Second new one")
    case(PREAMBLE.replace("Preamble prose.", LINE) + section("Decide the refund window") + OLD + OLD_B, 1,
         "[check][new] a line in the preamble covers no section")

    print("== a section is old by identity, not by its heading")
    case(PREAMBLE + OLD.replace("## Old A", "## RESOLVED 2026-03-03 — Old A") + OLD_B
         + "\n## Old A (2026-01-01)\n\nA different question entirely.\n", 1,
         "[check][new][identity] resolve Old A, then ask a new bare question under the same heading: refused",
         "Old A")
    case(PREAMBLE + OLD_B + "\n## Old A (2026-01-01)\n\nA different question entirely.\n", 1,
         "[check][new][identity] delete Old A, re-add a bare Old A with a new body at the end: refused",
         "Old A")
    case(PREAMBLE + OLD.replace("Asked before the rule existed.", "Now asking something else.") + OLD_B, 1,
         "[check][new][identity] rewriting an old section's body in place makes it new: refused", "Old A")
    case(PREAMBLE + OLD + "\nA later note under the old section.\n" + OLD_B, 1,
         "[check][new][identity] a note appended under an old open section changes it: refused")
    case(PREAMBLE + OLD + "\nA later note.\n\n" + LINE + OLD_B, 0,
         "[check][control] the same change WITH the line passes")
    case(PREAMBLE + OLD_B + section("Old C, a new question"), 1,
         "[check][new] delete an old bare section and add a new bare one: refused")
    case(PREAMBLE + OLD + OLD + OLD_B, 1,
         "[check][new][identity] a second copy under an old heading is new, not grandfathered")

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
        case(PREAMBLE + section("Decide the refund window", line) + OLD + OLD_B, 1,
             f"[check][empty] refused: {label}")
    case(PREAMBLE + section("Decide the refund window",
                            "Precedent searched: grep -rn refund specs docs/adr → none\n") + OLD + OLD_B, 0,
         "[check][control] a search that found nothing is a real result and passes")

    print("== the line counts only where the rendered file shows it, on one line")
    for tag, label, line, says in (
        ("fence", "inside a ``` fenced block", "```\n" + LINE + "```\n", "fenced code"),
        ("fence", "inside a ~~~ fenced block", "~~~text\n" + LINE + "~~~\n", "fenced code"),
        ("fence", "inside a fence that never closes", "````\n" + LINE, "fenced code"),
        ("comment", "inside a multi-line HTML comment", "<!--\n" + LINE + "-->\n", "HTML comment"),
        ("comment", "inside a one-line HTML comment", "<!-- " + LINE.strip() + " -->\n", "HTML comment"),
        ("comment", "inside a comment that never closes", "<!-- draft\n" + LINE, "HTML comment"),
        ("indent", "indented four spaces (a code block)", "    " + LINE, "indented code"),
        ("indent", "indented with a tab (a code block)", "\t" + LINE, "indented code"),
        ("nextline", "the label empty, the result on the next line (the reviewer's case)",
         "Precedent searched:\nOption A -> costs two days\n", "empty"),
        ("nextline", "the label empty, a → result on the next line",
         "Precedent searched:\ngrep -rn refund specs → no ruling found\n", "empty"),
        ("nextline", "wrapped over the arrow onto a second line",
         'Precedent searched: memory recall "refund window"; grep -rn refund\n→ no ruling found\n', "→"),
        ("label", "mid-sentence", "We looked. Precedent searched: grep -rn refund specs → none\n", "exactly"),
        ("label", "as a ### sub-heading", "### Precedent searched: grep -rn refund specs → none\n", "exactly"),
    ):
        case(PREAMBLE + section("Decide the refund window", line) + OLD + OLD_B, 1,
             f"[check][{tag}] refused: {label}", says)
    for label, line in (
        ("in a > blockquote", "> " + LINE),
        ("as a - list item", "- " + LINE),
        ("after a fence that closed", "```\ncode\n```\n\n" + LINE),
        ("after a multi-line comment that closed", "<!--\nnote\n-->\n" + LINE),
        ("with a comment after it on the same line", LINE.strip() + " <!-- reviewer note -->\n"),
        ("with no spaces round the arrow", "Precedent searched: grep -rn refund specs→none\n"),
    ):
        case(PREAMBLE + section("Decide the refund window", line) + OLD + OLD_B, 0,
             f"[check][control] counted: {label}")

    print("== only → is the arrow, and the label is exact")
    for arrow in ("->", "-->", "=>", "⇒", "⟶"):
        case(PREAMBLE + section("Decide the refund window",
                                f"Precedent searched: grep -rn refund specs {arrow} no ruling found\n") + OLD + OLD_B, 1,
             f"[check][arrow] refused, and the refusal names → : '{arrow}'", "→ (U+2192)")
    for label, line in (
        ("lower case", "precedent searched: grep -rn refund specs → none\n"),
        ("title case", "Precedent Searched: grep -rn refund specs → none\n"),
        ("in bold", "**Precedent searched:** grep -rn refund specs → none\n"),
        ("with no colon", "Precedent searched grep -rn refund specs → none\n"),
    ):
        case(PREAMBLE + section("Decide the refund window", line) + OLD + OLD_B, 1,
             f"[check][label] refused, and the refusal names the exact label: {label}", "Precedent searched:")

    print("== the edges of 'new'")
    r = new_repo(tmp, "c2", inbox=None)
    p = run_check(r)
    expect(p.returncode == 0, "[check][control] a project with no inbox file passes", both(p))
    (r / "inbox").mkdir(exist_ok=True)
    write_inbox(r, PREAMBLE + section("Decide the refund window"))
    p = run_check(r)
    expect(p.returncode == 1,
           "[check][new] an inbox file the base never had: every open section is new", both(p))

    # ----------------------------------------------------------------- commit
    print("== loop-commit.sh refuses the commit")
    r = new_repo(tmp, "k1")
    before = head(r)
    write_inbox(r, PREAMBLE + section("Decide the refund window") + OLD)
    p = run_commit(r, INBOX)
    staged = git(r, "diff", "--cached", "--name-only").stdout.strip()
    expect(p.returncode == 65 and head(r) == before and "Precedent searched" in both(p),
           "[commit][new] a new section with no line is not committed (exit 65), and the refusal says why", both(p))
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

    write_inbox(r, PREAMBLE + section("Decide the refund window", LINE) + OLD)
    p = run_commit(r, INBOX)
    expect(p.returncode == 0 and head(r) != before,
           "[commit][control] an ordinary entry WITH the line commits", both(p))

    before = head(r)
    write_inbox(r, (r / INBOX).read_text(encoding="utf-8").replace("Preamble prose.", "Preamble prose, edited."))
    p = run_commit(r, INBOX)
    expect(p.returncode == 0 and head(r) != before,
           "[commit][grandfather] editing a file that holds an old bare section, leaving it unchanged, commits", both(p))

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

    write_inbox(r, PREAMBLE + section("Decide the refund window", LINE) + OLD)
    p = run_gate(r)
    expect(p.returncode == 0 and "PASS" in both(p),
           "[gate][control] the same section WITH the line passes", both(p))

    r = new_repo(tmp, "g2", inbox=None)
    p = run_gate(r)
    expect(p.returncode == 0 and "PASS" in both(p),
           "[gate][control] a project with no inbox passes as before", both(p))

    # ------------------------------------------------------------------ agree
    # The reviewer's attack 3, reproduced: a PR branch added a bare section
    # before the gate existed, then merged the moved trunk. The section is new
    # against trunk, so the gate blocks. loop-commit.sh must give the SAME
    # answer when the inbox is committed — it used to judge against HEAD, which
    # already held the section, and commit.
    print("== loop-commit.sh and the stop gate judge against the same base")
    bare = tmp / "origin.git"
    git(tmp, "init", "-q", "--bare", "-b", "main", str(bare))
    up = new_repo(tmp, "a-up")
    git(up, "remote", "add", "origin", str(bare))
    git(up, "push", "-q", "origin", "main")
    git(tmp, "clone", "-q", str(bare), "a-pr")
    pr = tmp / "a-pr"
    configure(pr)
    (pr / ".loopkit").mkdir(exist_ok=True)
    git(pr, "checkout", "-q", "-b", "old-pr")
    write_inbox(pr, PREAMBLE + section("An old PR's question") + OLD)
    git(pr, "add", INBOX)
    git(pr, "commit", "-q", "-m", "added before the gate existed")
    (up / "seed.txt").write_text("trunk moved\n")
    git(up, "commit", "-q", "-am", "trunk moves")
    git(up, "push", "-q", "origin", "main")
    git(pr, "fetch", "-q", "origin")
    merged = git(pr, "merge", "-q", "--no-edit", "origin/main")
    p_gate = run_gate(pr)
    write_inbox(pr, (pr / INBOX).read_text(encoding="utf-8").replace("Preamble prose.", "Preamble prose, edited."))
    before = head(pr)
    p_inbox = run_commit(pr, INBOX)
    expect(merged.returncode == 0 and p_gate.returncode != 0 and p_inbox.returncode == 65 and head(pr) == before,
           "[agree] after merging trunk, a bare section added by the branch blocks BOTH the stop and a commit of the inbox",
           f"merge={merged.returncode} gate={p_gate.returncode} commit={p_inbox.returncode} " + both(p_gate) + both(p_inbox))
    (pr / "notes.txt").write_text("unrelated\n")
    p = run_commit(pr, "notes.txt")
    expect(p.returncode == 0 and head(pr) != before,
           "[agree][control] on that branch, a commit that does not name the inbox still goes through", both(p))
    write_inbox(pr, (pr / INBOX).read_text(encoding="utf-8").replace(
        "The question, with both costs.\n\n", "The question, with both costs.\n\n" + LINE))
    before = head(pr)
    p_inbox = run_commit(pr, INBOX)
    p_gate = run_gate(pr)
    expect(p_inbox.returncode == 0 and head(pr) != before and p_gate.returncode == 0,
           "[agree][control] once the line is added, both the commit and the stop pass",
           f"commit={p_inbox.returncode} gate={p_gate.returncode} " + both(p_inbox) + both(p_gate))

print()
print("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)")
sys.exit(0 if fails == 0 else 1)
