#!/usr/bin/env python3
"""source-snapshot.py — loop-assess step A0: where does the truth live, and can I reach it?

Run BEFORE classifying a finding. It prints, as markdown to paste into the
assessment (or appends with --into):

  ## Sources         one row per class of the project's .loopkit/sources.json
                     (or the built-in defaults), the system of record for each,
                     the finding's nouns matched to classes as a SUGGESTION, and
                     a placeholder evidence cell the evidence gate refuses until
                     it is replaced with `read: <what, env>` or `n/a: <why>`.
  ### Tools ...      which tools answered a cheap probe this run.

Honesty rules, because a snapshot that overstates reach is worse than none:

- A CLI is `answering` only if it is on PATH AND its probe exited 0 within the
  timeout. Answering proves the binary runs; it does not prove credentials.
- A class with no probe, and EVERY MCP server, is `unknown`. A script cannot see
  the session's MCP connections. Only the agent can, and it may pass what it
  observed with `--session-reported NAME`; that is labelled `session-reported`,
  never promoted to anything stronger.
- MCP server NAMES are printed, never their configuration: `.mcp.json` carries
  commands, environment and often credentials.

The evidence gate (docs/adr/0008-evidence-gate.md) never reads this script's
noun matches; it reads only what the assessment finally says.

usage: source-snapshot.py [--project DIR] [--finding TEXT] [--session-reported NAME ...]
                          [--format md|json] [--into ASSESSMENT.md] [--no-probe]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from loopkit_core import evidence  # noqa: E402

PROBE_TIMEOUT = 5


def project_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                           text=True, timeout=5)
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return Path.cwd()


def probe(argv: list[str]) -> str:
    """answering / not-on-path / not-answering (...). Output is never printed."""
    if not argv or shutil.which(argv[0]) is None:
        return "not-on-path"
    try:
        r = subprocess.run(argv, capture_output=True, timeout=PROBE_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"not-answering (no reply in {PROBE_TIMEOUT}s)"
    except OSError as exc:
        return f"not-answering ({exc.__class__.__name__})"
    return "answering" if r.returncode == 0 else f"not-answering (exit {r.returncode})"


def mcp_names(root: Path) -> list[str]:
    path = root / ".mcp.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return sorted(str(k) for k in servers) if isinstance(servers, dict) else []


def snapshot(root: Path, finding: str, reported: set[str], do_probe: bool) -> dict:
    classes = evidence.load_classes(root)
    nouns = evidence.match_nouns(finding, classes) if finding else {}
    tools: list[dict] = []
    for name, spec in classes.items():
        probes = spec.get("probes") or []
        if not probes:
            tools.append({"tool": "(no probe configured)", "class": name, "status": "unknown"})
            continue
        for argv in probes:
            status = probe(list(argv)) if do_probe else "unknown (probing disabled)"
            tools.append({"tool": str(argv[0]), "class": name, "status": status})
    for server in mcp_names(root):
        if server in reported:
            status = "session-reported (the agent observed it; this script did not probe it)"
        else:
            status = "unknown (configured in .mcp.json; a script cannot see the session's MCP state)"
        tools.append({"tool": f"mcp: {server}", "class": "—", "status": status})
    for server in sorted(reported - set(mcp_names(root))):
        tools.append({"tool": f"mcp: {server}", "class": "—",
                      "status": "session-reported (the agent observed it; this script did not probe it)"})
    return {
        "date": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
        "classes": {n: {"system": s["system"], "counts": s.get("counts", True) is not False,
                        "note": s.get("note", ""), "finding_mentions": nouns.get(n, [])}
                    for n, s in classes.items()},
        "tools": tools,
    }


def esc(text: str) -> str:
    return str(text).replace("|", "\\|")


def render(snap: dict) -> str:
    out = [
        "## Sources",
        "",
        f"<!-- source-snapshot.py {snap['date']}. Replace every evidence cell with "
        f"`read: <what you read, which env>` or `n/a: <why>`. At least "
        f"{evidence.MIN_INDEPENDENT} `read:` rows on different systems are needed for "
        f"spec-draft, spec-ready and pr-open, one of them from a live class (identity, "
        f"data, runtime); a `derived` row is recorded and worth zero. If the finding makes "
        f"no claim about live state, add `Live state: none — <why>` above the table; the "
        f"two systems are still needed. -->",
        "",
        "| class | system of record | evidence | finding mentions | note |",
        "|---|---|---|---|---|",
    ]
    for name, c in snap["classes"].items():
        system = c["system"] + ("" if c["counts"] else " (worth zero)")
        mentions = ", ".join(c["finding_mentions"]) or "—"
        out.append(f"| {esc(name)} | {esc(system)} | <read: what, env — or n/a: why> | "
                   f"{esc(mentions)} | {esc(c['note'])} |")
    out += [
        "",
        f"### Tools reachable when this was written ({snap['date']})",
        "",
        "| tool | class | status |",
        "|---|---|---|",
    ]
    for t in snap["tools"]:
        out.append(f"| {esc(t['tool'])} | {esc(t['class'])} | {esc(t['status'])} |")
    out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", default=None)
    ap.add_argument("--finding", default="", help="the finding's text; nouns become suggestions")
    ap.add_argument("--session-reported", action="append", default=[], metavar="NAME",
                    help="an MCP server the agent's own session reports as available")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--into", default=None, metavar="ASSESSMENT",
                    help="append the section to this assessment, only if it has no "
                         "`## Sources` yet (never overwrites)")
    ap.add_argument("--no-probe", action="store_true", help="report every CLI as unknown")
    args = ap.parse_args()

    root = project_root(args.project)
    try:
        snap = snapshot(root, args.finding, set(args.session_reported), not args.no_probe)
    except ValueError as exc:
        print(f"source-snapshot: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(snap, indent=2))
        return 0
    text = render(snap)
    if args.into:
        target = Path(args.into)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if evidence.sections(existing, "Sources"):
            print(f"source-snapshot: {target} already has a `## Sources` section; edit it in "
                  f"place (nothing written)", file=sys.stderr)
            return 1
        sep = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        target.write_text(existing + sep + text, encoding="utf-8")
        print(f"appended `## Sources` to {target}")
        return 0
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
