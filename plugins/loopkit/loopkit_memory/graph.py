"""GraphAdapter — structural code memory.

Primary: codebase-memory-mcp, driven through its CLI
(`codebase-memory-mcp cli <tool> '<json>'`), never through MCP, so unattended
runs do not depend on a server being attached. Fallback: grep, in the same
output shape, with a DEGRADED banner that says how to index.

Gotcha carried from production: the index is keyed by the project NAME the
indexer chose, and worktrees index as separate projects. available() matches
`root_path` against the project root, or honours `graph.project` from
memory.json; passing a path where a name is expected returns a false
"not indexed".
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import DEFAULT_TIMEOUT, GraphAdapter, run, which

RECALL_MCP = re.compile(r"mcp__codebase-memory(?:-mcp)?__(search_graph|search_code|query_graph|trace_path)", re.I)
RECALL_BASH = re.compile(
    r"codebase-memory-mcp\s+cli\s+(?:--\S+\s+)*(?:search_graph|search_code|query_graph|trace_path)\b"
    r"|memory\.py\s+(?:recall|graph)\b",
    re.I,
)
CLI_TIMEOUT = 20.0


class CodebaseMemoryGraph(GraphAdapter):
    name = "codebase-memory"

    def __init__(self, root: Path, cfg: dict[str, Any]):
        super().__init__(root, cfg)
        self.bin = cfg.get("bin") or which("codebase-memory-mcp")
        self._project: str | None = cfg.get("project") or None
        self._avail: tuple[bool, str] | None = None

    # -- availability ------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        if self._avail is not None:
            return self._avail
        if not self.bin:
            self._avail = (False, "codebase-memory-mcp not on PATH")
            return self._avail
        rc, out, err = run([self.bin, "cli", "list_projects"], timeout=CLI_TIMEOUT)
        if rc != 0:
            self._avail = (False, f"list_projects failed: {(err or out).strip()[:80]}")
            return self._avail
        try:
            projects = json.loads(out).get("projects") or []
        except Exception:
            self._avail = (False, "list_projects returned no JSON")
            return self._avail
        root = str(self.root.resolve())
        names = {p.get("name") for p in projects}
        if self._project and self._project in names:
            self._avail = (True, "")
        else:
            for p in projects:
                if str(p.get("root_path", "")).rstrip("/") == root.rstrip("/"):
                    self._project = p.get("name")
                    self._avail = (True, "")
                    break
            else:
                self._avail = (False, "not indexed — index with: codebase-memory-mcp cli index_repository "
                                      f"'{{\"path\":\"{root}\"}}' (or set graph.project in memory.json)")
        return self._avail

    def status(self) -> dict[str, Any]:
        s = super().status()
        if s["available"]:
            rc, out, _ = run([self.bin, "cli", "index_status", json.dumps({"project": self._project})], timeout=CLI_TIMEOUT)
            try:
                d = json.loads(out)
                s["detail"] = f"{d.get('nodes', '?')} nodes"
            except Exception:
                pass
        return s

    def recall_tool_pattern(self):
        return RECALL_MCP, RECALL_BASH

    # -- verbs -------------------------------------------------------------
    def _call(self, tool: str, args: dict[str, Any]) -> Any:
        ok, why = self.available()
        if not ok:
            return {"error": why}
        payload = dict(args)
        payload.setdefault("project", self._project)
        rc, out, err = run([self.bin, "cli", tool, json.dumps(payload)], timeout=CLI_TIMEOUT)
        if rc != 0:
            return {"error": (err or out).strip()[:200]}
        try:
            return json.loads(out)
        except Exception:
            return {"raw": out.strip()[:2000]}

    def find(self, pattern: str, *, label: str | None = None, limit: int = 20) -> list[dict]:
        args: dict[str, Any] = {"name_pattern": pattern, "limit": limit}
        if label:
            args["label"] = label
        r = self._call("search_graph", args)
        return r.get("results", []) if isinstance(r, dict) else []

    def callers(self, symbol: str, depth: int = 2) -> list[dict]:
        r = self._call("trace_path", {"function_name": symbol, "direction": "inbound", "depth": depth})
        return r if isinstance(r, list) else [r]

    def callees(self, symbol: str, depth: int = 2) -> list[dict]:
        r = self._call("trace_path", {"function_name": symbol, "direction": "outbound", "depth": depth})
        return r if isinstance(r, list) else [r]

    def snippet(self, qualified_name: str) -> str:
        r = self._call("get_code_snippet", {"qualified_name": qualified_name})
        if isinstance(r, dict):
            return str(r.get("code") or r.get("snippet") or r.get("raw") or r.get("error") or r)
        return str(r)

    def impact(self) -> list[dict]:
        r = self._call("detect_changes", {})
        return r if isinstance(r, list) else [r]


class GrepGraph(GraphAdapter):
    """The degraded path: text search in the graph's output shape."""
    name = "grep"

    def __init__(self, root: Path, cfg: dict[str, Any], reason: str):
        super().__init__(root, cfg)
        self._reason = reason

    def available(self) -> tuple[bool, str]:
        return False, self._reason

    def recall_tool_pattern(self):
        return RECALL_MCP, RECALL_BASH

    def _grep(self, pattern: str, limit: int) -> list[dict]:
        rc, out, _ = run(["grep", "-rnE", "--exclude-dir=node_modules", "--exclude-dir=.git", pattern, "."],
                         timeout=DEFAULT_TIMEOUT * 5, cwd=self.root)
        rows = []
        for line in out.splitlines()[:limit]:
            m = re.match(r"^\./(.+?):(\d+):(.*)$", line)
            if m:
                rows.append({"file_path": m.group(1), "line": int(m.group(2)), "text": m.group(3).strip()[:160]})
        return rows

    def find(self, pattern: str, *, label: str | None = None, limit: int = 20) -> list[dict]:
        return self._grep(pattern, limit)

    def callers(self, symbol: str, depth: int = 2) -> list[dict]:
        return self._grep(rf"\b{re.escape(symbol)}\s*\(", 40)

    def callees(self, symbol: str, depth: int = 2) -> list[dict]:
        return [{"note": "callees need the graph; grep cannot answer this", "symbol": symbol}]

    def snippet(self, qualified_name: str) -> str:
        name = qualified_name.split(".")[-1]
        rows = self._grep(rf"(function|def|const|class)\s+{re.escape(name)}\b", 5)
        return "\n".join(f"{r['file_path']}:{r['line']}: {r['text']}" for r in rows) or f"(no definition found for {name})"

    def impact(self) -> list[dict]:
        rc, out, _ = run(["git", "diff", "--name-only", "HEAD"], timeout=DEFAULT_TIMEOUT, cwd=self.root)
        return [{"file_path": f, "note": "changed; graph impact needs the index"} for f in out.split() if f]


def build(root: Path, cfg: dict[str, Any]) -> GraphAdapter:
    adapter = str(cfg.get("adapter") or "codebase-memory")
    if adapter == "codebase-memory":
        g = CodebaseMemoryGraph(root, cfg)
        ok, why = g.available()
        if ok:
            return g
        if str(cfg.get("fallback", "grep")) == "grep":
            return GrepGraph(root, cfg, why)
        return g
    if adapter == "grep":
        return GrepGraph(root, cfg, "grep chosen in memory.json")
    return GrepGraph(root, cfg, f"unknown graph adapter '{adapter}'")
