#!/usr/bin/env python3
"""offload_nudge.py — PostToolUse on Bash: when a tool result is large, say so.

Never blocks, never rewrites the result (PostToolUse cannot). It counts the
event into .loopkit/metrics.jsonl and prints one stderr line pointing at
scripts/run-capped.sh, so the next big command goes to a file. Threshold:
LOOPKIT_OFFLOAD_BYTES (default 8192).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

THRESHOLD = int(os.environ.get("LOOPKIT_OFFLOAD_BYTES", "8192") or 8192)
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


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


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        resp = payload.get("tool_response")
        text = resp if isinstance(resp, str) else json.dumps(resp) if resp is not None else ""
        size = len(text.encode("utf-8", "replace"))
        if size < THRESHOLD:
            return 0
        root = project_root()
        cmd = str((payload.get("tool_input") or {}).get("command") or "")[:120]
        try:
            m = root / ".loopkit" / "metrics.jsonl"
            m.parent.mkdir(parents=True, exist_ok=True)
            with m.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                     "event": "large_tool_output", "bytes": size, "command": cmd}) + "\n")
        except Exception:
            pass
        print(f"LOOPKIT: that Bash result was {size // 1024} KB of context. Next time wrap it: "
              f"bash {PLUGIN_ROOT / 'scripts' / 'run-capped.sh'} -- <command>  (full output to .loopkit/scratch/, head+tail in context)",
              file=sys.stderr)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
