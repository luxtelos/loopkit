#!/usr/bin/env python3
"""notify_needs_human.py — ping a chat webhook when inbox/needs-human.md changes.

The loop drops "I need a human" notes into a markdown file, but nobody stares
at that file all day. This PostToolUse hook watches every Write, Edit and Bash
call the agent makes; if the target is inbox/needs-human.md, it sends ONE
message so a human actually finds out. Bash is inspected too, because agents
often append with `cat >> inbox/needs-human.md`, and those writes used to skip
the notification for days.

Fail-open by design: missing webhook, chat outage, bad JSON — exit 0 in every
case and NEVER block the agent. Worst case the note just sits in the file,
exactly as before this hook existed.

Setup (one value, never committed):

  LOOPKIT_NEEDS_HUMAN_WEBHOOK   an incoming-webhook URL. Set it in the shell
                                Claude Code runs in, or in <project>/.loopkit/config.env
                                (gitignored by /loopkit:init).
  LOOPKIT_WEBHOOK_FORMAT        slack (default) | discord | generic
  LOOPKIT_PROJECT_NAME          shown in the message; defaults to the repo dir name
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

INBOX_SUFFIX = "inbox/needs-human.md"


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


def config_env(root: Path) -> dict[str, str]:
    """KEY=VALUE lines from .loopkit/config.env; the process env wins."""
    out: dict[str, str] = {}
    try:
        for line in (root / ".loopkit" / "config.env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def setting(name: str, root: Path, default: str = "") -> str:
    return os.environ.get(name) or config_env(root).get(name) or default


def message_body(fmt: str, text: str, project: str, branch: str) -> bytes:
    if fmt == "discord":
        payload = {"content": text}
    elif fmt == "generic":
        payload = {"text": text, "project": project, "branch": branch, "file": INBOX_SUFFIX}
    else:  # slack-compatible incoming webhook
        payload = {"text": text}
    return json.dumps(payload).encode()


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if not isinstance(payload, dict):
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    file_path = str(tool_input.get("file_path", ""))
    command = str(tool_input.get("command", ""))
    if not (file_path.endswith(INBOX_SUFFIX) or INBOX_SUFFIX in command):
        sys.exit(0)

    root = project_root()
    url = setting("LOOPKIT_NEEDS_HUMAN_WEBHOOK", root)
    if not url:
        sys.exit(0)  # not configured — silently do nothing

    fmt = setting("LOOPKIT_WEBHOOK_FORMAT", root, "slack").lower()
    project = setting("LOOPKIT_PROJECT_NAME", root, root.name)
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown-branch"
    except Exception:
        branch = "unknown-branch"

    text = (
        f":rotating_light: *needs-human* updated on `{branch}` ({project}) — "
        f"review `{INBOX_SUFFIX}`."
    )
    try:
        req = urllib.request.Request(
            url,
            data=message_body(fmt, text, project, branch),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # a chat hiccup must never block the agent over a notification

    sys.exit(0)


if __name__ == "__main__":
    main()
