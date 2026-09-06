#!/usr/bin/env python3
"""check-duplicate-hooks.py — a project that carries its own copies of the
plugin's hooks fires both. Harmless (same guard, same exit code) but noisy,
and it hides which copy is stale. Reports each project hook whose script
basename matches a plugin hook.

    check-duplicate-hooks.py [--root DIR] --plugin DIR
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


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


def commands(hooks_obj) -> list[str]:
    out: list[str] = []
    for event, entries in (hooks_obj or {}).items():
        for entry in entries or []:
            for h in (entry or {}).get("hooks", []) or []:
                if isinstance(h, dict) and h.get("type") == "command" and h.get("command"):
                    out.append(str(h["command"]))
    return out


def basenames(cmds: list[str]) -> set[str]:
    names = set()
    for c in cmds:
        for m in re.finditer(r"([\w.-]+\.(?:py|sh|mjs|js))\b", c):
            names.add(m.group(1))
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--plugin", required=True)
    args = ap.parse_args()
    root = project_root(args.root)
    try:
        plugin_hooks = json.loads((Path(args.plugin) / "hooks" / "hooks.json").read_text(encoding="utf-8")).get("hooks", {})
    except Exception:
        print("VERDICT: SKIP — plugin hooks.json unreadable")
        return 0
    plugin_names = basenames(commands(plugin_hooks))
    dupes: list[tuple[str, str]] = []
    for settings in ("settings.json", "settings.local.json"):
        sp = root / ".claude" / settings
        if not sp.exists():
            continue
        try:
            hooks = json.loads(sp.read_text(encoding="utf-8")).get("hooks", {})
        except Exception:
            continue
        for name in sorted(basenames(commands(hooks)) & plugin_names):
            dupes.append((settings, name))
    if not dupes:
        print("VERDICT: PASS — no project hook duplicates a plugin hook")
        return 0
    for settings, name in dupes:
        print(f"  DUPLICATE: .claude/{settings} wires {name}, which the plugin also wires — both fire; delete the project copy")
    print(f"VERDICT: {len(dupes)} duplicate hook(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
