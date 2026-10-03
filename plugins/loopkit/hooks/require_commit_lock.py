#!/usr/bin/env python3
"""require_commit_lock.py — a bare `git commit` is refused in a LoopKit project.

THE FAILURE THIS ENFORCES AGAINST (2026-09-07). Two agents in one worktree.
The reviewer staged exactly one file by name, as every instruction file told it
to. Before it could commit, a driver ran `git add … && git commit`. The index is
one file per worktree and every process in that worktree shares it, so the
driver's commit published the reviewer's staged file too — a review verdict now
sits inside a commit about something else — and the reviewer's own commit said
"nothing added to commit".

WHY A HOOK. The rule already existed in prose ("never run two loop drivers
concurrently; the state file is the lock") and in CLAUDE.md ("name the files").
Both were obeyed and neither could have prevented this, because the race is
between an agent's own `git add` and its own `git commit`. A prose rule cannot
hold a lock. `scripts/loop-commit.sh` does: it takes
`<worktree>/.loopkit/driver.lock` and does the add and the commit inside one
critical section. This hook is what makes that the path of least resistance.

WHAT IT DOES. PreToolUse on Bash. Blocks a `git commit` at a command position
when the project is LoopKit-initialised. Everything else passes, including
`git add`, which is harmless on its own once commits go through the pathspec.

WHAT IT DELIBERATELY DOES NOT DO.
  * It is passive in any directory with no LOOP in it — see
    `is_loopkit_project`. A bare `.loopkit/` is not enough: passive hooks
    create that directory in repos that never ran init.
  * It does not try to be unbypassable. `LOOPKIT_COMMIT_UNLOCKED=1` in the
    environment, or written inline at the front of the command, opens the door
    — and lands in the transcript where a human can see it, which is the point.
    Same shape as TEST_EDIT_OK and GOVERNANCE_EDIT_OK.
  * The name `commit-without-lock` can be listed in
    `.loopkit/block-disabled.txt`, so a project that genuinely has one writer
    can switch it off in a reviewed change rather than by rephrasing commands
    around the guard.

Exit 2 blocks and shows stderr to the model. Anything else allows, so a crash
in this file is an ALLOW: nothing here may raise.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

NAME = "commit-without-lock"
DOOR = "LOOPKIT_COMMIT_UNLOCKED"

CMD = r"(?:\A|[;&|]\s*|\n\s*|\(\s*)"  # "at a command position"
# `git commit`, tolerating global flags in between (`git -C x commit`, `git -c
# user.name=t commit`). Stops at a separator so it cannot run past `&&`.
GIT_COMMIT = CMD + r"git\s+(?:-[^\s;&|]+\s+(?:[^\s;&|]+\s+)??)*commit\b"
# The escape hatch written inline, e.g. `LOOPKIT_COMMIT_UNLOCKED=1 git commit …`
INLINE_DOOR = rf"{CMD}(?:\w+=[^\s;&|]*\s+)*{DOOR}=[^\s;&|]+"
# Anything routed through the wrapper (or through the lock itself) is fine.
VIA_LOCK = r"loop-commit\.sh|driver_lock\.py"

# A heredoc operator: `<<EOF`, `<<'EOF'`, `<<"EOF"`, `<<-EOF`. Not `<<<`, which
# is a here-string and opens no body. The delimiter must end at whitespace, a
# shell metacharacter or end of line: `<<E\OF` is then not read at all, so its
# body stays visible — the safe direction.
HEREDOC_OP = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2(?=[\s;&|<>()]|$)")
# ALLOWLIST: only these commands turn a heredoc body into DATA. Anything else —
# `bash`, `sudo bash`, `env sh`, `exec sh`, `docker exec -i c sh`, `python3 -` —
# keeps its body visible. The first version used a blocklist ("blank it unless
# a shell is the first word") and review found five ways a shell ran a body it
# had blanked (independent review FAIL, 2026-10-03).
DATA_SINKS = {"cat", "tee", "gh", "paste"}


def _mask(line: str) -> str:
    """A same-length copy of `line` with quoted text and a trailing comment
    blanked, so `echo "<<EOF"`, `grep '<<EOF' notes.md` or `# … <<EOF` opens
    no heredoc. Positions line up with `line`, so a match in `line` can be
    looked up in the mask."""
    out = []
    quote = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            out.append(c if c == quote else " ")
            if c == "\\" and quote == '"' and i + 1 < len(line):
                out.append(" ")
                i += 1
            elif c == quote:
                quote = None
        elif c == "\\":  # an escaped character, e.g. `\<<EOF`, is not an operator
            out.append("  "[: len(line[i : i + 2])])
            i += 1
        elif c in "'\"":
            quote = c
            out.append(c)
        elif c == "#" and (i == 0 or line[i - 1] in " \t;&|("):
            out.append(" " * (len(line) - i))
            break
        else:
            out.append(c)
        i += 1
    return "".join(out)[: len(line)].ljust(len(line))


def strip_heredoc_prose(command: str) -> str:
    """Blank out heredoc bodies that are DATA, so prose in them is not read as
    a command.

    Found 2026-10-02: an issue body written with `cat > f <<'EOF' … EOF` said
    "`git add x && git commit -F m` passes there". The `&&` inside the prose
    read as a command position and `gh issue create` was refused. Text you are
    writing to a file is not a command you are running.

    A body is blanked only when a DATA_SINKS command opens the heredoc and
    nothing after the operator is piped (`cat <<EOF | sh` runs it). Every
    other body stays: it may be the command. A `<<` inside quotes, in a
    comment, escaped, or after `$((` opens nothing. The operator's own line,
    and everything after the terminator, are always kept.

    Known limit (from review): a quoted string that spans several lines and
    contains `cat <<EOF` is not seen as quoted, because the mask works per
    line. Contrived; a whole-command scanner like push_guard's would close it.
    """
    lines = command.split("\n")
    out: list[str] = []
    pending: list[tuple[str, bool, bool]] = []  # (delimiter, dash, keep body)
    for line in lines:
        if pending:
            delim, dash, keep = pending[0]
            ended = (line.lstrip("\t") if dash else line) == delim
            if ended:
                pending.pop(0)
            out.append(line if (keep or ended) else "")
            continue
        out.append(line)
        masked = _mask(line)
        for m in HEREDOC_OP.finditer(line):
            if masked[m.start()] != "<" or "$((" in masked[: m.start()]:
                continue  # quoted, commented, or an arithmetic shift
            seg = re.split(r"[;&|(]", masked[: m.start()])[-1].split()
            while seg and re.match(r"\w+=", seg[0]):
                seg.pop(0)
            sink = os.path.basename(seg[0]) if seg else ""
            data = sink in DATA_SINKS and "|" not in masked[m.end():]
            pending.append((m.group(3), m.group(1) == "-", not data))
    return "\n".join(out)


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def is_loopkit_project(root: Path) -> bool:
    """Is there actually a LOOP here, or just a `.loopkit/` directory?

    `.loopkit/` alone is too loose a marker: passive hooks (metrics, session
    snapshots) create it in any repo a LoopKit-equipped session happens to touch.
    Caught by running this hook against the real checkout on 2026-09-07 — the
    main ai-k3 tree had nothing but a `metrics.jsonl` in there and the gate was
    ready to refuse every commit in it.

    The marker has to be something `loopkit-init.sh` lays down and a passive
    hook never would: the queue itself, or the scope config.
    """
    if not (root / ".loopkit").is_dir():
        return False
    return (root / "state" / "triage.md").exists() or (root / ".loopkit" / "scopes.json").exists()


def _disabled(root: Path) -> bool:
    try:
        lines = (root / ".loopkit" / "block-disabled.txt").read_text(encoding="utf-8").splitlines()
    except Exception:
        return False
    return NAME in {l.strip() for l in lines}


def needs_lock(command: str, root: Path | None = None) -> bool:
    """True when this command commits and has not gone through the lock."""
    if not command:
        return False
    root = root or project_root()
    if not is_loopkit_project(root):
        return False  # no loop here: stay out of the way entirely
    if _disabled(root):
        return False
    if os.environ.get(DOOR):
        return False
    if re.search(VIA_LOCK, command):
        return False
    if re.search(INLINE_DOOR, command):
        return False
    return bool(re.search(GIT_COMMIT, strip_heredoc_prose(command), re.IGNORECASE))


def read_command_from_stdin() -> str:
    """The shell command out of the PreToolUse JSON. "" when there is nothing
    to inspect — the safe default, since we only ever block on a positive."""
    raw = sys.stdin.read()
    if not raw.strip():
        return ""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    tool_input = payload.get("tool_input", {}) if isinstance(payload, dict) else {}
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("command") or tool_input.get("script") or "")


def message() -> str:
    wrapper = Path(__file__).resolve().parent.parent / "scripts" / "loop-commit.sh"
    return (
        f"BLOCKED [{NAME}]: a bare `git commit` in a LoopKit worktree.\n"
        "The git index is ONE file per worktree, shared by every process in it. "
        "Between your `git add` and your `git commit`, another agent's commit can "
        "publish your staged files under its own message — observed 2026-09-07, "
        "commit 6989d63.\n"
        f"Use the lock instead:\n"
        f'  bash "{wrapper}" -m "your message" -- <path> [<path>...]\n'
        "It takes <worktree>/.loopkit/driver.lock, stages and commits your named "
        "paths inside one critical section, and the kernel releases the lock "
        "however the process ends.\n"
        f"Genuinely single-writer? Set {DOOR}=1 (it stays visible in the "
        f"transcript), or list `{NAME}` in .loopkit/block-disabled.txt as a "
        "reviewed change. Do not rephrase the command around the guard."
    )


if __name__ == "__main__":
    try:
        cmd = read_command_from_stdin()
        hit = needs_lock(cmd) if cmd else False
    except Exception:
        hit = False  # a crash is an allow; say nothing rather than lie
    if hit:
        print(message(), file=sys.stderr)
        sys.exit(2)
    sys.exit(0)
