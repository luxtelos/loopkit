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

MUTATE A COPY, NEVER THE TRACKED TREE.

This script used to write the mutations straight into
plugins/loopkit/loopkit_core/*.py and restore them a moment later. That is the
exact shape that put a mutated stop_gate.sh into commit 62943e3 on 2026-09-07:
tests/pins/stop-gate-prove-red.sh edited a tracked file in place and restored
it, and a concurrent `git add` landed inside the window. This script's window
is WIDER than that one -- a selftest subprocess per mutation, nineteen of them,
about three minutes in total with a tracked source file wrong on disk for most
of it. The tracked-tree watcher added to tests/selftest.sh caught this script
red-handed on its first run: "a harness altered the tracked tree DURING the run
and restored it -- M plugins/loopkit/loopkit_core/provider.py".

A restore-afterwards is not a guarantee, it is a race with anything else that
reads the file. The old docstring's own recovery instructions ("restore with
`git checkout -- ...provider.py store.py`") were the tell: a harness that needs
a recovery command is one that can leave the tree wrong.

So nothing under the repo is written now. A scratch skeleton gets a copy of
plugins/ and every mutation happens there, exactly as stop-gate-prove-red.sh
and targets-prove-red.sh do it. run_selftest already ran the module with
cwd=PKG and PYTHONPATH=PKG, so pointing PKG at the copy is all it takes for the
mutations to keep biting -- and __pycache__ now lands in the scratch dir
instead of the tracked one. The tracked files are checksummed before and after
and the run FAILS if they moved, which is the assertion the two sibling scripts
carry and the only thing standing between a future edit and another mutated
commit.

The run lock this script used to take is GONE with the hazard it guarded. It
existed because two concurrent runs mutated the SAME source files and restored
each other's mutated text as if it were pristine; two runs now mutate two
private scratch directories and cannot see each other at all. Keeping it would
have meant a refusal message that describes a race that can no longer happen,
and a second run of the suite blocked for no reason. The pre-flight check
stays, and now means something stronger: replacement text found in the TRACKED
file is no longer "a previous run was killed mid-mutation" but "these sources
are wrong in git", which is worth refusing over.

usage: python3 tests/pins/m2-prove-red.py [repo-root]
exit 0 == every mutation went red and every file was restored.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.abspath(
    os.path.join(HERE, "..", "..")
)

# The tracked package. READ ONLY, from here to the end of the file -- the only
# thing that ever touches these paths is a checksum.
SRC_PKG = os.path.join(ROOT, "plugins", "loopkit")

# The scratch package. Everything below mutates THIS one. The directory is
# created at import so that PKG is a real path before MUTATIONS is built, and
# the copy itself is made in main(); `mkdtemp` mode 0700 means no other user
# can plant a file in the tree a selftest is about to import. `atexit` is the
# Python equivalent of the `trap ... EXIT` the two sibling prove-red scripts
# use; the signal handlers below raise SystemExit so it fires on Ctrl-C too.
SKEL = tempfile.mkdtemp(prefix="m2-prove-red.")
atexit.register(shutil.rmtree, SKEL, True)
PKG = os.path.join(SKEL, "plugins", "loopkit")
PROVIDER = os.path.join(PKG, "loopkit_core", "provider.py")
STORE = os.path.join(PKG, "loopkit_core", "store.py")
REDACT = os.path.join(PKG, "loopkit_core", "redact.py")
NETHTTP = os.path.join(PKG, "loopkit_core", "nethttp.py")


def tracked_counterpart(scratch_path: str) -> str:
    """The tracked file a scratch path is a copy of."""
    return os.path.join(SRC_PKG, os.path.relpath(scratch_path, PKG))


def sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()

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


def preflight(originals) -> list:
    """Refuse to start when a mutation's replacement text is already there.

    This used to mean "a previous run of this script was killed mid-mutation",
    which was the only way it could happen while the script wrote to the
    tracked tree. It cannot happen that way any more -- `originals` is read
    from a scratch copy made this second from git-tracked content -- so a hit
    here now says the TRACKED source carries the defect the mutation exists to
    reintroduce. That is a louder finding than the one it was written for, and
    still the right answer: refuse, and say which file.
    """
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
    # Stage the scratch copy. The WHOLE plugin, not just the four files that
    # get mutated: the modules import their siblings, and run_selftest runs
    # them with cwd=PKG. A copy, never a symlink -- an import through a
    # symlinked package writes __pycache__ back into the tracked tree, which
    # is the thing this rewrite exists to stop.
    if not os.path.isdir(SRC_PKG):
        print("no plugin to copy at %s" % SRC_PKG)
        return 2
    os.makedirs(os.path.dirname(PKG), exist_ok=True)
    shutil.copytree(SRC_PKG, PKG)

    # Derived from MUTATIONS, not hardcoded: a mutation naming a file the
    # restore loop did not know about would leave that file mutated on disk.
    targets = sorted({path for _id, _m, _w, edits in MUTATIONS for path, _o, _n in edits})
    for path in targets:
        if not os.path.isfile(path):
            print("mutation target missing from the staged copy: %s" % path)
            return 2
    # The assertion the two sibling prove-red scripts carry: the tracked files
    # are not written to. Cheap, and the only thing standing between a future
    # edit of this script and another mutated commit.
    tracked = {path: tracked_counterpart(path) for path in targets}
    tracked_sums = {src: sha256(src) for src in tracked.values()}
    originals = {path: open(path).read() for path in targets}

    dirty = preflight(originals)
    if dirty:
        for line in dirty:
            print("  REFUSED  " + line)
        return 1

    def restore_and_die(signum, _frame):  # pragma: no cover - signal path
        # Nothing tracked is ever wrong on disk now, so there is nothing to
        # put back. SystemExit is still raised rather than os._exit, because
        # that is what runs the atexit hook that removes the scratch tree.
        print("\n  interrupted (signal %d) -- scratch copy discarded" % signum)
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
    # The rule this script now follows, asserted rather than trusted.
    moved = [src for src, want in tracked_sums.items() if sha256(src) != want]
    if moved:
        for src in moved:
            print("  NOT RED  THIS SCRIPT MODIFIED THE TRACKED %s. Restore it"
                  % os.path.relpath(src, ROOT))
            print("           from git before committing anything.")
        return 1
    print("  ok — the tracked loopkit_core sources were never written to")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
