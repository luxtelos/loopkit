#!/usr/bin/env python3
"""check-claude-md.py — the CLAUDE.md budget, as a check instead of a hope.

Anthropic's guidance (code.claude.com/docs/en/best-practices): keep it short,
test each line with "would removing this cause Claude to make mistakes?",
emphasise ONE line only — "if you emphasize many lines, none of them stands
out" — and never restate what is derivable from code. And the compliance
ceiling this plugin's own doctrine cites: roughly 150–200 standing instructions
before adherence degrades.

    check-claude-md.py [--root DIR] [--strict]

Reports: line count, standing instructions (bullets + SHALL lines), emphasised
lines (NEVER/ALWAYS/IMPORTANT/MUST/**bold**), duplicates against
constitution.md and the three contracts, and lines that merely restate a
package.json script. Exit 1 with --strict when an ERROR-level finding exists
(> 200 instructions, or duplicates); warnings never fail the run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SIBLINGS = ("constitution.md", "FILES.md", "TOOLS.md", "COMMANDS.md", "AGENTS.md")
EMPHASIS = re.compile(r"\b(NEVER|ALWAYS|IMPORTANT|MUST)\b|\*\*[^*]+\*\*")
BULLET = re.compile(r"^\s*[-*]\s+\S")
CEILING_WARN, CEILING_ERR = 150, 200


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


def norm(line: str) -> str:
    s = re.sub(r"^\s*[-*]\s+", "", line).strip().lower()
    s = re.sub(r"[`*_]", "", s)
    return re.sub(r"\s+", " ", s)


def lint(text: str, siblings: dict[str, str], package_scripts: set[str]) -> dict:
    lines = text.splitlines()
    non_blank = [l for l in lines if l.strip()]
    standing = [l for l in lines if BULLET.match(l) or re.search(r"\bSHALL\b", l)]
    emphasised = [(i + 1, l) for i, l in enumerate(lines) if EMPHASIS.search(l) and not l.lstrip().startswith("#")]
    sib_norm: dict[str, str] = {}
    for name, body in siblings.items():
        for l in body.splitlines():
            if BULLET.match(l) and len(norm(l)) > 40:
                sib_norm.setdefault(norm(l), name)
    duplicates = [(i + 1, sib_norm[norm(l)], l.strip()[:80]) for i, l in enumerate(lines)
                  if BULLET.match(l) and norm(l) in sib_norm]
    derivable = []
    for i, l in enumerate(lines):
        m = re.search(r"npm run (\S+)", l)
        if m and m.group(1) in package_scripts and BULLET.match(l) and len(l) < 90:
            derivable.append((i + 1, l.strip()[:80]))
    findings: list[tuple[str, str]] = []
    if len(standing) > CEILING_ERR:
        findings.append(("ERROR", f"{len(standing)} standing instructions — past the ~{CEILING_ERR} ceiling; deletion is the work, not rewording"))
    elif len(standing) > CEILING_WARN:
        findings.append(("WARN", f"{len(standing)} standing instructions — inside the {CEILING_WARN}–{CEILING_ERR} band where compliance degrades"))
    if len(emphasised) > 1:
        findings.append(("WARN", f"{len(emphasised)} emphasised lines (NEVER/ALWAYS/IMPORTANT/MUST/bold) — emphasise one; when many stand out, none does: lines " + ", ".join(str(n) for n, _ in emphasised[:8])))
    for n, where, snippet in duplicates:
        findings.append(("ERROR", f"line {n} restates a rule already in {where}: {snippet}"))
    for n, snippet in derivable:
        findings.append(("WARN", f"line {n} restates a package.json script — derivable from code: {snippet}"))
    return {
        "lines": len(lines), "non_blank": len(non_blank), "standing": len(standing),
        "emphasised": len(emphasised), "duplicates": len(duplicates), "derivable": len(derivable),
        "findings": findings,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--file", default="CLAUDE.md")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    root = project_root(args.root)
    target = root / args.file
    if not target.exists():
        print(f"VERDICT: SKIP — no {args.file} at {root}")
        return 0
    siblings = {s: (root / s).read_text(encoding="utf-8") for s in SIBLINGS if (root / s).exists()}
    scripts: set[str] = set()
    pkg = root / "package.json"
    if pkg.exists():
        try:
            scripts = set((json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}).keys())
        except Exception:
            scripts = set()
    r = lint(target.read_text(encoding="utf-8"), siblings, scripts)
    print(f"{args.file}: {r['lines']} lines, {r['non_blank']} non-blank, {r['standing']} standing instructions, "
          f"{r['emphasised']} emphasised, {r['duplicates']} duplicated, {r['derivable']} derivable")
    for level, msg in r["findings"]:
        print(f"  {level}: {msg}")
    errors = sum(1 for lvl, _ in r["findings"] if lvl == "ERROR")
    if errors and args.strict:
        print(f"VERDICT: FAIL — {errors} error(s)")
        return 1
    print("VERDICT: PASS" + (f" with {len(r['findings'])} note(s)" if r["findings"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
