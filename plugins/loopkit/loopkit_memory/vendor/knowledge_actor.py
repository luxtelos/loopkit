# VENDORED verbatim from the origin project at commit 48d75b05 (2026-08-02 build),
# scripts/knowledge_actor.py. Do not edit here: fix upstream, re-vendor, keep this header.
#!/usr/bin/env python3
"""knowledge_actor.py — the mailbox actor that owns `knowledge/`.

The bundle is never hand-edited. Every change arrives as a message file in
`state/knowledge-mailbox/inbox/`, and this actor applies it. `okf_bundle.py`
owns the FORMAT; this file owns the PROTOCOL.

Why an actor at all: knowledge rots when anyone can edit it and nobody records
why. Serialising every change through one queue gives an audit trail
(`log.md`, `ledger.md`), makes replay safe, and lets a deterministic program —
not a model — decide what the bundle looks like.

THE DETERMINISM BOUNDARY (constitution.md, "the Minions rule")
--------------------------------------------------------------
This program NEVER calls a model. Parsing, ordering, locking, dedup, digests,
all six apply_* functions, index generation, drift detection and escalation are
plain deterministic Python. `drain` is a pure function of (mailbox, bundle),
so it is reproducible and safe to run unattended.

A model is genuinely needed for three things, and all three happen BEFORE
enqueue, never inside drain:
  1. writing the prose body of a concept,
  2. choosing which concept a new fact belongs to, and its title/tags/type,
  3. judging whether a drifted source actually invalidates the claim.
`scan-drift` can only say "the bytes moved"; only a reader can say "the rule
changed". Model output enters this system exclusively as data in a message file.

DELIVERY GUARANTEE
------------------
At-least-once delivery, effectively-once application. Exactly-once is not
claimable across a crash and this code does not pretend otherwise. Two
independent idempotency layers make redelivery harmless:
  1. `keys.txt` — a seen idempotency_key short-circuits to `duplicate`.
  2. Convergent apply — every op is a pure function whose result is byte-compared
     to disk; identical bytes mean no write and no log entry. So even a total
     ledger loss re-converges on replay.

Exit codes (they carry meaning; CI depends on it):
  0 success / nothing to do        4 bundle non-conformant
  1 unexpected internal error      5 reindex --check would change bytes
  2 transient, caller may retry    6 drain worked BUT dead-lettered something
  3 message validation failure
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import okf_bundle as okf  # noqa: E402

ACTOR_ID = "knowledge-actor/0.1"
MESSAGE_SCHEMA = "okf-mailbox/1"
MESSAGE_TYPE = "okf.message"

VALID_OPS = ("upsert", "deprecate", "verify", "link", "stale", "delete")
#: `link` carries from/to in its payload instead of a single target.
OPS_REQUIRING_TARGET = ("upsert", "deprecate", "verify", "stale", "delete")
#: Ops that cannot create a concept — the target must already exist.
OPS_REQUIRING_EXISTING = ("deprecate", "verify", "stale", "delete")

DEFAULT_BUNDLE = "knowledge"
DEFAULT_MAILBOX = "state/knowledge-mailbox"
DEFAULT_LOCK_TTL = 900
DEFAULT_MAX_ATTEMPTS = 3

LEDGER_COLUMNS = (
    "idempotency_key",
    "msg_id",
    "op",
    "target",
    "applied_at",
    "result",
)

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_TRANSIENT = 2
EXIT_INVALID = 3
EXIT_NONCONFORMANT = 4
EXIT_WOULD_CHANGE = 5
EXIT_DEAD_LETTERS = 6


class TransientError(RuntimeError):
    """Retry later — the lock is held, or the filesystem said no."""


class PermanentError(ValueError):
    """Dead-letter now. Retrying a schema violation three times is just noise."""


# ==========================================================================
# Paths
# ==========================================================================


@dataclass
class Paths:
    root: Path
    bundle: Path
    mailbox: Path

    @property
    def inbox(self) -> Path:
        return self.mailbox / "inbox"

    @property
    def processing(self) -> Path:
        return self.mailbox / "processing"

    @property
    def processed(self) -> Path:
        return self.mailbox / "processed"

    @property
    def dead_letter(self) -> Path:
        return self.mailbox / "dead-letter"

    @property
    def pending_delete(self) -> Path:
        return self.mailbox / "pending-delete"

    @property
    def ledger(self) -> Path:
        return self.mailbox / "ledger.md"

    @property
    def keys(self) -> Path:
        return self.mailbox / "keys.txt"

    @property
    def source_index(self) -> Path:
        return self.mailbox / "source-index.json"

    @property
    def lock(self) -> Path:
        return self.mailbox / ".lock"

    @property
    def needs_human(self) -> Path:
        """Where dead-letters escalate. ESCALATION FOLLOWS THE MAILBOX.

        If the mailbox lives inside the repo (the normal case) this is the real
        `inbox/needs-human.md`, which fires notify_needs_human.py → Slack. If the
        mailbox has been redirected elsewhere — a test, a scratch bundle — the
        escalation goes with it. Anchoring on the repo root instead would let a
        throwaway run prepend synthetic entries to the real escalation door,
        which is exactly what happened the first time this was tested.
        """
        try:
            self.mailbox.resolve().relative_to(self.root.resolve())
        except ValueError:
            return self.mailbox / "needs-human.md"
        return self.root / "inbox" / "needs-human.md"

    def ensure(self) -> None:
        for path in (
            self.bundle,
            self.inbox,
            self.processing,
            self.processed,
            self.dead_letter,
            self.pending_delete,
        ):
            path.mkdir(parents=True, exist_ok=True)


def resolve_paths(args: argparse.Namespace) -> Paths:
    """Anchor everything on the working-tree root.

    `--show-toplevel`, not `--git-common-dir`: this repo is a git submodule, so
    the common dir is `.git/modules/ledgermind`, which is not a working tree.
    The mailbox is tracked and per-branch by design — messages travel with the
    PR, and git (not a filesystem lock) is the real concurrency control.
    """
    root = okf.repo_root(Path.cwd())
    bundle = Path(getattr(args, "bundle", None) or os.environ.get("KNOWLEDGE_BUNDLE_DIR") or (root / DEFAULT_BUNDLE))
    mailbox = Path(getattr(args, "mailbox", None) or os.environ.get("KNOWLEDGE_MAILBOX_DIR") or (root / DEFAULT_MAILBOX))
    return Paths(root=root, bundle=bundle, mailbox=mailbox)


# ==========================================================================
# Time, ids, digests
# ==========================================================================


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def rfc3339(moment: _dt.datetime) -> str:
    return moment.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso(moment: _dt.datetime | None = None) -> str:
    return (moment or utc_now()).astimezone(_dt.timezone.utc).strftime("%Y-%m-%d")


def derive_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (slug[:limit].rstrip("-")) or "message"


def git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def git_introducer_email(root: Path, path: Path) -> str | None:
    """Who first committed `path`. The only authority available for a
    `verified: human:` claim — a file cannot authenticate its own author."""
    try:
        result = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%ae", "--", str(path)],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else None


# ==========================================================================
# Messages
# ==========================================================================


@dataclass
class Message:
    path: Path
    frontmatter: dict
    body: str

    @property
    def op(self) -> str:
        return str(self.frontmatter.get("op", ""))

    @property
    def target(self) -> str:
        return str(self.frontmatter.get("target", "") or "")

    @property
    def msg_id(self) -> str:
        return str(self.frontmatter.get("msg_id", ""))

    @property
    def key(self) -> str:
        return str(self.frontmatter.get("idempotency_key", ""))

    @property
    def reason(self) -> str:
        return str(self.frontmatter.get("reason", ""))

    @property
    def by(self) -> str:
        return str(self.frontmatter.get("enqueued_by", ""))

    @property
    def payload(self) -> dict:
        payload = self.frontmatter.get("payload") or {}
        return payload if isinstance(payload, dict) else {}

    @property
    def attempts(self) -> int:
        try:
            return int(self.frontmatter.get("x_attempts") or 0)
        except (TypeError, ValueError):
            return 0

    def render(self) -> str:
        return okf.render(self.frontmatter, self.body)


def read_message(path: Path) -> Message:
    try:
        frontmatter, body = okf.parse_frontmatter(path.read_text(encoding="utf-8"))
    except okf.FrontmatterError as exc:
        raise PermanentError(f"unparseable message frontmatter: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise PermanentError(f"message is not valid UTF-8: {exc}") from exc
    return Message(path=path, frontmatter=frontmatter, body=body)


def validate_message(message: Message, *, now: _dt.datetime | None = None) -> None:
    """Every failure here is PERMANENT — a malformed envelope will not fix
    itself on a retry, so it dead-letters on the first pass."""
    fm = message.frontmatter

    if fm.get("type") != MESSAGE_TYPE:
        raise PermanentError(f"type must be {MESSAGE_TYPE!r}, got {fm.get('type')!r}")
    schema = fm.get("schema")
    if schema != MESSAGE_SCHEMA:
        raise PermanentError(
            f"unknown schema {schema!r}; this actor implements {MESSAGE_SCHEMA!r}. "
            "Refusing rather than guessing at an envelope from another version."
        )
    for required in ("msg_id", "op", "enqueued_at", "enqueued_by", "idempotency_key", "reason"):
        if not fm.get(required):
            raise PermanentError(f"missing required field: {required}")

    op = message.op
    if op not in VALID_OPS:
        raise PermanentError(f"unknown op {op!r}; expected one of {', '.join(VALID_OPS)}")

    if not okf.ACTOR_RE.fullmatch(message.by):
        raise PermanentError(
            f"enqueued_by {message.by!r} is not a valid actor "
            "(human:<id>, process:<id>, or <producer>/<version>)"
        )

    if len(message.reason) > 200:
        raise PermanentError("reason must be <= 200 chars; it goes verbatim into log.md")

    if op in OPS_REQUIRING_TARGET:
        if not message.target:
            raise PermanentError(f"op {op!r} requires a `target`")
        if not message.target.startswith("/") or not message.target.endswith(".md"):
            raise PermanentError(
                f"target must be bundle-absolute and .md, got {message.target!r}"
            )
        if okf.is_reserved(message.target):
            raise PermanentError(
                f"{message.target} is a reserved file; index.md and log.md are "
                "generated, never messaged"
            )

    if op == "link":
        payload = message.payload
        for side in ("from", "to"):
            value = payload.get(side)
            if not value or not str(value).startswith("/"):
                raise PermanentError(f"link payload needs a bundle-absolute `{side}`")
        if not payload.get("relation"):
            raise PermanentError("link payload needs a `relation`")

    if op == "delete" and not message.payload.get("confirm_digest"):
        raise PermanentError(
            "delete requires payload.confirm_digest (the current file's digest), "
            "so a stale delete cannot destroy newer content"
        )

    if op == "verify":
        payload = message.payload
        if not payload.get("verified_by") or not payload.get("verified_at"):
            raise PermanentError("verify payload needs `verified_by` and `verified_at`")
        if not okf.ACTOR_RE.fullmatch(str(payload["verified_by"])):
            raise PermanentError(f"verified_by {payload['verified_by']!r} is not a valid actor")

    expires = fm.get("expires_at")
    if expires:
        moment = now or utc_now()
        if str(expires) < rfc3339(moment):
            raise PermanentError(f"message expired at {expires}")


# ==========================================================================
# Ledger and keys
# ==========================================================================


def read_keys(paths: Paths) -> set[str]:
    if not paths.keys.is_file():
        return set()
    return {
        line.strip()
        for line in paths.keys.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def append_key(paths: Paths, key: str) -> None:
    """Append-only, and never garbage-collected: losing a key would let an old
    drift re-fire forever."""
    header = "" if paths.keys.is_file() else "# idempotency keys, append-only. Never prune.\n"
    with paths.keys.open("a", encoding="utf-8") as handle:
        handle.write(header + key + "\n")


def ledger_rows(paths: Paths) -> list[dict]:
    if not paths.ledger.is_file():
        return []
    rows: list[dict] = []
    for line in paths.ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("| -:"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(LEDGER_COLUMNS) or cells[0] == LEDGER_COLUMNS[0]:
            continue
        rows.append(dict(zip(LEDGER_COLUMNS, cells)))
    return rows


def append_ledger(paths: Paths, row: dict) -> None:
    if not paths.ledger.is_file():
        header = [
            "# ledger.md — every message this actor has applied",
            "",
            "> Append-only. `keys.txt` is the fast dedup path; this table is the",
            "> human-readable one, and `knowledge_actor.py log --rebuild` can",
            "> reconstruct `log.md` from it after a bad merge.",
            "",
            "| " + " | ".join(LEDGER_COLUMNS) + " |",
            "| " + " | ".join("---" for _ in LEDGER_COLUMNS) + " |",
            "",
        ]
        okf.write_text_atomic(paths.ledger, "\n".join(header))
    cells = [str(row.get(column, "")).replace("|", "\\|") for column in LEDGER_COLUMNS]
    text = paths.ledger.read_text(encoding="utf-8").rstrip("\n")
    okf.write_text_atomic(paths.ledger, text + "\n| " + " | ".join(cells) + " |\n")


# ==========================================================================
# Locking
# ==========================================================================


class Lock:
    """`os.mkdir` as the primitive — atomic on POSIX, and unlike `flock` it is
    dependable on the mounted volume this checkout lives on.

    Only ever protects ONE working tree. Two checkouts draining concurrently
    both succeed and then collide in git, which is the documented behaviour, not
    a bug: git is the real concurrency control here.
    """

    def __init__(self, paths: Paths, ttl: int = DEFAULT_LOCK_TTL):
        self.paths = paths
        self.ttl = ttl
        self.acquired = False
        self.broke_stale = False

    def __enter__(self) -> "Lock":
        self.paths.mailbox.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(self.paths.lock)
        except FileExistsError:
            self._handle_existing()
            os.mkdir(self.paths.lock)
        self.acquired = True
        okf.write_text_atomic(
            self.paths.lock / "owner.json",
            json.dumps(
                {
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "started_at": rfc3339(utc_now()),
                    "argv": sys.argv[1:],
                },
                indent=2,
            )
            + "\n",
        )
        return self

    def _handle_existing(self) -> None:
        owner_file = self.paths.lock / "owner.json"
        owner: dict = {}
        try:
            owner = json.loads(owner_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

        age = None
        started = owner.get("started_at")
        if started:
            try:
                age = (utc_now() - _dt.datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=_dt.timezone.utc
                )).total_seconds()
            except ValueError:
                age = None

        same_host = owner.get("host") == socket.gethostname()
        if not same_host:
            # Never break another machine's lock automatically. If it is really
            # stale, a human deleting one directory is cheaper than two actors
            # writing the bundle at once.
            raise TransientError(
                f"lock held by {owner.get('host', 'an unknown host')} "
                f"(pid {owner.get('pid', '?')}); not breaking a remote lock"
            )
        if age is not None and age < self.ttl and _pid_alive(owner.get("pid")):
            raise TransientError(
                f"lock held by live pid {owner.get('pid')} for {int(age)}s; retry later"
            )
        sys.stderr.write(
            f"knowledge-actor: breaking stale lock (pid {owner.get('pid')}, "
            f"age {int(age) if age is not None else '?'}s)\n"
        )
        shutil.rmtree(self.paths.lock, ignore_errors=True)
        self.broke_stale = True

    def __exit__(self, *_exc) -> None:
        if self.acquired:
            shutil.rmtree(self.paths.lock, ignore_errors=True)


def _pid_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


# ==========================================================================
# The six operations — pure functions, all of them
# ==========================================================================


def _as_list(value: Any) -> list:
    """OKF consumers must treat a bare mapping as a single-element list."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _merge_related(body: str, entries: list[tuple[str, str]]) -> str:
    """Insert `## Related` bullets, sorted and deduped on (relation, path)."""
    marker = "## Related"
    existing: list[str] = []
    head = body
    if marker in body:
        head, _, tail = body.partition(marker)
        existing = [line.strip() for line in tail.splitlines() if line.strip().startswith("- ")]
    wanted = {f"- {relation}: [{path}]({path})" for relation, path in entries}
    merged = sorted(set(existing) | wanted)
    return okf.normalize_body(head.rstrip() + "\n\n" + marker + "\n\n" + "\n".join(merged) + "\n")


def apply_upsert(concept: okf.Concept | None, message: Message, paths: Paths) -> okf.Concept:
    payload = message.payload
    frontmatter = dict(concept.frontmatter) if concept else {}
    frontmatter.update(payload.get("frontmatter") or {})

    sources = payload.get("sources")
    if sources is not None:
        frontmatter["sources"] = _as_list(sources)

    frontmatter.setdefault("type", "Concept")
    frontmatter.setdefault("status", "draft")
    frontmatter["generated"] = {"by": message.by, "at": message.frontmatter["enqueued_at"]}

    body_mode = str(payload.get("body_mode") or "replace")
    old_body = concept.body if concept else ""
    if body_mode == "replace":
        body = message.body
    elif body_mode == "append":
        body = old_body.rstrip("\n") + "\n\n" + message.body if old_body else message.body
    elif body_mode == "section":
        section = payload.get("section")
        if not section:
            raise PermanentError("body_mode 'section' requires payload.section")
        body = _replace_section(old_body, str(section), message.body)
    else:
        raise PermanentError(f"unknown body_mode {body_mode!r}")

    if payload.get("record_digests", True):
        frontmatter["x_source_digests"] = _capture_digests(
            paths, frontmatter.get("sources"), at=str(message.frontmatter["enqueued_at"])
        )
    # A fresh upsert supersedes any recorded drift: the author has now seen it.
    frontmatter.pop("x_drift", None)
    frontmatter.pop("stale_after", None) if payload.get("clear_stale", True) else None

    return okf.Concept(path=message.target, frontmatter=frontmatter, body=okf.normalize_body(body))


def _replace_section(body: str, heading: str, replacement: str) -> str:
    pattern = re.compile(
        rf"^{re.escape(heading)}[ \t]*$.*?(?=^#{{1,6}} |\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    new_block = heading.rstrip() + "\n\n" + replacement.strip() + "\n\n"
    if pattern.search(body):
        return okf.normalize_body(pattern.sub(new_block, body, count=1))
    return okf.normalize_body(body.rstrip("\n") + "\n\n" + new_block)


def apply_deprecate(concept: okf.Concept, message: Message, _paths: Paths) -> okf.Concept:
    frontmatter = dict(concept.frontmatter)
    frontmatter["status"] = "deprecated"
    successor = message.payload.get("superseded_by")
    if successor:
        frontmatter["superseded_by"] = successor
    date = str(message.frontmatter["enqueued_at"])[:10]
    callout = f"> **Deprecated {date}** — {message.reason}"
    if successor:
        callout += f" Superseded by [{successor}]({successor})."
    body = concept.body
    if not body.startswith("> **Deprecated"):
        body = callout + "\n\n" + body
    return okf.Concept(concept.path, frontmatter, okf.normalize_body(body))


def apply_verify(concept: okf.Concept, message: Message, _paths: Paths) -> okf.Concept:
    frontmatter = dict(concept.frontmatter)
    entries = _as_list(frontmatter.get("verified"))
    new_entry = {
        "by": str(message.payload["verified_by"]),
        "at": str(message.payload["verified_at"]),
    }
    seen = {(str(e.get("by")), str(e.get("at"))) for e in entries if isinstance(e, dict)}
    if (new_entry["by"], new_entry["at"]) not in seen:
        entries.append(new_entry)
    frontmatter["verified"] = sorted(
        (e for e in entries if isinstance(e, dict)),
        key=lambda e: (str(e.get("at")), str(e.get("by"))),
    )
    return okf.Concept(concept.path, frontmatter, concept.body)


def apply_stale(concept: okf.Concept, message: Message, _paths: Paths) -> okf.Concept:
    frontmatter = dict(concept.frontmatter)
    frontmatter["stale_after"] = str(message.frontmatter["enqueued_at"])[:10]
    frontmatter["x_drift"] = {
        "kind": str(message.payload.get("kind", "source-changed")),
        "detected_at": str(message.frontmatter["enqueued_at"]),
        "findings": _as_list(message.payload.get("findings")),
    }
    # Deliberately does NOT touch `status`. Clobbering a human's `stable` would
    # destroy the exact ratification this system exists to preserve.
    return okf.Concept(concept.path, frontmatter, concept.body)


def apply_link(message: Message, paths: Paths) -> list[okf.Concept]:
    payload = message.payload
    left, right = str(payload["from"]), str(payload["to"])
    relation = str(payload["relation"])
    inverse = str(payload.get("inverse_relation") or relation)
    bidirectional = payload.get("bidirectional", True)

    out: list[okf.Concept] = []
    source = okf.load_concept(paths.bundle, left)
    if source is None:
        raise PermanentError(f"link source does not exist: {left}")
    out.append(okf.Concept(left, dict(source.frontmatter), _merge_related(source.body, [(relation, right)])))

    if bidirectional:
        other = okf.load_concept(paths.bundle, right)
        if other is None:
            raise PermanentError(f"link target does not exist: {right}")
        out.append(
            okf.Concept(right, dict(other.frontmatter), _merge_related(other.body, [(inverse, left)]))
        )
    return out


def _capture_digests(paths: Paths, sources: Any, *, at: str) -> list[dict]:
    """Record a `git hash-object` digest per repo-file source.

    Kept in the concept's own frontmatter rather than a sidecar: the claim and
    the digest of its evidence then land in ONE atomic write and cannot
    desynchronise. Legal under OKF, which requires consumers to tolerate unknown
    keys; the `x_` prefix guarantees no collision with a future official key.

    `at` is the MESSAGE's enqueued_at, never the clock. Every apply_* must be a
    pure function of (concept, message) or the convergent-apply idempotency
    layer breaks: a replay would differ by one second, rewrite the file, and
    emit a spurious log entry. Same trap as `generated.at` on index files.
    """
    captured: list[dict] = []
    head = git_head(paths.root)
    for entry in _as_list(sources):
        if not isinstance(entry, dict):
            continue
        resource = str(entry.get("resource", ""))
        if not resource or "://" in resource:
            continue  # remote sources are digested only with --include-remote
        digest = okf.blob_digest(paths.root / resource)
        record = {
            "id": entry.get("id") or slugify(resource),
            "resource": resource,
            "kind": "repo-file",
            "digest": digest or "missing",
            "captured_at": at,
            "granularity": "file",
        }
        if head:
            record["captured_commit"] = head
        captured.append(record)
    return captured


# ==========================================================================
# log.md
# ==========================================================================

_LOG_HEADER_FM = {
    "type": "log",
    "title": "Knowledge bundle change log",
    "description": "Every change the knowledge actor applied to this bundle, newest first.",
}


def append_log_entries(paths: Paths, entries: list[dict]) -> None:
    """Insert into the right `## YYYY-MM-DD` group. Append-only and *edited*,
    never regenerated — regenerating would let a `delete` erase its own history.
    Deduped on (date, msg_id) so a replay cannot double-log."""
    if not entries:
        return
    log_path = paths.bundle / "log.md"
    if log_path.is_file():
        frontmatter, body = okf.parse_frontmatter(log_path.read_text(encoding="utf-8"))
    else:
        frontmatter, body = dict(_LOG_HEADER_FM), "# Change history\n\nNewest first.\n"

    groups, preamble = _split_log_groups(body)
    for entry in entries:
        date = entry["applied_at"][:10]
        line = (
            f"- **{entry['op']}** [{entry['target']}]({entry['target']}) — "
            f"{entry['reason']} _({entry['msg_id'][:8]}, {entry['by']})_"
        )
        bucket = groups.setdefault(date, [])
        if any(f"_({entry['msg_id'][:8]}," in existing for existing in bucket):
            continue
        bucket.append(line)

    rebuilt = [preamble.rstrip("\n"), ""]
    for date in sorted(groups, reverse=True):
        rebuilt.append(f"## {date}")
        rebuilt.append("")
        rebuilt.extend(sorted(groups[date]))
        rebuilt.append("")
    okf.write_text_atomic(log_path, okf.render(frontmatter, "\n".join(rebuilt)))


def _split_log_groups(body: str) -> tuple[dict[str, list[str]], str]:
    groups: dict[str, list[str]] = {}
    preamble_lines: list[str] = []
    current: str | None = None
    for line in body.splitlines():
        match = re.match(r"^## (\d{4}-\d{2}-\d{2})\s*$", line)
        if match:
            current = match.group(1)
            groups.setdefault(current, [])
            continue
        if current is None:
            preamble_lines.append(line)
        elif line.strip().startswith("- "):
            groups[current].append(line.strip())
    return groups, "\n".join(preamble_lines)


# ==========================================================================
# index.md
# ==========================================================================


def _subtree_timestamp(rows: list[dict], prefix: str) -> str:
    """`generated.at` for an index, derived from the ledger — NEVER the clock.

    This one detail is what makes the actor quiet. With a wall-clock timestamp
    every `reindex` would rewrite every index, `--check` would fail forever, and
    a scheduled run would commit noise daily. Derived from content, the bytes
    only change when the content does.
    """
    stamps = [
        row["applied_at"]
        for row in rows
        if row.get("target", "").startswith(prefix) and row.get("applied_at")
    ]
    return max(stamps) if stamps else "1970-01-01T00:00:00Z"


def regenerate_indexes(paths: Paths, *, check_only: bool = False) -> list[str]:
    """Rebuild every `index.md`. Returns the bundle paths that changed
    (or would change, under --check)."""
    rows = ledger_rows(paths)
    changed: list[str] = []
    directories = {paths.bundle}
    for path in paths.bundle.rglob("*"):
        if path.is_dir() and not path.name.startswith("."):
            directories.add(path)

    for directory in sorted(directories):
        concepts = sorted(
            p for p in directory.glob("*.md") if not okf.is_reserved(p)
        )
        subdirs = sorted(
            d for d in directory.iterdir()
            if d.is_dir() and not d.name.startswith(".") and any(d.rglob("*.md"))
        )
        if not concepts and not subdirs:
            continue

        rel = "/" + directory.resolve().relative_to(paths.bundle.resolve()).as_posix()
        rel = "/" if rel == "/." else rel
        prefix = rel if rel.endswith("/") else rel + "/"
        rendered = _render_index(paths, directory, concepts, subdirs, prefix, rows)

        index_path = directory / "index.md"
        current = index_path.read_text(encoding="utf-8") if index_path.is_file() else None
        if current != rendered:
            changed.append(prefix + "index.md")
            if not check_only:
                okf.write_text_atomic(index_path, rendered)
    return changed


def _render_index(
    paths: Paths,
    directory: Path,
    concepts: list[Path],
    subdirs: list[Path],
    prefix: str,
    rows: list[dict],
) -> str:
    is_root = directory.resolve() == paths.bundle.resolve()
    title = "Knowledge bundle" if is_root else directory.name.replace("-", " ").title()

    frontmatter: dict[str, Any] = {
        "type": "index",
        "title": title,
        "description": f"Concepts under {prefix}",
        "generated": {"by": ACTOR_ID, "at": _subtree_timestamp(rows, prefix)},
    }
    if is_root:
        frontmatter["okf_version"] = okf.OKF_VERSION

    lines: list[str] = [f"# {title}", ""]
    if concepts:
        lines += [
            "## Concepts",
            "",
            "| concept | type | status | trust |",
            "| --- | --- | --- | --- |",
        ]
        for path in concepts:
            try:
                fm, _ = okf.parse_frontmatter(path.read_text(encoding="utf-8"))
            except okf.FrontmatterError:
                fm = {}
            link = prefix + path.name
            label = str(fm.get("title") or path.stem.replace("-", " "))
            lines.append(
                f"| [{label}]({link}) | {fm.get('type', '?')} | "
                f"{fm.get('status', 'draft')} | {okf.trust_tier(fm)} |"
            )
        lines.append("")
    if subdirs:
        lines += ["## Subdirectories", ""]
        for directory_child in subdirs:
            lines.append(f"- [{directory_child.name}/]({prefix}{directory_child.name}/index.md)")
        lines.append("")
    if is_root:
        lines += ["## Change history", "", "[/log.md](/log.md)", ""]
    return okf.render(frontmatter, "\n".join(lines))


def rebuild_source_index(paths: Paths) -> dict[str, list[str]]:
    """repo-relative source path -> [concept paths that cite it].

    The PostToolUse hook does one dict lookup against this. A hook that walked
    the bundle on every edit would be disabled inside a week.
    """
    index: dict[str, list[str]] = {}
    for path in okf.iter_concepts(paths.bundle):
        try:
            fm, _ = okf.parse_frontmatter(path.read_text(encoding="utf-8"))
        except okf.FrontmatterError:
            continue
        concept_path = okf.bundle_path_of(paths.bundle, path)
        for entry in _as_list(fm.get("x_source_digests")):
            if isinstance(entry, dict) and entry.get("kind") == "repo-file":
                index.setdefault(str(entry.get("resource")), []).append(concept_path)
    for value in index.values():
        value.sort()
    okf.write_text_atomic(paths.source_index, json.dumps(index, indent=2, sort_keys=True) + "\n")
    return index


# ==========================================================================
# Enqueue
# ==========================================================================


def enqueue_message(
    paths: Paths,
    *,
    op: str,
    reason: str,
    by: str,
    target: str | None = None,
    payload: dict | None = None,
    body: str = "",
    idempotency_key: str | None = None,
    priority: int = 50,
) -> Path:
    paths.ensure()
    now = utc_now()
    msg_id = uuid.uuid4().hex
    key = idempotency_key or derive_key(op, target or "", body, json.dumps(payload or {}, sort_keys=True))

    frontmatter: dict[str, Any] = {
        "type": MESSAGE_TYPE,
        "schema": MESSAGE_SCHEMA,
        "msg_id": msg_id,
        "op": op,
        "enqueued_at": rfc3339(now),
        "enqueued_by": by,
        "idempotency_key": key,
        "reason": reason,
        "priority": priority,
    }
    if target:
        frontmatter["target"] = target
    if payload:
        frontmatter["payload"] = payload
    head = git_head(paths.root)
    if head:
        frontmatter["x_source_commit"] = head

    counter = len(list(paths.inbox.glob("*.msg.md"))) + 1
    name = (
        f"{now.strftime('%Y%m%dT%H%M%SZ')}-{counter:04d}-{op}-"
        f"{slugify(target or payload and payload.get('from') or reason)}-{msg_id[:8]}.msg.md"
    )
    path = paths.inbox / name
    message = Message(path=path, frontmatter=frontmatter, body=okf.normalize_body(body or reason))
    validate_message(message, now=now)
    okf.write_text_atomic(path, message.render())
    return path


# ==========================================================================
# Drain
# ==========================================================================


@dataclass
class DrainResult:
    applied: list[dict] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    noops: list[str] = field(default_factory=list)
    dead: list[tuple[str, str]] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)


def drain(paths: Paths, *, max_messages: int | None = None, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
          dry_run: bool = False) -> DrainResult:
    paths.ensure()
    result = DrainResult()

    # Anything in processing/ is crash residue from a previous run: drive it
    # first, at the head of the queue, with attempts already incremented.
    queue = sorted(paths.processing.glob("*.msg.md")) + sorted(paths.inbox.glob("*.msg.md"))
    if max_messages is not None:
        queue = queue[:max_messages]

    seen_keys = read_keys(paths)
    tombstones: set[str] = set()
    # Group by target so same-target ops stay ordered while different targets
    # commute; `link` runs last so it can never reference a concept a later
    # message in this same run removes.
    ordered = [p for p in queue if _peek_op(p) != "link"] + [p for p in queue if _peek_op(p) == "link"]

    for path in ordered:
        try:
            _process_one(paths, path, seen_keys, tombstones, result, max_attempts, dry_run)
        except TransientError as exc:
            result.deferred.append(f"{path.name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            result.dead.append((path.name, f"unexpected: {exc!r}"))

    if not dry_run:
        regenerate_indexes(paths)
        rebuild_source_index(paths)
        if result.dead:
            escalate_to_inbox(paths, result)
    return result


def _peek_op(path: Path) -> str:
    try:
        return read_message(path).op
    except Exception:  # noqa: BLE001 - a bad message still needs an ordering slot
        return ""


def _process_one(
    paths: Paths,
    path: Path,
    seen_keys: set[str],
    tombstones: set[str],
    result: DrainResult,
    max_attempts: int,
    dry_run: bool,
) -> None:
    try:
        message = read_message(path)
        validate_message(message)
    except PermanentError as exc:
        _dead_letter(paths, path, str(exc), dry_run)
        result.dead.append((path.name, str(exc)))
        return

    if message.key in seen_keys:
        result.duplicates.append(message.msg_id)
        if not dry_run:
            _archive(paths, path, message, "duplicate")
        return

    if message.op in OPS_REQUIRING_EXISTING or message.op == "upsert":
        if message.target in tombstones:
            reason = "resurrect-after-delete: this target was deleted earlier in the same drain"
            _dead_letter(paths, path, reason, dry_run)
            result.dead.append((path.name, reason))
            return

    try:
        concepts, removed = _apply(paths, message)
    except PermanentError as exc:
        _dead_letter(paths, path, str(exc), dry_run)
        result.dead.append((path.name, str(exc)))
        return
    except OSError as exc:
        raise TransientError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        attempts = message.attempts + 1
        if attempts >= max_attempts:
            reason = f"failed {attempts}x, last error: {exc!r}"
            _dead_letter(paths, path, reason, dry_run)
            result.dead.append((path.name, reason))
        else:
            message.frontmatter["x_attempts"] = attempts
            if not dry_run:
                okf.write_text_atomic(path, message.render())
            result.deferred.append(f"{path.name}: attempt {attempts}")
        return

    if dry_run:
        result.applied.append({"msg_id": message.msg_id, "op": message.op, "target": message.target})
        return

    changed = False
    for concept in concepts:
        changed |= okf.write_concept(paths.bundle, concept)
    for target in removed:
        changed = True
        tombstones.add(target)

    applied_at = rfc3339(utc_now())
    append_key(paths, message.key)
    seen_keys.add(message.key)
    append_ledger(
        paths,
        {
            "idempotency_key": message.key,
            "msg_id": message.msg_id,
            "op": message.op,
            "target": message.target or str(message.payload.get("from", "")),
            "applied_at": applied_at,
            "result": "applied" if changed else "no-op",
        },
    )
    if changed:
        entry = {
            "msg_id": message.msg_id,
            "op": message.op,
            "target": message.target or str(message.payload.get("from", "")),
            "reason": message.reason,
            "by": message.by,
            "applied_at": applied_at,
        }
        append_log_entries(paths, [entry])
        result.applied.append(entry)
    else:
        result.noops.append(message.msg_id)
    _archive(paths, path, message, "applied" if changed else "no-op")


def _apply(paths: Paths, message: Message) -> tuple[list[okf.Concept], list[str]]:
    """Dispatch to the pure apply_* function. Returns (concepts to write, targets removed)."""
    if message.op == "link":
        return apply_link(message, paths), []

    try:
        concept = okf.load_concept(paths.bundle, message.target)
    except ValueError as exc:
        raise PermanentError(str(exc)) from exc

    if message.op in OPS_REQUIRING_EXISTING and concept is None:
        raise PermanentError(f"op {message.op!r} needs an existing concept at {message.target}")

    if message.op == "upsert":
        return [apply_upsert(concept, message, paths)], []
    if message.op == "deprecate":
        return [apply_deprecate(concept, message, paths)], []
    if message.op == "verify":
        _assert_human_claim_is_credible(paths, message)
        return [apply_verify(concept, message, paths)], []
    if message.op == "stale":
        return [apply_stale(concept, message, paths)], []
    if message.op == "delete":
        return [], [_stage_delete(paths, message, concept)]
    raise PermanentError(f"unhandled op {message.op!r}")


def _assert_human_claim_is_credible(paths: Paths, message: Message) -> None:
    """A message file cannot authenticate its own author.

    `verified: human:` is the only trust signal OKF has, so a `verify` claiming a
    human is cross-checked against git authorship — the one authority available
    that an agent cannot simply type. Unknown authorship (the message is not
    committed yet) is allowed; a KNOWN bot author claiming to be a human is not.
    """
    claimed = str(message.payload.get("verified_by", ""))
    if not claimed.startswith("human:"):
        return
    introducer = git_introducer_email(paths.root, message.path)
    if introducer and ("[bot]" in introducer or introducer.endswith("github-actions.com")):
        raise PermanentError(
            f"message claims {claimed!r} but was committed by {introducer!r}; "
            "the actor never signs for a human"
        )


def _stage_delete(paths: Paths, message: Message, concept: okf.Concept) -> str:
    """Never removes bytes on a message alone.

    An agent can write `enqueued_by: human:akul` on anything. So a delete moves
    the concept to pending-delete/ and escalates; only `confirm-delete`, run by a
    person, actually removes it.
    """
    path = okf.resolve_target(paths.bundle, message.target)
    actual = okf.blob_digest(path)
    expected = str(message.payload.get("confirm_digest"))
    if actual != expected:
        raise PermanentError(
            f"confirm_digest mismatch for {message.target}: message expected "
            f"{expected}, file is {actual}. Refusing to delete newer content."
        )
    staged = paths.pending_delete / (message.target.strip("/").replace("/", "__"))
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(staged))
    return message.target


def _dead_letter(paths: Paths, path: Path, reason: str, dry_run: bool) -> None:
    if dry_run:
        return
    paths.dead_letter.mkdir(parents=True, exist_ok=True)
    destination = paths.dead_letter / path.name
    shutil.move(str(path), str(destination))
    okf.write_text_atomic(
        destination.with_suffix(".err.md"),
        "\n".join(
            [
                f"# Dead letter: {path.name}",
                "",
                f"- **when**: {rfc3339(utc_now())}",
                f"- **git HEAD**: {git_head(paths.root) or 'unknown'}",
                "",
                "## Why",
                "",
                reason,
                "",
                "## What to do",
                "",
                "Fix the message and re-enqueue it, or delete it. This actor will",
                "not retry a permanently-invalid message — retrying a schema",
                "violation three times is noise, not resilience.",
                "",
            ]
        ),
    )


def _archive(paths: Paths, path: Path, message: Message, outcome: str) -> None:
    day = paths.processed / str(message.frontmatter.get("enqueued_at", ""))[:10]
    day.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.move(str(path), str(day / path.name))


def escalate_to_inbox(paths: Paths, result: DrainResult) -> None:
    """ONE aggregated entry per drain run.

    Per-message escalation would turn a bad batch into forty Slack pings via
    notify_needs_human.py, and an escalation door nobody can face is a door
    nobody opens.
    """
    needs_human = paths.needs_human
    needs_human.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"## {today_iso()} — knowledge actor dead-lettered {len(result.dead)} message(s)",
        "",
        "These did not apply and will not retry. Files are in",
        "`state/knowledge-mailbox/dead-letter/` with a `.err.md` sibling each.",
        "",
    ]
    for name, reason in result.dead:
        lines.append(f"- `{name}` — {reason}")
    lines.append("")
    existing = needs_human.read_text(encoding="utf-8") if needs_human.is_file() else ""
    okf.write_text_atomic(needs_human, "\n".join(lines) + "\n" + existing)


# ==========================================================================
# Drift
# ==========================================================================


def scan_drift(paths: Paths, *, enqueue: bool = False) -> list[dict]:
    """Compare each concept's recorded digests against reality.

    One message per CONCEPT, batching all its findings — a refactor touching six
    sources must not produce six messages. The idempotency key folds in the
    observed digests, so an unchanged drift never re-enqueues but a further
    change does. Nagging without spam.
    """
    pending_keys = _keys_in_flight(paths)
    seen = read_keys(paths) | pending_keys
    reports: list[dict] = []

    for path in okf.iter_concepts(paths.bundle):
        try:
            frontmatter, _ = okf.parse_frontmatter(path.read_text(encoding="utf-8"))
        except okf.FrontmatterError:
            continue
        target = okf.bundle_path_of(paths.bundle, path)
        findings: list[dict] = []

        recorded = {
            str(e.get("resource")): e
            for e in _as_list(frontmatter.get("x_source_digests"))
            if isinstance(e, dict)
        }
        for resource, entry in sorted(recorded.items()):
            if entry.get("kind") != "repo-file":
                continue
            observed = okf.blob_digest(paths.root / resource)
            if observed is None:
                kind = "source-missing"
            elif observed != entry.get("digest"):
                kind = "source-changed"
            else:
                continue
            findings.append(
                {
                    "source_id": entry.get("id"),
                    "resource": resource,
                    "recorded_digest": entry.get("digest"),
                    "observed_digest": observed or "missing",
                    "recorded_at": entry.get("captured_at"),
                }
            )

        cited = {
            str(e.get("resource"))
            for e in _as_list(frontmatter.get("sources"))
            if isinstance(e, dict) and "://" not in str(e.get("resource", ""))
        }
        unrecorded = sorted(cited - set(recorded))
        for resource in unrecorded:
            findings.append(
                {
                    "source_id": slugify(resource),
                    "resource": resource,
                    "recorded_digest": "none",
                    "observed_digest": okf.blob_digest(paths.root / resource) or "missing",
                }
            )

        stale_after = frontmatter.get("stale_after")
        expired = bool(stale_after and str(stale_after) < today_iso())

        if not findings and not expired:
            continue

        kind = "expired" if expired and not findings else (
            "unrecorded" if unrecorded and len(unrecorded) == len(findings) else "source-changed"
        )
        key = derive_key(
            "stale",
            target,
            ",".join(sorted(f"{f['source_id']}:{f['observed_digest']}" for f in findings)),
            "expired" if expired else "",
        )
        report = {
            "target": target,
            "kind": kind,
            "findings": findings,
            "idempotency_key": key,
            "trust": okf.trust_tier(frontmatter),
            "enqueued": False,
        }
        if key not in seen:
            if enqueue:
                enqueue_message(
                    paths,
                    op="stale",
                    target=target,
                    by="process:knowledge-drift-scan/0.1",
                    reason=_drift_reason(kind, findings),
                    payload={"kind": kind, "findings": findings},
                    idempotency_key=key,
                    body=(
                        "Detected by `knowledge_actor.py scan-drift`. The bytes of the "
                        "source moved; whether the CLAIM changed is a judgement call and "
                        "is NOT made here."
                    ),
                )
                report["enqueued"] = True
            reports.append(report)
    return reports


def _drift_reason(kind: str, findings: list[dict]) -> str:
    if not findings:
        return f"{kind}: concept passed its stale_after date"
    first = findings[0]["resource"]
    extra = f" (+{len(findings) - 1} more)" if len(findings) > 1 else ""
    return f"{kind}: {first}{extra} changed since this concept was written"[:200]


def _keys_in_flight(paths: Paths) -> set[str]:
    keys: set[str] = set()
    for directory in (paths.inbox, paths.processing):
        for path in directory.glob("*.msg.md"):
            try:
                keys.add(read_message(path).key)
            except Exception:  # noqa: BLE001
                continue
    return keys


# ==========================================================================
# CLI
# ==========================================================================


def cmd_enqueue(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    payload = json.loads(args.payload_json) if args.payload_json else {}
    if args.payload_file:
        payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    body = ""
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif args.body_stdin:
        body = sys.stdin.read()
    try:
        path = enqueue_message(
            paths,
            op=args.op,
            reason=args.reason,
            by=args.by,
            target=args.target,
            payload=payload,
            body=body,
            idempotency_key=args.idempotency_key,
            priority=args.priority,
        )
    except PermanentError as exc:
        sys.stderr.write(f"refusing to enqueue: {exc}\n")
        return EXIT_INVALID
    print(path if args.print_path else f"enqueued {path.name}")
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    targets = [Path(p) for p in args.message] if args.message else sorted(paths.inbox.glob("*.msg.md"))
    failures: list[tuple[str, str]] = []
    for path in targets:
        try:
            validate_message(read_message(path))
        except PermanentError as exc:
            failures.append((path.name, str(exc)))
    if args.json:
        print(json.dumps({"checked": len(targets), "failures": [
            {"message": n, "error": e} for n, e in failures]}, indent=2))
    else:
        for name, error in failures:
            print(f"INVALID {name}: {error}")
        print(f"{len(targets) - len(failures)}/{len(targets)} messages valid")
    return EXIT_INVALID if failures else EXIT_OK


def cmd_verify_bundle(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    errors = okf.conformance_errors(
        paths.bundle, require_sources=args.require_sources, root=paths.root
    )
    if args.json:
        print(json.dumps({"conformant": not errors, "errors": errors}, indent=2))
    else:
        for error in errors:
            print(f"NONCONFORMANT {error}")
        print(f"{'FAIL' if errors else 'PASS'}: {len(errors)} conformance error(s)")
    return EXIT_NONCONFORMANT if errors else EXIT_OK


def cmd_drain(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    try:
        with Lock(paths, ttl=args.lock_ttl):
            os.environ["KNOWLEDGE_ACTOR_RUNNING"] = "1"
            result = drain(
                paths,
                max_messages=args.max,
                max_attempts=args.max_attempts,
                dry_run=args.dry_run,
            )
    except TransientError as exc:
        sys.stderr.write(f"knowledge-actor: {exc}\n")
        return EXIT_TRANSIENT

    summary = {
        "applied": len(result.applied),
        "no_ops": len(result.noops),
        "duplicates": len(result.duplicates),
        "dead_letters": len(result.dead),
        "deferred": len(result.deferred),
    }
    if args.json:
        print(json.dumps({**summary, "details": {
            "applied": result.applied,
            "dead": [{"message": n, "error": e} for n, e in result.dead],
            "deferred": result.deferred,
        }}, indent=2))
    else:
        for entry in result.applied:
            print(f"applied  {entry['op']:<10} {entry['target']}")
        for name, error in result.dead:
            print(f"DEAD     {name}: {error}")
        for note in result.deferred:
            print(f"deferred {note}")
        print(", ".join(f"{k}={v}" for k, v in summary.items()))
    return EXIT_DEAD_LETTERS if result.dead else EXIT_OK


def cmd_reindex(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    os.environ["KNOWLEDGE_ACTOR_RUNNING"] = "1"
    changed = regenerate_indexes(paths, check_only=args.check)
    if not args.check:
        rebuild_source_index(paths)
    if args.json:
        print(json.dumps({"changed": changed}, indent=2))
    else:
        for path in changed:
            print(f"{'WOULD CHANGE' if args.check else 'rewrote'} {path}")
        print(f"{len(changed)} index file(s) {'would change' if args.check else 'rewritten'}")
    return EXIT_WOULD_CHANGE if (args.check and changed) else EXIT_OK


def cmd_scan_drift(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    reports = scan_drift(paths, enqueue=args.enqueue)
    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for report in reports:
            flag = "enqueued" if report["enqueued"] else "detected"
            print(f"{flag} {report['kind']:<16} {report['target']} ({len(report['findings'])} finding(s))")
        print(f"{len(reports)} concept(s) drifted")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    inbox = sorted(paths.inbox.glob("*.msg.md"))
    oldest_age_hours = None
    if inbox:
        oldest = min(p.stat().st_mtime for p in inbox)
        oldest_age_hours = round((utc_now().timestamp() - oldest) / 3600, 1)
    state = {
        "bundle": str(paths.bundle),
        "concepts": len(list(okf.iter_concepts(paths.bundle))),
        "queue_depth": len(inbox),
        "in_processing": len(list(paths.processing.glob("*.msg.md"))),
        "dead_letters": len(list(paths.dead_letter.glob("*.msg.md"))),
        "pending_delete": len(list(paths.pending_delete.glob("*"))),
        "oldest_message_age_hours": oldest_age_hours,
        "lock_held": paths.lock.is_dir(),
        "applied_total": len(ledger_rows(paths)),
    }
    if args.json:
        print(json.dumps(state, indent=2))
    else:
        for key, value in state.items():
            print(f"{key:<26} {value}")
        if state["queue_depth"] and (oldest_age_hours or 0) > 48:
            print("\nWARNING: oldest message is over 48h old. An undrained mailbox is just slower rot.")
    return EXIT_OK


def cmd_confirm_delete(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    staged = sorted(paths.pending_delete.glob("*"))
    if not staged:
        print("nothing staged for deletion")
        return EXIT_OK
    for path in staged:
        if args.target not in ("all", path.name):
            continue
        path.unlink()
        print(f"deleted {path.name}")
    return EXIT_OK


def cmd_log_rebuild(args: argparse.Namespace) -> int:
    """Repair log.md from ledger.md — for merge damage, not the normal path."""
    paths = resolve_paths(args)
    log_path = paths.bundle / "log.md"
    if log_path.is_file():
        log_path.unlink()
    entries = [
        {
            "msg_id": row["msg_id"],
            "op": row["op"],
            "target": row["target"],
            "reason": "(rebuilt from ledger)",
            "by": ACTOR_ID,
            "applied_at": row["applied_at"],
        }
        for row in ledger_rows(paths)
        if row.get("result") == "applied"
    ]
    append_log_entries(paths, entries)
    print(f"rebuilt log.md from {len(entries)} ledger row(s)")
    return EXIT_OK


def cmd_gc(args: argparse.Namespace) -> int:
    paths = resolve_paths(args)
    cutoff = (utc_now() - _dt.timedelta(days=args.keep_days)).strftime("%Y-%m-%d")
    removed = 0
    for day_dir in sorted(paths.processed.iterdir()) if paths.processed.is_dir() else []:
        if day_dir.is_dir() and day_dir.name < cutoff:
            removed += len(list(day_dir.glob("*")))
            shutil.rmtree(day_dir)
    # keys.txt is deliberately untouched: dropping a key lets an old drift re-fire.
    print(f"removed {removed} archived message(s) older than {cutoff}; keys.txt kept whole")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge_actor.py",
        description="Mailbox actor for the OKF knowledge bundle. Never calls a model.",
    )
    parser.add_argument("--bundle", help=f"bundle root (default: <repo>/{DEFAULT_BUNDLE})")
    parser.add_argument("--mailbox", help=f"mailbox root (default: <repo>/{DEFAULT_MAILBOX})")
    sub = parser.add_subparsers(dest="command", required=True)

    enqueue = sub.add_parser("enqueue", help="write a message into the inbox")
    enqueue.add_argument("--op", required=True, choices=VALID_OPS)
    enqueue.add_argument("--reason", required=True, help="one line, <=200 chars, lands in log.md")
    enqueue.add_argument("--by", required=True, help="human:<id> | process:<id> | <producer>/<version>")
    enqueue.add_argument("--target", help="bundle-absolute concept path, e.g. /loop/foo.md")
    enqueue.add_argument("--payload-json")
    enqueue.add_argument("--payload-file")
    enqueue.add_argument("--body-file")
    enqueue.add_argument("--body-stdin", action="store_true")
    enqueue.add_argument("--idempotency-key")
    enqueue.add_argument("--priority", type=int, default=50)
    enqueue.add_argument("--print-path", action="store_true")
    enqueue.set_defaults(func=cmd_enqueue)

    validate = sub.add_parser("validate", help="check messages without applying them")
    validate.add_argument("--message", action="append", default=[])
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)

    verify = sub.add_parser("verify-bundle", help="OKF conformance check")
    verify.add_argument("--require-sources", action="store_true",
                        help="also fail any `stable` concept that names no evidence")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify_bundle)

    drain_cmd = sub.add_parser("drain", help="apply the queue")
    drain_cmd.add_argument("--max", type=int)
    drain_cmd.add_argument("--lock-ttl", type=int, default=DEFAULT_LOCK_TTL)
    drain_cmd.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    drain_cmd.add_argument("--dry-run", action="store_true")
    drain_cmd.add_argument("--json", action="store_true")
    drain_cmd.set_defaults(func=cmd_drain)

    reindex = sub.add_parser("reindex", help="regenerate index.md files")
    reindex.add_argument("--check", action="store_true", help="exit 5 if bytes would change")
    reindex.add_argument("--json", action="store_true")
    reindex.set_defaults(func=cmd_reindex)

    drift = sub.add_parser("scan-drift", help="find concepts whose sources moved")
    drift.add_argument("--enqueue", action="store_true")
    drift.add_argument("--json", action="store_true")
    drift.set_defaults(func=cmd_scan_drift)

    status = sub.add_parser("status", help="queue depth, dead letters, lock holder")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    confirm = sub.add_parser("confirm-delete", help="the ONLY path that removes concept bytes")
    confirm.add_argument("target", help="staged filename, or 'all'")
    confirm.set_defaults(func=cmd_confirm_delete)

    log_cmd = sub.add_parser("log", help="repair log.md from ledger.md")
    log_cmd.add_argument("--rebuild", action="store_true", required=True)
    log_cmd.set_defaults(func=cmd_log_rebuild)

    gc_cmd = sub.add_parser("gc", help="archive old processed messages")
    gc_cmd.add_argument("--keep-days", type=int, default=90)
    gc_cmd.set_defaults(func=cmd_gc)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except TransientError as exc:
        sys.stderr.write(f"knowledge-actor: transient: {exc}\n")
        return EXIT_TRANSIENT
    except PermanentError as exc:
        sys.stderr.write(f"knowledge-actor: invalid: {exc}\n")
        return EXIT_INVALID
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
