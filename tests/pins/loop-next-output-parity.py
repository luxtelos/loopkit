#!/usr/bin/env python3
"""loop-next-output-parity.py — `loop-next.sh` prints what it printed before M2.

M2 part A lifted the stage precedence and the CONTINUE/WAIT/IDLE split out of
two inline `if/elif` chains in `plugins/loopkit/scripts/loop-next.sh` and into
`loopkit_core.decide()`. Every consumer of that script — the `loop-tick` skill,
`stop_gate.sh`, a human reading a terminal — reads its LINES. So the refactor's
acceptance bar is not "the stage is still right", it is "every line is still
byte-identical".

This pin drives the live script over the three fixture queues that carry the
whole three-way split (01 CONTINUE, 02 WAIT, 03 IDLE) and diffs the output
against a golden capture of the PRE-M2 script.

The goldens under `tests/pins/golden/` were produced by running the OLD script,
not by pasting the new one's output:

    python3 tests/pins/loop-next-output-parity.py --regen <git-ref>

extracts `plugins/loopkit` from that ref with `git archive`, runs that tree's
`loop-next.sh` over the same queues and writes the files. The M2 goldens were
regenerated from the branch's base commit. Regenerating them from a ref that
already contains the change would make this pin assert nothing, so `--regen`
prints the ref and the commit subject it used and the PR quotes them.

Two absolute paths appear in the output (the scripts directory in the POLL and
RULE lines, and the project directory in the RULE line). Both are replaced with
`<SCRIPTS>` and `<PROJECT>` before comparing — they differ between the archive
checkout and the worktree by construction, and pinning a machine's temp path
would make the pin fail on every other machine.

usage: python3 tests/pins/loop-next-output-parity.py
       python3 tests/pins/loop-next-output-parity.py --regen origin/main
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parent / "golden"
FIXTURES = REPO / "spec" / "fixtures"

# The three fixtures whose queues produce the whole three-way split. Named
# rather than globbed: a pin that walks whatever files exist goes quietly green
# when the interesting one is deleted.
CASES = ["01-stage-precedence", "02-blocked-only", "03-empty-queue"]

fails: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    fails.append(msg)


def queue_of(case: str) -> list[dict]:
    data = json.loads((FIXTURES / f"{case}.json").read_text(encoding="utf-8"))
    return data["input"]["queue"]


def build_project(scripts: Path, queue: list[dict]) -> Path:
    """A scratch project whose state/triage.md holds the fixture's queue."""
    project = Path(tempfile.mkdtemp(prefix="loopkit-parity-"))
    (project / "state").mkdir(parents=True)
    state = project / "state" / "triage.md"
    triage = scripts / "triage_state.py"
    subprocess.run(
        [sys.executable, str(triage), "ensure-schema", "--state", str(state)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    for row in queue:
        # `--override-verdict` because a `pr-open` row here is a FIXTURE, not a
        # transition the pipeline earned. Since 2026-09-07 the recorder refuses
        # `pr-open` without a recorded reviewer PASS, and this pin builds queues
        # that already contain one. Saying so explicitly is the point of the
        # flag: the alternative — leaving `upsert` ungated so fixtures keep
        # working — is how the gate would have acquired a signposted detour on
        # the day it was written.
        override = (["--override-verdict",
                     "fixture construction in loop-next-output-parity.py"]
                    if row.get("status") == "pr-open" else [])
        subprocess.run(
            [sys.executable, str(triage), "upsert", "--state", str(state),
             "--finding", row.get("finding", ""), "--source", row.get("source", ""),
             "--priority", row.get("priority", ""), "--spec", row.get("spec", ""),
             "--status", row.get("status", ""), *override],
            check=True, capture_output=True, text=True, timeout=60,
        )
    return project


def run_loop_next(scripts: Path, project: Path) -> str:
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    p = subprocess.run(
        ["bash", str(scripts / "loop-next.sh")],
        capture_output=True, text=True, env=env, timeout=120,
    )
    if p.returncode != 0:
        return f"<<loop-next.sh exited {p.returncode}>>\n{p.stdout}{p.stderr}"
    return p.stdout


def normalize(text: str, scripts: Path, project: Path) -> str:
    """Machine-specific absolute paths out; everything else compared exactly."""
    return (text
            .replace(str(scripts), "<SCRIPTS>")
            .replace(str(project.resolve()), "<PROJECT>")
            .replace(str(project), "<PROJECT>"))


def capture(scripts: Path, case: str) -> str:
    project = build_project(scripts, queue_of(case))
    try:
        return normalize(run_loop_next(scripts, project), scripts, project)
    finally:
        shutil.rmtree(project, ignore_errors=True)


def regen(ref: str) -> int:
    subject = subprocess.run(
        ["git", "-C", str(REPO), "log", "-1", "--format=%H %s", ref],
        capture_output=True, text=True, timeout=60,
    )
    if subject.returncode != 0:
        print(f"cannot resolve ref {ref}: {subject.stderr.strip()}")
        return 1
    print(f"regenerating goldens from {ref} = {subject.stdout.strip()}")

    tmp = Path(tempfile.mkdtemp(prefix="loopkit-oldtree-"))
    try:
        archive = subprocess.run(
            ["git", "-C", str(REPO), "archive", ref, "plugins/loopkit"],
            capture_output=True, timeout=120,
        )
        if archive.returncode != 0:
            print("git archive failed")
            return 1
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=archive.stdout, check=True, timeout=120)
        old_scripts = tmp / "plugins" / "loopkit" / "scripts"
        if not (old_scripts / "loop-next.sh").exists():
            print(f"{ref} has no plugins/loopkit/scripts/loop-next.sh")
            return 1
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for case in CASES:
            out = capture(old_scripts, case)
            (GOLDEN / f"loop-next-{case}.txt").write_text(out, encoding="utf-8")
            print(f"  wrote tests/pins/golden/loop-next-{case}.txt ({len(out)} bytes)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regen", metavar="GIT_REF",
                    help="rebuild the goldens by running that ref's loop-next.sh")
    args = ap.parse_args()
    if args.regen:
        return regen(args.regen)

    scripts = REPO / "plugins" / "loopkit" / "scripts"
    print("== loop-next.sh output is byte-identical to the pre-M2 capture")
    for case in CASES:
        golden = GOLDEN / f"loop-next-{case}.txt"
        if not golden.exists():
            fail(f"{case}: tests/pins/golden/loop-next-{case}.txt is missing")
            continue
        want = golden.read_text(encoding="utf-8")
        got = capture(scripts, case)
        if got == want:
            lines = [ln.split(":")[0] for ln in want.splitlines() if ln and not ln.startswith(" ")]
            fail_free = ",".join(sorted(set(lines)))
            ok(f"{case}: identical ({len(want.splitlines())} lines: {fail_free})")
        else:
            diff = "\n".join(list(difflib.unified_diff(
                want.splitlines(), got.splitlines(),
                fromfile=f"golden/{case}", tofile="current", lineterm="",
            ))[:24])
            fail(f"{case}: output changed\n{diff}")

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S)")
        return 1
    print("loop-next.sh output parity PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
