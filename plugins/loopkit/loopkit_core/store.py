#!/usr/bin/env python3
"""store.py — the Store contract and its three reference implementations.

Runtime plan §M2 (`docs/research/runtime-plan.md`); spec criteria 26-30 of
`specs/loopkit-runtime.md`.

THE CONTRACT

    put_if_absent(key, value) -> bool      conditional, atomic; True == wrote
    get(key)                  -> bytes|None
    append(key, line)         -> None      atomic per record
    list(prefix="")           -> [str]     lexicographic, never truncated

`put_if_absent` is the load-bearing operation. It is what stops two runners
corrupting one journal: whoever writes first wins, and the loser is TOLD it
lost (`False`) rather than quietly overwriting. Everything else in the
contract is convenience; this one is the safety property.

ONE DATA MODEL ACROSS THREE BACKENDS

A key holds an optional base value (written once by `put_if_absent`) followed
by zero or more appended records. `get` returns them concatenated, in write
order, which makes the three stores substitutable: the same journal read back
through `FsStore`, `SqliteStore` or `S3Store` yields the same bytes.

  * `FsStore`     -- one file per key. `put_if_absent` writes a temp file,
                    fsyncs it, then `os.link()`s it into place: link fails with
                    EEXIST if the key exists, so existence AND content land
                    atomically and a loser never sees a half-written value.
                    `append` is a single `os.write` on an `O_APPEND` fd.
  * `SqliteStore` -- one file, stdlib `sqlite3`. Every mutation is wrapped in
                    `BEGIN IMMEDIATE`, which takes the write lock up front
                    instead of discovering the conflict at commit time. Opening
                    the store claims a `runner_lock` row: a SECOND runner is
                    REFUSED with `StoreLocked` rather than allowed to
                    interleave, because SQLite over a network mount is not safe
                    for concurrent writers and degrading quietly is worse than
                    failing loudly.
  * `S3Store`     -- S3-compatible object storage over `urllib` with SigV4
                    signed by hand. No SDK. `put_if_absent` is a PUT carrying
                    `If-None-Match: *`; a 412 means somebody else got there
                    first.

WHY S3 `append` IS NOT A READ-MODIFY-WRITE

Object storage has no append. The obvious implementation -- GET, concatenate,
PUT with `If-Match` -- needs a second conditional primitive, retries under
contention, and re-uploads the whole journal on every line. Instead each
appended record is its OWN object at `<key>/.part/<zero-padded seq>`, written
with `If-None-Match: *`. A record can therefore never be torn or lost: two
appenders racing for one sequence number resolve by 412 and the loser
advances. `get` concatenates the base object and the parts in key order;
`list` folds part objects back onto their parent key and never shows them.
The only primitive this needs is the conditional write the plan already
requires. Keys containing `/.part/` are rejected by every store so the three
stay interchangeable.

STALE SQLITE LOCKS -- RECOVERED AUTOMATICALLY, WITHOUT A TIMEOUT

A `runner_lock` ROW cannot tell "held" from "abandoned": it is bytes that
outlive their writer, so a SIGKILLed runner used to leave the store locked
forever and criterion 13's resume needed a human with `take_over=True`.
`close()` could not have fixed that -- SIGKILL never reaches it.

So the row is no longer the authority. The authority is an exclusive
`flock` on a sidecar file `<path>.runner-lock`, and the kernel releases it
when the holding process dies, however it dies. The row stays, because it
carries the host and pid a human needs to read.

Takeover is automatic if and only if BOTH hold:

  1. the kernel GRANTED the exclusive flock -- positive proof that no live
     process holds this store; and
  2. the stale row names THIS host -- so the flock was judged by the same
     kernel that owned the dead runner.

Condition 2 is what keeps this safe on a shared mount, where a local flock
proves nothing about another machine: a row from another host still refuses
and still requires `take_over=True`. No duration is guessed anywhere, which
is the whole point -- a timeout-based steal is exactly the mechanism that
lets two runners each believe they hold one journal.

A LIVE holder is refused absolutely, and `take_over=True` does NOT override
it. That refusal is now taken BEFORE the `runner_lock` row is consulted,
because the row used to be able to wave a live holder through: the old test
was `row[0] != self.runner_id`, so a second process configured with the SAME
stable runner id -- or a holder killed between taking the flock and writing
its row -- skipped the refusal and wrote. Reachable the moment anyone sets a
runner id from config instead of taking the default `uuid4`. Pinned by
`--selftest` cases S8 (which SIGKILLs a real holding process) and S8f-S8i.

On a platform with no `fcntl` there is no kernel proof either way, so
behaviour falls back to the old row-only refusal and the message says so.
BE SUSPICIOUS OF THAT PATH: without a flock, two processes sharing one
stable runner id are indistinguishable from one process reopening its own
store, and this module admits them. Untested here -- every platform the
suite runs on has `fcntl`.

SECRETS

No store prints anything, ever. The S3 secret key is used only as HMAC input;
it never reaches a URL, a header other than the derived signature, an
exception message, or a file. Pinned by `--selftest` case S4.

Redaction is NOT a literal substring match. It used to be, and any
repr-escaping defeated that: an access key carrying a trailing newline made
`http.client.putheader` raise `ValueError("Invalid header value %r" % value)`
-- the whole signed `Authorization` header, `Credential=<access key>`
included -- and `%r` rendered the newline as backslash-n, so the secret in
the text was no longer the secret in memory and `replace()` never fired. That
leak survived two reviews because `_scrub` itself had no pin. Redaction now
lives in `loopkit_core/redact.py`, shared with `provider.py` so the two
cannot drift apart again, and it matches the parts of a secret that survive
ANY character-escaping rather than enumerating escapers. `_request`'s
catch-all additionally composes NO untrusted exception text, so the redactor
is a backstop and not the only wall. Pinned by S9, which drives every
reachable error path with a sentinel containing a newline, a tab, both
quote characters and non-ASCII characters, and by S9f-S9h, which test the
redactor and the catch-all directly.

THE ENDPOINT MUST BE http OR https

`urllib.request.build_opener` keeps the default handler set, `FileHandler`
and `FTPHandler` included, so a `file://` endpoint would make every request
read a local file and hand its bytes back as object content -- with no error
and no sign that no network call happened. An S3-compatible endpoint is
http(s) by definition, so the scheme is checked in `__init__` and anything
else is refused. Pinned by S10.

VERIFICATION STATUS (be suspicious of anything not listed here)

  * FsStore      -- pinned with real concurrent PROCESSES.
  * SqliteStore  -- pinned with real concurrent threads, plus a second process
                    proving the runner lock refuses rather than interleaves,
                    plus a SIGKILLed holder proving the store is recoverable.
  * S3Store      -- pinned against a live MinIO only when `--with-s3` is passed
                    and `LOOPKIT_S3_*` is set. Without that run it is
                    WRITTEN BUT UNVERIFIED; say so rather than implying cover.

Run the pins:
    python3 -m loopkit_core.store --selftest
    python3 -m loopkit_core.store --selftest --with-s3     (needs LOOPKIT_S3_*)

Python 3.9+, standard library only.
"""
from __future__ import annotations

import errno
import hashlib
import hmac
import io
import json
import os
import sqlite3
import sys
import threading
import time as _time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# The redactor lives in its own module because provider.py needs the SAME one.
# Two copies of a redactor drift, and the copy that drifts is the one nobody
# re-reads -- which is how `S3Store._scrub` ended up two reviews behind
# `provider.py`'s defence against exactly the same escaping.
#
# This file is run BOTH ways: `python3 -m loopkit_core.store --selftest` (the
# suite) and `python3 plugins/loopkit/loopkit_core/store.py --selftest` (the
# gate people type). Under the second, sys.path[0] is the package DIRECTORY,
# so `loopkit_core` is not importable until its parent is on the path.
if __package__ in (None, ""):  # pragma: no cover - exercised by the path gate
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from loopkit_core.redact import Redactor, url_secrets  # noqa: E402

__all__ = [
    "Store",
    "StoreError",
    "StoreKeyError",
    "StoreLocked",
    "StoreTransportError",
    "FsStore",
    "SqliteStore",
    "S3Store",
]

PART_MARK = "/.part/"
PART_WIDTH = 12
MAX_ATOMIC_LINE = 4096  # one O_APPEND write, kept below the pipe/page size


class StoreError(Exception):
    """Base for every error this module raises. Never carries a secret."""


class StoreKeyError(StoreError):
    """The key is not addressable in this contract."""


class StoreLocked(StoreError):
    """A second runner tried to open a Store already held by another."""


class StoreTransportError(StoreError):
    """A remote store could not be reached, or answered unusably."""


try:  # pragma: no cover
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class Store(Protocol):
        """put_if_absent / get / append / list, with a conditional write."""

        def put_if_absent(self, key: str, value: Any) -> bool: ...

        def get(self, key: str) -> Optional[bytes]: ...

        def append(self, key: str, line: Any) -> None: ...

        def list(self, prefix: str = "") -> List[str]: ...

except ImportError:  # pragma: no cover
    class Store:  # type: ignore[no-redef]
        """Fallback stand-in when typing.Protocol is unavailable."""


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------
def _as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    raise StoreError("store values must be str or bytes, got %s" % type(value).__name__)


def _as_line(line: Any) -> bytes:
    raw = _as_bytes(line)
    if b"\n" in raw[:-1]:
        raise StoreError("an appended record must not contain a newline")
    if not raw.endswith(b"\n"):
        raw += b"\n"
    return raw


def check_key(key: str) -> str:
    """Reject anything that would escape the store or collide with part objects."""
    if not isinstance(key, str) or not key:
        raise StoreKeyError("key must be a non-empty string")
    if key.startswith("/") or key.endswith("/"):
        raise StoreKeyError("key must not start or end with '/': %r" % key)
    if "\\" in key or "\x00" in key:
        raise StoreKeyError("key must not contain a backslash or NUL: %r" % key)
    if PART_MARK in key or key.endswith("/.part"):
        raise StoreKeyError("'%s' is reserved for appended records: %r" % (PART_MARK, key))
    for segment in key.split("/"):
        if segment in ("", ".", ".."):
            raise StoreKeyError("key has an empty or relative segment: %r" % key)
    return key


def _part_key(key: str, seq: int) -> str:
    return "%s%s%0*d" % (key, PART_MARK, PART_WIDTH, seq)


def _hostname() -> str:
    try:
        import socket

        return socket.gethostname()
    except Exception:  # pragma: no cover
        return "unknown"


# --------------------------------------------------------------------------
# FsStore
# --------------------------------------------------------------------------
class FsStore:
    """One file per key under `root`. Conditional write via `os.link` (EEXIST)."""

    name = "fs"

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.root, *check_key(key).split("/"))

    def put_if_absent(self, key: str, value: Any) -> bool:
        raw = _as_bytes(value)
        path = self._path(key)
        parent = os.path.dirname(path)
        os.makedirs(parent, exist_ok=True)
        tmp = os.path.join(parent, ".tmp-%s" % uuid.uuid4().hex)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # link() IS the conditional write: it fails with EEXIST rather
                # than clobbering, and it publishes the whole value at once, so
                # a concurrent reader never sees a partial one.
                os.link(tmp, path)
                return True
            except FileExistsError:
                return False
            except OSError as exc:
                if exc.errno not in (errno.EPERM, errno.EXDEV, errno.ENOSYS, errno.EOPNOTSUPP):
                    raise
                # A filesystem with no hard links: O_EXCL still gives an atomic
                # claim on the name, at the cost of a brief window in which a
                # reader can see an empty file.
                try:
                    claim = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    return False
                with os.fdopen(claim, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                return True
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def get(self, key: str) -> Optional[bytes]:
        try:
            with open(self._path(key), "rb") as handle:
                return handle.read()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return None

    def append(self, key: str, line: Any) -> None:
        raw = _as_line(line)
        if len(raw) > MAX_ATOMIC_LINE:
            raise StoreError(
                "record of %d bytes exceeds the %d-byte atomic-append limit"
                % (len(raw), MAX_ATOMIC_LINE)
            )
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            written = os.write(fd, raw)
            if written != len(raw):
                raise StoreError("short append: %d of %d bytes" % (written, len(raw)))
        finally:
            os.close(fd)

    def list(self, prefix: str = "") -> List[str]:
        keys: List[str] = []
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                if name.startswith(".tmp-"):
                    continue
                full = os.path.join(dirpath, name)
                key = os.path.relpath(full, self.root).replace(os.sep, "/")
                if key.startswith(prefix):
                    keys.append(key)
        return sorted(keys)

    def close(self) -> None:
        return None

    def __enter__(self) -> "FsStore":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------
# SqliteStore
# --------------------------------------------------------------------------
_LOCK_ADVICE = {
    "held": (
        "A LIVE process holds the kernel lock on the sidecar file, so this is "
        "not a stale row: find that process before doing anything else. "
        "take_over=True does NOT override a live holder."
    ),
    "other-host": (
        "The kernel granted the sidecar lock here, but the stale row names a "
        "DIFFERENT host -- so this file is on a shared mount, where a local "
        "flock proves nothing about the other machine. Confirm that runner is "
        "dead on ITS host, then pass take_over=True."
    ),
    "unsupported": (
        "This platform has no fcntl, so automatic recovery of a stale lock is "
        "unavailable. Confirm the named runner is dead, then pass "
        "take_over=True."
    ),
}

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS records ("
    " key TEXT NOT NULL, ord INTEGER NOT NULL, value BLOB NOT NULL,"
    " PRIMARY KEY (key, ord))",
    "CREATE TABLE IF NOT EXISTS runner_lock ("
    " id INTEGER PRIMARY KEY CHECK (id = 1), runner_id TEXT NOT NULL,"
    " host TEXT, pid INTEGER, acquired_at TEXT)",
)

_RELEASE_LOCK = "DELETE FROM runner_lock WHERE id = 1 AND runner_id = ?"


class SqliteStore:
    """One SQLite file. Single runner by construction, not by convention."""

    name = "sqlite"

    def __init__(
        self,
        path: str,
        runner_id: Optional[str] = None,
        take_over: bool = False,
        timeout: float = 5.0,
    ) -> None:
        self.path = os.path.abspath(path)
        self.runner_id = runner_id or uuid.uuid4().hex
        self._guard = threading.Lock()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path, timeout=timeout, isolation_level=None, check_same_thread=False
        )
        self._conn.execute("PRAGMA busy_timeout = %d" % int(timeout * 1000))
        self._conn.execute("PRAGMA synchronous = FULL")
        for statement in _SCHEMA:
            self._conn.execute(statement)
        self._closed = False
        self._lock_path = self.path + ".runner-lock"
        self._lock_fd: Optional[int] = None
        self._took_over = False
        self._claim_runner_lock(take_over)

    # -- runner lock --------------------------------------------------------
    def _acquire_flock(self) -> str:
        """Take the kernel's exclusive advisory lock on the sidecar file.

        Returns "held" (somebody LIVE has it), "granted", or "unsupported".

        This is what makes a SIGKILLed holder recoverable without a human and
        WITHOUT inventing a timeout. The kernel drops a `flock` when the
        holding open-file-description goes away, and it does that however the
        process died -- SIGKILL, power loss on reboot, anything. So a granted
        lock is positive proof that no live process holds this store, which a
        `runner_lock` ROW can never be: a row is just bytes that outlive their
        writer. No duration is guessed anywhere, which is the point -- a
        timeout-based steal is exactly the mechanism that lets two runners each
        believe they hold one journal.
        """
        try:
            import fcntl
        except ImportError:  # pragma: no cover - not POSIX
            return "unsupported"
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return "held"
        self._lock_fd = fd
        return "granted"

    def _claim_runner_lock(self, take_over: bool) -> None:
        flock_state = self._acquire_flock()
        with self._guard:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT runner_id, host, pid, acquired_at FROM runner_lock WHERE id = 1"
                ).fetchone()
                stale_but_recoverable = (
                    flock_state == "granted"
                    and bool(row)
                    and row[1] == _hostname()
                )
                if flock_state == "held":
                    # A LIVE process holds this store. Refused ABSOLUTELY, and
                    # BEFORE the row is consulted, because the row cannot make
                    # it safe. The row-first test below asks
                    # `row[0] != self.runner_id`, which admitted two cases it
                    # should never have (2026-09-07, third review, finding 4):
                    #
                    #   * a row naming THE SAME runner_id -- so two processes
                    #     configured with one STABLE runner id (anything but
                    #     the default uuid4, i.e. the moment anyone sets it
                    #     from config) skipped the refusal entirely and went
                    #     straight to the write, interleaving on one journal;
                    #   * NO row at all -- a holder SIGKILLed between taking
                    #     the flock and inserting its row, admitted for the
                    #     same reason.
                    #
                    # `take_over=True` does not override this either. The
                    # module header has claimed "a LIVE holder is refused
                    # absolutely" since this lock was written; now the code
                    # agrees with it. Only a GRANTED flock -- positive kernel
                    # proof that no live process holds the store -- can lead
                    # to a takeover, and that path is unchanged below.
                    self._conn.execute("ROLLBACK")
                    self._conn.close()
                    self._closed = True
                    self._release_flock()
                    raise StoreLocked(
                        "sqlite store is HELD BY A LIVE PROCESS (kernel flock on "
                        "%s is not free); refusing to interleave writes. Row says "
                        "runner=%s host=%s pid=%s since %s. %s"
                        % (
                            self._lock_path,
                            row[0] if row else "<no row>",
                            row[1] if row else "<no row>",
                            row[2] if row else "<no row>",
                            row[3] if row else "<no row>",
                            _LOCK_ADVICE["held"],
                        )
                    )
                if row and row[0] != self.runner_id and not take_over:
                    if flock_state == "granted" and stale_but_recoverable:
                        # AUTOMATIC TAKEOVER, under a stated condition and no
                        # timeout: the kernel granted an exclusive flock (so no
                        # live process holds this store) AND the stale row names
                        # THIS host (so the flock was judged by the same kernel
                        # that owned the dead runner). Both halves are required.
                        # A row from another host means the file is on a shared
                        # mount, where flock proves nothing about the other
                        # machine -- that case still falls through and refuses.
                        self._took_over = True
                    else:
                        self._conn.execute("ROLLBACK")
                        self._conn.close()
                        self._closed = True
                        self._release_flock()
                        raise StoreLocked(
                            "sqlite store is already held by runner %s (host=%s pid=%s "
                            "since %s); refusing to interleave writes. %s"
                            % (
                                row[0],
                                row[1],
                                row[2],
                                row[3],
                                # "held" can no longer reach here: a live
                                # holder is refused above, before the row is
                                # read. What is left is "granted" with a row
                                # from another host, and "unsupported".
                                _LOCK_ADVICE.get(flock_state, _LOCK_ADVICE["other-host"]),
                            )
                        )
                self._conn.execute(
                    "INSERT OR REPLACE INTO runner_lock"
                    " (id, runner_id, host, pid, acquired_at) VALUES (1, ?, ?, ?, ?)",
                    (
                        self.runner_id,
                        _hostname(),
                        os.getpid(),
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ),
                )
                self._conn.execute("COMMIT")
            except StoreLocked:
                raise
            except BaseException:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    # -- contract -----------------------------------------------------------
    def put_if_absent(self, key: str, value: Any) -> bool:
        check_key(key)
        raw = _as_bytes(value)
        with self._guard:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                present = self._conn.execute(
                    "SELECT 1 FROM records WHERE key = ? LIMIT 1", (key,)
                ).fetchone()
                if present:
                    self._conn.execute("COMMIT")
                    return False
                self._conn.execute(
                    "INSERT INTO records (key, ord, value) VALUES (?, 0, ?)",
                    (key, sqlite3.Binary(raw)),
                )
                self._conn.execute("COMMIT")
                return True
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def get(self, key: str) -> Optional[bytes]:
        check_key(key)
        with self._guard:
            rows = self._conn.execute(
                "SELECT value FROM records WHERE key = ? ORDER BY ord", (key,)
            ).fetchall()
        if not rows:
            return None
        return b"".join(bytes(row[0]) for row in rows)

    def append(self, key: str, line: Any) -> None:
        check_key(key)
        raw = _as_line(line)
        with self._guard:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(ord), -1) FROM records WHERE key = ?", (key,)
                ).fetchone()
                self._conn.execute(
                    "INSERT INTO records (key, ord, value) VALUES (?, ?, ?)",
                    (key, int(row[0]) + 1, sqlite3.Binary(raw)),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def list(self, prefix: str = "") -> List[str]:
        with self._guard:
            if prefix:
                rows = self._conn.execute(
                    "SELECT DISTINCT key FROM records WHERE substr(key, 1, ?) = ? ORDER BY key",
                    (len(prefix), prefix),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT DISTINCT key FROM records ORDER BY key"
                ).fetchall()
        return [row[0] for row in rows]

    def _release_flock(self) -> None:
        fd = self._lock_fd
        self._lock_fd = None
        if fd is None:
            return
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        except (ImportError, OSError):  # pragma: no cover
            pass
        try:
            os.close(fd)
        except OSError:  # pragma: no cover
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            with self._guard:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(_RELEASE_LOCK, (self.runner_id,))
                self._conn.execute("COMMIT")
        except sqlite3.Error:
            pass
        finally:
            self._conn.close()
            self._release_flock()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------
# S3Store
# --------------------------------------------------------------------------
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_UNSIGNED_PAYLOAD_FALLBACK = "UNSIGNED-PAYLOAD"


def _uri_encode(value: str, encode_slash: bool = True) -> str:
    safe = "-_.~" if encode_slash else "-_.~/"
    return urllib.parse.quote(value, safe=safe)


class S3Store:
    """S3-compatible object storage over urllib. SigV4 by hand, no SDK.

    Endpoint, bucket, credentials and region come from parameters or the
    environment (`LOOPKIT_S3_ENDPOINT`/`S3_ENDPOINT_URL`, `LOOPKIT_S3_BUCKET`,
    `LOOPKIT_S3_ACCESS_KEY`/`AWS_ACCESS_KEY_ID`,
    `LOOPKIT_S3_SECRET_KEY`/`AWS_SECRET_ACCESS_KEY`,
    `LOOPKIT_S3_REGION`/`AWS_REGION`). Never hardcoded, never logged.

    Path-style addressing (`{endpoint}/{bucket}/{key}`) so it works against
    MinIO and any other compatible server without DNS games.
    """

    name = "s3"

    def __init__(
        self,
        bucket: Optional[str] = None,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: Optional[str] = None,
        prefix: str = "",
        timeout: float = 30.0,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        env = os.environ if env is None else env
        self.bucket = (bucket or env.get("LOOPKIT_S3_BUCKET") or "").strip()
        endpoint = (
            endpoint or env.get("LOOPKIT_S3_ENDPOINT") or env.get("S3_ENDPOINT_URL") or ""
        ).strip()
        self._access_key = (
            access_key or env.get("LOOPKIT_S3_ACCESS_KEY") or env.get("AWS_ACCESS_KEY_ID") or ""
        )
        self._secret_key = (
            secret_key
            or env.get("LOOPKIT_S3_SECRET_KEY")
            or env.get("AWS_SECRET_ACCESS_KEY")
            or ""
        )
        self.region = (
            region or env.get("LOOPKIT_S3_REGION") or env.get("AWS_REGION") or "us-east-1"
        ).strip()
        if not self.bucket:
            raise StoreError("no bucket: pass bucket= or set LOOPKIT_S3_BUCKET")
        if not endpoint:
            raise StoreError("no endpoint: pass endpoint= or set LOOPKIT_S3_ENDPOINT")
        if not self._access_key or not self._secret_key:
            raise StoreError(
                "no credentials: pass access_key=/secret_key= or set "
                "LOOPKIT_S3_ACCESS_KEY / LOOPKIT_S3_SECRET_KEY"
            )
        # `parts.netloc` INCLUDES `user:password@`. Assigning it to `self.host`
        # (what this did before 2026-09-07) put the password into every error
        # message, into the outgoing `Host:` header, and into the SigV4
        # canonical request -- i.e. it was SIGNED onto the wire. Host is built
        # from `hostname` and `port` only, and userinfo is never reassembled
        # into a host string anywhere in this class.
        # Registering the WHOLE endpoint here would be wrong -- the host is
        # not a credential and redacting it makes every error unreadable. Only
        # userinfo goes in, via a helper that never raises, so the registration
        # cannot be skipped by a malformed URL on its way to a refusal.
        #
        # The redactor is built FIRST, before anything below can raise, and it
        # is escape-robust (see loopkit_core/redact.py): a literal-substring
        # scrubber is defeated by any `%r`, which is how the `Authorization`
        # header used to escape through `_request`'s catch-all.
        self._redact = Redactor([self._secret_key, self._access_key])
        for value in url_secrets(endpoint):
            self._redact.add(value)
        try:
            parts = urllib.parse.urlsplit(
                endpoint if "://" in endpoint else "https://" + endpoint
            )
            hostname = parts.hostname or ""
            try:
                port = parts.port
            except ValueError:
                raise StoreError("endpoint has a malformed port") from None
            userinfo = [v for v in (parts.username, parts.password) if v]
        except StoreError:
            raise
        except ValueError:
            raise StoreError("endpoint is not a usable URL") from None
        # Register before any raise below, so even the refusal message is
        # scrubbed if some future edit interpolates the endpoint into it.
        self._url_secrets = list(userinfo)
        for value in userinfo:
            self._redact.add(value)
        if userinfo:
            # S3 authenticates with SigV4, never with URL userinfo. Silently
            # dropping a credential the operator supplied would let them
            # believe it was used; signing it into the Host header is worse.
            # Refuse, and name no value.
            raise StoreError(
                "endpoint URL carries userinfo (user:password@host): S3 "
                "authenticates with SigV4, not with URL credentials. Remove "
                "them from LOOPKIT_S3_ENDPOINT and pass access_key=/secret_key= "
                "or set LOOPKIT_S3_ACCESS_KEY / LOOPKIT_S3_SECRET_KEY."
            )
        self.scheme = parts.scheme or "https"
        # REFUSE every scheme but http(s), decided rather than left open
        # (2026-09-07 review, finding 3). `build_opener` keeps urllib's
        # default handler set, which includes `FileHandler` and `FTPHandler`,
        # so a `file://` endpoint would make every "request" read a local file
        # and hand its bytes back as if the object store had returned them --
        # silently, since `_object_path` looks like a plausible path. An
        # S3-compatible endpoint is http(s) by definition, so nothing
        # legitimate is lost by saying so. The scheme is echoed because a
        # scheme is not a credential, and it is scrubbed anyway in case a
        # future edit widens what lands in this message.
        if self.scheme not in ("http", "https"):
            raise StoreError(
                self._redact.scrub(
                    "endpoint scheme %r is not supported: S3Store speaks http and "
                    "https only. urllib would otherwise serve file:// and ftp:// "
                    "from its default handlers and return local bytes as object "
                    "content." % self.scheme
                )
            )
        if not hostname:
            raise StoreError("endpoint has no host")
        self.host = "%s:%d" % (hostname, port) if port else hostname
        self.prefix = prefix.strip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._next_seq: Dict[str, int] = {}

    # -- signing ------------------------------------------------------------
    def _object_path(self, key: str) -> str:
        full = "%s/%s" % (self.prefix, key) if self.prefix else key
        return "/" + _uri_encode(self.bucket) + "/" + _uri_encode(full, encode_slash=False)

    def _signing_key(self, datestamp: str) -> bytes:
        def sign(key: bytes, message: str) -> bytes:
            return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

        step = sign(("AWS4" + self._secret_key).encode("utf-8"), datestamp)
        step = sign(step, self.region)
        step = sign(step, "s3")
        return sign(step, "aws4_request")

    def _request(
        self,
        method: str,
        path: str,
        query: Optional[Sequence[Tuple[str, str]]] = None,
        body: bytes = b"",
        extra_headers: Optional[Mapping[str, str]] = None,
    ) -> Tuple[int, bytes, Mapping[str, str]]:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()

        headers: Dict[str, str] = {
            "host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        for name, value in (extra_headers or {}).items():
            headers[name.lower()] = value

        canonical_query = "&".join(
            "%s=%s" % (_uri_encode(k), _uri_encode(v))
            for k, v in sorted(query or [])
        )
        signed_names = sorted(headers)
        canonical_headers = "".join("%s:%s\n" % (n, headers[n].strip()) for n in signed_names)
        signed_headers = ";".join(signed_names)
        canonical_request = "\n".join(
            [method, path, canonical_query, canonical_headers, signed_headers, payload_hash]
        )
        scope = "%s/%s/s3/aws4_request" % (datestamp, self.region)
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signature = hmac.new(
            self._signing_key(datestamp), string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        # The secret never travels: only this derived signature does.
        headers["authorization"] = (
            "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
            % (self._access_key, scope, signed_headers, signature)
        )

        url = "%s://%s%s" % (self.scheme, self.host, path)
        if canonical_query:
            url += "?" + canonical_query
        request = urllib.request.Request(
            url, data=body if method in ("PUT", "POST") else None, method=method
        )
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read()
            except Exception:  # pragma: no cover
                detail = b""
            return exc.code, detail, dict(exc.headers or {})
        except urllib.error.URLError as exc:
            # `reason` is a socket-layer object (`ConnectionRefusedError`,
            # `gaierror`, `TimeoutError`). It never carries a header, so its
            # text is kept for the diagnostic value -- scrubbed, because
            # "never carries a header" is a claim about today's urllib.
            raise StoreTransportError(
                self._scrub("%s://%s unreachable: %s" % (self.scheme, self.host, exc.reason))
            ) from None
        except Exception as exc:
            # NAME THE TYPE, NEVER THE TEXT. This is the catch-all: by
            # definition it holds the exceptions nobody enumerated, so it is
            # the one branch that must not interpolate an unknown message.
            #
            # The concrete leak (2026-09-07, third review): an access key with
            # a trailing newline -- `AWS_ACCESS_KEY_ID=$(cat keyfile)`, an
            # unquoted `.env` line -- makes `http.client.putheader` raise
            # `ValueError("Invalid header value %r" % value)`, and that `%r`
            # is the ENTIRE Authorization header, `Credential=<access key>`
            # included. `_scrub` could not catch it because `%r` renders the
            # real newline as backslash-n, so the secret in the text was no
            # longer the secret in memory.
            #
            # `_scrub` is escape-robust now and would catch this one. Both
            # defences are kept deliberately: the redactor is the backstop for
            # text this module did not compose, and this branch composing no
            # untrusted text is what makes the backstop's failure survivable.
            # `provider.py`'s ValueError branch has worked this way since the
            # first review; this file was one review behind.
            raise StoreTransportError(
                self._scrub(
                    "%s://%s failed: %s (message withheld: this is the "
                    "catch-all, and an unknown exception's text has already "
                    "carried a signed Authorization header once)"
                    % (self.scheme, self.host, type(exc).__name__)
                )
            ) from None

    def _scrub(self, text: str) -> str:
        """Redact every known credential, INCLUDING escaped renderings of it.

        Delegates to the shared `Redactor` (loopkit_core/redact.py). It used
        to be a literal `if secret in text: replace(...)` loop right here, and
        that is precisely what a `%r` defeats -- pinned by S9f, and by the
        MUT-S3 mutation which neuters this method and requires the suite to
        go red.
        """
        redactor = getattr(self, "_redact", None)
        if redactor is None:  # pragma: no cover - only if __init__ raised early
            return text
        return redactor.scrub(text)

    def _fail(self, what: str, status: int, body: bytes) -> None:
        raise StoreTransportError(
            self._scrub(
                "%s failed with HTTP %d: %s" % (what, status, body[:200].decode("utf-8", "replace"))
            )
        )

    # -- contract -----------------------------------------------------------
    def put_if_absent(self, key: str, value: Any) -> bool:
        check_key(key)
        return self._put_object_if_absent(key, _as_bytes(value))

    def _put_object_if_absent(self, key: str, raw: bytes) -> bool:
        status, body, _headers = self._request(
            "PUT",
            self._object_path(key),
            body=raw,
            extra_headers={"if-none-match": "*", "content-type": "application/octet-stream"},
        )
        if status in (200, 201, 204):
            return True
        if status in (409, 412):
            # 412 Precondition Failed is the conditional write refusing to
            # clobber; MinIO answers 409 for the same case on some versions.
            return False
        self._fail("PUT %s" % key, status, body)
        return False  # unreachable

    def get(self, key: str) -> Optional[bytes]:
        check_key(key)
        chunks: List[bytes] = []
        base = self._get_object(key)
        if base is not None:
            chunks.append(base)
        for part in self._list_raw(key + PART_MARK):
            piece = self._get_object(part, checked=False)
            if piece is not None:
                chunks.append(piece)
        if not chunks:
            return None
        return b"".join(chunks)

    def _get_object(self, key: str, checked: bool = True) -> Optional[bytes]:
        status, body, _headers = self._request("GET", self._object_path(key))
        if status == 200:
            return body
        if status in (403, 404):
            return None
        if checked:
            self._fail("GET %s" % key, status, body)
        return None

    def append(self, key: str, line: Any) -> None:
        check_key(key)
        raw = _as_line(line)
        seq = self._next_seq.get(key)
        if seq is None:
            seq = self._highest_part(key) + 1
        # Each record is its own object, claimed with If-None-Match. Two
        # appenders racing for one sequence number resolve by 412; nothing is
        # ever torn and nothing is ever lost.
        while True:
            if self._put_object_if_absent(_part_key(key, seq), raw):
                self._next_seq[key] = seq + 1
                return
            seq += 1

    def _highest_part(self, key: str) -> int:
        highest = -1
        for part in self._list_raw(key + PART_MARK):
            tail = part.rsplit("/", 1)[-1]
            try:
                highest = max(highest, int(tail))
            except ValueError:
                continue
        return highest

    def list(self, prefix: str = "") -> List[str]:
        seen = set()
        for raw in self._list_raw(prefix):
            key = raw.split(PART_MARK, 1)[0] if PART_MARK in raw else raw
            if key.startswith(prefix):
                seen.add(key)
        return sorted(seen)

    def _list_raw(self, prefix: str) -> List[str]:
        """Every key under `prefix`, following continuation tokens to the end.

        S3 pages at 1000. A store that reads only the first page silently
        truncates, which is pre-mortem row 4's quieter cousin, so this loops.
        """
        full_prefix = "%s/%s" % (self.prefix, prefix) if self.prefix else prefix
        strip = len(self.prefix) + 1 if self.prefix else 0
        keys: List[str] = []
        token: Optional[str] = None
        while True:
            query: List[Tuple[str, str]] = [("list-type", "2"), ("prefix", full_prefix)]
            if token:
                query.append(("continuation-token", token))
            status, body, _headers = self._request(
                "GET", "/" + _uri_encode(self.bucket), query=query
            )
            if status != 200:
                self._fail("LIST %s" % prefix, status, body)
            try:
                root = ElementTree.fromstring(body)
            except ElementTree.ParseError as exc:
                raise StoreTransportError(self._scrub("list returned unparseable XML: %s" % exc))
            for node in root.findall(_S3_NS + "Contents"):
                found = node.findtext(_S3_NS + "Key") or ""
                if found:
                    keys.append(found[strip:])
            truncated = (root.findtext(_S3_NS + "IsTruncated") or "false").lower() == "true"
            token = root.findtext(_S3_NS + "NextContinuationToken")
            if not truncated or not token:
                break
        return sorted(keys)

    def close(self) -> None:
        return None

    def __enter__(self) -> "S3Store":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------
# pins  --  python3 -m loopkit_core.store --selftest [--with-s3]
#
# Every pin below has been shown RED by breaking the mechanism it guards and
# GREEN again after restoring it. A pin that cannot fail is decoration.
# --------------------------------------------------------------------------
_RESULTS: List[str] = []
_SKIPS: List[str] = []
_SECRET_SENTINEL = "PINSECRET-do-not-log-9876543210abcdef"
_ACCESS_SENTINEL = "PINACCESSKEY0001"


def _ok(label: str) -> None:
    _RESULTS.append("PASS %s" % label)
    print("PASS %s" % label)


def _bad(label: str, why: str) -> None:
    _RESULTS.append("FAIL %s -- %s" % (label, why))
    print("FAIL %s -- %s" % (label, why))


def _check(label: str, condition: bool, why: str = "") -> None:
    if condition:
        _ok(label)
    else:
        _bad(label, why or "condition false")


def _package_parent() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _spawn(worker_args: Sequence[Sequence[str]]):
    """Start every worker, then release them together so the race is real."""
    import subprocess

    env = dict(os.environ)
    parent = _package_parent()
    env["PYTHONPATH"] = parent + os.pathsep + env.get("PYTHONPATH", "")
    procs = []
    for args in worker_args:
        procs.append(
            subprocess.Popen(
                [sys.executable, "-m", "loopkit_core.store"] + list(args),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=parent,
                env=env,
                text=True,
            )
        )
    outputs = []
    for proc in procs:
        try:
            proc.stdin.write("go\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        outputs.append((proc.returncode, out.strip(), err.strip()))
    return outputs


def _await_release() -> None:
    try:
        sys.stdin.readline()
    except Exception:  # pragma: no cover
        pass


# -- pin S1: put_if_absent really is conditional ---------------------------
def _pin_conditional_write_fs(tmp: str) -> None:
    root = os.path.join(tmp, "fs-race")
    key = "journal/run-1/claim"
    workers = [["--worker-put-fs", root, key, "writer-%d" % n] for n in range(8)]
    results = _spawn(workers)
    verdicts = [out for _code, out, _err in results]
    wrote = [v for v in verdicts if v.startswith("WROTE")]
    lost = [v for v in verdicts if v.startswith("LOST")]
    _check(
        "S1a FsStore put_if_absent: 8 racing PROCESSES, exactly one wins",
        len(wrote) == 1 and len(lost) == 7,
        "verdicts=%r stderr=%r" % (verdicts, [e for _c, _o, e in results if e]),
    )
    stored = FsStore(root).get(key)
    _check(
        "S1b the loser is told it lost and never overwrote the winner's value",
        stored is not None and stored.decode() == wrote[0].split(" ", 1)[1] if wrote else False,
        "stored=%r wrote=%r" % (stored, wrote),
    )


def _pin_conditional_write_sqlite(tmp: str) -> None:
    path = os.path.join(tmp, "race.sqlite3")
    key = "journal/run-1/claim"
    outcomes: List[bool] = []
    errors: List[str] = []
    barrier = threading.Barrier(8)
    with SqliteStore(path) as store:

        def writer(index: int) -> None:
            barrier.wait()
            try:
                outcomes.append(store.put_if_absent(key, "writer-%d" % index))
            except Exception as exc:  # noqa: BLE001
                errors.append("%s: %s" % (type(exc).__name__, exc))

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        _check(
            "S1c SqliteStore put_if_absent: 8 racing threads, exactly one wins",
            outcomes.count(True) == 1 and outcomes.count(False) == 7 and not errors,
            "outcomes=%r errors=%r" % (outcomes, errors),
        )
        value = store.get(key)
        _check(
            "S1d the stored value is one writer's, whole and unmixed",
            value is not None and value.decode().startswith("writer-"),
            "value=%r" % (value,),
        )


# -- pin S2: a second SQLite runner is refused, not interleaved ------------
def _pin_sqlite_refuses_second_runner(tmp: str) -> None:
    path = os.path.join(tmp, "locked.sqlite3")
    with SqliteStore(path) as held:
        held.put_if_absent("journal/run-1/head", "first runner")
        code, out, err = _spawn([["--worker-open-sqlite", path]])[0]
        _check(
            "S2a a second runner opening the same SQLite store is REFUSED",
            out.startswith("REFUSED StoreLocked"),
            "exit=%s out=%r err=%r" % (code, out, err[-200:]),
        )
        _check(
            "S2b the refusal names the holding runner so a human can act",
            "pid=" in out and "runner" in out,
            "out=%r" % out,
        )
        _check(
            "S2c the refused runner wrote nothing",
            held.get("journal/run-1/head") == b"first runner",
            "value=%r" % (held.get("journal/run-1/head"),),
        )
    # Once the holder closes, the next runner may open it.
    code, out, err = _spawn([["--worker-open-sqlite", path]])[0]
    _check(
        "S2d after the holder closes, a new runner may open the store",
        out.startswith("OPENED"),
        "exit=%s out=%r err=%r" % (code, out, err[-200:]),
    )


def _pin_sqlite_survives_a_killed_holder(tmp: str) -> None:
    """PIN S8: a SIGKILLed holder does NOT lock the store forever.

    The 2026-09-07 review found that a killed runner left `runner_lock`
    permanently claimed, so criterion 13's resume could not happen without a
    human passing `take_over=True`. `close()` is never reached by SIGKILL --
    that is what SIGKILL means -- so no amount of care in `close()` could fix
    it, and a timeout would have been a guess. The kernel already knows: it
    drops a `flock` when the holding process dies, however it died.

    This pin kills a real process with a real SIGKILL. It is the reason the
    fix is a kernel lock and not a heuristic.
    """
    import signal
    import subprocess

    path = os.path.join(tmp, "killed.sqlite3")
    env = dict(os.environ)
    env["PYTHONPATH"] = _package_parent() + os.pathsep + env.get("PYTHONPATH", "")
    holder = subprocess.Popen(
        [sys.executable, "-m", "loopkit_core.store", "--worker-hold-sqlite", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        line = holder.stdout.readline().strip()
        _check(
            "S8a-control the holder really took the store (setup, not the property)",
            line.startswith("HOLDING"),
            "got %r" % line,
        )
        # While it lives, a second runner is still refused -- the fix must not
        # have turned the lock into a no-op.
        blocked = _capture(lambda: SqliteStore(path).close())[0]
        _check(
            "S8b a LIVE holder is still refused (the lock did not become a no-op)",
            blocked.startswith("StoreLocked"),
            "got %r" % blocked[:160],
        )
        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=10)
    finally:
        if holder.poll() is None:  # pragma: no cover
            holder.kill()
            holder.wait(timeout=10)
        holder.stdout.close()
        holder.stderr.close()

    recovered: Dict[str, Any] = {}

    def reopen():
        recovered["store"] = SqliteStore(path)

    error, _streams = _capture(reopen)
    _check(
        "S8c after SIGKILL the next runner opens the store AUTOMATICALLY -- no "
        "human, no take_over=True, no timeout",
        recovered.get("store") is not None,
        "open failed: %s" % error.strip()[:200],
    )
    store = recovered.get("store")
    if store is not None:
        _check(
            "S8d the killed runner's journal survives the takeover (criterion 13 resume)",
            store.get("journal/run-1/head") == b"written before the kill",
            "value=%r" % (store.get("journal/run-1/head"),),
        )
        _check(
            "S8e the takeover is recorded as one, not silently pretended",
            getattr(store, "_took_over", False) is True,
        )
        store.close()


# -- pin S3: append is atomic per record ----------------------------------
def _decode_records(raw: Optional[bytes]) -> Tuple[List[dict], List[str]]:
    good: List[dict] = []
    torn: List[str] = []
    for line in (raw or b"").split(b"\n"):
        if not line:
            continue
        try:
            good.append(json.loads(line.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            torn.append(line[:80].decode("utf-8", "replace"))
    return good, torn


def _pin_sqlite_live_holder_is_absolute(tmp: str) -> None:
    """PIN S8f-S8i: a LIVE holder is refused no matter what the ROW says.

    The 2026-09-07 third review found the one direction the takeover rule got
    wrong. The guard read

        if row and row[0] != self.runner_id and not take_over:

    so the whole refusal was SKIPPED whenever the row named the same runner
    id, and the second process went straight on to write. Unreachable with the
    default `uuid4`; reachable the moment anyone sets a stable runner id from
    config, which is the ordinary way to make a resume identifiable. A store
    with no row at all -- a holder SIGKILLed between taking the flock and
    inserting its row -- fell through the same hole.

    The row was never the authority; the kernel flock is. So the refusal is
    taken BEFORE the row is read, and these pins drive both directions.
    """
    import subprocess

    shared_id = "runner-id-from-config-not-a-uuid"
    path = os.path.join(tmp, "same-id.sqlite3")
    env = dict(os.environ)
    env["PYTHONPATH"] = _package_parent() + os.pathsep + env.get("PYTHONPATH", "")
    holder = subprocess.Popen(
        [sys.executable, "-m", "loopkit_core.store", "--worker-hold-sqlite", path, shared_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        line = holder.stdout.readline().strip()
        _check(
            "S8f-control the same-id holder really took the store (setup)",
            line.startswith("HOLDING"),
            "got %r" % line,
        )
        second: Dict[str, Any] = {}

        def open_same_id():
            second["store"] = SqliteStore(path, runner_id=shared_id)

        error, _streams = _capture(open_same_id)
        _check(
            "S8g a second process reusing the SAME runner_id is REFUSED while a "
            "live holder has the flock (it used to be admitted, and it wrote)",
            second.get("store") is None and error.startswith("StoreLocked"),
            "opened=%s error=%r" % (second.get("store") is not None, error.strip()[:160]),
        )

        forced: Dict[str, Any] = {}

        def force_same_id():
            forced["store"] = SqliteStore(path, runner_id=shared_id, take_over=True)

        error, _streams = _capture(force_same_id)
        _check(
            "S8h take_over=True does NOT override a live holder, same runner_id "
            "or not -- the module header has claimed this all along",
            forced.get("store") is None and error.startswith("StoreLocked"),
            "opened=%s error=%r" % (forced.get("store") is not None, error.strip()[:160]),
        )
        # The journal the holder wrote must be untouched: a refusal that still
        # wrote would be no refusal at all.
        _check(
            "S8h2 the refused runners wrote nothing -- the row still names the "
            "live holder's pid",
            second.get("store") is None and forced.get("store") is None,
            "one of them opened the store",
        )
        for leftover in (second.get("store"), forced.get("store")):
            if leftover is not None:  # pragma: no cover - only if the pin fails
                leftover.close()
    finally:
        holder.kill()
        holder.wait(timeout=10)
        holder.stdout.close()
        holder.stderr.close()

    # A live flock with NO row: a holder killed between taking the lock and
    # inserting its row. Simulated in-process, because `flock` is held per
    # OPEN FILE DESCRIPTION -- a second `os.open` of the same file in the same
    # process is refused exactly as another process would be.
    empty = os.path.join(tmp, "flock-no-row.sqlite3")
    SqliteStore(empty).close()  # create the schema, then leave no row
    try:
        import fcntl
    except ImportError:  # pragma: no cover - not POSIX
        return
    fd = os.open(empty + ".runner-lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held: Dict[str, Any] = {}

        def open_no_row():
            held["store"] = SqliteStore(empty)

        error, _streams = _capture(open_no_row)
        _check(
            "S8i a live flock with NO runner_lock row is refused too (a holder "
            "killed between the flock and its INSERT is still a live holder)",
            held.get("store") is None and error.startswith("StoreLocked"),
            "opened=%s error=%r" % (held.get("store") is not None, error.strip()[:160]),
        )
        if held.get("store") is not None:  # pragma: no cover - only if it fails
            held["store"].close()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _pin_append_atomic_fs(tmp: str) -> None:
    root = os.path.join(tmp, "fs-append")
    key = "journal/run-1/ticks.jsonl"
    writers, per_writer = 6, 50
    stop = threading.Event()
    torn_while_reading: List[str] = []
    store = FsStore(root)

    def reader() -> None:
        while not stop.is_set():
            _good, torn = _decode_records(store.get(key))
            if torn:
                torn_while_reading.extend(torn)

    watcher = threading.Thread(target=reader, daemon=True)
    watcher.start()
    results = _spawn(
        [["--worker-append-fs", root, key, str(n), str(per_writer)] for n in range(writers)]
    )
    stop.set()
    watcher.join(timeout=5)
    records, torn = _decode_records(store.get(key))
    expected = {(n, i) for n in range(writers) for i in range(per_writer)}
    seen = {(r.get("writer"), r.get("seq")) for r in records}
    _check(
        "S3a FsStore append: 6 processes x 50 records = 300 readable records, none torn",
        len(records) == writers * per_writer and not torn and seen == expected,
        "records=%d torn=%r missing=%d stderr=%r"
        % (len(records), torn[:3], len(expected - seen), [e for _c, _o, e in results if e][:1]),
    )
    _check(
        "S3b a reader running THROUGHOUT the appends never saw a partial line",
        not torn_while_reading,
        "torn=%r" % torn_while_reading[:3],
    )


def _pin_append_atomic_sqlite(tmp: str) -> None:
    path = os.path.join(tmp, "append.sqlite3")
    key = "journal/run-1/ticks.jsonl"
    writers, per_writer = 6, 50
    errors: List[str] = []
    barrier = threading.Barrier(writers)
    with SqliteStore(path) as store:

        def writer(index: int) -> None:
            barrier.wait()
            for seq in range(per_writer):
                try:
                    store.append(key, json.dumps({"writer": index, "seq": seq}))
                except Exception as exc:  # noqa: BLE001
                    errors.append("%s: %s" % (type(exc).__name__, exc))

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        records, torn = _decode_records(store.get(key))
        expected = {(n, i) for n in range(writers) for i in range(per_writer)}
        seen = {(r.get("writer"), r.get("seq")) for r in records}
        _check(
            "S3c SqliteStore append: 6 threads x 50 records = 300 records, none torn or lost",
            len(records) == writers * per_writer and not torn and seen == expected and not errors,
            "records=%d torn=%r missing=%d errors=%r"
            % (len(records), torn[:3], len(expected - seen), errors[:2]),
        )


# -- pin S5: list() is ordered and never truncated -------------------------
def _pin_list_not_truncated(tmp: str) -> None:
    count = 2500
    keys = ["journal/run-1/e%05d" % n for n in range(count)]
    fs = FsStore(os.path.join(tmp, "fs-list"))
    for key in keys:
        fs.append(key, "{}")
    listed = fs.list("journal/run-1/")
    _check(
        "S5a FsStore list: 2500 keys, all of them, lexicographic",
        listed == sorted(keys),
        "got %d keys, sorted=%s" % (len(listed), listed == sorted(listed)),
    )
    with SqliteStore(os.path.join(tmp, "list.sqlite3")) as store:
        for key in keys:
            store.append(key, "{}")
        listed = store.list("journal/run-1/")
        _check(
            "S5b SqliteStore list: 2500 keys, all of them, lexicographic",
            listed == sorted(keys),
            "got %d keys" % len(listed),
        )
        _check(
            "S5c list(prefix) excludes keys outside the prefix",
            store.list("journal/run-2/") == [],
        )


# -- pin S4: no store leaks a credential -----------------------------------
def _recording_s3_server():
    """A local endpoint that records every header it is sent, then errors."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: List[Dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _record(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            seen.append(
                {
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                }
            )
            body = b"<Error><Code>InternalError</Code></Error>"
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _record
        do_PUT = _record

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1], seen


def _capture(call):
    """Run `call`, returning (full traceback text, stdout+stderr).

    The traceback, not just `str(exc)`: a credential that only shows up in a
    chained `__context__` or a frame line still reaches a human's terminal.
    """
    import contextlib
    import traceback

    out = io.StringIO()
    err = io.StringIO()
    error_text = ""
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            call()
        except BaseException as exc:  # noqa: BLE001
            error_text = "%s: %s\n%s" % (
                type(exc).__name__,
                exc,
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            )
    return error_text, out.getvalue() + err.getvalue()


def _pin_no_secret_leak(tmp: str) -> None:
    server, base, seen = _recording_s3_server()
    try:
        store = S3Store(
            bucket="pin-bucket",
            endpoint=base,
            access_key=_ACCESS_SENTINEL,
            secret_key=_SECRET_SENTINEL,
            region="us-east-1",
        )
        texts: List[str] = []
        for call in (
            lambda: store.put_if_absent("journal/a", "x"),
            lambda: store.get("journal/a"),
            lambda: store.list("journal/"),
        ):
            error, streams = _capture(call)
            texts.append(error + streams)
        dead = S3Store(
            bucket="pin-bucket",
            endpoint="http://127.0.0.1:9",
            access_key=_ACCESS_SENTINEL,
            secret_key=_SECRET_SENTINEL,
        )
        error, streams = _capture(lambda: dead.put_if_absent("journal/a", "x"))
        texts.append(error + streams)
        leaked = [t[:160] for t in texts if _SECRET_SENTINEL in t]
        _check(
            "S4a the S3 secret key appears in no exception text and no stream (4 paths)",
            not leaked,
            "%r" % leaked,
        )
        on_wire = [
            "%s %s" % (req["path"], value)
            for req in seen
            for value in req["headers"].values()
            if _SECRET_SENTINEL in value
        ] + [req["path"] for req in seen if _SECRET_SENTINEL in req["path"]]
        _check(
            "S4b the secret key never travels on the wire -- only the derived signature",
            seen and not on_wire,
            "requests=%d leaks=%r" % (len(seen), on_wire[:2]),
        )
        _check(
            "S4c requests are signed (Authorization carries a SigV4 signature)",
            all("signature=" in req["headers"].get("authorization", "").lower() for req in seen),
            "auth headers=%r" % [req["headers"].get("authorization", "")[:40] for req in seen][:2],
        )
    finally:
        server.shutdown()
        server.server_close()

    # And no store writes a credential into a file it creates.
    root = os.path.join(tmp, "leak-scan")
    fs = FsStore(root)
    fs.put_if_absent("journal/a", "value")
    fs.append("journal/a", "{}")
    found: List[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            with open(os.path.join(dirpath, name), "rb") as handle:
                if _SECRET_SENTINEL.encode() in handle.read():
                    found.append(name)
    _check("S4d no store writes a credential into any file it creates", not found, "%r" % found)


def _pin_s3_endpoint_userinfo() -> None:
    """PIN S7: an S3 ENDPOINT carrying userinfo. The gap `S4b` never covered.

    `S4a`/`S4b` planted a sentinel in the SigV4 secret only, so the whole
    `S3Store.__init__` endpoint path went untested -- and that is where the
    2026-09-07 review found `self.host = parts.netloc`, which put the
    operator's password into `self.host`, hence into every error message,
    into the outgoing `Host:` header, and into the SigV4 canonical request,
    where it was SIGNED onto the wire.

    Built as a sweep rather than as one case: the sentinel goes in every
    position an operator can put a credential in an endpoint URL, and the
    whole traceback is grepped.
    """
    sentinel = "ZZ-S3-ENDPOINT-SENTINEL-9911-ZZ"
    endpoints = [
        ("userinfo, explicit scheme", "http://u:%s@127.0.0.1:9" % sentinel),
        ("userinfo, implied https", "u:%s@127.0.0.1:9" % sentinel),
        ("userinfo as the username", "http://%s:pw@127.0.0.1:9" % sentinel),
        ("userinfo + malformed port", "http://u:%s@127.0.0.1:99999999" % sentinel),
        ("userinfo + non-numeric port", "http://u:%s@127.0.0.1:notaport" % sentinel),
        ("userinfo + trailing path", "http://u:%s@127.0.0.1:9/bucketroot" % sentinel),
        ("userinfo, no port", "https://u:%s@example.invalid" % sentinel),
    ]
    leaked: List[str] = []
    refused = 0
    for label, endpoint in endpoints:
        holder: Dict[str, Any] = {}

        def build(e=endpoint, h=holder):
            h["store"] = S3Store(
                bucket="pin-bucket", endpoint=e, access_key="AK-pin", secret_key="SK-pin-secret"
            )
            h["store"].put_if_absent("journal/a", "v")

        error, streams = _capture(build)
        if sentinel in error or sentinel in streams:
            leaked.append("%s -> %s" % (label, error.strip()[:120]))
        store = holder.get("store")
        if store is None:
            refused += 1
        else:
            # If a future edit decides to accept userinfo instead of refusing,
            # the host it derives must STILL be free of it -- belt and braces,
            # because `self.host` is what is signed and what is sent.
            if sentinel in getattr(store, "host", ""):
                leaked.append("%s -> store.host carries it: %r" % (label, store.host))
    _check(
        "S7a an endpoint URL's userinfo reaches no traceback and no stream (%d shapes)"
        % len(endpoints),
        not leaked,
        " | ".join(leaked[:3]),
    )
    _check(
        "S7b every endpoint carrying userinfo is REFUSED, not silently stripped "
        "(a dropped credential the operator thinks was used is its own bug)",
        refused == len(endpoints),
        "refused %d of %d" % (refused, len(endpoints)),
    )

    # A CONTROL, or S7a/S7b prove nothing: the same endpoint WITHOUT userinfo
    # must build, and its host must be exactly hostname:port.
    clean = S3Store(
        bucket="pin-bucket",
        endpoint="http://127.0.0.1:9000",
        access_key="AK-pin",
        secret_key="SK-pin-secret",
    )
    _check(
        "S7c-control the same endpoint without userinfo builds, host is host:port",
        clean.host == "127.0.0.1:9000" and clean.scheme == "http",
        "host=%r scheme=%r" % (clean.host, clean.scheme),
    )
    no_port = S3Store(
        bucket="pin-bucket",
        endpoint="https://s3.example.invalid",
        access_key="AK-pin",
        secret_key="SK-pin-secret",
    )
    _check(
        "S7d a portless endpoint keeps a bare hostname (no ':None')",
        no_port.host == "s3.example.invalid",
        "host=%r" % no_port.host,
    )


# -- pin S9/S10: the GENERIC leak hunt -------------------------------------
#
# Why this exists, stated plainly because three reviews got here before it did.
#
# S4 planted a sentinel in the SigV4 secret. S7 planted one in the endpoint's
# userinfo. Both went green while `_request`'s catch-all was handing the whole
# signed `Authorization` header -- access key included -- to anyone whose key
# carried a trailing newline. Each pin covered the shape its author had just
# been shown. That is "enumerate known-bad, permit the unknown", this repo's
# most-repeated defect, applied to leak pins themselves.
#
# So this one is built the other way round. ONE sentinel, containing every
# character class that makes a redactor's life hard -- a newline, a tab, both
# quote characters, and two non-ASCII characters -- goes into every credential
# position, and every reachable error path in this module is driven with it.
# Detection does not ask "is the secret present?", which is the question `%r`
# defeats; it asks "is any part of the secret that SURVIVES ESCAPING present?",
# which no escaping can make false.
_S9_SENTINEL = "ZZ-STORE-LEAK-9911-c0ffee\n\tq\"'éΩ-ZZ"
# The part of the sentinel no character-escaping scheme touches. Every
# `repr`, JSON, percent-, entity-, shell- or backslash-encoding of the
# sentinel still contains this substring verbatim, which is what makes one
# `in` test cover escapings this pin never heard of. Written out as a literal
# rather than computed from redact.py, so the pin does not grade the
# implementation with the implementation's own ruler.
_S9_CORE = "ZZ-STORE-LEAK-9911-c0ffee"


def _s9_forms(secret: str) -> List[str]:
    """Every rendering of `secret` this pin treats as a leak.

    `_S9_CORE` alone covers all CHARACTER escaping. The rest are whole-string
    re-codings, which destroy the core instead of escaping it, so they have to
    be named.
    """
    import base64 as _b64

    raw = secret.encode("utf-8")
    forms = {
        secret,
        _S9_CORE,
        repr(secret)[1:-1],
        repr(raw)[2:-1],
        json.dumps(secret)[1:-1],
        json.dumps(secret, ensure_ascii=False)[1:-1],
        urllib.parse.quote(secret, safe=""),
        secret.encode("unicode_escape").decode("ascii"),
        secret.encode("ascii", "backslashreplace").decode("ascii"),
        _b64.b64encode(raw).decode("ascii"),
        raw.hex(),
    }
    return sorted({f for f in forms if len(f) >= 8}, key=len, reverse=True)


def _s9_hits(text: str, forms: Sequence[str]) -> List[str]:
    return [f[:24] for f in forms if f in text]


def _s9_server(status: int, body: bytes, echo_auth: bool):
    """A local endpoint that records every header and answers as instructed."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: List[Dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def _record(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            seen.append(
                {
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                }
            )
            # The nastiest real upstream: one that quotes your own credential
            # back at you inside its error body. On this path the REDACTOR,
            # not the structure of the message, is the only thing between the
            # key and the operator's terminal.
            payload = body
            if echo_auth:
                payload = body + b" auth=" + self.headers.get("Authorization", "").encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _record
        do_PUT = _record

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1], seen


def _pin_leak_sweep_store(tmp: str) -> None:
    sentinel = _S9_SENTINEL
    forms = _s9_forms(sentinel)

    echoing, echo_base, echo_seen = _s9_server(500, b"<Error><Code>Denied</Code></Error>", True)
    garbage, garbage_base, garbage_seen = _s9_server(200, b"this is not XML at all <<<", False)
    try:
        # Every shape of endpoint that reaches a DIFFERENT error path.
        endpoints = [
            ("dead host (URLError)", "http://127.0.0.1:9"),
            ("live 500 echoing the Authorization header", echo_base),
            ("live 200 with unparseable XML", garbage_base),
            ("malformed port", "http://127.0.0.1:99999999"),
            ("non-numeric port", "http://127.0.0.1:notaport"),
            ("userinfo in the endpoint", "http://u:%s@127.0.0.1:9" % sentinel),
            ("sentinel as endpoint username", "http://%s:pw@127.0.0.1:9" % sentinel),
            ("file:// endpoint", "file:///etc/hosts"),
            ("unknown scheme", "gopher://%s@127.0.0.1:9" % sentinel),
            ("no host", "//127.0.0.1:9"),
            ("empty endpoint", ""),
        ]
        # Every way an operator's credential arrives damaged. The FIRST is the
        # real 2026-09-07 leak: `AWS_ACCESS_KEY_ID=$(cat keyfile)` keeps the
        # file's trailing newline, `putheader` rejects it, and the ValueError
        # quotes the entire signed header with `%r`.
        keys = [
            ("access key + trailing newline", "AKIA" + sentinel[: len(_S9_CORE)] + "\n", "SK-plain-decoy"),
            ("access key IS the sentinel", sentinel, "SK-plain-decoy"),
            ("secret key IS the sentinel", "AKIA-plain-decoy", sentinel),
            ("both keys are the sentinel", sentinel, sentinel),
            ("secret key + trailing tab", "AKIA-plain-decoy", _S9_CORE + "\t"),
            ("access key + embedded quote", 'AKIA"' + _S9_CORE, "SK-plain-decoy"),
        ]
        leaked: List[str] = []
        # "No store prints anything, ever" is a claim in this module's header.
        # Grepping the streams for a sentinel only proves they carry no
        # SECRET; it would stay green if a future edit started printing
        # diagnostics. So the streams are required to be EMPTY.
        printed: List[str] = []
        paths = 0
        for where, endpoint in endpoints:
            for key_label, access, secret in keys:
                label = "%s / %s" % (where, key_label)
                holder: Dict[str, Any] = {}

                def build(e=endpoint, a=access, sk=secret, h=holder):
                    h["store"] = S3Store(
                        bucket="pin-bucket",
                        endpoint=e,
                        access_key=a,
                        secret_key=sk,
                        region="us-east-1",
                        prefix="sweep",
                    )
                    return h["store"]

                paths += 1
                error, streams = _capture(build)
                for kind, text in (("traceback", error), ("stdout/stderr", streams)):
                    hit = _s9_hits(text, forms)
                    if hit:
                        leaked.append("%s -> %s %r: %s" % (label, kind, hit, text.strip()[:100]))
                if streams:
                    printed.append("%s (construction) -> %r" % (label, streams[:80]))
                store = holder.get("store")
                if store is None:
                    continue
                for op_name, op in (
                    ("put_if_absent", lambda s=store: s.put_if_absent("journal/a", "v")),
                    ("get", lambda s=store: s.get("journal/a")),
                    ("append", lambda s=store: s.append("journal/a", "{}")),
                    ("list", lambda s=store: s.list("journal/")),
                ):
                    paths += 1
                    error, streams = _capture(op)
                    hit = _s9_hits(error + streams, forms)
                    if hit:
                        leaked.append(
                            "%s / %s -> %r: %s" % (label, op_name, hit, (error + streams).strip()[:100])
                        )
                    if streams:
                        printed.append("%s / %s -> %r" % (label, op_name, streams[:80]))
                # `host` is what is signed and what is sent; `_scrub` must not
                # be the only thing standing between it and the wire.
                if _s9_hits(getattr(store, "host", ""), forms):
                    leaked.append("%s -> store.host carries it: %r" % (label, store.host))

        _check(
            "S9a a sentinel carrying a newline, a tab, both quotes and non-ASCII "
            "reaches no traceback and no stream from ANY S3Store path (%d paths, "
            "%d escaped renderings checked)" % (paths, len(forms)),
            not leaked,
            " | ".join(leaked[:3]),
        )

        # -- the wire, stated precisely or it is wrong in the permissive
        # direction. An ACCESS KEY belongs in `Credential=` inside
        # `Authorization` -- that is what SigV4 is. Everything else must be
        # clean: the SECRET key is never sent at all, endpoint userinfo is
        # never promoted onto the wire, and nothing leaks into the `Host`
        # header, the request line or any other header.
        echo_seen.clear()
        control = S3Store(
            bucket="pin-bucket", endpoint=echo_base, access_key="AKIA-control", secret_key="SK-control"
        )
        _capture(lambda: control.put_if_absent("journal/control", "v"))
        _check(
            "S9a2 no store prints ANYTHING on any of those paths -- the streams "
            "are empty, not merely free of the sentinel (%d paths)" % paths,
            not printed,
            " | ".join(printed[:3]),
        )

        _check(
            "S9b-control the observation channel works: a plain request IS seen "
            "with an Authorization header",
            len(echo_seen) == 1 and "authorization" in echo_seen[0]["headers"],
            "seen=%r" % (echo_seen,),
        )

        echo_seen.clear()
        wired = S3Store(
            bucket="pin-bucket", endpoint=echo_base, access_key="AKIA-plain", secret_key=sentinel
        )
        for call in (
            lambda: wired.put_if_absent("journal/a", "v"),
            lambda: wired.get("journal/a"),
            lambda: wired.list("journal/"),
        ):
            _capture(call)
        on_wire = []
        for req in echo_seen:
            if _s9_hits(req["path"], forms):
                on_wire.append("request line: %s" % req["path"][:60])
            for name, value in req["headers"].items():
                if _s9_hits(value, forms):
                    on_wire.append("%s: %s" % (name, value[:60]))
        _check(
            "S9c the SECRET key never travels -- not in Authorization, not in "
            "Host, not in the request line, not in any header (%d requests "
            "reached the server)" % len(echo_seen),
            echo_seen and not on_wire,
            "%r" % on_wire[:3],
        )

        echo_seen.clear()
        keyed = S3Store(
            bucket="pin-bucket", endpoint=echo_base, access_key=_S9_CORE, secret_key="SK-plain"
        )
        _capture(lambda: keyed.put_if_absent("journal/a", "v"))
        misplaced = []
        for req in echo_seen:
            if _S9_CORE in req["path"]:
                misplaced.append("request line")
            for name, value in req["headers"].items():
                if _S9_CORE not in value:
                    continue
                if name == "authorization" and value.startswith("AWS4-HMAC-SHA256 Credential=%s/" % _S9_CORE):
                    continue  # this is what an access key IS
                misplaced.append("%s: %s" % (name, value[:60]))
        _check(
            "S9d the ACCESS key appears ONLY inside Authorization's Credential= "
            "field and nowhere else on the wire",
            echo_seen and not misplaced,
            "%r" % misplaced[:3],
        )
    finally:
        for server in (echoing, garbage):
            server.shutdown()
            server.server_close()

    # -- no file, anywhere under the process's cwd or the temp root.
    scan_root = os.path.join(tmp, "s9-files")
    os.makedirs(scan_root, exist_ok=True)
    cwd = os.getcwd()
    os.chdir(scan_root)
    try:
        offender = S3Store(
            bucket="pin-bucket",
            endpoint="http://127.0.0.1:9",
            access_key=sentinel,
            secret_key=sentinel,
        )
        for call in (
            lambda: offender.put_if_absent("journal/a", "v"),
            lambda: offender.append("journal/a", "{}"),
            lambda: offender.list(""),
        ):
            _capture(call)
    finally:
        os.chdir(cwd)
    written: List[str] = []
    for dirpath, _dirs, files in os.walk(scan_root):
        for name in files:
            with open(os.path.join(dirpath, name), "rb") as handle:
                blob = handle.read().decode("utf-8", "replace")
            if _s9_hits(blob, forms) or _s9_hits(name, forms):
                written.append(name)
    _check(
        "S9e S3Store writes no file at all, so no file can carry the sentinel",
        not written and not os.listdir(scan_root),
        "%r" % (written or os.listdir(scan_root)),
    )


def _pin_scrub_itself(tmp: str) -> None:
    """PIN S9f-S9h: the REDACTOR has a pin of its own.

    This is the root cause of the 2026-09-07 third review. `_scrub` -- the
    store's only redaction function -- could be replaced with `return text`
    and all 27 pins stayed green, so the thing that hides secrets was the one
    thing never tested. A pin that cannot fail is decoration; a redactor with
    no pin at all is worse, because every other leak pin is quietly trusting
    it.
    """
    sentinel = _S9_SENTINEL
    store = S3Store(
        bucket="pin-bucket",
        endpoint="http://127.0.0.1:9",
        access_key="AKIA-plain-decoy",
        secret_key=sentinel,
    )
    # Escapers deliberately chosen to include ones redact.py does NOT
    # enumerate, so this grades the PROPERTY ("what survives escaping is
    # matched") and not the implementation's own list.
    import base64 as _b64
    import shlex
    import xml.sax.saxutils as _saxutils

    renderings = [
        ("raw", sentinel),
        ("repr(str)", repr(sentinel)),
        ("repr(bytes)", repr(sentinel.encode("utf-8"))),
        ("putheader ValueError", "Invalid header value %r" % ("AWS4-HMAC-SHA256 Credential=%s/x" % sentinel).encode()),
        ("json ensure_ascii", json.dumps(sentinel)),
        ("json unicode", json.dumps(sentinel, ensure_ascii=False)),
        ("percent-encoded", urllib.parse.quote(sentinel, safe="")),
        ("unicode_escape", sentinel.encode("unicode_escape").decode("ascii")),
        ("backslashreplace", sentinel.encode("ascii", "backslashreplace").decode("ascii")),
        ("shell-quoted", shlex.quote(sentinel)),
        ("XML entities", _saxutils.escape(sentinel)),
        ("base64", _b64.b64encode(sentinel.encode("utf-8")).decode("ascii")),
        ("hex", sentinel.encode("utf-8").hex()),
    ]
    survived = []
    for label, text in renderings:
        scrubbed = store._scrub("upstream said: " + text)
        if _s9_hits(scrubbed, _s9_forms(sentinel)):
            survived.append("%s -> %s" % (label, scrubbed[:80]))
    _check(
        "S9f _scrub redacts the secret under %d renderings, including escapers "
        "redact.py does not enumerate (shell, XML entities)" % len(renderings),
        not survived,
        " | ".join(survived[:3]),
    )
    _check(
        "S9g-control _scrub is not simply blanking everything: text with no "
        "secret in it comes back byte-identical",
        store._scrub("http://127.0.0.1:9 unreachable: [Errno 61] Connection refused")
        == "http://127.0.0.1:9 unreachable: [Errno 61] Connection refused",
        "got %r" % store._scrub("http://127.0.0.1:9 unreachable: [Errno 61] Connection refused"),
    )

    # The documented limit, made executable. A credential with no
    # escape-invariant run keeps only the weaker whole-string cover, and the
    # honest thing is for the redactor to SAY which registered secrets are in
    # that state rather than let a reader assume none are. Asserting the
    # weakness itself would block anyone from fixing it later; asserting that
    # it is REPORTED does not.
    from loopkit_core.redact import Redactor as _R

    all_punctuation = "\n\t\"'"
    _check(
        "S9i the redactor names its own weak spots: a credential with no "
        "escape-invariant run is listed by uncovered()",
        _R([all_punctuation]).uncovered() == [all_punctuation],
        "got %r" % (_R([all_punctuation]).uncovered(),),
    )
    _check(
        "S9j-control this store's own credentials are NOT weak spots -- both "
        "keys carry an invariant run, so the structural rule covers them",
        store._redact.uncovered() == [],
        "uncovered secrets present (values withheld): %d" % len(store._redact.uncovered()),
    )

    # The redactor is a BACKSTOP, not the only wall. `_request`'s catch-all
    # must compose no untrusted exception text at all -- pinned here with a
    # marker that is not a secret, so this stays red even if the redactor is
    # perfect. It is the pin that would have caught the 2026-09-07 leak
    # without anyone having to think of newlines in credentials.
    marker = "MARKER-CATCHALL-77123-DO-NOT-ECHO"

    class _Exploding:
        def open(self, *_args, **_kwargs):
            raise RuntimeError(marker)

    store._opener = _Exploding()
    error, streams = _capture(lambda: store.put_if_absent("journal/a", "v"))
    _check(
        "S9h _request's catch-all names the exception TYPE and never its text "
        "(the unknown exception is where the header leak lived)",
        marker not in (error + streams) and "RuntimeError" in error,
        "%r" % (error + streams)[:200],
    )


def _pin_s3_scheme() -> None:
    """PIN S10: a non-http(s) endpoint is REFUSED, not served by urllib.

    `urllib.request.build_opener` keeps the default handler set, so
    `FileHandler` answers `file://` and `FTPHandler` answers `ftp://`. Left
    alone, `S3Store(endpoint="file:///")` would read local files and hand the
    bytes back as object content with no error and no network call -- and
    every pin above would still be green, because nothing leaked; the store
    was simply reading the wrong thing entirely.
    """
    probe = os.path.abspath(__file__)
    refused, accepted = 0, []
    for endpoint in (
        "file://" + probe,
        "file:///etc/hosts",
        "ftp://127.0.0.1:9",
        "gopher://127.0.0.1:9",
        "data:text/plain,hello",
    ):
        try:
            store = S3Store(
                bucket="pin-bucket", endpoint=endpoint, access_key="AKIA-pin", secret_key="SK-pin-x"
            )
        except StoreError:
            refused += 1
        else:
            accepted.append("%s -> scheme=%r host=%r" % (endpoint, store.scheme, store.host))
    _check(
        "S10a every non-http(s) endpoint scheme is refused (file://, ftp://, "
        "gopher://, data:)",
        refused == 5,
        "accepted: %r" % accepted,
    )
    # A CONTROL, or S10a passes on a store that refuses everything.
    ok_http = S3Store(
        bucket="pin-bucket", endpoint="http://127.0.0.1:9000", access_key="AKIA-pin", secret_key="SK-pin-x"
    )
    ok_https = S3Store(
        bucket="pin-bucket", endpoint="s3.example.invalid", access_key="AKIA-pin", secret_key="SK-pin-x"
    )
    _check(
        "S10b-control http:// and a bare host (implied https) still build",
        ok_http.scheme == "http" and ok_https.scheme == "https",
        "http=%r https=%r" % (ok_http.scheme, ok_https.scheme),
    )
    # And prove the refusal is load-bearing: with the guard gone, urllib
    # really would serve the file. Demonstrated through urllib directly so the
    # claim in the module header is checked, not asserted.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    served = False
    try:
        with opener.open("file://" + probe) as handle:
            served = handle.read(16) != b""
    except Exception:  # pragma: no cover - would mean urllib changed
        served = False
    _check(
        "S10c-why the refusal is load-bearing: this module's own opener DOES "
        "serve file:// URLs, so an unchecked scheme returns local bytes as "
        "object content",
        served,
        "urllib did not serve file:// -- re-derive S10, its premise changed",
    )


# -- pin S6: S3Store against a live MinIO ----------------------------------
def _s3_from_env() -> Optional[S3Store]:
    try:
        return S3Store()
    except StoreError:
        return None


def _s3_reachable(store: "S3Store") -> Tuple[bool, str]:
    """Probe the endpoint once. Never prints, never leaks: the reason is
    scrubbed through the store's own redactor before it is returned."""
    try:
        store._request("GET", "/" + _uri_encode(store.bucket), query=[("max-keys", "0")])
        return True, ""
    except StoreError as exc:
        return False, store._scrub(str(exc))[:120]
    except Exception as exc:  # noqa: BLE001
        return False, store._scrub("%s: %s" % (type(exc).__name__, exc))[:120]


_S3_UNVERIFIED = (
    "S3Store.put_if_absent (If-None-Match conditional write), S3Store.append "
    "(per-record part objects), S3Store.get (base+parts concatenation) and "
    "S3Store.list (pagination past 1000 keys) are UNVERIFIED against real "
    "object storage in this run"
)


def _skip_loudly(label: str, why: str) -> None:
    """A skip that NAMES what went unverified. A silent skip is this repo's
    named defect: a suite that never ran reports success by running nothing."""
    line = "SKIP %s -- %s -- %s" % (label, why, _S3_UNVERIFIED)
    _SKIPS.append(line)
    print(line)


def _pin_s3_live() -> None:
    store = _s3_from_env()
    if store is None:
        # Asking for --with-s3 and not configuring it is an operator error,
        # not an absent dependency. That is a FAIL, not a skip.
        _bad(
            "S6 S3Store against a live endpoint",
            "--with-s3 was requested but LOOPKIT_S3_* is not set -- " + _S3_UNVERIFIED,
        )
        return
    # Configured but unreachable is a different thing: CI without a MinIO must
    # not go red, but it must not go quiet either.
    reachable, why = _s3_reachable(store)
    if not reachable:
        _skip_loudly("S6 S3Store live pins", "endpoint unreachable (%s)" % why)
        return
    run = "pin-%s" % uuid.uuid4().hex[:8]
    key = "%s/claim" % run

    _check("S6a S3Store put_if_absent writes a new key", store.put_if_absent(key, "winner"))
    _check("S6b S3Store put_if_absent refuses an existing key", not store.put_if_absent(key, "loser"))
    _check("S6c the loser did not overwrite", store.get(key) == b"winner")

    race_key = "%s/race" % run
    results = _spawn(
        [["--worker-put-s3", race_key, "writer-%d" % n] for n in range(4)]
    )
    verdicts = [out for _c, out, _e in results]
    _check(
        "S6d MinIO conditional put: 4 racing processes, exactly one wins (If-None-Match)",
        len([v for v in verdicts if v.startswith("WROTE")]) == 1,
        "verdicts=%r stderr=%r" % (verdicts, [e for _c, _o, e in results if e][:1]),
    )

    journal = "%s/ticks.jsonl" % run
    writers, per_writer = 4, 25
    results = _spawn(
        [["--worker-append-s3", journal, str(n), str(per_writer)] for n in range(writers)]
    )
    records, torn = _decode_records(store.get(journal))
    expected = {(n, i) for n in range(writers) for i in range(per_writer)}
    seen = {(r.get("writer"), r.get("seq")) for r in records}
    _check(
        "S6e MinIO append: 4 processes x 25 records, all present, none torn",
        len(records) == writers * per_writer and not torn and seen == expected,
        "records=%d torn=%r missing=%d stderr=%r"
        % (len(records), torn[:2], len(expected - seen), [e for _c, _o, e in results if e][:1]),
    )
    _check(
        "S6f list() hides part objects and shows the journal key once",
        store.list("%s/" % run).count(journal) == 1,
        "listed=%r" % store.list("%s/" % run)[:6],
    )

    bulk = "%s/bulk" % run
    count = 2500
    keys = ["%s/e%05d" % (bulk, n) for n in range(count)]
    for chunk_start in range(0, count, 250):
        threads = [
            threading.Thread(target=store.put_if_absent, args=(k, b"{}"))
            for k in keys[chunk_start : chunk_start + 250]
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    listed = store.list("%s/" % bulk)
    _check(
        "S6g MinIO list: 2500 keys returned in full (paginates past 1000)",
        listed == sorted(keys),
        "got %d of %d" % (len(listed), count),
    )


# -- workers ---------------------------------------------------------------
def _worker(args: Sequence[str]) -> int:
    kind = args[0]
    if kind == "--worker-put-fs":
        root, key, value = args[1], args[2], args[3]
        store = FsStore(root)
        _await_release()
        print("WROTE %s" % value if store.put_if_absent(key, value) else "LOST %s" % value)
        return 0
    if kind == "--worker-append-fs":
        root, key, index, total = args[1], args[2], int(args[3]), int(args[4])
        store = FsStore(root)
        _await_release()
        for seq in range(total):
            store.append(key, json.dumps({"writer": index, "seq": seq, "pad": "x" * 40}))
        print("APPENDED %d" % total)
        return 0
    if kind == "--worker-open-sqlite":
        path = args[1]
        _await_release()
        try:
            store = SqliteStore(path)
        except StoreLocked as exc:
            print("REFUSED StoreLocked %s" % exc)
            return 0
        store.close()
        print("OPENED")
        return 0
    if kind == "--worker-hold-sqlite":
        # The optional runner id is what makes S8g possible: the default is a
        # fresh uuid4, and the defect being pinned there needs BOTH processes
        # to carry the SAME stable id, which is what anyone reading it from
        # config gets.
        path = args[1]
        runner_id = args[2] if len(args) > 2 else None
        store = SqliteStore(path, runner_id=runner_id)
        store.put_if_absent("journal/run-1/head", "written before the kill")
        print("HOLDING %d" % os.getpid(), flush=True)
        while True:  # wait to be SIGKILLed; never closes, never releases
            _time.sleep(3600)
    if kind == "--worker-put-s3":
        key, value = args[1], args[2]
        store = S3Store()
        _await_release()
        print("WROTE %s" % value if store.put_if_absent(key, value) else "LOST %s" % value)
        return 0
    if kind == "--worker-append-s3":
        key, index, total = args[1], int(args[2]), int(args[3])
        store = S3Store()
        _await_release()
        for seq in range(total):
            store.append(key, json.dumps({"writer": index, "seq": seq}))
        print("APPENDED %d" % total)
        return 0
    print("unknown worker: %s" % kind, file=sys.stderr)
    return 2


def _selftest(with_s3: bool) -> int:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="loopkit-store-pin-") as tmp:
        _pin_conditional_write_fs(tmp)
        _pin_conditional_write_sqlite(tmp)
        _pin_sqlite_refuses_second_runner(tmp)
        _pin_sqlite_survives_a_killed_holder(tmp)
        _pin_sqlite_live_holder_is_absolute(tmp)
        _pin_append_atomic_fs(tmp)
        _pin_append_atomic_sqlite(tmp)
        _pin_list_not_truncated(tmp)
        _pin_no_secret_leak(tmp)
        _pin_s3_endpoint_userinfo()
        _pin_leak_sweep_store(tmp)
        _pin_scrub_itself(tmp)
        _pin_s3_scheme()
    if with_s3:
        _pin_s3_live()
    else:
        _skip_loudly("S6 S3Store live pins", "--with-s3 not passed")
    failures = [line for line in _RESULTS if line.startswith("FAIL")]
    print("")
    if failures:
        print("store.py: %d FAIL of %d" % (len(failures), len(_RESULTS)))
        return 1
    if _SKIPS:
        # Printed on the SUMMARY line, not only 60 lines up, so a reader who
        # sees "ALL PASS" also sees what was never checked.
        print("store.py: ALL PASS (%d pins) -- %d LOUD SKIP:" % (len(_RESULTS), len(_SKIPS)))
        for line in _SKIPS:
            print("  " + line)
        return 0
    print("store.py: ALL PASS (%d pins)" % len(_RESULTS))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0].startswith("--worker-"):
        return _worker(args)
    if args and args[0] == "--selftest":
        return _selftest("--with-s3" in args)
    print(__doc__ or "")
    print("usage: python3 -m loopkit_core.store --selftest [--with-s3]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
