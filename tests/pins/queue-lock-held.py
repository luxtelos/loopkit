#!/usr/bin/env python3
"""queue-lock-held.py — the queue's read-modify-write really holds the lock,
at the location the code lives at NOW.

Why this pin exists, and why it is not redundant with test_driver_lock.py.

`triage_state.write_lock` is FAIL-OPEN by design: if the `driver_lock` helper
cannot be loaded it returns `contextlib.nullcontext()`, on the reasoning that an
unlocked write beats a triage command that refuses to run. That is a defensible
choice for a missing helper. It is a trap for a MOVED one, because the failure
is silent and the return value still looks like a working lock.

The original branch resolved the helper as `Path(__file__).parent /
"driver_lock.py"`, which was correct while `triage_state.py` lived in
`scripts/`. M2 part A moved the module into `loopkit_core/`, where that same
expression names a file that does not exist. Carried across unchanged, every
`upsert`, `update`, `ensure-schema` and inbox-bridge `--apply` would have taken
a no-op lock and reported success — the guard would have moved into a file
nobody checks, which is exactly what a relocated guard without a pin is.

So this pin asserts the thing the unit tests cannot see: that the context
manager handed back is a REAL flock and not the fail-open stub, and that it is
mutually exclusive in the way the callers depend on.

  1. `write_lock` does not return `nullcontext` — the fail-open path is not
     being taken silently.
  2. Holding it creates `<root>/.loopkit/driver.lock`, the per-worktree path.
  3. It genuinely excludes a FOREIGN process while held, and lets one in once
     released — while a DESCENDANT still re-enters, which is what keeps
     `loop-commit.sh` from deadlocking on its own nested `triage_state` calls.
  4. The inbox bridge lands on the same lock as the CLI, so `--apply` and a
     concurrent `upsert` are in one critical section rather than two.

To see it red: in `loopkit_core/triage_state.py`, make `_driver_lock_path()`
return `None` — the exact shape a wrong path degrades to. Checks 1, 2, 3 and
the bridge pair all fail. Done below, on this code, before it was committed.

usage: python3 tests/pins/queue-lock-held.py
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "loopkit"
sys.path.insert(0, str(PLUGIN))

from loopkit_core import inbox_to_triage as itt  # noqa: E402
from loopkit_core import triage_state as ts  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    print("queue-lock-held.py")

    # The helper must be found at all. This is the check that the relocation
    # would have failed.
    path = ts._driver_lock_path()
    check("driver_lock.py is located", path is not None and path.is_file(),
          str(path))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "state").mkdir()
        state = root / "state" / "triage.md"
        state.write_text("")

        cm = ts.write_lock(state)

        # 1. Not the fail-open stub.
        check("write_lock is a real lock, not nullcontext()",
              not isinstance(cm, contextlib.nullcontext),
              type(cm).__name__)

        lockfile = root / ".loopkit" / "driver.lock"

        # 2 + 3. It is held, at the per-worktree path, and it excludes.
        with cm:
            check("holding it creates <root>/.loopkit/driver.lock", lockfile.exists())
            check("a FOREIGN process cannot acquire while held",
                  not _acquires(root, foreign=True))
            # The other direction, and the reason the check above must scrub
            # the environment: a DESCENDANT of the holder is deliberately
            # re-entrant, so `loop-commit.sh` can call `triage_state upsert`
            # inside its own critical section without deadlocking itself.
            check("a DESCENDANT re-enters rather than deadlocking",
                  _acquires(root, foreign=False))

        check("a foreign process CAN acquire once released",
              _acquires(root, foreign=True))

        # 4. The bridge's critical section is the SAME lock as the CLI's.
        # Identity is not the test: `inbox_to_triage` loads `triage_state` by
        # path under its own module name, so it holds a distinct module object
        # of the same source. What must agree is the lock they land on.
        check("inbox bridge resolves the same driver_lock helper",
              getattr(itt.ts, "_driver_lock_path", lambda: None)() == path)
        bridge_cm = itt.ts.write_lock(state)
        check("inbox bridge gets a real lock too",
              not isinstance(bridge_cm, contextlib.nullcontext))

    print("PASS" if not failures else f"FAIL ({len(failures)})")
    return 1 if failures else 0


def _acquires(root: Path, *, foreign: bool) -> bool:
    """Try to take the lock from a SEPARATE process.

    Separate process on purpose: flock is advisory per open file description,
    and a same-process attempt would re-enter rather than contend, which would
    make this check pass no matter what.

    `foreign` decides whether that process is a stranger or a descendant.
    `driver_lock.held` marks its critical section with the LOOPKIT_DRIVER_LOCK
    environment variable, which children inherit, so an unscrubbed subprocess
    is treated as already inside the section and re-enters. That is intended —
    it is what stops `loop-commit.sh` deadlocking on its own nested calls — but
    it also means a contention check that forgets to scrub the variable tests
    nothing and passes always. Scrubbing it is what makes the process foreign.
    """
    env = dict(os.environ)
    if foreign:
        env.pop("LOOPKIT_DRIVER_LOCK", None)
    code = textwrap.dedent(f"""
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "driver_lock", {str(ts._driver_lock_path())!r})
        m = importlib.util.module_from_spec(spec)
        sys.modules["driver_lock"] = m
        spec.loader.exec_module(m)
        try:
            with m.held({str(root)!r}, label="pin", timeout=0.5):
                print("ACQUIRED")
        except SystemExit:
            print("BLOCKED")
        except Exception as exc:
            print("BLOCKED")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60, env=env)
    return "ACQUIRED" in out.stdout


if __name__ == "__main__":
    raise SystemExit(main())
