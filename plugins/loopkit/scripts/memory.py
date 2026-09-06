#!/usr/bin/env python3
"""memory.py — one CLI over the memory registry.

    memory.py status [--line] [--no-cache]
    memory.py recall "<query>" [--limit N]
    memory.py remember --title T (--body B | --file F) [--tags a,b] [--supersedes ID]
    memory.py invalidate <id> --reason "<why>"
    memory.py wakeup
    memory.py graph find <pattern> [--label L] | callers <sym> | callees <sym> | snippet <qualified> | impact
    memory.py knowledge status|enqueue|drain|verify|get …      (0.2.0-c)

Global: --root DIR, --format concise|detailed. Every output begins with the
adapter banner ("ADAPTER: graph=codebase-memory [available]" or
"[DEGRADED: not indexed — …]") so the reader always knows which path answered.

concise (default) caps output at 30 lines and writes the full result to
.loopkit/scratch/<ts>-<verb>.txt, printing the path — the token-lean mode.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

from loopkit_memory import load, project_root  # noqa: E402

CONCISE_LINES = 30


def emit(text: str, root: Path, verb: str, fmt: str) -> None:
    lines = text.rstrip("\n").splitlines()
    if fmt == "detailed" or len(lines) <= CONCISE_LINES:
        print(text.rstrip("\n"))
        return
    scratch = root / ".loopkit" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / f"{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{verb}.txt"
    path.write_text(text, encoding="utf-8")
    print("\n".join(lines[:CONCISE_LINES]))
    print(f"… {len(lines) - CONCISE_LINES} more lines in {path.relative_to(root)} (use --format detailed to inline)")


def rows_to_text(rows) -> str:
    if isinstance(rows, dict):
        return json.dumps(rows, indent=1)
    out = []
    for r in rows or []:
        if isinstance(r, dict):
            if "error" in r:
                out.append(f"ERROR: {r['error']}")
            else:
                fp = r.get("file_path") or r.get("file") or ""
                ln = r.get("start_line") or r.get("line") or ""
                name = r.get("qualified_name") or r.get("name") or r.get("text") or ""
                extra = r.get("note") or ""
                out.append(f"- {fp}{':' + str(ln) if ln else ''}  {name}  {extra}".rstrip())
        else:
            out.append(f"- {r}")
    return "\n".join(out) if out else "(no results)"


def main() -> int:
    # --root / --format are accepted before OR after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=None)
    common.add_argument("--format", choices=["concise", "detailed"], default=None)
    ap = argparse.ArgumentParser(parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", parents=[common]); s.add_argument("--line", action="store_true"); s.add_argument("--no-cache", action="store_true")
    r = sub.add_parser("recall", parents=[common]); r.add_argument("query"); r.add_argument("--limit", type=int, default=8)
    m = sub.add_parser("remember", parents=[common]); m.add_argument("--title", required=True); m.add_argument("--body"); m.add_argument("--file"); m.add_argument("--tags"); m.add_argument("--supersedes")
    i = sub.add_parser("invalidate", parents=[common]); i.add_argument("id"); i.add_argument("--reason", required=True)
    sub.add_parser("wakeup", parents=[common])
    g = sub.add_parser("graph", parents=[common]); gs = g.add_subparsers(dest="verb", required=True)
    f = gs.add_parser("find", parents=[common]); f.add_argument("pattern"); f.add_argument("--label"); f.add_argument("--limit", type=int, default=20)
    gs.add_parser("callers", parents=[common]).add_argument("symbol"); gs.add_parser("callees", parents=[common]).add_argument("symbol")
    gs.add_parser("snippet", parents=[common]).add_argument("qualified_name"); gs.add_parser("impact", parents=[common])
    k = sub.add_parser("knowledge", parents=[common]); k.add_argument("verb", nargs="?", default="status"); k.add_argument("args", nargs="*")
    a = ap.parse_args()
    a.format = a.format or "concise"

    root = project_root(a.root)
    reg = load(root)
    if not reg.configured:
        print("MEMORY: not configured — no .loopkit/memory.json (run /loopkit:init, or copy the template)")
        return 0

    if a.cmd == "status":
        if a.line:
            print(reg.status_line(use_cache=not a.no_cache)); return 0
        out = [reg.status_line(use_cache=not a.no_cache)]
        for ad in reg.adapters.values():
            out.append(ad.banner())
        emit("\n".join(out), root, "status", a.format); return 0

    if a.cmd in ("recall", "remember", "invalidate", "wakeup"):
        mem = reg.get("memory")
        head = mem.banner()
        if a.cmd == "recall":
            facts = mem.recall(a.query, limit=a.limit)
            body = "\n".join(fc.line() for fc in facts) or "(no facts recalled — say so in your reasoning; do not guess)"
            emit(f"{head}\nRECALL \"{a.query}\":\n{body}", root, "recall", a.format)
        elif a.cmd == "remember":
            body = a.body or (Path(a.file).read_text(encoding="utf-8") if a.file else "")
            if not body:
                print("remember: --body or --file required", file=sys.stderr); return 2
            tags = [t.strip() for t in (a.tags or "").split(",") if t.strip()]
            print(f"{head}\nREMEMBERED: {mem.remember(a.title, body, tags=tags, supersedes=a.supersedes)}")
        elif a.cmd == "invalidate":
            print(f"{head}\nINVALIDATED: {mem.invalidate(a.id, a.reason)}")
        else:
            emit(f"{head}\nWAKEUP: {mem.wakeup()}", root, "wakeup", a.format)
        return 0

    if a.cmd == "graph":
        gr = reg.get("graph")
        head = gr.banner()
        if a.verb == "find":
            body = rows_to_text(gr.find(a.pattern, label=a.label, limit=a.limit))
        elif a.verb == "callers":
            body = rows_to_text(gr.callers(a.symbol))
        elif a.verb == "callees":
            body = rows_to_text(gr.callees(a.symbol))
        elif a.verb == "snippet":
            body = gr.snippet(a.qualified_name)
        else:
            body = rows_to_text(gr.impact())
        emit(f"{head}\n{body}", root, f"graph-{a.verb}", a.format); return 0

    if a.cmd == "knowledge":
        kn = reg.get("knowledge")
        print(kn.banner())
        if a.verb != "status":
            print("knowledge verbs arrive in 0.2.0-c (OKF adapter)")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
