#!/usr/bin/env python3
"""m2-prove-red.py — prove the M2 provider/store pins can actually FAIL.

A pin that cannot go red is decoration. `provider.py --selftest` and
`store.py --selftest` printing ALL PASS says nothing on its own about whether
they would ever print anything else — the same trap the model-invariants pin
records for the FizzBee driver ("PASSED says nothing about whether the checker
would ever say FAILED").

So this applies, one at a time, the exact mutation that reintroduces each
defect found in the 2026-09-07 hostile review, requires the relevant selftest
to exit non-zero, and restores the file. Every mutation names its target
string; if the target is no longer in the file the run FAILS rather than
passing quietly, so a module that drifts away from its own pin is caught
instead of silently losing cover.

SAFETY, because this script WRITES TO SOURCE FILES

Two of these running at once corrupt each other: the second captures its
"originals" while the first has the file mutated, and then restores that
mutated text as if it were pristine. Observed for real on 2026-09-07 while
timing this script beside a suite run, so it is guarded rather than
documented:

  * an exclusive `flock`, anchored to the CHECKOUT (`<repo>/tmp/`, gitignored)
    and NOT to `TMPDIR`, means a second run REFUSES instead of interleaving.
    It was TMPDIR-scoped until the third review pointed out that this repo
    tells people to change TMPDIR on macOS, so the guard's first layer was
    bypassed by following the instructions;
  * a pre-flight check refuses to start if any mutation's replacement text is
    already present, which is what a previous killed run would leave behind;
  * SIGINT and SIGTERM restore before exiting.

SIGKILL still cannot be caught -- nothing can -- but the pre-flight check
turns the wreckage into a loud refusal on the next run instead of a mutated
file that could be committed. If you ever see that refusal, restore with
`git checkout -- plugins/loopkit/loopkit_core/provider.py
plugins/loopkit/loopkit_core/store.py`.

usage: python3 tests/pins/m2-prove-red.py [repo-root]
exit 0 == every mutation went red and every file was restored.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
# `tempfile` is deliberately NOT imported any more: the run lock is anchored
# to the checkout, not to TMPDIR. See lock_path().

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.abspath(
    os.path.join(HERE, "..", "..")
)
PKG = os.path.join(ROOT, "plugins", "loopkit")
PROVIDER = os.path.join(PKG, "loopkit_core", "provider.py")
STORE = os.path.join(PKG, "loopkit_core", "store.py")
REDACT = os.path.join(PKG, "loopkit_core", "redact.py")
NETHTTP = os.path.join(PKG, "loopkit_core", "nethttp.py")

# The two redirect mutations below both need the SAME opener edit, so it is
# named once here rather than retyped: two copies of a mutation's target
# string is how one of them silently stops matching after a reformat.
SAFE_OPENER = (
    "    return urllib.request.build_opener(\n"
    "        urllib.request.ProxyHandler({}), _RefuseRedirect\n"
    "    )"
)
VULN_OPENER = "    return urllib.request.build_opener(urllib.request.ProxyHandler({}))"

# (id, module, why it matters, [(file, old, new), ...])
MUTATIONS = [
    (
        "MUT-R1",
        "provider",
        "normalise_result zero-fills an empty usage envelope. Green across all "
        "16 original pins, because none of them entered that branch with a "
        "mapping.",
        [
            (
                PROVIDER,
                "            usage = None\n        elif len(present) == 1:",
                '            usage = {"input_tokens": 0, "output_tokens": 0}\n'
                "        elif len(present) == 1:",
            )
        ],
    ),
    (
        "MUT-R2",
        "provider",
        "a PARTIAL upstream usage survives _coerce_usage, so one real count "
        "travels beside a fabricated one.",
        # BOTH edits are needed to reintroduce the defect, and that is the
        # finding: reverting only the `or` back to `and` leaves the module
        # correct, because the coercion loop's `int(None)` -> TypeError -> None
        # catches the partial on its own. The load-bearing line is the loop's
        # refusal to SKIP a missing count, not the early return. A one-line
        # mutation here would have gone green and been read as "unpinned".
        [
            (
                PROVIDER,
                "    if got_in is None or got_out is None:\n        return None",
                "    if got_in is None and got_out is None:\n        return None",
            ),
            (
                PROVIDER,
                '    for name, value in (("input_tokens", got_in), ("output_tokens", got_out)):\n'
                "        if isinstance(value, bool):",
                '    for name, value in (("input_tokens", got_in), ("output_tokens", got_out)):\n'
                "        if value is None:\n"
                "            continue\n"
                "        if isinstance(value, bool):",
            ),
        ],
    ),
    (
        "MUT-L1",
        "provider",
        "_safe_url keeps the URL PATH, so a credential in the path is echoed "
        "back verbatim into every transport error. (Leak 3 — not in the "
        "reviewer's two.)",
        [
            (
                PROVIDER,
                '    return "%s://%s" % (scheme or "?", host or "?")',
                '    return "%s://%s%s" % (scheme or "?", host or "?", '
                "urllib.parse.urlsplit(url).path)",
            )
        ],
    ),
    (
        "MUT-L2",
        "provider",
        "the bare ValueError from Request() escapes unwrapped and unscrubbed, "
        "quoting the whole URL with its userinfo. (Reviewer's leak 1.)",
        [
            (
                PROVIDER,
                "        except ValueError as exc:\n"
                "            # `Request(url)` on a URL with no usable scheme raises this and",
                "        except ValueError as exc:\n"
                "            raise\n"
                "            # `Request(url)` on a URL with no usable scheme raises this and",
            )
        ],
    ),
    (
        "MUT-S1",
        "store",
        "S3Store.host is parts.netloc again, so the operator's password lands "
        "in every error, in the Host: header, and in the SigV4 canonical "
        "request. (Reviewer's leak 2.)",
        [
            (STORE, "        if userinfo:", "        if False:"),
            (
                STORE,
                '        self.host = "%s:%d" % (hostname, port) if port else hostname',
                "        self.host = parts.netloc",
            ),
            (
                STORE,
                "        self._url_secrets = list(userinfo)",
                "        self._url_secrets = []",
            ),
        ],
    ),
    (
        "MUT-S2",
        "store",
        "the runner lock is authoritative from the ROW alone again, so a "
        "SIGKILLed holder locks the store forever and criterion 13 cannot "
        "resume without a human.",
        [
            (
                STORE,
                "                    if flock_state == \"granted\" and stale_but_recoverable:",
                "                    if False and stale_but_recoverable:",
            )
        ],
    ),
    # ---- added after the THIRD review, 2026-09-07 -------------------------
    # Every one of these mutates something that was UNPINNED when the third
    # reviewer found it. `MUT-S3` is the root cause of that review: `_scrub`
    # could be replaced with `return text` and all 27 store pins stayed green,
    # so the one function whose job is hiding secrets was the one function
    # nothing tested -- and that is why a leak survived two rounds of fixes.
    (
        "MUT-S3",
        "store",
        "S3Store._scrub is a no-op. The store's ONLY redaction function, "
        "deletable with every pin still green before S9 existed.",
        [
            (
                STORE,
                '        redactor = getattr(self, "_redact", None)\n'
                "        if redactor is None:  # pragma: no cover - only if __init__ raised early\n"
                "            return text\n"
                "        return redactor.scrub(text)",
                # The marker comment is not decoration: the pre-flight check
                # looks for each replacement string in the pristine file to
                # spot a killed run, and a bare `return text` occurs all over
                # store.py legitimately. The comment makes the mutation's
                # fingerprint unique without changing what it does.
                "        return text  # MUT-S3: _scrub neutered",
            )
        ],
    ),
    (
        "MUT-S4",
        "store",
        "S3Store._request's catch-all interpolates the exception again, so a "
        "ValueError from putheader hands over the whole signed Authorization "
        "header. (The third reviewer's leak 1.)",
        [
            (
                STORE,
                '                self._scrub(\n'
                '                    "%s://%s failed: %s (message withheld: this is the "\n'
                '                    "catch-all, and an unknown exception\'s text has already "\n'
                '                    "carried a signed Authorization header once)"\n'
                "                    % (self.scheme, self.host, type(exc).__name__)\n"
                "                )",
                '                self._scrub("%s://%s failed: %s" % (self.scheme, self.host, exc))',
            )
        ],
    ),
    (
        "MUT-S5",
        "store",
        "S3Store accepts any URL scheme again, so a file:// endpoint makes "
        "urllib's FileHandler return local bytes as object content.",
        [
            (
                STORE,
                '        if self.scheme not in ("http", "https"):',
                "        if False:",
            )
        ],
    ),
    (
        "MUT-S6",
        "store",
        "the runner lock consults the ROW before the kernel again, so a second "
        "process reusing the same runner_id is admitted past a LIVE holder and "
        "writes.",
        [
            (
                STORE,
                '                if flock_state == "held":',
                "                if False:",
            )
        ],
    ),
    (
        "MUT-L3",
        "provider",
        "providers accept any base URL scheme again, so a file:// base URL "
        "makes complete() read a local file and return it as model output.",
        [
            (
                PROVIDER,
                '        if scheme not in ("http", "https"):',
                "        if False:",
            )
        ],
    ),
    (
        "MUT-L4",
        "provider",
        "_post_json's catch-all interpolates the exception again -- the same "
        "shape as MUT-S4, in the file that got it right first.",
        [
            (
                PROVIDER,
                "                self._redact.scrub(\n"
                '                    "%s %s failed: %s (message withheld: catch-all)"\n'
                "                    % (where, label, type(exc).__name__)\n"
                "                )",
                '                self._redact.scrub("%s %s failed: %s" % (where, label, exc))',
            )
        ],
    ),
    (
        "MUT-RD1",
        "store",
        "the redactor's STRUCTURAL rule is gone, so it matches only the whole "
        "secret again and any repr-escaping walks straight through it.",
        [
            (
                REDACT,
                "        needles.update(run for run in invariant_runs(secret) if len(run) >= _MIN_RUN)",
                "        needles.update(())",
            )
        ],
    ),
    (
        "MUT-RD2",
        "provider",
        "Redactor.scrub is a no-op, proving the PROVIDER's pins would notice "
        "too -- both callers share one redactor, so both must hold it.",
        [
            (
                REDACT,
                "        text = text if isinstance(text, str) else str(text)\n"
                "        for needle in self._needles:\n"
                "            if needle in text:\n"
                "                text = text.replace(needle, MASK)\n"
                "        return text",
                "        return text if isinstance(text, str) else str(text)",
            )
        ],
    ),
    # --- the fourth review's blocker: a credential carried off the configured
    # endpoint by an HTTP redirect. Two INDEPENDENT defences guard it (the
    # opener refuses every 3xx; the credential is attached with
    # `add_unredirected_header`), so removing either one alone leaves the
    # system safe -- which is the point of defence in depth and also means a
    # one-line mutation cannot reach the disclosure. Same shape as MUT-R2:
    # the pair is the mutation, and that IS the finding. RH1/RH4 remove one
    # defence and prove the pins that guard the redirect itself; RH2/RH3
    # remove both and reproduce the reviewer's live-sink leak exactly.
    (
        "MUT-RH1",
        "provider",
        "the opener keeps urllib's HTTPRedirectHandler, so a 301 is FOLLOWED "
        "and a stranger's body is returned as model output. (Reviewer's leak "
        "5, first half.)",
        [(NETHTTP, SAFE_OPENER, VULN_OPENER)],
    ),
    (
        "MUT-RH2",
        "provider",
        "BOTH redirect defences removed: the API key itself reaches a second "
        "host on 301/302/303, which is the reviewer's live-sink finding "
        "verbatim. P9c2 pins WHICH HEADER and stays green throughout; only a "
        "pin that asserts the DESTINATION catches this.",
        [
            (NETHTTP, SAFE_OPENER, VULN_OPENER),
            (
                PROVIDER,
                "                request.add_unredirected_header(header, value)",
                "                request.add_header(header, value)",
            ),
        ],
    ),
    (
        "MUT-RH5",
        "provider",
        "nethttp.safe_origin keeps the URL PATH, so a refusal message echoes "
        "the redirect target's path -- the MUT-L1 defect, in the copy MUT-L1 "
        "does not reach. P12 is what stops the two renderers drifting.",
        [
            (
                NETHTTP,
                '    return "%s://%s" % (scheme or "?", host or "?")',
                '    return "%s://%s%s" % (scheme or "?", host or "?", '
                "urllib.parse.urlsplit(url).path)",
            )
        ],
    ),
    (
        "MUT-RH3",
        "store",
        "BOTH redirect defences removed: the signed Authorization header, "
        "Credential=<access key id> included, reaches a second host on ALL "
        "FIVE redirect codes because list/get are GETs. S9d pins WHICH HEADER "
        "and stays green.",
        [
            (NETHTTP, SAFE_OPENER, VULN_OPENER),
            (
                STORE,
                "            request.add_unredirected_header(name, value)",
                "            request.add_header(name, value)",
            ),
        ],
    ),
    (
        "MUT-RH4",
        "store",
        "the opener keeps HTTPRedirectHandler, so a redirect is followed: a "
        "stranger's listing is returned as this bucket's contents, AND `ftp://` "
        "on a Location reaches the FTP handler -- reopening the door the "
        "__init__ scheme guard closed, because that guard only ever saw the "
        "URL the operator typed.",
        [(NETHTTP, SAFE_OPENER, VULN_OPENER)],
    ),
]


def run_selftest(module: str) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = PKG + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "loopkit_core." + module, "--selftest"],
        cwd=PKG,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode


def lock_path() -> str:
    """Where the run lock lives: beside the SOURCE FILES this script mutates.

    It used to be `tempfile.gettempdir()`, which is TMPDIR-scoped -- and this
    repository's documented macOS workaround is to SET TMPDIR, so two runs
    following the instructions did not exclude each other at all and only the
    pre-flight check stood between them and a corrupted tree (2026-09-07 third
    review, finding 4). The identity that matters is the CHECKOUT whose files
    get mutated, and ROOT is exactly that: it cannot differ between two runs of
    the same checkout, and it correctly does NOT serialise two runs in
    different worktrees, which mutate different files.

    `tmp/` at the repo root is already in .gitignore (`/tmp/`), so the lock
    leaves `git status` clean -- which the harness's own tree-clean check
    depends on.
    """
    return os.path.join(ROOT, "tmp", "m2-prove-red.lock")


# The ONE way to run this script without a lock, and it has to be typed.
# See `acquire_lock` for why there is exactly one.
UNLOCKED_ENV = "M2_PROVE_RED_ALLOW_UNLOCKED"


def _no_lock(why: str, path: str):
    """One answer for every reason the lock is unavailable: refuse.

    Round 4 review, finding 2: this script had TWO ways to end up without a
    lock and gave them OPPOSITE answers -- an uncreatable lock file was a
    `SystemExit(1)`, while a platform with no `fcntl` printed a warning and
    ran on. No reason was stated for the difference, and there is not one.
    The hazard is identical in both cases and is the hazard the lock exists
    for: two concurrent runs rewrite the SAME source files and restore each
    other's mutated text as if it were pristine, which leaves a corrupted
    working tree that the tree-clean check then reports as clean. Whether the
    lock is missing because the platform has no flock or because the path is
    unwritable changes nothing about that. Loud is not the same as safe, and a
    lock that is sometimes advisory is a lock nobody can reason about.

    So: refuse in both, and offer ONE deliberate escape hatch covering both,
    which a human has to type. An env var is a decision with a name on it; a
    printed warning in a 14-mutation run is a line nobody reads.
    """
    if os.environ.get(UNLOCKED_ENV) == "1":
        print(
            "  UNLOCKED BY REQUEST (%s=1): %s\n"
            "  Two concurrent runs will corrupt each other's source files.\n"
            "  The pre-flight check is the only guard left." % (UNLOCKED_ENV, why)
        )
        return None
    print(
        "REFUSED: %s (lock path: %s).\n"
        "  This script rewrites source files in place and will not do it\n"
        "  without a lock: two runs restore each other's mutated text as if\n"
        "  it were pristine, and the tree-clean check then passes on a\n"
        "  corrupted tree.\n"
        "  If you accept that risk, re-run with %s=1." % (why, path, UNLOCKED_ENV)
    )
    raise SystemExit(1)


def acquire_lock():
    """Refuse to run beside another instance. Returns the held fd, or None.

    Every path out of here is a REFUSAL except a lock actually held (or the
    explicit `M2_PROVE_RED_ALLOW_UNLOCKED=1` opt-out). `None` is returned only
    on that opt-out, never as a silent fallback.
    """
    path = lock_path()
    try:
        import fcntl
    except ImportError:  # pragma: no cover - not POSIX
        return _no_lock("this platform has no fcntl, so no lock can be taken", path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        return _no_lock(
            "cannot create the run lock (%s)" % exc.strerror, path
        )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        print(
            "REFUSED: another m2-prove-red run holds %s.\n"
            "  Two runs mutate the same source files and restore each other's\n"
            "  mutated text as if it were pristine. Wait for the other run."
            % path
        )
        raise SystemExit(1)
    return fd


def preflight(originals) -> list:
    """Refuse to start on a tree a previous run left mutated."""
    dirty = []
    for mut_id, _module, _why, edits in MUTATIONS:
        for path, _old, new in edits:
            if new in originals[path]:
                dirty.append(
                    "%s: replacement text for %s is ALREADY in the file -- a "
                    "previous run was killed mid-mutation. Restore with "
                    "`git checkout -- %s` before re-running."
                    % (mut_id, os.path.basename(path), os.path.relpath(path, ROOT))
                )
    return dirty


def main() -> int:
    lock_fd = acquire_lock()
    # Derived from MUTATIONS, not hardcoded: a mutation naming a file the
    # restore loop did not know about would leave that file mutated on disk.
    targets = sorted({path for _id, _m, _w, edits in MUTATIONS for path, _o, _n in edits})
    originals = {path: open(path).read() for path in targets}

    dirty = preflight(originals)
    if dirty:
        for line in dirty:
            print("  REFUSED  " + line)
        return 1

    def restore_and_die(signum, _frame):  # pragma: no cover - signal path
        for path, text in originals.items():
            with open(path, "w") as handle:
                handle.write(text)
        print("\n  interrupted (signal %d) -- source files restored" % signum)
        raise SystemExit(130)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, restore_and_die)
        except (ValueError, OSError):  # pragma: no cover - not main thread
            pass

    red = 0
    problems = []
    try:
        for mut_id, module, why, edits in MUTATIONS:
            texts = {path: originals[path] for path in originals}
            missing = False
            for path, old, new in edits:
                if old not in texts[path]:
                    problems.append(
                        "%s: target string is GONE from %s -- the module drifted "
                        "away from its own pin, so this mutation proves nothing "
                        "any more. Re-derive it.\n      missing: %r"
                        % (mut_id, os.path.basename(path), old[:80])
                    )
                    missing = True
                    break
                texts[path] = texts[path].replace(old, new, 1)
            if missing:
                continue
            for path, text in texts.items():
                with open(path, "w") as handle:
                    handle.write(text)
            rc = run_selftest(module)
            for path, text in originals.items():
                with open(path, "w") as handle:
                    handle.write(text)
            if rc == 0:
                problems.append(
                    "%s: STILL GREEN under mutation -- %s" % (mut_id, why)
                )
            else:
                red += 1
                print("  RED, as required  %-8s %s" % (mut_id, why.split(". ")[0]))
    finally:
        # Restore unconditionally, including on an interrupt.
        for path, text in originals.items():
            with open(path, "w") as handle:
                handle.write(text)

    for line in problems:
        print("  NOT RED  " + line)
    print("")
    print("%d of %d mutations went red" % (red, len(MUTATIONS)))
    if problems:
        return 1
    # The restore must be real, or the next run tests a mutated file.
    for path, text in originals.items():
        if open(path).read() != text:
            print("  RESTORE FAILED: %s" % path)
            return 1
    if lock_fd is not None:
        os.close(lock_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
