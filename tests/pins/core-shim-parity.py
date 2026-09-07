#!/usr/bin/env python3
"""core-shim-parity.py — every module M2 moved is reachable BOTH ways.

M2 part A moved five modules from `plugins/loopkit/scripts/` into
`plugins/loopkit/loopkit_core/`. The scripts are still called by path from
hooks, `loop-next.sh`, `tests/selftest.sh`, `loop-scan.py` and from projects
outside this repo, so each old path is now a thin shim over the package.

A shim that merely LOOKS right is the failure mode this pin exists for. Three
things are checked, and each can fail on its own:

  1. `loopkit_core.<name>` imports.
  2. The old script path still loads by `importlib.util.spec_from_file_location`
     — the mechanism `inbox_to_triage.py`, `test_inbox_to_triage.py` and
     `test_loop_next_pick.py` all use — and every public symbol of the package
     module is present on it AS THE SAME OBJECT. Same object, not merely same
     name: a shim that re-implemented a function would pass a name check and
     drift the next day.
  3. The behaviour is the same through both doors: a triage round trip, a
     ticks append, a `pick()` over rows, and the script CLIs still run.

usage: python3 tests/pins/core-shim-parity.py
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "loopkit"
SCRIPTS = PLUGIN / "scripts"

# (old script path, package module name). The dash in `loop-metrics.py` is why
# the module gained an underscore: a dash is not a legal identifier, so
# `loopkit_core.loop-metrics` could never be imported. The SCRIPT keeps its
# dash because callers name it by path.
MOVED = [
    ("triage_state.py", "triage_state"),
    ("ticks.py", "ticks"),
    ("loop_next_pick.py", "loop_next_pick"),
    ("inbox_to_triage.py", "inbox_to_triage"),
    ("loop-metrics.py", "loop_metrics"),
]

fails: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    fails.append(msg)


def load_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec because triage_state defines a @dataclass and
    # dataclasses resolves annotations through sys.modules[cls.__module__].
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    sys.path.insert(0, str(PLUGIN))

    print("== every moved module imports as loopkit_core.<name>")
    cores = {}
    for script, mod_name in MOVED:
        try:
            cores[mod_name] = importlib.import_module(f"loopkit_core.{mod_name}")
            ok(f"import loopkit_core.{mod_name}")
        except Exception as exc:
            fail(f"import loopkit_core.{mod_name}: {exc}")

    print("== every moved module still loads at its old script path")
    shims = {}
    for script, mod_name in MOVED:
        path = SCRIPTS / script
        if not path.exists():
            fail(f"{script} no longer exists at plugins/loopkit/scripts/")
            continue
        try:
            shims[mod_name] = load_by_path(f"_shim_{mod_name}", path)
            ok(f"spec_from_file_location scripts/{script}")
        except Exception as exc:
            fail(f"loading scripts/{script}: {exc}")

    print("== the shim's surface IS the package module's, object for object")
    for _script, mod_name in MOVED:
        core, shim = cores.get(mod_name), shims.get(mod_name)
        if core is None or shim is None:
            continue
        missing, different = [], []
        for attr, value in vars(core).items():
            if attr.startswith("__"):
                continue
            if not hasattr(shim, attr):
                missing.append(attr)
            elif getattr(shim, attr) is not value:
                different.append(attr)
        if missing or different:
            fail(f"{mod_name}: missing={missing[:5]} not-the-same-object={different[:5]}")
        else:
            ok(f"{mod_name}: {len(vars(core))} attributes, all identical objects")

    print("== behaviour is the same through both doors")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "state").mkdir()
        state = root / "state" / "triage.md"

        # triage_state: write through the SHIM, read through the CORE.
        shims["triage_state"].upsert_row(
            state, finding="Totals drift | refunds", source="gh#1",
            priority="high", spec="", status="new",
        )
        rows = cores["triage_state"].parse_table(state).rows
        if [r["finding"] for r in rows] == ["Totals drift | refunds"]:
            ok("triage_state: shim write is readable by the package (escaped pipe survives)")
        else:
            fail(f"triage_state round trip: {rows}")

        # Criterion 9: identity is `source`, so a second upsert updates.
        created = shims["triage_state"].upsert_row(
            state, finding="Totals drift, again", source="gh#1",
            priority="high", spec="", status="new",
        )
        rows = cores["triage_state"].parse_table(state).rows
        if created is False and len(rows) == 1:
            ok("triage_state: a repeated source updates one row, never adds a second")
        else:
            fail(f"triage_state dedup: created={created} rows={len(rows)}")

        # ticks: append through the CORE, read through the SHIM.
        cores["ticks"].append(root, "stage", stage="new", scope="")
        journal = shims["ticks"].rows(root)
        if len(journal) == 1 and journal[0]["event"] == "stage" and "at" in journal[0]:
            ok("ticks: package append is readable by the shim, `at` present")
        else:
            fail(f"ticks round trip: {journal}")

        # loop_next_pick: same rows, same tab-separated stdout both ways.
        sample = [
            {"finding": "a", "source": "s1", "priority": "high", "spec": "", "status": "new"},
            {"finding": "b", "source": "s2", "priority": "low", "spec": "", "status": "fixing"},
        ]
        if cores["loop_next_pick"].pick(sample, "") == shims["loop_next_pick"].pick(sample, ""):
            ok("loop_next_pick: identical output through both doors")
        else:
            fail("loop_next_pick: shim and package disagree")

        # The CLIs still run from the old paths. A shim whose __main__ arm is
        # wrong imports cleanly and fails only when a hook calls it.
        p = subprocess.run(
            [sys.executable, str(SCRIPTS / "triage_state.py"), "list",
             "--state", str(state), "--format", "json"],
            capture_output=True, text=True, timeout=60,
        )
        try:
            listed = json.loads(p.stdout)
        except Exception:
            listed = None
        if p.returncode == 0 and isinstance(listed, list) and len(listed) == 1:
            ok("scripts/triage_state.py CLI still lists rows")
        else:
            fail(f"scripts/triage_state.py CLI: rc={p.returncode} out={p.stdout[:120]}")

        p = subprocess.run(
            [sys.executable, str(SCRIPTS / "loop-metrics.py"), "--root", str(root), "--json"],
            capture_output=True, text=True, timeout=60,
        )
        if p.returncode == 0 and '"ticks"' in p.stdout:
            ok("scripts/loop-metrics.py CLI still reports metrics")
        else:
            fail(f"scripts/loop-metrics.py CLI: rc={p.returncode} out={p.stdout[:120]}")

        p = subprocess.run(
            [sys.executable, str(SCRIPTS / "ticks.py"), "tail", "--root", str(root), "--n", "5"],
            capture_output=True, text=True, timeout=60,
        )
        if p.returncode == 0 and '"stage"' in p.stdout:
            ok("scripts/ticks.py CLI still tails the journal")
        else:
            fail(f"scripts/ticks.py CLI: rc={p.returncode} out={p.stdout[:120]}")

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S)")
        return 1
    print("core/shim parity PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
