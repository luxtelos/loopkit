#!/usr/bin/env python3
"""protect_governance.py — guard ratified governance files by ABSOLUTE path.

Why a hook and not a permission deny-rule: deny patterns in settings.json are
matched relative to the session's project directory, so the same file reached
through a different git worktree path is NOT protected — this was proven by
editing a constitution from a sibling worktree while the deny rule stood. A
PreToolUse hook receives the real path the tool is about to write, so it
protects the file wherever it lives. Keep the deny rules as defence in depth;
this is the mechanism that actually holds.

Protected by default:

  constitution.md     ratified governance
  specs/              the source of truth; code is its build output

Per-project extension: <project>/.loopkit/protected.txt, one regex per line,
matched against the normalised absolute path.

ESCAPE HATCH: an owner who has ratified a change sets GOVERNANCE_EDIT_OK=1 for
that invocation. Explicit and visible beats an agent quietly finding a way
around a wall, which is what happens when a guard has no legitimate door.

Wired as PreToolUse on Write|Edit|MultiEdit|NotebookEdit|Bash. Exit 2 blocks,
exit 0 allows.

Bash matters and was missing until 2026-09-07: the hook guarded the edit tools
while `cat > specs/foo.md`, `tee`, `sed -i`, `cp`, `mv` and `rm` walked straight
past it. Its sibling protect_tests.py had carried a Bash matcher from the start,
three lines away in the same file — a guard that covers most doors is read as a
guard that covers the door. Found in review of the M1 specification, which the
same gap had let an agent write.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

PROTECTED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:^|/)constitution\.md$"), "constitution.md is ratified governance"),
    (re.compile(r"(?:^|/)specs/"), "specs/ is the source of truth"),
]



# A shell command can reach a protected file without ever naming a `file_path`.
# These are the shapes that actually write: a redirect, the tools that edit in
# place, and the tools that remove or replace. Deliberately over-broad — a false
# block is one visible override away, a miss is a silent write to ratified text.
WRITE_SHELL = re.compile(
    r"""(?:
          >{1,2}\s*(?P<redir>[^\s;|&<>()]+)                     # > file, >> file
        | \b(?P<tool>tee|sed|perl|awk|install|truncate|dd|rm|mv|cp|ln|touch|chmod|chown|git)\b(?P<rest>[^;|&]*)
    )""",
    re.X,
)

# Only the first token after `sed -i ""` is a flag and an empty argument; the
# path is further along. So every token after a writing tool is a candidate,
# never just the next one — that miss is how `sed -i "" s/a/b/ specs/x.md` got
# through the first version of this guard on 2026-09-07.
def shell_targets(cmd: str) -> list[str]:
    """Every path a shell command might write. Over-collects on purpose: a
    false block costs one visible override, a miss is a silent write to
    ratified text."""
    out: list[str] = []
    for m in WRITE_SHELL.finditer(cmd):
        redir = m.group("redir")
        if redir:
            out.append(redir.strip("\"'"))
        rest = m.group("rest")
        if rest:
            try:
                toks = shlex.split(rest)
            except ValueError:
                toks = rest.split()
            out.extend(t for t in toks if t and not t.startswith("-"))
    return [t for t in out if t and t not in {"/dev/null", "/dev/stdout", "/dev/stderr"}]


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


def project_patterns() -> list[tuple[re.Pattern[str], str]]:
    out: list[tuple[re.Pattern[str], str]] = []
    try:
        lines = (project_root() / ".loopkit" / "protected.txt").read_text(encoding="utf-8").splitlines()
    except Exception:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append((re.compile(line), f"protected by .loopkit/protected.txt ({line})"))
        except re.error:
            continue
    return out


def registry_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Paths the memory registry asks to guard (the knowledge bundle, when
    enabled): every write goes through the mailbox, never a hand edit."""
    if os.getenv("KNOWLEDGE_EDIT_OK") == "1":
        return []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from loopkit_memory import load
        return [(re.compile(p), "knowledge bundle is written through `memory.py knowledge enqueue`, never by hand (KNOWLEDGE_EDIT_OK=1 to override)")
                for p in load(project_root()).guard_patterns()]
    except Exception:
        return []


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Malformed input is not evidence of a violation. Fail open, loudly.
        print(">> protect_governance: unreadable hook payload; allowing", file=sys.stderr)
        return 0

    tool = payload.get("tool_name", "")
    if tool not in {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"}:
        return 0

    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        return 0

    if tool == "Bash":
        candidates = shell_targets(str(ti.get("command") or ""))
    else:
        raw = ti.get("file_path") or ""
        candidates = [raw] if raw else []
    if not candidates:
        return 0
    paths = [os.path.normpath(os.path.abspath(c)).replace(os.sep, "/") for c in candidates]

    for pattern, why in PROTECTED + project_patterns() + registry_patterns():
        hit = next((p for p in paths if pattern.search(p)), None)
        if hit:
            path = hit
            if os.getenv("GOVERNANCE_EDIT_OK") == "1":
                print(
                    f">> protect_governance: ALLOWED by GOVERNANCE_EDIT_OK — {path}",
                    file=sys.stderr,
                )
                return 0
            print(
                f"BLOCKED: {why}. Path: {path}\n"
                "This is a human decision, not a loop edit. If an owner has "
                "ratified the change, re-run with GOVERNANCE_EDIT_OK=1 so the "
                "override is visible in the transcript. Otherwise route the "
                "proposed wording to inbox/needs-human.md.",
                file=sys.stderr,
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
