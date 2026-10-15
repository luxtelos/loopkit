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

A THIRD lesson, added 2026-09-07 after a token was burned: this hook also
refuses a command carrying a secret-looking literal in its TEXT. `gho_` on an
SSH command line is how the leak happened — the value lands in the remote
host's process table, readable by any user with `ps`, and in the transcript.
`check-tools.py` scanned `.mcp.json` and nothing else, so nothing stood in that
command's way. The shape table is shared with it (`loopkit_core.secrets`) so
the two scanners cannot drift.

That check gets its own branch rather than a row in BUILTIN_PATTERNS, and the
reason is the message: the pattern branch prints the offending command back so
a false positive can be reported, and printing a command that contains a secret
is the same leak by another route. The secret branch reports the SHAPE only
("github", "doppler") and never the value. It also runs FIRST, so a command
that is both dangerous and secret-bearing can never reach the branch that
quotes it — and that branch now redacts as well, belt and braces.

Per-project extension, both optional, both under <project>/.loopkit/:

  block-patterns.txt   one extra regex per line (blank lines and # comments ok)
  block-disabled.txt   one built-in pattern NAME per line to switch off

The secret check answers to `block-disabled.txt` under the name
`secret-on-command-line`, like any other pattern — switching it off is a
reviewed change to a tracked file, not a rephrasing of the command.

A FOURTH lesson, 2026-10-03: force push and remote delete are not regexes.
A regex cannot read shell quoting — the separator-stopping version let a
quoted "-f", `2>&1 -f` and `$(a; b) -f` through. `loopkit_core.push_guard`
splits the command the way the shell does and reads each `git push` argv.
Unlike the rest of this file it FAILS CLOSED: a command that mentions git and
push and cannot be read, or that crashes the hook, is blocked. The rule names
`git-force-push` and `git-push-delete` are unchanged.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# `plugins/loopkit`, the directory that CONTAINS the package.
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from loopkit_core import push_guard as _push  # noqa: E402
from loopkit_core import secrets as _secrets  # noqa: E402

# Same name and meaning as the downstream project copy's function, so its tests (and the
# reviewer's adversarial scripts) load this hook and call it unchanged.
push_danger_reason = _push.push_danger_reason

# Force push and remote delete, checked by reading the command the way the
# shell does (loopkit_core.push_guard), not by a regex. History: the regexes
# ran past `&&` into a `gh pr create` whose title said "-F ... -f" and refused
# a plain push (2026-10-02); the separator-stopping regexes that replaced them
# then let nine real force pushes through — a quoted "-f", a `;` inside a
# quoted ref, `2>&1 -f`, `$(a; b) -f` — because a regex cannot read shell
# quoting (independent review FAIL, 2026-10-03). The names stay, so
# `.loopkit/block-disabled.txt` switches each one off as before.
PUSH_RULES: tuple[tuple[str, str], ...] = (
    ("git-force-push", _push.FORCE),
    ("git-push-delete", _push.DELETE),
)

SECRET_RULE = "secret-on-command-line"

CMD = r"(?:\A|[;&|]\s*|\n\s*)"  # "at a command position"

# (name, pattern). The name is what block-disabled.txt refers to and what the
# BLOCKED message prints, so a false positive can be reported by name.
BUILTIN_PATTERNS: list[tuple[str, str]] = [
    # Recursive force delete, in any flag order: -rf, -fr, -r -f, --recursive --force
    ("rm-recursive-force", r"\brm\b[^\n]*\s-[a-z]*r[a-z]*f|\brm\b[^\n]*\s-[a-z]*f[a-z]*r"),
    ("rm-recursive-force-long", r"\brm\b[^\n]*--recursive[^\n]*--force|\brm\b[^\n]*--force[^\n]*--recursive"),
    # History is never deleted. Hard reset, rebase, filter-branch, amend.
    # Force push and remote-branch delete are NOT regexes: see PUSH_RULES.
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
    # The flag part is CASE-SENSITIVE (`(?-i:...)` switches off IGNORECASE for
    # just that group): force-add is lowercase `-f` or `--force` only. Before
    # this, `-F` still read as `-f`, and agents dodged the guard by rewriting
    # `git commit -F msg` as `--file=msg`. A rule people route around is a bug.
    # `[a-zA-Z]` keeps short-flag clusters like `-vf` and `-Af` blocked.
    ("git-add-force", r"git\s+add\b[^\n;&|]*\s(?-i:-[a-zA-Z]*f[a-zA-Z]*|--force)\b"),
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


def _read_named(path: Path) -> list[tuple[str, str]]:
    """Extras with optional names: a `#name <name>` comment line right above a
    regex names it, so a block can say which RULING it enforces and
    rulings-compile.py can prove the ruling has a gate. Unnamed extras are
    `project-N` in file order."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    pending: str | None = None
    n = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^#\s*name\s+(\S+)", line)
        if m:
            pending = m.group(1)
            continue
        if line.startswith("#"):
            continue
        n += 1
        out.append((pending or f"project-{n}", line))
        pending = None
    return out


def active_patterns(root: Path | None = None) -> list[tuple[str, str]]:
    """Built-ins minus the project's disabled names, plus the project's extras."""
    root = root or project_root()
    disabled = set(_read_list(root / ".loopkit" / "block-disabled.txt"))
    patterns = [(n, p) for n, p in BUILTIN_PATTERNS if n not in disabled]
    for name, extra in _read_named(root / ".loopkit" / "block-patterns.txt"):
        if name in disabled:
            continue
        try:
            re.compile(extra)
        except re.error:
            continue  # a broken extra pattern must not disable the whole gate
        patterns.append((name, extra))
    return patterns


def is_dangerous(command: str, patterns: list[tuple[str, str]] | None = None) -> str | None:
    """Return the NAME of the first matching pattern, or None."""
    for name, pattern in (patterns if patterns is not None else active_patterns()):
        if re.search(pattern, command, re.IGNORECASE):
            return name
    return None


def enabled_push_rules(root: Path | None = None) -> list[tuple[str, str]]:
    """PUSH_RULES minus the names listed in .loopkit/block-disabled.txt."""
    root = root or project_root()
    disabled = set(_read_list(root / ".loopkit" / "block-disabled.txt"))
    return [(n, k) for n, k in PUSH_RULES if n not in disabled]


def push_hit(command: str, root: Path | None = None) -> tuple[str, str] | None:
    """(rule name, reason) if the command force-pushes or deletes a remote ref.

    A finding the guard cannot attribute to one kind (unreadable quoting, a
    `$F` before the remote) is reported under the first rule still on.
    """
    rules = enabled_push_rules(root)
    found = _push.push_danger(command, {k for _, k in rules})
    if not found:
        return None
    kind, reason = found
    for name, k in rules:
        if kind == _push.EITHER or kind == k:
            return name, reason
    return None


def secret_shapes(command: str, root: Path | None = None) -> list[str]:
    """Shape names of any secret-looking literal in `command` — never values.

    Honours `.loopkit/block-disabled.txt`, so a project that genuinely cannot
    live with this check switches it off the same reviewed way it switches off
    any other pattern.
    """
    root = root or project_root()
    if SECRET_RULE in set(_read_list(root / ".loopkit" / "block-disabled.txt")):
        return []
    return _secrets.scan(command)


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
    cmd = ""
    shapes: list[str] = []
    hit = None
    why = None  # the reason, when a PUSH_RULES rule matched rather than a regex
    try:
        cmd = read_command_from_stdin()
        if cmd:
            # FIRST, always. A command carrying a secret must never reach the
            # pattern branch below, which quotes the command back.
            shapes = secret_shapes(cmd)
            hit = is_dangerous(cmd)
            if not hit:
                found = push_hit(cmd)
                if found:
                    hit, why = found
    except Exception as exc:
        shapes, hit, why = [], None, None  # a crash is an allow...
        # ...EXCEPT on a push. Exit 1 lets the command run, so a hook that
        # crashes on a force push would wave it through (independent review
        # round 2 of the downstream copy, 2026-10-03). Plain substring tests: the regex engine may be what
        # failed. The command is NOT printed: it may carry a secret, and the
        # redactor may be what failed.
        if "git" in cmd and "push" in cmd:
            try:
                on = [n for n, _ in enabled_push_rules()]
            except Exception:
                on = [n for n, _ in PUSH_RULES]  # cannot read the config: fail closed
            if on:
                print(
                    f"BLOCKED [{on[0]}]: the safety hook failed "
                    f"({type(exc).__name__}) while reading a command that "
                    "mentions git push; refusing rather than letting it run "
                    "unchecked.",
                    file=sys.stderr,
                )
                sys.exit(2)

    def _ruling(name: str) -> str:
        builtin = {n for n, _ in BUILTIN_PATTERNS} | {n for n, _ in PUSH_RULES}
        return "" if name.startswith("project-") or name in builtin else f" ruling: {name}"

    if shapes:
        # Exit 2 is the ONLY code Claude Code treats as "block this tool call".
        # Note what is NOT in this message: the command, and the value. Only
        # the shape names, and what to do instead.
        #
        # A pattern that ALSO matched is still named. Pre-empting it silently
        # would make a project's named ruling — the commerce profile's
        # `no-live-keys` fires on exactly the literals this branch catches —
        # look unenforced, and `rulings-compile.py` sells that name to the
        # reader as the thing standing guard. Both reasons, no command text.
        also = f"\nAlso matches [{hit}]:{_ruling(hit)}" if hit else ""
        print(
            f"BLOCKED [{SECRET_RULE}]: {_secrets.advice(shapes)}{also}\n"
            "If this is genuinely not a secret, name "
            f"`{SECRET_RULE}` in .loopkit/block-disabled.txt (a reviewed "
            "change to a tracked file).",
            file=sys.stderr,
        )
        sys.exit(2)

    if hit and why:
        print(
            f"BLOCKED [{hit}]: force push or remote delete: {_secrets.redact(why)}: "
            f"{_secrets.redact(cmd)}\n"
            "If this is a false positive, name the rule in "
            ".loopkit/block-disabled.txt (a reviewed change), do not rephrase "
            "the command around the guard.",
            file=sys.stderr,
        )
        sys.exit(2)

    if hit:
        ruling = _ruling(hit)
        # `redact` and not `cmd`: this line printed the raw command until
        # 2026-09-07, so a `sk_live_` literal caught by the commerce profile's
        # `no-live-keys` pattern was echoed straight into the transcript. The
        # secret branch above catches most of it; this is the backstop for a
        # shape the table does not know yet.
        print(
            f"BLOCKED [{hit}]:{ruling} dangerous command pattern detected: {_secrets.redact(cmd)}\n"
            "If this is a false positive, name the pattern in "
            ".loopkit/block-disabled.txt (a reviewed change), do not rephrase "
            "the command around the guard.",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(0)
