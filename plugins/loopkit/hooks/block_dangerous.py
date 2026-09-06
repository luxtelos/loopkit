#!/usr/bin/env python3
"""block_dangerous.py — the deterministic floor under every Bash call.

Claude Code runs this as a PreToolUse hook on the Bash tool. It does not pass
the command as an argument; it pipes a JSON object on STDIN:

    {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}

Exit 0 allows the call. Exit 2 blocks it and shows stderr to the model. Any
other code is a non-blocking error and the command runs anyway — so a crash in
this file is an ALLOW, which is why nothing here is permitted to raise.

Why a hook and not a rule: a rule in CLAUDE.md is read by the model and
forgotten under pressure. A hook fires on the tool matcher whatever the prompt
said. Anything deterministic logic can decide is decided here, not by the model.

Two lessons are baked into the patterns:

  * Command-position patterns are anchored (`\\A`, or just after `;`, `&`, `|`
    or a newline) so they fire on a COMMAND and not on prose. The first version
    blocked the very commit whose message described the guard. A guard that
    stops you writing about the guard gets worked around, not respected.
  * A pattern stops at a command separator (`[^\\n;&|]*`) so it cannot run past
    `&&` into the next command. `git add x && git commit -F msg` was once
    blocked because `-F` on the wrong command looked like force-add.

Per-project extension, both optional, both under <project>/.loopkit/:

  block-patterns.txt   one extra regex per line (blank lines and # comments ok)
  block-disabled.txt   one built-in pattern NAME per line to switch off
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CMD = r"(?:\A|[;&|]\s*|\n\s*)"  # "at a command position"

# (name, pattern). The name is what block-disabled.txt refers to and what the
# BLOCKED message prints, so a false positive can be reported by name.
BUILTIN_PATTERNS: list[tuple[str, str]] = [
    # Recursive force delete, in any flag order: -rf, -fr, -r -f, --recursive --force
    ("rm-recursive-force", r"\brm\b[^\n]*\s-[a-z]*r[a-z]*f|\brm\b[^\n]*\s-[a-z]*f[a-z]*r"),
    ("rm-recursive-force-long", r"\brm\b[^\n]*--recursive[^\n]*--force|\brm\b[^\n]*--force[^\n]*--recursive"),
    # History is never deleted. Force push, remote-branch delete, hard reset,
    # rebase, filter-branch, amend.
    ("git-force-push", r"git\s+push\b[^\n]*(-f\b|--force)"),
    ("git-push-delete", r"git\s+push\b[^\n]*(--delete|\s:)"),
    ("git-reset-hard", r"git\s+reset\s+--hard"),
    ("git-rebase", r"git\s+rebase\b"),
    ("git-filter-branch", r"git\s+filter-branch\b"),
    ("git-commit-amend", r"git\s+commit\b[^\n]*--amend"),
    # Destructive SQL from a shell.
    ("sql-drop-table", r"DROP\s+TABLE"),
    ("sql-delete-where", r"DELETE\s+FROM[^\n]*WHERE"),
    ("sql-truncate", r"TRUNCATE\s+TABLE"),
    # Tool cache and agent state never enter git. They are regenerable, they
    # churn on every run, and a force-add bypasses .gitignore.
    ("git-add-tool-cache", r"git\s+add\b[^\n]*(\.codebase-memory|\.mempalace|\.hive-mind|\.claude-flow|\.swarm)"),
    ("git-add-force", r"git\s+add\b[^\n;&|]*\s(-[a-z]*f[a-z]*|--force)\b"),
    # Never stage everything: name the files, so the diff you commit is the
    # diff you reviewed. `./` and a directly-appended separator (`.;`, `.&&`)
    # must block too, while `.gitignore` and `./src/a.ts` must still pass.
    ("git-add-all", CMD + r"git\s+add\s+(?:-A\b|--all\b|\./?(?:\s|;|&|\||\Z))"),
    # Separation of powers: the loop never merges and never approves. Stated
    # in four instruction files and enforced in none is the weakest kind of
    # rule; these three lines make it as real as the rm -rf floor.
    ("gh-pr-merge", CMD + r"gh\s+pr\s+merge\b"),
    ("gh-pr-approve", CMD + r"gh\s+pr\s+review\b[^\n]*--approve"),
    ("gh-api-merge", CMD + r"gh\s+api\b[^\n]*pulls/\d+/merge"),
]


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


def _read_list(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def active_patterns(root: Path | None = None) -> list[tuple[str, str]]:
    """Built-ins minus the project's disabled names, plus the project's extras."""
    root = root or project_root()
    disabled = set(_read_list(root / ".loopkit" / "block-disabled.txt"))
    patterns = [(n, p) for n, p in BUILTIN_PATTERNS if n not in disabled]
    for i, extra in enumerate(_read_list(root / ".loopkit" / "block-patterns.txt")):
        try:
            re.compile(extra)
        except re.error:
            continue  # a broken extra pattern must not disable the whole gate
        patterns.append((f"project-{i + 1}", extra))
    return patterns


def is_dangerous(command: str, patterns: list[tuple[str, str]] | None = None) -> str | None:
    """Return the NAME of the first matching pattern, or None."""
    for name, pattern in (patterns if patterns is not None else active_patterns()):
        if re.search(pattern, command, re.IGNORECASE):
            return name
    return None


def read_command_from_stdin() -> str:
    """Pull the shell command out of the PreToolUse JSON that Claude pipes in.

    Returns "" when there is nothing to inspect (non-Bash tool, malformed
    input). We only block on a positive match, so an empty string means
    "nothing dangerous to see" — the safe default for a denylist. It never
    blocks the whole session on parse trouble.
    """
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


if __name__ == "__main__":
    try:
        cmd = read_command_from_stdin()
        hit = is_dangerous(cmd) if cmd else None
    except Exception:
        hit = None  # a crash is an allow; say nothing rather than lie
    if hit:
        # Exit 2 is the ONLY code Claude Code treats as "block this tool call".
        print(
            f"BLOCKED [{hit}]: dangerous command pattern detected: {cmd}\n"
            "If this is a false positive, name the pattern in "
            ".loopkit/block-disabled.txt (a reviewed change), do not rephrase "
            "the command around the guard.",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(0)
