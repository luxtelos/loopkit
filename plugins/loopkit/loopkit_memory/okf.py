"""KnowledgeAdapter — curated, versioned knowledge as an OKF v0.2 bundle.

The bundle is a directory of markdown concepts, each with typed frontmatter
(Doctrine, Invariant, Decision, Trap, Topology, Contract, Gate, Playbook,
Post-mortem, Finding), maintained by a mailbox actor: every change is a message that a
deterministic program applies; the bundle is never hand-edited (protect_
governance guards it when enabled). A source is one of a CLOSED set of shapes
(`resolve_source`): a repo file, digested at apply time so drift is detectable,
or an artifact anchored on a commit id. All else is refused at author time.

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
TYPES = ("Doctrine", "Invariant", "Decision", "Trap", "Topology", "Contract", "Gate", "Playbook", "Post-mortem", "Finding")

# -- sources: a CLOSED set ------------------------------------------------------
# This used to be `res.startswith(("inbox/", "state/triage", …))` on the raw
# string — a deny-list. It named the known-bad and permitted the unknown, so
# `x://state/triage.md` walked past it, as did a branch under `tag://`, a tag
# that did not exist, `../outside.md`, an absolute path, and the same queue
# passed as `payload.frontmatter.sources` instead of `payload.sources`. Adding
# those to the list would have fixed the instances and kept the class.
#
# So the rule is stated the other way round. A source is accepted only when it
# IS one of these, and is refused otherwise, whatever it looks like:
#
#   repo file  `<seg>/<seg>/…`  every segment is, spelled exactly, an entry of
#              the directory before it; every segment but the last is a real
#              directory and the last a real regular file (lstat — a symlink is
#              neither). That one walk is what refuses `..`, `.`, `//`, an
#              absolute path, a scheme, a case-dressed name and a missing file:
#              none of them is a directory entry.
#   artifact   `commit://<40 lowercase hex>/<path>`  the id is a COMMIT object
#              in this repository and <path> is a regular-file blob in its tree.
#
# and, as INPUT only, never recorded:
#
#   tag        `tag://<tag>/<path>`  resolved through `refs/tags/` alone — so a
#              branch cannot wear the scheme — peeled to its commit, and
#              REWRITTEN to the artifact shape. A tag is a name that can be
#              moved or deleted; the commit is the thing that cannot. The name
#              is kept beside the anchor as `x_label`, a note, so that the day
#              it stops pointing here can be reported (`artifact_drift`).
#
# Last, the path of an accepted source — either shape — is not one of the loop's
# own queues. That list IS an enumeration, on purpose: it is the spec's case 2,
# four named files. It is safe here and was not safe before, because it now
# runs on a path already proven canonical; there is no spelling left to hide in.
QUEUE_PREFIXES = ("inbox/", "state/triage", "state/ticks", "state/progress")
COMMIT_SOURCE = re.compile(r"commit://([0-9a-f]{40})/([^\x00-\x1f]+)")
TAG_SOURCE = re.compile(r"tag://([^\x00-\x1f]+)")
REGULAR_MODES = ("100644", "100755")
GIT_TIMEOUT = 15.0


class SourceRefused(ValueError):
    """A source outside the closed set. The message says which rule it missed."""


def _git(root: Path, *args: str) -> tuple[int, str]:
    rc, out, _ = run(["git", "--literal-pathspecs", *args], timeout=GIT_TIMEOUT, cwd=root)
    return rc, out


def _is_queue(path: str) -> bool:
    return path.startswith(QUEUE_PREFIXES)


def _walk_repo_file(root: Path, resource: str) -> bool:
    """True only when `resource` names a regular file by its exact spelling."""
    import os, stat
    here = root
    segments = resource.split("/")
    for i, seg in enumerate(segments):
        try:
            if seg not in os.listdir(here):
                return False
            st = os.lstat(here / seg)
        except OSError:
            return False
        last = i == len(segments) - 1
        if not (stat.S_ISREG(st.st_mode) if last else stat.S_ISDIR(st.st_mode)):
            return False
        here = here / seg
    return True


def _blob_in_commit(root: Path, sha: str, path: str) -> str | None:
    """The blob id of `path` in commit `sha`, or None unless sha is a commit
    object here and path is a regular file in its tree, spelled exactly."""
    rc, out = _git(root, "cat-file", "-t", sha)
    if rc != 0 or out.strip() != "commit":
        return None
    rc, out = _git(root, "ls-tree", "-z", sha, "--", path)
    entries = [e for e in out.split("\0") if e] if rc == 0 else []
    if len(entries) != 1 or "\t" not in entries[0]:
        return None
    meta, name = entries[0].split("\t", 1)
    mode, kind, oid = (meta.split() + ["", "", ""])[:3]
    if name != path or kind != "blob" or mode not in REGULAR_MODES:
        return None
    return oid


def _peel_tag(root: Path, tag: str) -> str | None:
    """The commit a TAG names, or None. `refs/tags/` only: never a branch."""
    if _git(root, "check-ref-format", f"refs/tags/{tag}")[0] != 0:
        return None
    rc, out = _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    sha = out.strip()
    return sha if rc == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def resolve_source(root: Path, entry: Any) -> dict:
    """Return the source entry to RECORD, or raise SourceRefused.

    The only way out of this function with a value is through one of the
    accepted shapes; every other path raises. Add a shape by adding a branch
    that proves it, never by loosening one that exists."""
    if isinstance(entry, str):
        entry = {"resource": entry}
    if not isinstance(entry, dict):
        raise SourceRefused(f"a source is a mapping with a `resource`, got {type(entry).__name__}")
    resource = entry.get("resource")
    if not isinstance(resource, str) or not resource:
        raise SourceRefused(f"a source needs a non-empty string `resource`, got {resource!r}")
    kept = {k: v for k, v in entry.items() if k not in ("x_blob", "x_label")}  # ours to set, never the caller's

    label, given = None, resource
    tagged = TAG_SOURCE.fullmatch(resource)
    if tagged:
        segments = tagged.group(1).split("/")
        # A tag name may hold slashes. Git refuses refs/tags/a beside
        # refs/tags/a/b, so at most one split names a tag: no judgement here.
        for k in range(1, len(segments)):
            sha = _peel_tag(root, "/".join(segments[:k]))
            if sha:
                label, resource = "tag:" + "/".join(segments[:k]), f"commit://{sha}/{'/'.join(segments[k:])}"
                break
        else:
            raise SourceRefused(f"{resource!r}: no tag under refs/tags/ matches — a branch or a missing tag is not an anchor")

    pinned = COMMIT_SOURCE.fullmatch(resource)
    if pinned:
        sha, path = pinned.groups()
        blob = _blob_in_commit(root, sha, path)
        if blob is None:
            raise SourceRefused(f"{resource!r}: not a regular file in a commit of this repository")
        if _is_queue(path):
            raise SourceRefused(f"{resource!r}: the path is one of the loop's queues — loop state at a commit is still loop state")
        out = {**kept, "resource": resource, "x_blob": blob}
        if label:
            out["x_label"] = label
            if out.get("title") == given:  # the CLI titles a source with its own string;
                out["title"] = f"{path} at {label[4:]}, commit {sha[:12]}"  # do not keep the label dressed as a source
        return out

    if not _walk_repo_file(root, resource):
        raise SourceRefused(f"{resource!r} is not a regular file in this repository, named by its exact "
                            "repo-relative path — nor `commit://<40-hex>/<path>`, nor `tag://<tag>/<path>`")
    if _is_queue(resource):
        raise SourceRefused(f"{resource!r} is a mutable queue — cite a file whose bytes mean something")
    return {**kept, "resource": resource}


def _as_entries(sources: Any) -> list:
    if sources is None:
        return []
    return [sources] if isinstance(sources, (dict, str)) else list(sources) if isinstance(sources, (list, tuple)) else [sources]


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
        # Every source goes through the closed set, from BOTH places the actor
        # reads them: `payload.sources`, and `payload.frontmatter.sources`,
        # which apply_upsert copies onto the concept wholesale.
        payload = dict(payload) if payload else payload
        try:
            if payload and payload.get("sources") is not None:
                payload["sources"] = [resolve_source(self.root, e) for e in _as_entries(payload["sources"])]
            fm = (payload or {}).get("frontmatter")
            if isinstance(fm, dict) and fm.get("sources") is not None:
                payload["frontmatter"] = {**fm, "sources": [resolve_source(self.root, e) for e in _as_entries(fm["sources"])]}
        except SourceRefused as e:
            return 3, f"refused: source {e}"
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

    def artifact_drift(self) -> list[dict]:
        """What the vendored scan cannot see: sources that carry a scheme.

        The actor digests nothing for them and `scan_drift` walks only
        `repo-file` records, so without this a concept anchored on an artifact
        is never looked at by anything. One report per concept, in the actor's
        own report shape. Kinds:
          source-shape-unknown   on disk, and outside the closed set
          artifact-unresolvable  the recorded commit, or the path in it, is gone
                                 (rewritten history, or a shallow clone)
          artifact-changed       the path resolves to other bytes than recorded
          label-moved / -gone    the tag noted beside the anchor no longer
                                 names this commit. The BYTES are still proven;
                                 the concept's words name the label, so a reader
                                 about to act treats it as failed all the same."""
        reports: list[dict] = []
        for p in self.concepts():
            fm = self._frontmatter(p)
            findings: list[dict] = []
            for entry in _as_entries(fm.get("sources")):
                resource = str(entry.get("resource", "")) if isinstance(entry, dict) else str(entry)
                if "://" not in resource:
                    continue  # a repo file: the actor's scan owns it
                found = {"source_id": entry.get("id") if isinstance(entry, dict) else None, "resource": resource}
                pinned = COMMIT_SOURCE.fullmatch(resource)
                if not pinned:
                    findings.append({**found, "kind": "source-shape-unknown"}); continue
                sha, path = pinned.groups()
                blob = _blob_in_commit(self.root, sha, path)
                recorded = entry.get("x_blob")
                if blob is None:
                    findings.append({**found, "kind": "artifact-unresolvable", "recorded_blob": recorded})
                elif recorded and blob != recorded:
                    findings.append({**found, "kind": "artifact-changed", "recorded_blob": recorded, "observed_blob": blob})
                label = str(entry.get("x_label") or "")
                if label.startswith("tag:"):
                    now = _peel_tag(self.root, label[4:])
                    if now != sha:
                        findings.append({**found, "kind": "label-moved" if now else "label-gone", "label": label,
                                         "recorded_commit": sha, "observed_commit": now or "missing"})
            if findings:
                kinds = sorted({f["kind"] for f in findings})
                reports.append({"target": okf.bundle_path_of(self.bundle, p), "kind": kinds[0] if len(kinds) == 1 else "artifact",
                                "findings": findings, "trust": okf.trust_tier(fm), "enqueued": False})
        return reports

    def verify(self) -> tuple[int, str]:
        rc, out, err = self._actor(["verify-bundle", "--json"])
        # A house rule on top of conformance: a source on disk whose SHAPE is
        # outside the closed set is non-conformant (4). Resolution is drift, not
        # conformance — it depends on the clone — and lives in scan_drift.
        bad = [f"{r['target']}: source {f['resource']!r} is not an accepted shape"
               for r in self.artifact_drift() for f in r["findings"] if f["kind"] == "source-shape-unknown"]
        if bad and rc in (0, 4):
            try:
                doc = json.loads(out)
                doc["errors"] = list(doc.get("errors") or []) + bad
                doc["conformant"] = False
                return 4, json.dumps(doc, indent=2)
            except Exception:
                return 4, ((out or err).strip() + "\n" + "\n".join(bad)).strip()
        return rc, (out or err).strip()

    def reindex(self, check: bool = False) -> tuple[int, str]:
        rc, out, err = self._actor(["reindex"] + (["--check"] if check else []))
        return rc, (out or err).strip()

    def scan_drift(self) -> tuple[int, str]:
        rc, out, err = self._actor(["scan-drift", "--json"])
        extra = self.artifact_drift()
        if extra and rc == 0:
            try:
                return rc, json.dumps(list(json.loads(out)) + extra, indent=2)
            except Exception:
                return rc, (out or err).strip() + "\n" + json.dumps(extra, indent=2)
        return rc, (out or err).strip()

    def actor_status(self) -> tuple[int, str]:
        rc, out, err = self._actor(["status"])
        return rc, (out or err).strip()


def build(root: Path, cfg: dict[str, Any]) -> KnowledgeAdapter:
    return OkfKnowledge(root, cfg)
