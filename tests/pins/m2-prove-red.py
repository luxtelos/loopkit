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

usage: python3 tests/pins/m2-prove-red.py [repo-root]
exit 0 == every mutation went red and every file was restored.
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.abspath(
    os.path.join(HERE, "..", "..")
)
PKG = os.path.join(ROOT, "plugins", "loopkit")
PROVIDER = os.path.join(PKG, "loopkit_core", "provider.py")
STORE = os.path.join(PKG, "loopkit_core", "store.py")

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


def main() -> int:
    originals = {path: open(path).read() for path in (PROVIDER, STORE)}
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
