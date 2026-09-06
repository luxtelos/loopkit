#!/usr/bin/env python3
"""check-tools.py — the tool-surface audit.

From anthropic.com/engineering/writing-tools-for-agents and
claude.com/blog/seeing-like-an-agent: every tool is "one more option to think
about", so the bar for a server is high and every one must be accounted for.
This plugin's TOOLS.md contract says it in one line: no row, no server.

    check-tools.py [--root DIR] [--plugin DIR ...] [--strict]

Reads <root>/.mcp.json (mcpServers), each plugin's .mcp.json and
.claude-plugin/plugin.json mcpServers, and <root>/TOOLS.md. ERROR: a server
with no mention in TOOLS.md; a relative `command` or path argument (worktrees
launch MCP with a different cwd and a relative path dies silently); a
secret-looking literal in any value (reported by key path, never by value).
WARN: more than 10 servers. Exit 1 with --strict on errors.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SECRET = re.compile(r"\b(sk_(live|test)_[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|ctx7sk-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})")
MAX_SERVERS = 10


def project_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
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


def load_servers(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return {k: v for k, v in (servers or {}).items() if isinstance(v, dict)}


def walk_strings(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{prefix}[{i}]")
    elif isinstance(obj, str):
        yield prefix, obj


def is_relative_path(s: str) -> bool:
    return ("/" in s and not s.startswith(("/", "http://", "https://", "${", "$"))) or s.startswith("./")


def audit(servers: dict[str, dict], tools_md: str) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    mentioned = set(re.findall(r"`([^`\s]+)`", tools_md))
    for name, cfg in servers.items():
        if name not in mentioned:
            findings.append(("ERROR", f"{name}: no row in TOOLS.md (no row, no server)"))
        cmd = cfg.get("command")
        if isinstance(cmd, str) and is_relative_path(cmd):
            findings.append(("ERROR", f"{name}.command is a relative path: {cmd} — worktrees launch with another cwd"))
        for i, a in enumerate(cfg.get("args") or []):
            if isinstance(a, str) and is_relative_path(a) and not a.startswith("-"):
                findings.append(("ERROR", f"{name}.args[{i}] is a relative path: {a}"))
        for key, val in walk_strings(cfg, name):
            if SECRET.search(val):
                findings.append(("ERROR", f"{key}: secret-looking literal — reference an env var, never inline a value"))
    if len(servers) > MAX_SERVERS:
        findings.append(("WARN", f"{len(servers)} servers — past ~{MAX_SERVERS}; every tool is one more option the model must weigh; namespace or retire"))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--plugin", action="append", default=[])
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    root = project_root(args.root)
    servers = load_servers(root / ".mcp.json")
    for pl in args.plugin:
        servers.update(load_servers(Path(pl) / ".mcp.json"))
        servers.update(load_servers(Path(pl) / ".claude-plugin" / "plugin.json"))
    tools_md = (root / "TOOLS.md").read_text(encoding="utf-8") if (root / "TOOLS.md").exists() else ""
    if not servers:
        print("VERDICT: PASS — no MCP servers declared")
        return 0
    findings = audit(servers, tools_md)
    print(f"{len(servers)} server(s): " + ", ".join(sorted(servers)))
    for lvl, msg in findings:
        print(f"  {lvl}: {msg}")
    errors = sum(1 for lvl, _ in findings if lvl == "ERROR")
    if errors and args.strict:
        print(f"VERDICT: FAIL — {errors} error(s)")
        return 1
    print("VERDICT: PASS" + (f" with {len(findings)} note(s)" if findings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
