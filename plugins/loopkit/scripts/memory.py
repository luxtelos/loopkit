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
    k = sub.add_parser("knowledge", parents=[common]); ks = k.add_subparsers(dest="verb", required=True)
    ks.add_parser("status", parents=[common])
    kq = ks.add_parser("search", parents=[common]); kq.add_argument("query"); kq.add_argument("--type"); kq.add_argument("--lane"); kq.add_argument("--limit", type=int, default=8)
    ks.add_parser("get", parents=[common]).add_argument("path")
    ke = ks.add_parser("enqueue", parents=[common]); ke.add_argument("--op", default="upsert"); ke.add_argument("--target", required=True)
    ke.add_argument("--reason", required=True); ke.add_argument("--by", default="process:loopkit/memory.py"); ke.add_argument("--type", dest="ctype")
    ke.add_argument("--title"); ke.add_argument("--description"); ke.add_argument("--tags"); ke.add_argument("--sources", help="comma-separated repo-relative files")
    ke.add_argument("--enforced-by", help="comma-separated kind:ref, e.g. hook:protect_tests.py,test:tests/x.test.ts")
    ke.add_argument("--body"); ke.add_argument("--body-file"); ke.add_argument("--status", dest="cstatus", default="draft")
    ks.add_parser("drain", parents=[common]); ks.add_parser("verify", parents=[common]); ks.add_parser("scan-drift", parents=[common])
    kr = ks.add_parser("reindex", parents=[common]); kr.add_argument("--check", action="store_true")
    ki = ks.add_parser("init", parents=[common]); ki.add_argument("--by", default="process:loopkit/seed")
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
            out = f"{head}\nRECALL \"{a.query}\":\n{body}"
            kn = reg.get("knowledge")
            kok, _ = kn.available()
            if kok:
                hits = kn.search(a.query, limit=min(a.limit, 5))
                out += "\n" + kn.banner() + "\nCONCEPTS:\n" + ("\n".join(f"- [{h['type']}] {h['title']} ({h['path']})" for h in hits) or "- none match")
            emit(out, root, "recall", a.format)
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
        return knowledge_cmd(a, reg, root)
    return 0


def knowledge_cmd(a, reg, root: Path) -> int:
    kn = reg.get("knowledge")
    head = kn.banner()
    if a.verb == "init":
        from loopkit_memory import seed as seedmod
        return seedmod.seed(root, by=a.by, fmt=a.format)
    if not hasattr(kn, "search"):
        print(head); print("knowledge is disabled: set knowledge.enabled=true in .loopkit/memory.json, then `memory.py knowledge init`"); return 0
    if a.verb == "status":
        st = kn.status()
        print(head)
        if st.get("by_type"):
            print("  " + "  ".join(f"{t}={n}" for t, n in sorted(st["by_type"].items())))
        rc, out = kn.actor_status(); print(out); return 0
    if a.verb == "search":
        hits = kn.search(a.query, limit=a.limit, types=(a.type,) if a.type else None, lane=a.lane)
        body = "\n".join(f"- [{h['type']}] {h['title']} ({h['path']}) {' '.join(h['tags'])}".rstrip() for h in hits) or "(no concept matches — say so; do not guess)"
        emit(f"{head}\nCONCEPTS \"{a.query}\":\n{body}", root, "knowledge-search", a.format); return 0
    if a.verb == "get":
        emit(f"{head}\n{kn.get(a.path)}", root, "knowledge-get", a.format); return 0
    if a.verb == "enqueue":
        fm: dict = {}
        if a.ctype: fm["type"] = a.ctype
        if a.title: fm["title"] = a.title
        if a.description: fm["description"] = a.description
        if a.cstatus: fm["status"] = a.cstatus
        if a.tags: fm["tags"] = [t.strip() for t in a.tags.split(",") if t.strip()]
        if a.enforced_by:
            fm["enforced_by"] = [{k.strip(): v.strip()} for k, v in (x.split(":", 1) for x in a.enforced_by.split(",") if ":" in x)]
        payload: dict = {"frontmatter": fm}
        if a.sources:
            payload["sources"] = [{"id": Path(sp.strip()).stem, "resource": sp.strip(), "title": sp.strip()} for sp in a.sources.split(",") if sp.strip()]
        body = a.body or (Path(a.body_file).read_text(encoding="utf-8") if a.body_file else "")
        rc, out = kn.enqueue(op=a.op, reason=a.reason, by=a.by, target=a.target, payload=payload, body=body)
        print(f"{head}\nENQUEUED rc={rc}: {out}\nNext: memory.py knowledge drain"); return rc
    if a.verb == "drain":
        rc, out = kn.drain(); print(f"{head}\nDRAIN rc={rc} (0 ok, 6 = something dead-lettered — see inbox/needs-human.md)\n{out}"); return rc
    if a.verb == "verify":
        rc, out = kn.verify(); print(f"{head}\nVERIFY rc={rc} (0 conformant, 4 non-conformant)\n{out}"); return rc
    if a.verb == "reindex":
        rc, out = kn.reindex(check=a.check); print(f"{head}\nREINDEX rc={rc}{' (5 = bytes would change)' if a.check else ''}\n{out}"); return rc
    if a.verb == "scan-drift":
        rc, out = kn.scan_drift(); emit(f"{head}\nDRIFT rc={rc}\n{out}", root, "knowledge-drift", a.format); return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
