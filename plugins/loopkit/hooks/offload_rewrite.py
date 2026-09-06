#!/usr/bin/env python3
"""offload_rewrite.py — PreToolUse on Bash: a known-noisy command runs capped.

A PostToolUse hook cannot shrink a result that already landed in context, and
a PreToolUse hook cannot see how big the result will be. What it CAN see is the
command's shape. So: when the command matches a line of
`.loopkit/offload-patterns.txt` (Python `re.search`), rewrite it to

    bash <plugin>/scripts/run-capped.sh --shell -- '<original, single-quoted>'

and the full output lands in `.loopkit/scratch/` with only head+tail in the
window. Passive when the file is absent or holds only blank and `#` lines.

Mechanism — Claude Code hooks reference, https://code.claude.com/docs/en/hooks.md
Quoted VERBATIM below, and byte-checked by tests/selftest.sh against
vendor/hooks-doc-excerpt.md, which records the document digest and the date it
was fetched. Two earlier versions of this header quoted sentences that are not
in the document — a summarising fetch's paraphrase pasted as if it were the
source, twice. Hence the check: a quote nothing compares is not a citation.
CITE-BEGIN
  `PreToolUse`: `updatedInput` directly under `hookSpecificOutput` replaces a tool's arguments before it runs. See [PreToolUse decision control](#pretooluse-decision-control)
  Modifies the tool's input parameters before execution. Replaces the entire input object, so include unchanged fields alongside modified ones. Claude Code evaluates permission rules and a Bash command's [auto-background eligibility](/docs/en/tools-reference#background-commands) against the input your hook returns, not the input Claude sent. Combine with `"allow"` to auto-approve, or `"ask"` to show the modified input to the user. For `"defer"`, ignored
CITE-END
Three consequences, in the order they bite:
1. "Replaces the entire input object" is why this hook echoes the whole
   `tool_input` back with only `command` changed. Emitting {"command": ...}
   alone silently dropped run_in_background, timeout and description from every
   rewritten call.
2. Permission rules are evaluated against the input the HOOK returns, so
   emitting `updatedInput` without `permissionDecision` prompts on the
   rewritten command and skips nothing. That is the intended behaviour here;
   `"allow"` would suppress the user's prompt and this hook has no business
   deciding permissions.
3. Auto-background eligibility is judged on the returned input too, so a
   wrapped command is judged as the wrapper — a reason to keep the wrapper
   thin rather than to avoid it.

Fail-open everywhere: any error → exit 0 with no stdout, the command runs as
typed. Unchanged when the command is empty, already wrapped, holds a heredoc
(`<<`) or a newline (single-quoting a multi-line script is a different job).
Project root: CLAUDE_PROJECT_DIR → `git rev-parse --show-toplevel` → cwd, the
same order every other hook here uses.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = PLUGIN_ROOT / "scripts" / "run-capped.sh"
PATTERNS_REL = Path(".loopkit") / "offload-patterns.txt"
METRICS_REL = Path(".loopkit") / "metrics.jsonl"

# `bash <token>` where the first token is the wrapper, quoted or bare.
_WRAPPED = re.compile(r"""\s*bash\s+(?:'([^']*)'|"([^"]*)"|(\S+))""")


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


def already_wrapped(cmd: str) -> bool:
    m = _WRAPPED.match(cmd)
    if not m:
        return False
    token = next((g for g in m.groups() if g is not None), "")
    return token.endswith("run-capped.sh")


def load_patterns(path: Path) -> list[tuple[str, "re.Pattern[str]"]]:
    """Every usable (source, compiled) pair. An invalid line is skipped with one
    stderr line; it is never a match and never a block (EARS 6)."""
    out: list[tuple[str, "re.Pattern[str]"]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append((line, re.compile(line)))
        except re.error as exc:
            print(f"LOOPKIT offload_rewrite: skipping invalid pattern {line!r}: {exc}", file=sys.stderr)
    return out


def record(root: Path, pattern: str, cmd: str) -> None:
    """One `offload_rewrite` line, one O_APPEND write — the same file and shape
    offload_nudge.py uses for `large_tool_output`."""
    try:
        m = root / METRICS_REL
        m.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                           "event": "offload_rewrite", "pattern": pattern[:80], "command": cmd[:120]}) + "\n"
        fd = os.open(m, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass


def decide(cmd: str, patterns: list[tuple[str, "re.Pattern[str]"]], wrapper: Path) -> tuple[str, str] | None:
    """Pure: (rewritten command, matching pattern source) or None for unchanged."""
    if not cmd or "\n" in cmd or "\r" in cmd or "<<" in cmd:
        return None
    if already_wrapped(cmd):
        return None
    for src, rx in patterns:
        if rx.search(cmd):
            return f"bash {shlex.quote(str(wrapper))} --shell -- {shlex.quote(cmd)}", src
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name", "Bash") != "Bash":
            return 0
        cmd = (payload.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str) or not cmd:
            return 0
        root = project_root()
        pfile = root / PATTERNS_REL
        if not pfile.is_file():
            return 0
        try:
            patterns = load_patterns(pfile)
        except Exception as exc:
            print(f"LOOPKIT offload_rewrite: cannot read {pfile}: {exc}", file=sys.stderr)
            return 0
        if not patterns:
            return 0
        if not WRAPPER.is_file():
            print(f"LOOPKIT offload_rewrite: wrapper missing at {WRAPPER}; command left unchanged", file=sys.stderr)
            return 0
        hit = decide(cmd, patterns, WRAPPER)
        if hit is None:
            return 0
        new_cmd, src = hit
        record(root, src, cmd)
        # updatedInput REPLACES the entire input object (doc, above), so carry
        # every field the caller sent and change only `command`.
        updated = dict(payload.get("tool_input") or {})
        updated["command"] = new_cmd
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                            "updatedInput": updated}}))
        sys.stdout.flush()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
