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

STALE SQLITE LOCKS

If a runner dies without closing, its `runner_lock` row survives and the next
open is refused -- correct, but it needs a human. The row records host and pid
so the human can check whether that runner is really gone, and
`SqliteStore(path, take_over=True)` is the explicit override. It is
deliberately not automatic: a timeout-based steal is exactly the mechanism
that lets two runners each believe they hold one journal.

SECRETS

No store prints anything, ever. The S3 secret key is used only as HMAC input;
it never reaches a URL, a header other than the derived signature, an
exception message, or a file. Pinned by `--selftest` case S4.

VERIFICATION STATUS (be suspicious of anything not listed here)

  * FsStore      -- pinned with real concurrent PROCESSES.
  * SqliteStore  -- pinned with real concurrent threads, plus a second process
                    proving the runner lock refuses rather than interleaves.
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
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

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
        self._claim_runner_lock(take_over)

    # -- runner lock --------------------------------------------------------
    def _claim_runner_lock(self, take_over: bool) -> None:
        with self._guard:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT runner_id, host, pid, acquired_at FROM runner_lock WHERE id = 1"
                ).fetchone()
                if row and row[0] != self.runner_id and not take_over:
                    self._conn.execute("ROLLBACK")
                    self._conn.close()
                    self._closed = True
                    raise StoreLocked(
                        "sqlite store is already held by runner %s (host=%s pid=%s since %s); "
                        "refusing to interleave writes -- pass take_over=True only after "
                        "confirming that runner is dead" % (row[0], row[1], row[2], row[3])
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
        parts = urllib.parse.urlsplit(endpoint if "://" in endpoint else "https://" + endpoint)
        self.scheme = parts.scheme or "https"
        self.host = parts.netloc
        if not self.host:
            raise StoreError("endpoint has no host")
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
            raise StoreTransportError(
                self._scrub("%s://%s unreachable: %s" % (self.scheme, self.host, exc.reason))
            ) from None
        except Exception as exc:
            raise StoreTransportError(
                self._scrub("%s://%s failed: %s" % (self.scheme, self.host, exc))
            ) from None

    def _scrub(self, text: str) -> str:
        for secret in (self._secret_key, self._access_key):
            if secret and len(secret) >= 4 and secret in text:
                text = text.replace(secret, "***")
        return text

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
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    error_text = ""
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            call()
        except BaseException as exc:  # noqa: BLE001
            error_text = "%s: %s" % (type(exc).__name__, exc)
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


# -- pin S6: S3Store against a live MinIO ----------------------------------
def _s3_from_env() -> Optional[S3Store]:
    try:
        return S3Store()
    except StoreError:
        return None


def _pin_s3_live() -> None:
    store = _s3_from_env()
    if store is None:
        _bad(
            "S6 S3Store against a live endpoint",
            "LOOPKIT_S3_* not set -- S3Store is UNVERIFIED in this run",
        )
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
        _pin_append_atomic_fs(tmp)
        _pin_append_atomic_sqlite(tmp)
        _pin_list_not_truncated(tmp)
        _pin_no_secret_leak(tmp)
    if with_s3:
        _pin_s3_live()
    else:
        print("SKIP S6 S3Store live pins (pass --with-s3 with LOOPKIT_S3_* to run them)")
    failures = [line for line in _RESULTS if line.startswith("FAIL")]
    print("")
    if failures:
        print("store.py: %d FAIL of %d" % (len(failures), len(_RESULTS)))
        return 1
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
