#!/usr/bin/env python3
"""count_approvals.py — count permission prompts per session; never decide one.

The oversight paper's point (Mitchell, Ghosh, Passi 2026, arXiv 2608.23642):
after enough "allow" clicks the human is in the loop only nominally. This hook
does not remove the prompt and does not answer it — it exits 0 with no output,
so Claude Code asks the user exactly as before. It only counts, into
$TMPDIR/loopkit-approvals-<session>/count, and loop_doctrine.py reads that
number to inject ONE line once it passes LOOPKIT_APPROVAL_FATIGUE_N (30).
Fail-open always.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


def session_key(payload: dict) -> str:
    for c in (payload.get("session_id"), os.environ.get("CLAUDE_SESSION_ID"), payload.get("transcript_path"), str(os.getppid())):
        if c:
            return hashlib.sha256(str(c).encode()).hexdigest()[:16]
    return "unknown"


def counter_path(key: str) -> Path:
    d = Path(tempfile.gettempdir()) / f"loopkit-approvals-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d / "count"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        p = counter_path(session_key(payload))
        n = int(p.read_text().strip() or 0) if p.exists() else 0
        p.write_text(str(n + 1))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
