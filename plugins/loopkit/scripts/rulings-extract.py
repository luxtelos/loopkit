#!/usr/bin/env python3
"""rulings-extract.py — the rulings ledger: find what a human already decided.

Scans the places rulings hide — RESOLVED sections of inbox/needs-human.md,
ADRs under docs/adr/, "owner ruling"/"ruled"/"ratified" lines in specs/ and
state/ — and turns each into a candidate knowledge concept. Dry run by
default: it prints the table and writes nothing. With --apply it enqueues one
upsert per ruling through the OKF actor; a human drains and ratifies.

Why: the re-ask rate is the metric. A ruling that lives only in a thread is
asked again; one in the bundle answers `memory.py recall` and can carry an
`enforced_by`.

Source citation rules (the bundle refuses the rest at author time): ADR and
spec files are cited as sources; inbox sections are NOT (the inbox is a
mutable queue) — the heading and date go into the body instead.

    rulings-extract.py [--root DIR] [--apply] [--by process:loopkit/rulings-extract]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

RESOLVED = re.compile(r"^##\s+(?:RESOLVED|CLOSED|SUPERSEDED|ANSWERED|DONE)\b[^\n]*?(\d{4}-\d{2}-\d{2})?[^\n]*?[—–-]\s*(.+?)\s*$", re.M)
RULED_LINE = re.compile(r"^[^\n]*\b(owner (?:ruling|ruled|rule)|ratified|ruled that|ruling:)\b[^\n]*$", re.I | re.M)
ADR_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.M)
ADR_DECISION = re.compile(r"^##\s+Decision\s*\n(.+?)(?=^##\s|\Z)", re.M | re.S)


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


def classify(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(never|always|trap|gotcha|do not|don't)\b", t) and re.search(r"\b(bit|broke|fails?|blocked)\b", t):
        return "Trap"
    if re.search(r"\b(shall|invariant|one .{0,20} per|exactly one|must never|unique)\b", t):
        return "Invariant"
    if re.search(r"\b(hook|gate|blocks?|refuse[sd]?)\b", t):
        return "Gate"
    return "Decision"


STAMP = re.compile(r"\b(RESOLVED|CLOSED|ANSWERED|SUPERSEDED|WITHDRAWN|DONE|COMPLETE|COMPLETED|MERGED|SHIPPED|LANDED)\b")


def clean_title(heading: str) -> str:
    """The finding's own title: strike-through, a leading [tag], the stamp word,
    dates and the separators around them removed; the date keeps its dashes."""
    t = heading.replace("~~", "")
    t = re.sub(r"^\s*\[[^\]]+\]\s*", "", t)
    # A stamp is a stamp only at the START ("RESOLVED 2026-… — title") or
    # after a trailing separator ("title — RESOLVED 2026-…"); a stamp word
    # inside the title ("Spec v3 SHIPPED: PR open") is part of the title.
    t = re.sub(r"^\s*" + STAMP.pattern + r"\s*(\d{4}-\d{2}-\d{2})?(\s*\(EOD\))?\s*[:—–-]*\s*", "", t)
    t = re.sub(r"\s*[—–-]\s*" + STAMP.pattern + r"\s*(\d{4}-\d{2}-\d{2})?\s*$", "", t)
    t = re.sub(r"^\s*\d{4}-\d{2}-\d{2}(\s*\(EOD\))?\s*[:—–-]*\s*", "", t)
    t = re.sub(r"^[\s:—–-]+|[\s:—–-]+$", "", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip() or heading.strip()


def slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-") or "ruling"


def extract(root: Path) -> list[dict]:
    out: list[dict] = []
    inbox = root / "inbox" / "needs-human.md"
    if inbox.exists():
        # One recogniser of "closed", shared with the inbox bridge: a struck
        # heading, a leading [tag], or an UPPERCASE stamp anywhere in it.
        import importlib.util
        spec = importlib.util.spec_from_file_location("i2t", Path(__file__).resolve().parent / "inbox_to_triage.py")
        i2t = importlib.util.module_from_spec(spec); sys.modules["i2t"] = i2t; spec.loader.exec_module(i2t)
        text = inbox.read_text(encoding="utf-8")
        for m in re.finditer(r"^##\s+(.+?)\s*$", text, re.M):
            heading = m.group(1)
            if not i2t.is_closed(heading):
                continue
            end = text.find("\n## ", m.end())
            body = text[m.end():end if end > 0 else len(text)].strip()
            date = re.search(r"\d{4}-\d{2}-\d{2}", heading)
            title = clean_title(heading)
            out.append({"kind": "inbox", "title": title[:120], "date": date.group(0) if date else "", "type": classify(heading + " " + body[:600]),
                        "sources": [], "quote": body[:800], "origin": f"inbox/needs-human.md § {heading[:80]}"})
    for adr in sorted((root / "docs" / "adr").glob("*.md")) if (root / "docs" / "adr").is_dir() else []:
        text = adr.read_text(encoding="utf-8", errors="replace")
        t = ADR_TITLE.search(text)
        d = ADR_DECISION.search(text)
        if t:
            out.append({"kind": "adr", "title": t.group(1)[:120], "date": "", "type": "Decision", "sources": [str(adr.relative_to(root))],
                        "quote": (d.group(1).strip() if d else text[:600])[:800], "origin": str(adr.relative_to(root))})
    for folder in ("specs", "state"):
        for f in sorted((root / folder).glob("*.md")) if (root / folder).is_dir() else []:
            if f.name in ("triage.md", "progress.md"):
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            for m in RULED_LINE.finditer(text):
                line = m.group(0).strip()
                if len(line) < 25:
                    continue
                date = re.search(r"\d{4}-\d{2}-\d{2}", line)
                out.append({"kind": folder, "title": re.sub(r"^[-*>\s]+", "", line)[:120], "date": date.group(0) if date else "",
                            "type": classify(line), "sources": [str(f.relative_to(root))] if folder == "specs" else [],
                            "quote": line[:800], "origin": f"{f.relative_to(root)}"})
    seen: set[str] = set()
    uniq = []
    for r in out:
        k = slug(r["title"])
        if k in seen:
            continue
        seen.add(k); r["slug"] = k; uniq.append(r)
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root"); ap.add_argument("--apply", action="store_true"); ap.add_argument("--by", default="process:loopkit/rulings-extract")
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()
    root = project_root(a.root)
    rulings = extract(root)[: a.limit]
    print(f"RULINGS FOUND: {len(rulings)} (inbox RESOLVED sections, ADR decisions, ruled lines in specs/ and state/)")
    for r in rulings:
        print(f"  [{r['type']:<9}] {r['title'][:80]}  <- {r['origin'][:60]}")
    if not a.apply:
        print("\ndry run — nothing written. Re-run with --apply to enqueue one upsert per ruling, then: memory.py knowledge drain")
        return 0
    from loopkit_memory import load
    kn = load(root).get("knowledge")
    if not hasattr(kn, "enqueue"):
        print(f"cannot enqueue: knowledge adapter unavailable ({kn.available()[1]})"); return 2
    n = 0
    for r in rulings:
        fm = {"type": r["type"], "title": r["title"], "status": "draft",
              "tags": ["domain/rulings", f"origin/{r['kind']}"] + ([f"date/{r['date']}"] if r["date"] else [])}
        payload = {"frontmatter": fm, "sources": [{"id": Path(s).stem, "resource": s, "title": s} for s in r["sources"]]}
        body = f"## The ruling\n\n{r['quote']}\n\n## Origin\n\n{r['origin']}{(' (' + r['date'] + ')') if r['date'] else ''}\n\n## Enforced by\n\n(none yet — add `enforced_by` when a gate, test or pattern exists)\n"
        rc, out = kn.enqueue(op="upsert", reason=f"rulings-extract: {r['title'][:150]}", by=a.by, target=f"/rulings/{r['slug']}.md", payload=payload, body=body)
        n += 1 if rc == 0 else 0
    print(f"\nENQUEUED {n}/{len(rulings)}. Next: memory.py knowledge drain, then a human ratifies (status: stable) what is real.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
