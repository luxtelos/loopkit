#!/usr/bin/env python3
"""protect_tests.py — strategic friction on the one thing agents most want to delete.

Anthropic's long-running-agent harness puts it in the imperative: "It is
unacceptable to remove or edit tests." A rule in a prompt is forgotten under
pressure; this hook is not. PreToolUse on Write|Edit|MultiEdit|Bash:

  * a Write/Edit to a TEST FILE that lowers its test count is blocked
    (adding tests, renaming, refactoring with the same count all pass);
  * a Bash `rm` / `git rm` / `mv` that names a test path is blocked;
  * if the project keeps `state/features.json` (a feature list the agent
    may only flip to passes), any edit that changes anything except a
    `passes` value is blocked.

Test files are matched by .loopkit/test-globs.txt (defaults below). Escape:
TEST_EDIT_OK=1 for a human-ratified deletion, in either of two forms:

  * in this hook's own environment (works for Write, Edit, MultiEdit, Bash);
  * as a plain prefix on ONE command inside a Bash call, for example
    `TEST_EDIT_OK=1 git rm <test path>`. The hook is its own process, so a
    prefix never reaches os.environ; it is text in tool_input.command and is
    read from there, per command. The prefix covers only the command it sits
    in front of: a later command in the same line needs its own. `export
    TEST_EDIT_OK=1`, `env TEST_EDIT_OK=1 ...`, a quoted or `$VAR` form, and
    any value but the single character 1 never count.

Every honoured override prints one stderr line per test path and, when the
project has a state/ directory, appends a `test_edit_override` event to
state/ticks.jsonl (best effort; a ledger failure never changes the verdict).
Exit 2 blocks; every other path is exit 0, including every parse error.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_GLOBS = [
    "tests/**", "test/**", "spec/**", "__tests__/**",
    "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/*_test.go", "**/*Test.java",
]
TEST_TOKEN = re.compile(
    r"\b(?:it|test|describe)\s*\(|\bdef\s+test_\w+|#\[test\]|\bfunc\s+Test\w+\s*\(|@Test\b",
)
# The 0.2.5 matcher, kept as is: it still decides what counts as a removal at a
# command position. `cmd` only adds a handle on where the command word starts.
DESTRUCTIVE = re.compile(r"(?:\A|[;&|]\s*|\n\s*)(?P<cmd>rm|git\s+rm|mv|git\s+mv)\b([^\n;&|]*)", re.I)
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
OVERRIDE_WORD = "TEST_EDIT_OK=1"
OVERRIDE_NAME = "TEST_EDIT_OK="
FAIL_CLOSED_TOKENS = ("$(", "`", "<(", ">(", "<<")
# NAME=value words, value quoted or bare; used to keep secrets out of the ledger.
ASSIGN_WORD = re.compile(r"""(?:(?<=\s)|(?<=[;&|(])|\A)([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|'[^']*'|\S*)""")


def redact(command: str) -> str:
    """Every NAME=value word except TEST_EDIT_OK=1 becomes NAME=***."""
    return ASSIGN_WORD.sub(
        lambda m: m.group(0) if m.group(0) == OVERRIDE_WORD else f"{m.group(1)}=***", command)


def project_root() -> Path:
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


def load_globs(root: Path) -> list[str]:
    try:
        lines = (root / ".loopkit" / "test-globs.txt").read_text(encoding="utf-8").splitlines()
        globs = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
        return globs or DEFAULT_GLOBS
    except Exception:
        return DEFAULT_GLOBS


def rel(root: Path, path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        return str(p.resolve().relative_to(root.resolve())).replace(os.sep, "/")
    except Exception:
        return str(p).replace(os.sep, "/")


def is_test_path(root: Path, path: str, globs: list[str]) -> bool:
    r = rel(root, path)
    for g in globs:
        if fnmatch.fnmatch(r, g) or fnmatch.fnmatch("/" + r, "/" + g):
            return True
        # `tests/**` should also match a file directly under tests/
        if g.endswith("/**") and (r + "/").startswith(g[:-2]):
            return True
    return False


def test_count(text: str) -> int:
    return len(TEST_TOKEN.findall(text or ""))


def only_passes_changed(old: object, new: object) -> bool:
    """features.json rule: agents may flip `passes`; nothing else may change."""
    if isinstance(old, dict) and isinstance(new, dict):
        if set(old) != set(new):
            return False
        return all(k == "passes" or only_passes_changed(old[k], new[k]) for k in old)
    if isinstance(old, list) and isinstance(new, list):
        return len(old) == len(new) and all(only_passes_changed(a, b) for a, b in zip(old, new))
    return old == new


def block(msg: str) -> int:
    print(
        f"BLOCKED [protect_tests]: {msg}\n"
        "Tests are the loop's evidence; removing one is a human decision. If a person "
        "ratified it, re-run with TEST_EDIT_OK=1 so the override is visible.",
        file=sys.stderr,
    )
    return 2


# --------------------------------------------------------------------------
# Per-command reading of a Bash line.
#
# The override is a property of ONE simple command, because that is what a shell
# prefix is: `A=1 cmd1 && cmd2` sets A for cmd1 only. So the line is cut into
# simple commands first, and each command is asked for its own leading
# assignments. Quotes, comments and `$( )` / backticks are respected so text that
# merely LOOKS like a prefix (an echo argument, a comment) is never one.
# --------------------------------------------------------------------------

class Word:
    __slots__ = ("text", "start", "end")

    def __init__(self, text: str, start: int, end: int) -> None:
        self.text, self.start, self.end = text, start, end


class Segment:
    """One simple command: [start, end) is the span between two separators."""

    def __init__(self, start: int) -> None:
        self.start = start
        self.end = start
        self.words: list[Word] = []

    @property
    def override(self) -> bool:
        """True iff this command's own leading assignments set TEST_EDIT_OK to
        exactly 1. Last assignment wins, as in the shell."""
        verdict = False
        for w in self.words:
            if not ASSIGNMENT.match(w.text):
                break
            if w.text.startswith(OVERRIDE_NAME):
                verdict = w.text == OVERRIDE_WORD
        return verdict


def split_commands(cmd: str) -> list[Segment] | None:
    """Cut `cmd` into simple commands on unquoted ; && || | & ( ) and newline.
    Returns None when quoting is unbalanced (the caller then falls back to the
    0.2.5 reading, with no inline override)."""
    segs: list[Segment] = []
    cur = Segment(0)
    n = len(cmd)
    i = 0
    wstart = -1  # start offset of the word being built, or -1

    def close_word(upto: int) -> None:
        nonlocal wstart
        if wstart >= 0:
            cur.words.append(Word(cmd[wstart:upto], wstart, upto))
            wstart = -1

    def close_seg(sep_start: int, sep_end: int) -> None:
        nonlocal cur
        close_word(sep_start)
        cur.end = sep_start
        segs.append(cur)
        cur = Segment(sep_end)

    while i < n:
        c = cmd[i]
        if c in " \t":
            close_word(i)
            i += 1
        elif c == "\\":
            if i + 1 < n and cmd[i + 1] == "\n":  # line continuation = whitespace
                close_word(i)
                i += 2
                continue
            if wstart < 0:
                wstart = i
            i += 2
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                return None
            if wstart < 0:
                wstart = i
            i = j + 1
        elif c == '"':
            j = i + 1
            while j < n and cmd[j] != '"':
                j += 2 if cmd[j] == "\\" else 1
            if j >= n:
                return None
            if wstart < 0:
                wstart = i
            i = j + 1
        elif c == "`":
            j = cmd.find("`", i + 1)
            if j < 0:
                return None
            if wstart < 0:
                wstart = i
            i = j + 1
        elif c == "$" and i + 1 < n and cmd[i + 1] == "(":
            depth, j = 1, i + 2
            while j < n and depth:
                if cmd[j] == "(":
                    depth += 1
                elif cmd[j] == ")":
                    depth -= 1
                j += 1
            if depth:
                return None
            if wstart < 0:
                wstart = i
            i = j
        elif c == "#" and wstart < 0:  # comment to end of line
            j = cmd.find("\n", i)
            i = n if j < 0 else j
        elif c == "\n":
            close_seg(i, i + 1)
            i += 1
        elif c in ";|()":
            width = 2 if cmd[i:i + 2] in ("||", "|&") else 1
            close_seg(i, i + width)
            i += width
        elif c == "&":
            # `>&`, `<&` and `&>` are redirections, not separators.
            # `>&` / `<&` only when the < or > is itself unescaped (even run of
            # backslashes before it); `&>` when > follows.
            k = i - 1
            nb = 0
            while k - 1 - nb >= 0 and cmd[k - 1 - nb] == "\\":
                nb += 1
            prev_redirect = i > 0 and cmd[i - 1] in "<>" and nb % 2 == 0
            if prev_redirect or (i + 1 < n and cmd[i + 1] == ">"):
                if wstart < 0:
                    wstart = i
                i += 1
            else:
                width = 2 if cmd[i:i + 2] == "&&" else 1
                close_seg(i, i + width)
                i += width
        else:
            if wstart < 0:
                wstart = i
            i += 1
    close_word(n)
    cur.end = n
    segs.append(cur)
    return segs


def _args_tokens(cmd: str, first: Word | None, seg_end: int) -> list[str]:
    rest = cmd[first.start:seg_end] if first is not None else ""
    try:
        return shlex.split(rest)
    except ValueError:
        return rest.split()


def command_hits(cmd: str, seg: Segment) -> list[tuple[str, Word | None, int]]:
    """Removal commands in one simple command: (kind, first-arg word, n/a).
    Looks past leading assignments, past a leading `$VAR` / backtick word (a
    command built from a variable is still a command), and through `env`, so
    those shapes are judged, not skipped. Only the plain command word counts:
    `bash -c '...'` and friends stay as they were in 0.2.5 (not closed here)."""
    words = list(seg.words)
    k = 0
    while k < len(words) and ASSIGNMENT.match(words[k].text):
        k += 1
    while k < len(words) and words[k].text[:1] in ("$", "`"):
        k += 1
    if k < len(words) and words[k].text == "env":
        k += 1
        while k < len(words) and (ASSIGNMENT.match(words[k].text) or words[k].text.startswith("-")):
            k += 1
    if k >= len(words):
        return []
    w0 = words[k].text.lower()
    if w0 in ("rm", "mv"):
        return [(w0, words[k + 1] if k + 1 < len(words) else None, 0)]
    if w0 == "git" and k + 1 < len(words) and words[k + 1].text.lower() in ("rm", "mv"):
        return [("git " + words[k + 1].text.lower(), words[k + 2] if k + 2 < len(words) else None, 0)]
    return []


def bash_hits(cmd: str, root: Path, globs: list[str]) -> list[dict]:
    """Every test path a Bash line removes or moves, each with whether ITS OWN
    command carries the inline override. The 0.2.5 matcher still runs, so
    nothing it blocked is lost; the per-command reader adds the shapes it
    missed (a leading assignment, `$VAR`, `env`)."""
    segs = split_commands(cmd)
    # Fail closed: when the line holds anything whose real execution order this
    # small reader cannot be sure of (command substitution, process
    # substitution, a heredoc, unbalanced quoting), NO inline override is
    # honoured anywhere in it. The line is then judged exactly as 0.2.5 did.
    unsure = segs is None or any(t in cmd for t in FAIL_CLOSED_TOKENS)
    hits: list[dict] = []
    seen: set[tuple[int, str]] = set()

    def seg_index(pos: int) -> int:
        if segs is None:
            return -1
        for idx, s in enumerate(segs):
            if s.start <= pos <= s.end:
                return idx
        return -1

    def add(idx: int, tok: str) -> None:
        if tok.startswith("-") or not is_test_path(root, tok, globs):
            return
        if (idx, tok) in seen:
            return
        seen.add((idx, tok))
        # honoured only when that simple command IS the removal, carries the
        # prefix itself, and the whole line parsed cleanly.
        inline = bool(not unsure and idx >= 0 and segs[idx].override and command_hits(cmd, segs[idx]))
        hits.append({"path": tok, "inline": inline, "msg":
                     f"`{cmd.strip()[:120]}` removes or moves a test path: {tok}"})

    for m in DESTRUCTIVE.finditer(cmd):
        try:
            tokens = shlex.split(m.group(2))
        except ValueError:
            tokens = m.group(2).split()
        idx = seg_index(m.start("cmd"))
        for t in tokens:
            add(idx, t)
    if segs is not None:
        for idx, seg in enumerate(segs):
            for _kind, first, _ in command_hits(cmd, seg):
                for t in _args_tokens(cmd, first, seg.end):
                    add(idx, t)
    return hits


def evaluate(payload: dict, root: Path, globs: list[str]) -> list[dict]:
    """Everything the hook would block, as {path, msg, inline}. `inline` is True
    only for a Bash removal whose own command carries the plain prefix."""
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        return []

    if tool == "Bash":
        cmd = ti.get("command")
        if not isinstance(cmd, str):
            return []
        return bash_hits(cmd, root, globs)

    path = str(ti.get("file_path") or "")
    if not path:
        return []
    r = rel(root, path)

    def one(msg: str) -> list[dict]:
        return [{"path": r, "msg": msg, "inline": False}]

    # features.json: only `passes` may change
    if r == "state/features.json" and tool in {"Write", "Edit", "MultiEdit"}:
        try:
            old = json.loads((root / r).read_text(encoding="utf-8")) if (root / r).exists() else None
        except Exception:
            old = None
        new_text = None
        if tool == "Write":
            new_text = str(ti.get("content") or "")
        elif tool == "Edit" and old is not None:
            cur = (root / r).read_text(encoding="utf-8")
            new_text = cur.replace(str(ti.get("old_string") or ""), str(ti.get("new_string") or ""), 1)
        if old is not None and new_text is not None:
            try:
                new = json.loads(new_text)
            except Exception:
                return one("state/features.json would no longer be valid JSON")
            if not only_passes_changed(old, new):
                return one("state/features.json: only a `passes` value may change; features are never added, removed or renamed by the loop")
        return []

    if not is_test_path(root, path, globs):
        return []

    if tool == "Write":
        new_text = str(ti.get("content") or "")
        try:
            old_text = (root / r).read_text(encoding="utf-8") if (root / r).exists() else ""
        except Exception:
            old_text = ""
        before, after = test_count(old_text), test_count(new_text)
        if old_text and after < before:
            return one(f"{r}: test count would drop {before} → {after}")
        return []

    if tool == "Edit":
        before, after = test_count(str(ti.get("old_string") or "")), test_count(str(ti.get("new_string") or ""))
        if after < before:
            return one(f"{r}: this edit removes {before - after} test(s)")
        return []

    if tool == "MultiEdit":
        edits = ti.get("edits") or []
        before = sum(test_count(str(e.get("old_string") or "")) for e in edits if isinstance(e, dict))
        after = sum(test_count(str(e.get("new_string") or "")) for e in edits if isinstance(e, dict))
        if after < before:
            return one(f"{r}: these edits remove {before - after} test(s)")
        return []
    return []


def record_override(payload: dict, root: Path, paths: list[str], via: str) -> None:
    """One stderr line per path, then a best-effort tick. Never raises."""
    for p in paths:
        print(f">> protect_tests: ALLOWED by TEST_EDIT_OK — {p}", file=sys.stderr)
    try:
        if not (root / "state").is_dir():
            return
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from loopkit_core import ticks
        ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        command = ti.get("command") if isinstance(ti.get("command"), str) else None
        ticks.append(
            root, "test_edit_override",
            tool=payload.get("tool_name"),
            via=via,
            command=redact(command)[:200] if command else None,
            paths=paths,
            cwd=payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd(),
        )
    except Exception:
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    if not isinstance(payload.get("tool_input") or {}, dict):
        return 0
    root = project_root()
    globs = load_globs(root)
    hits = evaluate(payload, root, globs)
    if not hits:
        return 0
    env_ok = os.getenv("TEST_EDIT_OK") == "1"
    if env_ok:
        record_override(payload, root, list(dict.fromkeys(h["path"] for h in hits)), "env")
        return 0
    for h in hits:
        if not h["inline"]:
            return block(h["msg"])
    record_override(payload, root, list(dict.fromkeys(h["path"] for h in hits)), "inline")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
