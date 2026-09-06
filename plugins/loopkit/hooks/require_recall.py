#!/usr/bin/env python3
"""require_recall.py — block authoring a storage invariant until memory was consulted.

The case this is built on: a UNIQUE index that enforced "one row per user" was
wrong about what a row means. The fact was recorded twice — a memory drawer
describing the same defect one layer up, and a code graph showing the column
written in 175 places — and neither was read. The migration revoked live
assignments. A loop with strong GENERATION discipline and no RECALL discipline
writes findings faithfully and reads them never.

Injection did not fix it (the protocol was already written down and skipped);
a refusal does. Same lesson as require_contracts.py.

Scope, deliberately narrow: only writes that create a DB-level invariant —
migration files by path, UNIQUE / PRIMARY KEY / CONSTRAINT … CHECK / EXCLUDE
by content on code-like files, and Bash that redirects, copies or moves into a
migrations directory. Rules come from <project>/.loopkit/recall-triggers.txt
(defaults below). Prose files are exempt from the content rule: documentation
about invariants is not an invariant, and gating it trains bypasses.

What counts as "memory consulted": whatever the registry's adapters say —
an MCP tool name or a Bash command matching an adapter's recall pattern this
session. The hook never names a server. Mark mode (PostToolUse) records it.

Outage escape, audited: when the content rule fires and the memory tools are
genuinely down, write the reason (non-empty) into
$TMPDIR/loopkit-recall-<session>/recall-unavailable. Never honoured for a real
migration write — those wait for memory or for a human.

Passive until .loopkit/memory.json exists. Exit 2 blocks; everything else 0.
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

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

DEFAULT_RULES = {
    "path": [r"(^|/)(db/)?migrations/.*\.sql$"],
    "content": [r"\b(UNIQUE\s+INDEX|UNIQUE\s*\(|PRIMARY\s+KEY|"
                r'CONSTRAINT\s+("[^"\n]+"|\w+)[^\n]{0,80}?CHECK\s*\(|EXCLUDE\s+USING|'
                r"ADD\s+CONSTRAINT)\b"],
    "bash": [r"(>>?\s*[^\n;|&]*migrations/[^\n;|&]*\.sql"
             r"|(?:^|[;|&]\s*|\$\(\s*)(?:tee\s+(?:-a\s+)?|(?:cp|mv|install)\s+)"
             r"[^\n;|&]*migrations/[^\n;|&]*\.sql)"],
}
PROSE_PATH = re.compile(r"\.(md|mdx|txt|rst|adoc)$", re.I)


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


def load_rules(root: Path) -> dict[str, list[re.Pattern[str]]]:
    raw: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_RULES.items()}
    f = root / ".loopkit" / "recall-triggers.txt"
    if f.exists():
        parsed: dict[str, list[str]] = {"path": [], "content": [], "bash": []}
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            kind, rx = line.split(":", 1)
            kind = kind.strip().lower()
            if kind in parsed and rx.strip():
                parsed[kind].append(rx.strip())
        if any(parsed.values()):
            raw = parsed
    out: dict[str, list[re.Pattern[str]]] = {}
    for kind, rxs in raw.items():
        compiled = []
        for rx in rxs:
            try:
                compiled.append(re.compile(rx, re.I | re.M))
            except re.error:
                continue
        out[kind] = compiled
    return out


def session_key(payload: dict) -> str:
    for c in (payload.get("session_id"), os.environ.get("CLAUDE_SESSION_ID"), payload.get("transcript_path"), str(os.getppid())):
        if c:
            return hashlib.sha256(str(c).encode()).hexdigest()[:16]
    return "unknown"


def marker_dir(key: str) -> Path:
    d = Path(tempfile.gettempdir()) / f"loopkit-recall-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    mode = sys.argv[1] if len(sys.argv) > 1 else "gate"
    root = project_root()
    try:
        from loopkit_memory import load as load_registry
        reg = load_registry(root)
    except Exception:
        return 0  # the registry failing to import must never lock the session
    if not reg.configured:
        return 0  # passive until the project opts in

    key = session_key(payload)
    flag = marker_dir(key) / "queried"
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}

    if mode == "mark":
        if reg.is_recall(tool, ti):
            flag.touch()
        return 0

    rules = load_rules(root)
    migration_scope = False
    touches = False
    target = ""
    if tool == "Bash":
        cmd = str(ti.get("command") or "")
        touches = migration_scope = any(rx.search(cmd) for rx in rules["bash"])
        target = "(Bash write into a migrations directory)" if touches else ""
    elif tool in ("Write", "Edit", "MultiEdit"):
        target = str(ti.get("file_path") or "")
        body = " ".join(str(ti.get(k) or "") for k in ("content", "new_string"))
        for e in ti.get("edits") or []:
            if isinstance(e, dict):
                body += " " + str(e.get("new_string") or "")
        migration_scope = any(rx.search(target) for rx in rules["path"])
        touches = migration_scope or (not PROSE_PATH.search(target) and any(rx.search(body) for rx in rules["content"]))
    else:
        return 0
    if not touches or flag.exists():
        return 0

    outage = marker_dir(key) / "recall-unavailable"
    if not migration_scope:
        try:
            if outage.exists() and outage.read_text().strip():
                return 0
        except OSError:
            pass

    memcli = PLUGIN_ROOT / "scripts" / "memory.py"
    print(
        "BLOCKED — you are authoring a STORAGE INVARIANT without having consulted memory this session.\n\n"
        f"  target: {target or '(inline SQL)'}\n"
        f"  {reg.status_line()}\n\n"
        "Consult BOTH before writing it:\n"
        f"  1. python3 {memcli} recall \"<table or column>\"   — has this invariant bitten before?\n"
        f"  2. python3 {memcli} graph callers <symbol>        — which functions write these columns?\n"
        "     A UNIQUE constraint is a claim about what ONE ROW MEANS. Enumerate every row kind the\n"
        "     predicate captures, especially via the columns you are NOT keying on.\n\n"
        "Then state, in the migration itself, what one row means and which row kinds the predicate captures.\n"
        + ("" if migration_scope else
           f"\nIf memory is genuinely unreachable this session, write the reason into {outage} and retry —\n"
           "content-rule writes only; the note is audited. Real migration writes are never exempted.\n"),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
