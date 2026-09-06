"""loopkit_memory — pluggable memory adapters, and the registry hooks ask.

Three kinds of memory, one interface each, and a registry so that a hook never
names a server:

  GraphAdapter      structural code memory  (callers, callees, snippets, impact)
  MemoryAdapter     episodic/semantic memory (recall, remember, invalidate, diary)
  KnowledgeAdapter  curated, versioned knowledge (enqueue, drain, verify)

The registry is <project>/.loopkit/memory.json. When it is absent every gate
that depends on memory is passive — a plugin must never lock a repo that has
not opted in. Every adapter answers available() -> (bool, reason) and every
consumer prints which path answered ("ADAPTER: graph [DEGRADED: not indexed]"),
because the honest state of a memory system is "sometimes down" and a mandate
that is silently unserved is worse than a visible fallback.

Two rules carried from production:
  * writes never block — a failed remember() logs and keeps the note on disk;
  * invalidate, don't delete — an old fact gets valid_until, never erased.

Stdlib only. Adapters shell out to CLIs; nothing here imports a vector store.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 3.0
STATUS_CACHE_TTL = 600  # seconds


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def project_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("LOOPKIT_PROJECT_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def run(cmd: list[str], timeout: float = DEFAULT_TIMEOUT, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a CLI, never raise. (rc, stdout, stderr); rc=-1 on timeout/missing."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return -1, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s: {' '.join(cmd[:2])}"
    except Exception as e:  # pragma: no cover
        return -1, "", str(e)


def which(name: str) -> str | None:
    for d in os.environ.get("PATH", "").split(os.pathsep):
        c = Path(d) / name
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


@dataclass
class Fact:
    id: str
    text: str
    source: str = ""
    date: str = ""
    valid_from: str = ""
    valid_until: str = ""
    score: float | None = None

    def line(self) -> str:
        tag = f" [{self.source}]" if self.source else ""
        return f"- {self.text.strip()[:240]}{tag}"


# ---------------------------------------------------------------------------
# adapter base classes — the contract
# ---------------------------------------------------------------------------

class Adapter:
    kind = "base"
    name = "none"

    def __init__(self, root: Path, cfg: dict[str, Any]):
        self.root = root
        self.cfg = cfg

    def available(self) -> tuple[bool, str]:
        return False, "not configured"

    def status(self) -> dict[str, Any]:
        ok, why = self.available()
        return {"kind": self.kind, "adapter": self.name, "available": ok, "reason": why}

    def recall_tool_pattern(self) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
        """(regex over MCP tool names, regex over Bash commands) that count as recall."""
        return None, None

    def banner(self) -> str:
        ok, why = self.available()
        return f"ADAPTER: {self.kind}={self.name} [{'available' if ok else 'DEGRADED: ' + why}]"


class GraphAdapter(Adapter):
    kind = "graph"

    def find(self, pattern: str, *, label: str | None = None, limit: int = 20) -> list[dict]: return []
    def callers(self, symbol: str, depth: int = 2) -> list[dict]: return []
    def callees(self, symbol: str, depth: int = 2) -> list[dict]: return []
    def snippet(self, qualified_name: str) -> str: return ""
    def impact(self) -> list[dict]: return []


class MemoryAdapter(Adapter):
    kind = "memory"

    def recall(self, query: str, *, limit: int = 8) -> list[Fact]: return []
    def remember(self, title: str, body: str, *, tags: list[str] | None = None, supersedes: str | None = None) -> str: return ""
    def invalidate(self, fact_id: str, reason: str) -> str: return ""
    def wakeup(self) -> str: return ""


class KnowledgeAdapter(Adapter):
    kind = "knowledge"

    def enqueue(self, message: dict) -> str: return ""
    def drain(self) -> list[str]: return []
    def verify(self) -> dict: return {}
    def get(self, concept_id: str) -> str: return ""


class NullAdapter(Adapter):
    """What a missing or disabled slot resolves to. Always DEGRADED, never raises."""

    def __init__(self, root: Path, cfg: dict[str, Any], kind: str, reason: str):
        super().__init__(root, cfg)
        self.kind = kind
        self.name = "none"
        self._reason = reason

    def available(self) -> tuple[bool, str]:
        return False, self._reason


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

@dataclass
class Registry:
    root: Path
    config: dict[str, Any] = field(default_factory=dict)
    adapters: dict[str, Adapter] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.config)

    def get(self, kind: str) -> Adapter:
        return self.adapters.get(kind) or NullAdapter(self.root, {}, kind, "not configured")

    def is_recall(self, tool_name: str, tool_input: dict | None = None) -> bool:
        """Did this tool call count as consulting memory? Asks every adapter."""
        cmd = ""
        if tool_name == "Bash" and isinstance(tool_input, dict):
            cmd = str(tool_input.get("command") or "")
        for a in self.adapters.values():
            mcp_rx, bash_rx = a.recall_tool_pattern()
            if mcp_rx and tool_name and mcp_rx.search(tool_name):
                return True
            if bash_rx and cmd and bash_rx.search(cmd):
                return True
        return False

    def guard_patterns(self) -> list[str]:
        """Extra absolute-path regexes protect_governance should enforce."""
        out: list[str] = []
        k = self.config.get("knowledge") or {}
        if k.get("enabled"):
            bundle = str(k.get("bundle") or "knowledge").strip("/")
            out.append(rf"(?:^|/){re.escape(bundle)}/")
        return out

    def status(self, use_cache: bool = True) -> list[dict[str, Any]]:
        cache = _cache_path(self.root)
        if use_cache:
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if time.time() - float(data.get("at", 0)) < STATUS_CACHE_TTL:
                    return data["status"]
            except Exception:
                pass
        status = [a.status() for a in self.adapters.values()]
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"at": time.time(), "status": status}), encoding="utf-8")
        except Exception:
            pass
        return status

    def status_line(self, use_cache: bool = True) -> str:
        parts = []
        for s in self.status(use_cache):
            detail = s.get("detail")
            if s["available"]:
                parts.append(f"{s['kind']}={s['adapter']}" + (f"({detail})" if detail else ""))
            else:
                parts.append(f"{s['kind']}=DEGRADED({s['reason']})")
        return "MEMORY: " + " ".join(parts) if parts else "MEMORY: not configured (.loopkit/memory.json absent)"


def _cache_path(root: Path) -> Path:
    import tempfile
    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"loopkit-memory-{key}.json"


def load(root: Path | str | None = None) -> Registry:
    """Read .loopkit/memory.json and build adapters. Never raises; a bad file
    yields an empty (passive) registry with the reason in status."""
    root = project_root(str(root) if root else None)
    cfg_path = root / ".loopkit" / "memory.json"
    reg = Registry(root=root)
    if not cfg_path.exists():
        return reg
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        reg.config = {"_error": str(e)}
        reg.adapters["memory"] = NullAdapter(root, {}, "memory", f"memory.json unreadable: {e}")
        return reg
    if not isinstance(cfg, dict):
        return reg
    reg.config = cfg
    for kind, factory in (("graph", _graph), ("memory", _memory), ("knowledge", _knowledge)):
        section = cfg.get(kind)
        if not isinstance(section, dict):
            continue
        try:
            reg.adapters[kind] = factory(root, section)
        except Exception as e:  # an adapter that cannot even construct is DEGRADED, not fatal
            reg.adapters[kind] = NullAdapter(root, section, kind, f"adapter error: {e}")
    return reg


def _graph(root: Path, cfg: dict) -> Adapter:
    from . import graph
    return graph.build(root, cfg)


def _memory(root: Path, cfg: dict) -> Adapter:
    from . import palace
    return palace.build(root, cfg)


def _knowledge(root: Path, cfg: dict) -> Adapter:
    if not cfg.get("enabled"):
        return NullAdapter(root, cfg, "knowledge", "disabled (memory.json knowledge.enabled=false)")
    try:
        from . import okf
        return okf.build(root, cfg)
    except ImportError:
        return NullAdapter(root, cfg, "knowledge", "OKF adapter not installed in this version")
