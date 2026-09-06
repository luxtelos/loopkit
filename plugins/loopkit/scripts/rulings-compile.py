#!/usr/bin/env python3
"""rulings-compile.py — which rulings are enforced by something that can fail?

A ruling that lives only as prose is re-asked and re-broken. Every concept of
type Invariant or Gate in the knowledge bundle must name its enforcing
artefact in frontmatter:

    enforced_by:
      - block-pattern: no-live-keys          # a NAMED line in .loopkit/block-patterns.txt
      - protected: (?:^|/)db/migrations/     # a regex in .loopkit/protected.txt
      - test: tests/billing/one-plan.test.ts # a test file that exists
      - model: specs/one-plan.model.fizz     # a model-checker property
      - hook: protect_tests.py               # a plugin/project hook by file name

    rulings-compile.py [--root DIR] [--strict]

Prints coverage (rulings with at least one artefact that exists / all
Invariant+Gate rulings) and lists the unenforced ones with what they say.
--strict exits 1 when coverage is below 100%. Exit 0 and "n=0" when the bundle
is absent — nothing is invented.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "loopkit_memory" / "vendor"))
import okf_bundle as okf  # noqa: E402

ENFORCED_TYPES = ("Invariant", "Gate")


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


def pattern_names(root: Path) -> set[str]:
    """Named extra block patterns: a `#name <name>` comment line right above the regex."""
    names: set[str] = set()
    f = root / ".loopkit" / "block-patterns.txt"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^#\s*name\s+(\S+)", line.strip())
            if m:
                names.add(m.group(1))
    return names


def protected_patterns(root: Path) -> set[str]:
    f = root / ".loopkit" / "protected.txt"
    if not f.exists():
        return set()
    return {l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}


def hook_names(root: Path) -> set[str]:
    names = {p.name for p in (PLUGIN_ROOT / "hooks").glob("*.py")} | {p.name for p in (PLUGIN_ROOT / "hooks").glob("*.sh")}
    for p in (root / ".claude" / "hooks").glob("*"):
        names.add(p.name)
    return names


def artefact_exists(root: Path, kind: str, ref: str) -> bool:
    if kind == "block-pattern":
        return ref in pattern_names(root)
    if kind == "protected":
        return ref in protected_patterns(root)
    if kind in ("test", "model"):
        return (root / ref).exists()
    if kind == "hook":
        return ref in hook_names(root)
    return False


def coverage(root: Path, bundle: Path) -> dict:
    out = {"rulings": [], "n": 0, "enforced": 0}
    if not bundle.exists():
        return out
    for path in okf.iter_concepts(bundle):
        try:
            fm, _ = okf.parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if fm.get("type") not in ENFORCED_TYPES:
            continue
        refs = fm.get("enforced_by") or []
        found: list[str] = []
        missing: list[str] = []
        for entry in refs if isinstance(refs, list) else []:
            if isinstance(entry, dict) and len(entry) == 1:
                (kind, ref), = entry.items()
                (found if artefact_exists(root, kind, str(ref)) else missing).append(f"{kind}: {ref}")
            elif isinstance(entry, str) and ":" in entry:
                kind, ref = entry.split(":", 1)
                (found if artefact_exists(root, kind.strip(), ref.strip()) else missing).append(entry)
        row = {"path": okf.bundle_path_of(bundle, path), "type": fm.get("type"), "title": fm.get("title", path.stem),
               "found": found, "missing": missing}
        out["rulings"].append(row)
        out["n"] += 1
        if found:
            out["enforced"] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root"); ap.add_argument("--bundle", default="knowledge"); ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    root = project_root(a.root)
    c = coverage(root, root / a.bundle)
    if c["n"] == 0:
        print("RULINGS: n=0 (no Invariant/Gate concepts in the bundle, or no bundle)")
        return 0
    print(f"RULINGS: {c['enforced']}/{c['n']} Invariant+Gate concepts have an enforcing artefact that exists")
    for r in c["rulings"]:
        mark = "ok  " if r["found"] else "NONE"
        print(f"  {mark} {r['type']:<9} {r['path']}  — {r['title'][:70]}")
        for f in r["found"]:
            print(f"        enforced by {f}")
        for m in r["missing"]:
            print(f"        MISSING artefact {m}")
    if a.strict and c["enforced"] < c["n"]:
        print("VERDICT: FAIL — a ruling without an enforcing artefact is a rule the loop is told, not a gate")
        return 1
    print("VERDICT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
