#!/usr/bin/env python3
"""Prove the driver lock excludes, releases, and stops the 2026-09-07 incident.

The point of this file is that it contains a CONTROL: the first git test
reproduces the original failure with the lock out of the way, so the fixed case
is measured against a race that demonstrably still bites. A concurrency test
that has never seen the bug fail proves nothing.

usage: python3 scripts/test_driver_lock.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = str(HERE / "driver_lock.py")
COMMIT = str(HERE / "loop-commit.sh")
PY = sys.executable

fails = 0


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def fail(msg: str) -> None:
    global fails
    fails += 1
    print(f"  FAIL {msg}")


def check(cond: bool, msg: str) -> None:
    ok(msg) if cond else fail(msg)


def git(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True,
        env=env or os.environ.copy(),
    )


def new_repo(tmp: Path, name: str) -> Path:
    root = tmp / name
    (root / ".loopkit").mkdir(parents=True)
    git(root.parent, "init", "-q", name)
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    (root / "seed").write_text("seed\n")
    git(root, "add", "seed")
    git(root, "commit", "-q", "-m", "init")
    return root


def commit_files(root: Path, rev: str = "HEAD") -> set[str]:
    out = git(root, "show", "--name-only", "--format=", rev).stdout
    return {l.strip() for l in out.splitlines() if l.strip()}


def staged(root: Path) -> set[str]:
    out = git(root, "diff", "--cached", "--name-only").stdout
    return {l.strip() for l in out.splitlines() if l.strip()}


# --------------------------------------------------------------------------
print("== mutual exclusion")
with tempfile.TemporaryDirectory() as t:
    root = Path(t)
    (root / ".loopkit").mkdir()
    log = root / "order.log"
    # Each worker writes "in N", holds briefly, writes "out N". If the lock is
    # real, every "in" is immediately followed by its own "out".
    body = (
        "import sys,time\n"
        "p=sys.argv[1]; n=sys.argv[2]\n"
        "open(p,'a').write('in %s\\n'%n); time.sleep(0.08)\n"
        "open(p,'a').write('out %s\\n'%n)\n"
    )
    procs = [
        subprocess.Popen(
            [PY, LOCK, "run", "--root", str(root), "--timeout", "30",
             "--", PY, "-c", body, str(log), str(i)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for i in range(6)
    ]
    rcs = [p.wait() for p in procs]
    check(all(r == 0 for r in rcs), f"all 6 holders exited 0 ({rcs})")
    lines = log.read_text().split()
    pairs = list(zip(lines[0::4], lines[1::4], lines[2::4], lines[3::4]))
    interleaved = [p for p in pairs if p[0] != "in" or p[2] != "out" or p[1] != p[3]]
    check(not interleaved, f"6 concurrent holders never interleaved ({len(pairs)} sections)")

# --------------------------------------------------------------------------
print("== release on failure")
with tempfile.TemporaryDirectory() as t:
    root = Path(t)
    (root / ".loopkit").mkdir()

    r = subprocess.run([PY, LOCK, "run", "--root", str(root), "--", "false"],
                       capture_output=True, text=True)
    check(r.returncode == 1, "run propagates the command's exit code")

    # SIGKILL the held command: nothing gets to run a cleanup handler, so only
    # the kernel can release this. That is exactly why it is flock and not mkdir.
    r = subprocess.run(
        [PY, LOCK, "run", "--root", str(root), "--", PY, "-c",
         "import os,signal; os.kill(os.getpid(), signal.SIGKILL)"],
        capture_output=True, text=True,
    )
    check(r.returncode != 0, f"a SIGKILLed holder reports failure (rc={r.returncode})")
    t0 = time.monotonic()
    r = subprocess.run([PY, LOCK, "run", "--root", str(root), "--timeout", "2",
                        "--", "true"], capture_output=True, text=True)
    elapsed = time.monotonic() - t0
    # Two unrelated failures used to share one assertion and one message:
    #   check(rc == 0 and elapsed < 2, "... (no stale lock)")
    # A correct-but-slow run — a machine under enough load to blow a 2s budget —
    # printed "no stale lock", naming a defect that had not occurred and sending
    # the next reader to debug flock. Measured margin on an idle host: seven
    # samples of this exact call, worst 0.049s against the 2s budget, ~40x. So a
    # timing failure here really does mean the machine, and the message should
    # say so instead of blaming the lock. Ask the two questions separately.
    check(r.returncode == 0,
          f"the lock is free again after a SIGKILL — no stale lock (rc={r.returncode})")
    check(elapsed < 2,
          f"...and it was granted inside the 2s budget ({elapsed:.3f}s) — a failure "
          f"on THIS line is a loaded machine, not a stale lock")

    r = subprocess.run([PY, LOCK, "status", "--root", str(root)],
                       capture_output=True, text=True)
    check(r.returncode == 0 and "FREE" in r.stdout, f"status says FREE: {r.stdout.strip()}")

# --------------------------------------------------------------------------
print("== re-entrancy and timeout")
with tempfile.TemporaryDirectory() as t:
    root = Path(t)
    (root / ".loopkit").mkdir()
    r = subprocess.run(
        [PY, LOCK, "run", "--root", str(root), "--timeout", "3",
         "--", PY, LOCK, "run", "--root", str(root), "--timeout", "3", "--", "true"],
        capture_output=True, text=True, timeout=20,
    )
    check(r.returncode == 0, "a nested acquire is a no-op, not a deadlock")

    holder = subprocess.Popen(
        [PY, LOCK, "run", "--root", str(root), "--", PY, "-c", "import time; time.sleep(3)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.4)
    r = subprocess.run([PY, LOCK, "run", "--root", str(root), "--timeout", "0.3",
                        "--", "true"], capture_output=True, text=True)
    check(r.returncode == 75, f"a contended acquire times out with EX_TEMPFAIL (rc={r.returncode})")
    check("LOCK BUSY" in r.stderr and "pid=" in r.stderr,
          "the timeout names the holder rather than just failing")
    s = subprocess.run([PY, LOCK, "status", "--root", str(root)],
                       capture_output=True, text=True)
    check(s.returncode == 1 and "HELD" in s.stdout, f"status says HELD: {s.stdout.strip()}")
    holder.wait()

# --------------------------------------------------------------------------
print("== CONTROL: the 2026-09-07 incident still reproduces without the lock")
with tempfile.TemporaryDirectory() as t:
    root = new_repo(Path(t), "control")
    # Agent A stages exactly one file, by name, as every instruction file says.
    (root / "a-review.md").write_text("A's verdict\n")
    git(root, "add", "a-review.md")
    # Agent B, in the same worktree, stages its own file and commits. Two calls
    # against one index; B's commit publishes A's staged file too.
    (root / "b-work.md").write_text("B's work\n")
    git(root, "add", "b-work.md")
    git(root, "commit", "-q", "-m", "B's commit")
    swept = commit_files(root)
    check(swept == {"a-review.md", "b-work.md"},
          f"B's commit swept A's staged file in — the bug is real ({sorted(swept)})")
    r = git(root, "commit", "-q", "-m", "A's commit")
    check(r.returncode != 0, "A's own commit then finds nothing staged")

# --------------------------------------------------------------------------
print("== FIXED: loop-commit.sh under contention keeps commits separate")
with tempfile.TemporaryDirectory() as t:
    root = new_repo(Path(t), "fixed")
    (root / "a-review.md").write_text("A's verdict\n")
    (root / "b-work.md").write_text("B's work\n")

    def spawn(msg: str, path: str) -> subprocess.Popen:
        return subprocess.Popen(
            ["bash", COMMIT, "--timeout", "60", "-m", msg, "--", path],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    a = spawn("A: review verdict", "a-review.md")
    b = spawn("B: verification", "b-work.md")
    ao, ae = a.communicate()
    bo, be = b.communicate()
    check(a.returncode == 0 and b.returncode == 0,
          f"both concurrent commits succeeded (A={a.returncode} B={b.returncode})")
    check(commit_files(root, "HEAD") | commit_files(root, "HEAD~1")
          == {"a-review.md", "b-work.md"}, "both files landed")
    check(commit_files(root, "HEAD") != commit_files(root, "HEAD~1"),
          "they landed in two different commits")
    check(len(commit_files(root, "HEAD")) == 1 and len(commit_files(root, "HEAD~1")) == 1,
          "each commit carries exactly one file — no sweeping")
    check("committed" in ao and "a-review.md" in ao,
          "the wrapper reports what git actually committed, not what was intended")

# --------------------------------------------------------------------------
print("== loop-commit.sh leaves another process's staged work alone")
with tempfile.TemporaryDirectory() as t:
    root = new_repo(Path(t), "pathspec")
    (root / "theirs.md").write_text("someone else's\n")
    git(root, "add", "theirs.md")          # another agent, mid-flight
    (root / "mine.md").write_text("mine\n")
    r = subprocess.run(["bash", COMMIT, "-m", "mine only", "--", "mine.md"],
                       cwd=root, capture_output=True, text=True)
    check(r.returncode == 0, f"commit succeeded ({r.stderr.strip()})")
    check(commit_files(root) == {"mine.md"}, f"committed only mine ({commit_files(root)})")
    check(staged(root) == {"theirs.md"}, f"their file is still staged ({staged(root)})")

# --------------------------------------------------------------------------
# The other shared thing is state/triage.md, and every mutating command there is
# parse-then-write. Same shape as the git case, so it gets the same treatment: a
# CONTROL that loses a row without the lock, then the locked run.
print("== CONTROL: the queue loses a row without the lock, keeps both with it")
WORKER = """
import contextlib, importlib.util, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ts", sys.argv[1])
ts = importlib.util.module_from_spec(spec)
sys.modules["ts"] = ts        # @dataclass resolves the class's module through
spec.loader.exec_module(ts)   # sys.modules; without this, exec_module raises
state, src, locked = Path(sys.argv[2]), sys.argv[3], sys.argv[4] == "lock"
with (ts.write_lock(state) if locked else contextlib.nullcontext()):
    table = ts.parse_table(state)
    time.sleep(0.3)           # the window the lock closes
    table.rows.append({"finding": src, "source": src, "priority": "low",
                       "spec": "", "status": "new"})
    ts.write_table(state, table)
"""
TS = str(HERE / "triage_state.py")
with tempfile.TemporaryDirectory() as t:
    root = Path(t)
    (root / ".loopkit").mkdir()
    for mode, want in (("nolock", 1), ("lock", 2)):
        state = root / f"{mode}.md"
        subprocess.run([PY, TS, "ensure-schema", "--state", str(state)],
                       capture_output=True, cwd=root)
        ps = [subprocess.Popen([PY, "-c", WORKER, TS, str(state), f"{who}-{mode}", mode],
                               cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
              for who in ("A", "B")]
        errs = [p.communicate()[1] for p in ps]
        check(not any(e.strip() for e in errs),
              f"{mode}: both writers ran clean" + ("" if not any(errs) else f" ({errs})"))
        out = subprocess.run([PY, TS, "list", "--state", str(state)],
                             capture_output=True, text=True, cwd=root).stdout
        # count ROWS, not occurrences: each row carries the marker twice, once
        # as the finding and once as the source.
        got = len([l for l in out.splitlines() if f"-{mode}\t" in l])
        if mode == "nolock":
            check(got == want,
                  f"unlocked: {got}/2 rows survived — the queue race is real too")
        else:
            check(got == want, f"locked: {got}/2 rows survived")

print("== loop-commit.sh refuses the shapes that caused this")
with tempfile.TemporaryDirectory() as t:
    root = new_repo(Path(t), "usage")
    for label, args in [
        ("no paths after --", ["-m", "x", "--"]),
        ("no -- separator", ["-m", "x", "mine.md"]),
        ("no message", ["--", "mine.md"]),
        # `-A` after `--` is a pathspec, not a flag: there is no way to spell
        # "commit everything" through this wrapper.
        ("-A is a path, not a flag", ["-m", "x", "--", "-A"]),
    ]:
        r = subprocess.run(["bash", COMMIT, *args], cwd=root, capture_output=True, text=True)
        check(r.returncode != 0, f"rejected: {label} (rc={r.returncode})")

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
