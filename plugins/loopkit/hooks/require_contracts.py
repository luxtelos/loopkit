#!/usr/bin/env python3
"""require_contracts.py — block work until the project's contracts are read.

A LoopKit project carries three short contracts in its root:

  FILES.md     which memory holds what (state/ vs inbox/ vs specs/ vs tools)
  TOOLS.md     which tool for which job
  COMMANDS.md  how work fires

They exist because an agent that has not read them guesses at all three, and
guesses differently each session. Pasting them into every session was tried
first and rejected: injected content is skimmed exactly like a file is skimmed,
and it costs thousands of tokens per session for something still ignorable.
Enforcement is the active ingredient, not injection. This hook is a cheap
pointer and a hard gate:

  - PreToolUse (gate mode) on every tool: exit 2 (BLOCK) until the contracts
    that EXIST in the project have been Read. Read/Glob/Grep and planning tools
    stay open, otherwise you could not obey the gate.
  - PostToolUse (mark mode) on Read: records which contract was read.

A project with no contracts at all is not gated — the plugin must never lock a
repo that has not run /loopkit:init. Only contracts that exist are required.

Exit codes (Claude Code): 0 = allow, 2 = BLOCK with stderr shown to the model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CONTRACTS = ("FILES.md", "TOOLS.md", "COMMANDS.md")

# Tools that may run before the contracts are read: reading, searching,
# planning. Everything else is "work" and is gated.
ALWAYS_ALLOWED = {
    "Read", "Glob", "Grep", "TodoWrite", "TaskList", "NotebookRead",
    "ListSkills", "Skill", "AskUserQuestion", "ToolSearch",
}


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


def session_key(payload: dict) -> str:
    """Stable per-session id. Falls back through progressively weaker signals
    so the gate degrades to per-process rather than failing open globally."""
    for candidate in (
        payload.get("session_id"),
        os.environ.get("CLAUDE_SESSION_ID"),
        payload.get("transcript_path"),
        str(os.getppid()),
    ):
        if candidate:
            return hashlib.sha256(str(candidate).encode()).hexdigest()[:16]
    return "unknown"


def marker_dir(key: str) -> Path:
    d = Path(tempfile.gettempdir()) / f"loopkit-contracts-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # never break the session on malformed input
    if not isinstance(payload, dict):
        return 0

    mode = sys.argv[1] if len(sys.argv) > 1 else "gate"
    key = session_key(payload)
    mdir = marker_dir(key)

    # ---- mark mode: PostToolUse on Read, record which contract was read ----
    if mode == "mark":
        path = str((payload.get("tool_input") or {}).get("file_path", ""))
        for c in CONTRACTS:
            if re.search(rf"(^|/){re.escape(c)}$", path):
                (mdir / c).touch()
        return 0

    # ---- gate mode: PreToolUse, block work until the present contracts are read ----
    if payload.get("tool_name", "") in ALWAYS_ALLOWED:
        return 0

    root = project_root()
    present = [c for c in CONTRACTS if (root / c).is_file()]
    if not present:
        return 0  # not a LoopKit project yet; nothing to require

    missing = [c for c in present if not (mdir / c).exists()]
    if not missing:
        return 0

    print(
        "BLOCKED — LoopKit contracts not read yet: "
        + ", ".join(missing)
        + ".\nRead them now with the Read tool (they are in the project root). "
        "They answer, before you guess at any of it:\n"
        "  FILES.md    which memory holds what, and 'verify, never guess'\n"
        "  TOOLS.md    which tool for which job\n"
        "  COMMANDS.md how work fires\n"
        "This gate exists because they were ignored for a whole session while "
        "sitting in the repo root.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
