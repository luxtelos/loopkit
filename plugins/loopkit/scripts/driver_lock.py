#!/usr/bin/env python3
"""driver_lock.py — one real lock over the things a worktree shares.

WHY THIS EXISTS (2026-09-07). COMMANDS.md carried the sentence "never run two
loop drivers concurrently against state/triage.md; the state file is the lock."
Nothing enforced it, and on 2026-09-07 it failed exactly as an unenforced rule
fails: a reviewer staged one file by name and, before it could commit, a
concurrent driver ran `git add … && git commit` in the same worktree. The
driver's commit swept the reviewer's file in; the reviewer's own commit found
nothing staged. No content was lost, but a review verdict is now attributed to
an unrelated verification commit.

WHAT IS ACTUALLY SHARED. Not the state file — the **git index**, which is one
file per worktree and process-global within it. `state/triage.md` is shared for
the same reason: same worktree, same path. So the lock is scoped to the
**worktree root**, which is precisely the granularity of the resource:

    <git worktree root>/.loopkit/driver.lock

Two agents in two different worktrees never contend, because they never shared
an index in the first place. Two agents in the SAME worktree — which is how
reviewers work, since they write verdicts to `state/` on the loop branch —
serialise.

WHY flock AND NOT mkdir. `fcntl.flock` is released by the kernel when the
holding process exits, including on a crash, a SIGKILL, or a timeout. There is
no stale lock to reap and no liveness heuristic to get wrong. A mkdir lock
would need a pid file, a staleness rule, and a way to be wrong about both. The
mkdir path here is only a fallback for a platform without `fcntl`, and it does
carry that staleness problem — see `_ExclusiveFallback`.

RE-ENTRANCY. The holder exports `LOOPKIT_DRIVER_LOCK=<resolved lock path>`.
A child process that resolves to the same path is already inside the critical
section and takes the lock as a no-op. Without this, `loop-commit.sh` (which
runs itself under the lock) would deadlock against its own parent.

USAGE

    # run a command under the lock, and release it however the command ends
    python3 driver_lock.py run --label "my-driver" -- git status

    # who holds it right now
    python3 driver_lock.py status

    # from Python
    from driver_lock import held
    with held(label="triage_state"):
        ...read-modify-write...

Exit codes for `run`: the command's own code, or 75 (EX_TEMPFAIL) if the lock
could not be acquired inside --timeout.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:  # POSIX: the real thing
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

ENV_HELD = "LOOPKIT_DRIVER_LOCK"
EX_TEMPFAIL = 75
DEFAULT_TIMEOUT = 120.0


class LockTimeout(RuntimeError):
    """Could not acquire inside the timeout. Carries the holder's own words."""


def worktree_root(start: str | os.PathLike[str] | None = None) -> Path:
    """The root of THIS worktree — not the main checkout.

    `git rev-parse --show-toplevel` is per-worktree, which is what we want: a
    linked worktree has its own index, so it must have its own lock. Falling
    back to CLAUDE_PROJECT_DIR before cwd keeps hooks and headless runs honest.
    """
    cwd = str(start) if start else None
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:
        pass
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path(cwd) if cwd else Path.cwd()


def lock_path(root: str | os.PathLike[str] | None = None) -> Path:
    base = Path(root) if root else worktree_root()
    return (base / ".loopkit" / "driver.lock").resolve()


def _holder_note(label: str) -> str:
    return json.dumps({
        "pid": os.getpid(),
        "label": label,
        "session": os.environ.get("CLAUDE_SESSION_ID", ""),
        "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }) + "\n"


def read_holder(path: Path) -> dict | None:
    """Whatever the holder wrote about itself. Diagnostics only — the flock is
    the lock, this is just so a timeout can say WHO."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return json.loads(raw) if raw else None
    except Exception:
        return None


class _ExclusiveFallback:
    """O_EXCL lock for a platform with no fcntl. Strictly worse: it needs a
    staleness rule, and a staleness rule can be wrong in both directions. Only
    reached when `import fcntl` failed."""

    def __init__(self, path: Path):
        self.path = path.with_suffix(".excl")
        self.fd = -1

    def acquire(self, deadline: float) -> None:
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
                return
            except FileExistsError:
                if self._steal_if_dead():
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"{self.path} held")
                time.sleep(0.05)

    def _steal_if_dead(self) -> bool:
        info = read_holder(self.path)
        pid = (info or {}).get("pid")
        if not isinstance(pid, int):
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            with contextlib.suppress(OSError):
                os.unlink(self.path)
            return True
        except OSError:
            return False
        return False

    def write(self, note: str) -> None:
        with contextlib.suppress(OSError):
            os.write(self.fd, note.encode())

    def release(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self.fd)
        with contextlib.suppress(OSError):
            os.unlink(self.path)


@contextlib.contextmanager
def held(
    root: str | os.PathLike[str] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    label: str = "",
):
    """Hold the worktree's driver lock for the body.

    Yields True if this call actually took the lock, False if an ancestor
    already holds it (re-entrant no-op). Raises LockTimeout if it could not be
    taken inside `timeout`.
    """
    path = lock_path(root)
    if os.environ.get(ENV_HELD) == str(path):
        yield False  # already inside the critical section
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    note = _holder_note(label)

    if fcntl is None:  # pragma: no cover - Windows
        fb = _ExclusiveFallback(path)
        fb.acquire(deadline)
        fb.write(note)
        os.environ[ENV_HELD] = str(path)
        try:
            yield True
        finally:
            os.environ.pop(ENV_HELD, None)
            fb.release()
        return

    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    who = read_holder(path) or {}
                    raise LockTimeout(
                        f"{path} held by pid={who.get('pid', '?')} "
                        f"label={who.get('label', '?')} since={who.get('since', '?')}",
                    )
                time.sleep(0.05)
        with contextlib.suppress(OSError):
            os.ftruncate(fd, 0)
            os.write(fd, note.encode())
        os.environ[ENV_HELD] = str(path)
        try:
            yield True
        finally:
            os.environ.pop(ENV_HELD, None)
            # Blank the note so `status` does not name a dead holder. The lock
            # itself is released by the close below — and by the kernel if we
            # never get here at all.
            with contextlib.suppress(OSError):
                os.ftruncate(fd, 0)
    finally:
        os.close(fd)


def _default_label(command: list[str]) -> str:
    """A label a human can read in `status`. The raw argv[0] is an absolute
    interpreter path, which names nothing useful when you are trying to find
    out who is holding the lock."""
    parts = [os.path.basename(c) for c in command[:2]]
    return " ".join(parts) or "?"


def _cmd_run(args: argparse.Namespace) -> int:
    if not args.command:
        print("driver_lock.py run: nothing after --", file=sys.stderr)
        return 2
    try:
        label = args.label or _default_label(args.command)
        with held(args.root, timeout=args.timeout, label=label):
            return subprocess.run(args.command).returncode
    except LockTimeout as exc:
        print(
            f"LOCK BUSY after {args.timeout:g}s: {exc}\n"
            "Another loop driver is staging or committing in this worktree. "
            "Wait for it, or raise --timeout. Do NOT bypass: the git index is "
            "shared, and two writers is how a commit steals another agent's "
            "staged files.",
            file=sys.stderr,
        )
        return EX_TEMPFAIL


def _cmd_status(args: argparse.Namespace) -> int:
    path = lock_path(args.root)
    if not path.exists():
        print(f"FREE  {path} (never taken)")
        return 0
    if fcntl is None:  # pragma: no cover - Windows
        print(f"UNKNOWN  {path} (no fcntl on this platform)")
        return 0
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            print(f"FREE  {path}")
            return 0
        except OSError:
            who = read_holder(path) or {}
            print(
                f"HELD  {path}  pid={who.get('pid', '?')} "
                f"label={who.get('label', '?')} since={who.get('since', '?')}",
            )
            return 1
    finally:
        os.close(fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a command holding the lock")
    run.add_argument("--root", default=None)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--label", default="")
    run.add_argument("command", nargs=argparse.REMAINDER)

    st = sub.add_parser("status", help="say whether the lock is held, and by whom")
    st.add_argument("--root", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "run":
        # argparse.REMAINDER keeps the "--" separator; drop it.
        if args.command and args.command[0] == "--":
            args.command = args.command[1:]
        return _cmd_run(args)
    return _cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
