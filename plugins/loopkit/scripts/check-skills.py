#!/usr/bin/env python3
"""check-skills.py — lint SKILL.md files against Anthropic's skill guidance.

From anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
and claude.com/blog/lessons-from-building-claude-code-how-we-use-skills:
frontmatter `name` + `description` written for the model's selection logic (say
WHEN to use it); "the highest-signal content in any skill is the Gotchas
section"; split when unwieldy; be explicit whether a script is RUN or READ.

    check-skills.py [--root DIR] [--plugin DIR ...] [--strict]

Scans <root>/.claude/skills/*/SKILL.md and <plugin>/skills/*/SKILL.md.
ERROR: missing frontmatter/name/description, description over 1024 chars,
no `## Gotchas` section. WARN: no trigger phrase in the description, body over
500 lines, a script path mentioned without a run/read verb on the same line,
no evals/. Exit 1 with --strict on errors.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

TRIGGER = re.compile(r"\b(use (when|for|whenever|on|this|it)|trigger|when (the|a|you|someone)|invoke)\b", re.I)
SCRIPT_REF = re.compile(r"(?<![\w/])([\w./-]+\.(?:sh|py|mjs|js))\b")
RUN_OR_READ = re.compile(r"\b(bash|sh|python3?|node|run|Read|read|reads|cat|source)\b")
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


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


def parse_front(text: str) -> dict[str, str]:
    m = FRONT.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def lint_skill(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    fm = parse_front(text)
    body = FRONT.sub("", text, count=1)
    f: list[tuple[str, str]] = []
    if not fm:
        f.append(("ERROR", "no YAML frontmatter (needs name + description)"))
    else:
        if not fm.get("name"):
            f.append(("ERROR", "frontmatter has no `name`"))
        desc = fm.get("description", "")
        if not desc:
            f.append(("ERROR", "frontmatter has no `description` — the model selects on it"))
        elif len(desc) > 1024:
            f.append(("ERROR", f"description is {len(desc)} chars (limit 1024)"))
        elif not TRIGGER.search(desc):
            f.append(("WARN", "description says what, not WHEN — add a trigger phrase ('Use when …')"))
    if not re.search(r"^##+\s+Gotchas\b", body, re.M):
        f.append(("ERROR", "no `## Gotchas` section — the highest-signal part of a skill"))
    n = len(body.splitlines())
    if n > 500:
        f.append(("WARN", f"body is {n} lines — split into references/ (progressive disclosure)"))
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            continue
        for m in SCRIPT_REF.finditer(line):
            if not RUN_OR_READ.search(line):
                f.append(("WARN", f"script mentioned without run/read verb: {m.group(1)}"))
                break
    if not (path.parent / "evals").exists():
        f.append(("WARN", "no evals/ directory"))
    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--plugin", action="append", default=[])
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    root = project_root(args.root)
    targets = sorted((root / ".claude" / "skills").glob("*/SKILL.md"))
    for pl in args.plugin:
        targets += sorted((Path(pl) / "skills").glob("*/SKILL.md"))
    if not targets:
        print("VERDICT: SKIP — no SKILL.md found")
        return 0
    errors = 0
    for t in targets:
        findings = lint_skill(t)
        errors += sum(1 for lvl, _ in findings if lvl == "ERROR")
        label = t.parent.name
        if not findings:
            print(f"  ok    {label}")
        for lvl, msg in findings:
            print(f"  {lvl:5} {label}: {msg}")
    print(f"Checked {len(targets)} skill(s); {errors} error(s).")
    if errors and args.strict:
        print("VERDICT: FAIL")
        return 1
    print("VERDICT: PASS" if not errors else "VERDICT: PASS (errors present; --strict to fail)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
