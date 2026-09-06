"""KnowledgeAdapter — curated, versioned knowledge as an OKF v0.2 bundle.

The bundle is a directory of markdown concepts, each with typed frontmatter
(Doctrine, Invariant, Decision, Trap, Topology, Contract, Gate, Playbook,
Post-mortem), maintained by a mailbox actor: every change is a message that a
deterministic program applies; the bundle is never hand-edited (protect_
governance guards it when enabled). Sources are cited as repo files and their
blob digests are captured at apply time, so drift is detectable and a citation
of a directory or a queue is refused at author time.

This adapter shells out to the vendored actor (loopkit_memory/vendor/
knowledge_actor.py) so its exit-code contract stays intact:
  0 ok · 3 invalid message · 4 bundle non-conformant · 5 reindex would change
  bytes · 6 drained but dead-lettered something.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from . import DEFAULT_TIMEOUT, KnowledgeAdapter, run

VENDOR = Path(__file__).resolve().parent / "vendor"
ACTOR = VENDOR / "knowledge_actor.py"
sys.path.insert(0, str(VENDOR))
try:
    import okf_bundle as okf  # type: ignore
except Exception:  # pragma: no cover
    okf = None

RECALL_BASH = re.compile(r"memory\.py\s+knowledge\s+(?:get|search)\b", re.I)
ACTOR_TIMEOUT = 120.0
TYPES = ("Doctrine", "Invariant", "Decision", "Trap", "Topology", "Contract", "Gate", "Playbook", "Post-mortem")


class OkfKnowledge(KnowledgeAdapter):
    name = "okf"

    def __init__(self, root: Path, cfg: dict[str, Any]):
        super().__init__(root, cfg)
        self.bundle = root / str(cfg.get("bundle") or "knowledge")
        self.mailbox = root / str(cfg.get("mailbox") or "state/knowledge-mailbox")

    # -- availability ------------------------------------------------------
    def concepts(self) -> list[Path]:
        if not self.bundle.is_dir() or okf is None:
            return []
        return [p for p in okf.iter_concepts(self.bundle)]

    def available(self) -> tuple[bool, str]:
        if okf is None:
            return False, "vendored okf_bundle failed to import"
        if not self.bundle.is_dir():
            return False, f"no bundle at {self.bundle.relative_to(self.root)} — seed one: memory.py knowledge init"
        if not self.concepts():
            return False, "bundle is empty — memory.py knowledge init, or enqueue + drain"
        return True, ""

    def status(self) -> dict[str, Any]:
        s = super().status()
        if s["available"]:
            by_type: dict[str, int] = {}
            for p in self.concepts():
                fm = self._frontmatter(p)
                t = str(fm.get("type", "?"))
                by_type[t] = by_type.get(t, 0) + 1
            s["detail"] = f"{sum(by_type.values())} concepts"
            s["by_type"] = by_type
            rc, out, _ = self._actor(["status", "--json"])
            try:
                st = json.loads(out)
                s["queue_depth"] = st.get("queue_depth")
                s["dead_letters"] = st.get("dead_letters")
                if st.get("queue_depth"):
                    s["detail"] += f", {st['queue_depth']} queued"
                if st.get("dead_letters"):
                    s["detail"] += f", {st['dead_letters']} dead-lettered"
            except Exception:
                pass
        return s

    def recall_tool_pattern(self):
        return None, RECALL_BASH

    # -- the actor -----------------------------------------------------------
    def _actor(self, args: list[str], timeout: float = ACTOR_TIMEOUT) -> tuple[int, str, str]:
        cmd = [sys.executable, str(ACTOR), "--bundle", str(self.bundle), "--mailbox", str(self.mailbox), *args]
        return run(cmd, timeout=timeout, cwd=self.root)

    def _frontmatter(self, path: Path) -> dict:
        try:
            fm, _ = okf.parse_frontmatter(path.read_text(encoding="utf-8"))
            return fm
        except Exception:
            return {}

    # -- verbs ---------------------------------------------------------------
    def search(self, query: str, *, limit: int = 8, types: tuple[str, ...] | None = None, lane: str | None = None) -> list[dict]:
        """Deterministic text match over title, description, tags and body.
        A lane matches tags `lane/<lane>` or `scope/<lane>`."""
        words = [w for w in re.split(r"\W+", (query or "").lower()) if len(w) > 2]
        hits: list[tuple[float, dict]] = []
        for p in self.concepts():
            fm = self._frontmatter(p)
            if types and fm.get("type") not in types:
                continue
            tags = [str(t).lower() for t in (fm.get("tags") or [])]
            if lane:
                ln = lane.lower()
                if not any(t in (f"lane/{ln}", f"scope/{ln}", f"domain/{ln}") or t.endswith(f"/{ln}") for t in tags):
                    continue
            head = f"{fm.get('title', '')} {fm.get('description', '')} {' '.join(tags)}".lower()
            try:
                body = p.read_text(encoding="utf-8").lower()
            except Exception:
                body = ""
            score = sum(3 * head.count(w) + body.count(w) for w in words) if words else 1.0
            if score:
                hits.append((float(score), {"path": okf.bundle_path_of(self.bundle, p), "type": fm.get("type"),
                                            "title": fm.get("title", p.stem), "status": fm.get("status", ""), "tags": tags}))
        hits.sort(key=lambda t: -t[0])
        return [h for _, h in hits[:limit]]

    def get(self, concept_path: str) -> str:
        try:
            target = okf.resolve_target(self.bundle, concept_path if concept_path.startswith("/") else "/" + concept_path)
            return target.read_text(encoding="utf-8") if target.is_file() else f"(no concept at {concept_path})"
        except Exception as e:
            return f"(error: {e})"

    def enqueue(self, *, op: str = "upsert", reason: str, by: str, target: str | None = None,
                payload: dict | None = None, body: str = "", extra_args: list[str] | None = None) -> tuple[int, str]:
        import tempfile
        # The actor refuses directories and machine-local paths as sources; the
        # loop adds one rule of its own: a mutable QUEUE is never a source. Its
        # bytes move by design, so a digest of it is permanent false drift.
        for src in (payload or {}).get("sources") or []:
            res = str((src or {}).get("resource") or "") if isinstance(src, dict) else str(src)
            if res.startswith(("inbox/", "state/triage", "state/ticks", "state/progress")) or res.endswith("/"):
                return 3, f"refused: source {res!r} is a mutable queue or a directory — cite a file whose bytes mean something"
        args = ["enqueue", "--op", op, "--reason", reason[:200], "--by", by, "--print-path"]
        if target:
            args += ["--target", target]
        if payload:
            args += ["--payload-json", json.dumps(payload)]
        tmp = None
        if body:
            tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
            tmp.write(body); tmp.close()
            args += ["--body-file", tmp.name]
        if extra_args:
            args += extra_args
        rc, out, err = self._actor(args)
        if tmp:
            try:
                Path(tmp.name).unlink()
            except Exception:
                pass
        return rc, (out or err).strip()

    def drain(self) -> tuple[int, str]:
        rc, out, err = self._actor(["drain", "--json"])
        return rc, (out or err).strip()

    def verify(self) -> tuple[int, str]:
        rc, out, err = self._actor(["verify-bundle", "--json"])
        return rc, (out or err).strip()

    def reindex(self, check: bool = False) -> tuple[int, str]:
        rc, out, err = self._actor(["reindex"] + (["--check"] if check else []))
        return rc, (out or err).strip()

    def scan_drift(self) -> tuple[int, str]:
        rc, out, err = self._actor(["scan-drift", "--json"])
        return rc, (out or err).strip()

    def actor_status(self) -> tuple[int, str]:
        rc, out, err = self._actor(["status"])
        return rc, (out or err).strip()


def build(root: Path, cfg: dict[str, Any]) -> KnowledgeAdapter:
    return OkfKnowledge(root, cfg)
