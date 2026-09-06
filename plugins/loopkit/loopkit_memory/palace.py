"""MemoryAdapter — episodic and semantic memory.

Primary: mempalace, through its CLI only (`mempalace --palace P search …`,
`wake-up`, `mine`). Its write verbs (drawers, diary, knowledge-graph add and
invalidate) exist only over MCP, so the honest CLI `remember()` is: write a
markdown note under state/memory/ with frontmatter, then `mine` that
directory. The note is the durable record; the mine is an index. A failed
mine is logged and the note stays — writes never block.

`invalidate()` never deletes: it appends a note carrying `valid_until` and
`supersedes: <id>`, so the old fact and the new one are never both live and
the history survives for temporal questions.

Fallback: `files` — grep over the notes directory, same Fact shape.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

from . import DEFAULT_TIMEOUT, Fact, MemoryAdapter, run, which

RECALL_MCP = re.compile(r"mcp__mempalace__mempalace_(search|kg_query|traverse)", re.I)
RECALL_BASH = re.compile(r"\bmempalace\b[^\n;|&]*\bsearch\b|memory\.py\s+recall\b", re.I)
SEARCH_TIMEOUT = 30.0
MINE_TIMEOUT = 120.0


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "note"


def _note_id(title: str) -> str:
    return f"{dt.date.today().isoformat()}-{_slug(title)}"


class FilesMemory(MemoryAdapter):
    """Notes on disk, grep to recall. Always works; the honest floor."""
    name = "files"

    def __init__(self, root: Path, cfg: dict[str, Any], reason: str = ""):
        super().__init__(root, cfg)
        self.notes = root / str(cfg.get("notes_dir") or "state/memory")
        self._reason = reason

    def available(self) -> tuple[bool, str]:
        return (True, "") if not self._reason else (False, self._reason)

    def status(self) -> dict[str, Any]:
        s = super().status()
        s["detail"] = f"{len(list(self.notes.glob('*.md')))} notes" if self.notes.exists() else "0 notes"
        return s

    def recall_tool_pattern(self):
        return RECALL_MCP, RECALL_BASH

    def recall(self, query: str, *, limit: int = 8) -> list[Fact]:
        if not self.notes.exists():
            return []
        words = [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]
        hits: list[tuple[int, Path]] = []
        for f in sorted(self.notes.glob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace").lower()
            score = sum(text.count(w) for w in words)
            if score:
                hits.append((score, f))
        out = []
        for score, f in sorted(hits, key=lambda t: -t[0])[:limit]:
            body = f.read_text(encoding="utf-8", errors="replace")
            fm = _frontmatter(body)
            title = next((l.lstrip("# ").strip() for l in body.splitlines() if l.startswith("# ")), f.stem)
            first = next((l for l in body.splitlines() if l and not l.startswith(("---", "#")) and ":" not in l[:20]), "")
            text = f"{title} — {first}" if first else title
            if fm.get("valid_until"):
                text = f"[invalidated {fm['valid_until']}] {text}"
            out.append(Fact(id=f.stem, text=text, source=str(f.relative_to(self.root)), date=fm.get("valid_from", ""),
                            valid_from=fm.get("valid_from", ""), valid_until=fm.get("valid_until", ""), score=float(score)))
        return out

    def remember(self, title: str, body: str, *, tags: list[str] | None = None, supersedes: str | None = None) -> str:
        self.notes.mkdir(parents=True, exist_ok=True)
        nid = _note_id(title)
        path = self.notes / f"{nid}.md"
        n = 2
        while path.exists():
            path = self.notes / f"{nid}-{n}.md"; n += 1
        fm = ["---", f"id: {path.stem}", f"valid_from: {dt.date.today().isoformat()}"]
        if tags:
            fm.append("tags: [" + ", ".join(tags) + "]")
        if supersedes:
            fm.append(f"supersedes: {supersedes}")
        fm.append("---")
        path.write_text("\n".join(fm) + f"\n\n# {title}\n\n{body.strip()}\n", encoding="utf-8")
        return str(path.relative_to(self.root))

    def invalidate(self, fact_id: str, reason: str) -> str:
        target = self.notes / f"{fact_id}.md"
        if not target.exists():
            return f"(no note {fact_id}; nothing invalidated)"
        text = target.read_text(encoding="utf-8")
        today = dt.date.today().isoformat()
        if "valid_until:" not in text:
            text = text.replace("---\n", f"---\nvalid_until: {today}\n", 1) if text.startswith("---\n") else f"---\nvalid_until: {today}\n---\n{text}"
        text += f"\n\n> INVALIDATED {today}: {reason.strip()}\n"
        target.write_text(text, encoding="utf-8")
        return f"{fact_id}: valid_until={today}"

    def wakeup(self) -> str:
        notes = sorted(self.notes.glob("*.md"))[-3:] if self.notes.exists() else []
        return "recent notes: " + ", ".join(n.stem for n in notes) if notes else "no notes yet"


class MempalaceCLI(FilesMemory):
    """mempalace via CLI for recall/wake-up; notes + mine for remember."""
    name = "mempalace"

    def __init__(self, root: Path, cfg: dict[str, Any]):
        super().__init__(root, cfg)
        self.bin = cfg.get("bin") or _find_bin(root)
        self.palace = _find_palace(root, str(cfg.get("palace") or ".mempalace/palace"))
        self.wing = cfg.get("wing")
        self._avail: tuple[bool, str] | None = None

    def available(self) -> tuple[bool, str]:
        if self._avail is None:
            if not self.bin:
                self._avail = (False, "mempalace not found (PATH, .venv/bin, or memory.json memory.bin)")
            elif not self.palace.exists():
                self._avail = (False, f"palace missing at {self.palace}")
            else:
                self._avail = (True, "")
        return self._avail

    def status(self) -> dict[str, Any]:
        s = MemoryAdapter.status(self)
        if s["available"]:
            rc, out, _ = run([self.bin, "--palace", str(self.palace), "status"], timeout=SEARCH_TIMEOUT)
            m = re.search(r"(\d+)\s+drawers", out)
            s["detail"] = f"{m.group(1)} drawers" if m else "cli"
        return s

    def _base(self) -> list[str]:
        return [self.bin, "--palace", str(self.palace)]

    def recall(self, query: str, *, limit: int = 8) -> list[Fact]:
        ok, _ = self.available()
        if not ok:
            return super().recall(query, limit=limit)
        cmd = self._base() + ["search", "--results", str(limit)]
        if self.wing:
            cmd += ["--wing", str(self.wing)]
        cmd.append(query)
        rc, out, err = run(cmd, timeout=SEARCH_TIMEOUT)
        if rc != 0:
            return super().recall(query, limit=limit)
        facts: list[Fact] = []
        for i, line in enumerate(l for l in out.splitlines() if l.strip()):
            if line.startswith(("=", "-", "MemPalace")):
                continue
            facts.append(Fact(id=f"palace-{i}", text=line.strip(), source="mempalace"))
            if len(facts) >= limit:
                break
        return facts or super().recall(query, limit=limit)

    def remember(self, title: str, body: str, *, tags=None, supersedes=None) -> str:
        rel = super().remember(title, body, tags=tags, supersedes=supersedes)
        ok, _ = self.available()
        if ok:
            cmd = self._base() + ["mine", "--mode", "projects"]
            if self.wing:
                cmd += ["--wing", str(self.wing)]
            cmd.append(str(self.notes))
            rc, out, err = run(cmd, timeout=MINE_TIMEOUT)
            if rc != 0:
                print(f"memory: note written ({rel}); mine failed and was skipped: {(err or out).strip()[:120]}", file=sys.stderr)
        return rel

    def wakeup(self) -> str:
        ok, _ = self.available()
        if not ok:
            return super().wakeup()
        cmd = self._base() + ["wake-up"] + (["--wing", str(self.wing)] if self.wing else [])
        rc, out, _ = run(cmd, timeout=SEARCH_TIMEOUT)
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        return " | ".join(lines[:6]) if rc == 0 and lines else super().wakeup()


def _main_checkout(root: Path) -> Path | None:
    """In a git worktree the .venv and the palace usually live in the main
    checkout; resolve it from the common git dir. None when not a worktree."""
    # `git worktree list` names the main working tree first, and unlike
    # --git-common-dir it is right for submodules too (whose common dir lives
    # under the superproject's .git/modules/, not under any working tree).
    rc, out, _ = run(["git", "worktree", "list", "--porcelain"], timeout=DEFAULT_TIMEOUT, cwd=root)
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.startswith("worktree "):
            main = Path(line[len("worktree "):].strip())
            # For a SUBMODULE the first entry is its git directory
            # (<super>/.git/modules/<name>), not a checkout; the checkout is
            # named by core.worktree in that directory's config, relative to it.
            if (main / "HEAD").is_file() and (main / "config").is_file() and not (main / ".git").exists():
                # core.worktree may sit in config.worktree (extensions.worktreeConfig);
                # git itself resolves it, so ask git rather than parsing either file.
                rc2, top, _ = run(["git", f"--git-dir={main}", "rev-parse", "--show-toplevel"], timeout=DEFAULT_TIMEOUT)
                if rc2 == 0 and top.strip():
                    main = Path(top.strip()).resolve()
            return main if main.resolve() != root.resolve() else None
    return None


def _find_bin(root: Path) -> str | None:
    candidates = [root / ".venv" / "bin" / "mempalace"]
    main = _main_checkout(root)
    if main:
        candidates.append(main / ".venv" / "bin" / "mempalace")
    candidates.append(Path.home() / ".local" / "bin" / "mempalace")
    for c in candidates:
        if c.is_file():
            return str(c)
    return which("mempalace")


def _find_palace(root: Path, rel: str) -> Path:
    here = root / rel
    if here.exists():
        return here
    main = _main_checkout(root)
    if main and (main / rel).exists():
        return main / rel
    return here


def _frontmatter(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        for line in text[4:end].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    return out


def build(root: Path, cfg: dict[str, Any]) -> MemoryAdapter:
    adapter = str(cfg.get("adapter") or "mempalace")
    if adapter == "mempalace":
        m = MempalaceCLI(root, cfg)
        ok, why = m.available()
        if ok or str(cfg.get("fallback", "files")) != "files":
            return m
        return FilesMemory(root, cfg, reason=f"mempalace {why}; using files")
    if adapter == "files":
        return FilesMemory(root, cfg)
    return FilesMemory(root, cfg, reason=f"unknown memory adapter '{adapter}'; using files")
