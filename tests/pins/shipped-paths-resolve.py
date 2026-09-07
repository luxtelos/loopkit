#!/usr/bin/env python3
"""shipped-paths-resolve.py — every path a SHIPPED file names must exist for a
CONSUMER, not merely for this checkout.

WHY THIS PIN EXISTS
-------------------
`plugins/loopkit/agents/implementer.md` and `reviewer.md` ship inside the
plugin. They told every consuming project's implementer and reviewer to run:

    bash tools/remote-gate.sh <branch>

`tools/` is this repository's root directory. It is not inside
`plugins/loopkit`, so it is not part of what ships; `loopkit-init.sh` does not
copy it into a project either. On the day that shipped, every agent in every
consuming project was told, in the file it reads before starting work, to run a
path that did not exist for it.

The harder half of the finding is the check that was supposed to prevent it.
`tests/pins/remote-gate-config.sh` asserted "implementer.md points at a script
present on this branch" — resolved against the REPO ROOT, where `tools/` does
exist. It would have passed forever, whatever consumers got. A check written
against the wrong root cannot observe the case it was written for.

So this pin never looks at the repository root. It builds what a consumer
actually receives:

  * an INSTALLED PLUGIN — a copy of `plugins/loopkit/`, which is the unit that
    ships, standing in for ~/.claude/plugins/cache/loopkit/loopkit/<version>/
  * a CONSUMER PROJECT — an empty git repo with `loopkit-init.sh` run into it,
    which is everything a project has on day one

and resolves every command reference in the shipped instruction files against
those two, the way `${CLAUDE_PLUGIN_ROOT}` and a bare relative path resolve for
someone who has never seen this repository.

usage: python3 tests/pins/shipped-paths-resolve.py [<repo-root>]
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else
            Path(__file__).resolve().parents[2]).resolve()
PLUGIN_SRC = REPO / "plugins" / "loopkit"

fails = []


def ok(msg):
    print(f"  ok   {msg}")


def bad(msg):
    print(f"  FAIL {msg}")
    fails.append(msg)


# A command reference in prose: `bash <path>` / `python3 <path>`, with the path
# optionally quoted and optionally prefixed by ${CLAUDE_PLUGIN_ROOT}.
REF = re.compile(
    r"""\b(?:bash|sh|python3|python)\s+"?(?P<path>[$\w{}./-]+\.(?:sh|py))"?"""
)
PLUGIN_ROOT_PREFIX = re.compile(r"^\$\{?CLAUDE_PLUGIN_ROOT\}?/")


def main():
    if not PLUGIN_SRC.is_dir():
        print(f"  FAIL no plugin at {PLUGIN_SRC}")
        return 2

    with tempfile.TemporaryDirectory(prefix="loopkit-shipped-") as tmp:
        tmp = Path(tmp)

        # ---- 1. what ships -------------------------------------------------
        installed = tmp / "installed"
        shutil.copytree(PLUGIN_SRC, installed, symlinks=True)

        # ---- 2. what a consumer's project looks like -----------------------
        consumer = tmp / "project"
        consumer.mkdir()
        env = dict(os.environ,
                   CLAUDE_PLUGIN_ROOT=str(installed),
                   CLAUDE_PROJECT_DIR=str(consumer),
                   GIT_CONFIG_GLOBAL=str(tmp / "gitconfig"),
                   HOME=str(tmp))
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q",
                        str(consumer)], check=False, capture_output=True)
        init = installed / "scripts" / "loopkit-init.sh"
        r = subprocess.run(["bash", str(init), "--project", str(consumer)],
                           env=env, capture_output=True, text=True)
        if r.returncode != 0:
            bad(f"loopkit-init.sh failed in a fresh project (rc={r.returncode}): "
                f"{(r.stderr or r.stdout).strip().splitlines()[-1:]}")

        # ---- 3. resolve every path the shipped files name ------------------
        docs = sorted(
            p for d in ("agents", "skills", "commands")
            for p in (installed / d).rglob("*.md")
        )
        if not docs:
            bad("found no shipped instruction files to check — this pin is scanning nothing")
            return 1

        missing, checked = [], 0
        for doc in docs:
            rel = doc.relative_to(installed)
            for m in REF.finditer(doc.read_text(encoding="utf-8", errors="replace")):
                raw = m.group("path")
                if PLUGIN_ROOT_PREFIX.search(raw):
                    target = installed / PLUGIN_ROOT_PREFIX.sub("", raw)
                    how = "${CLAUDE_PLUGIN_ROOT}"
                elif "$" in raw:
                    # A prose-local variable (`$RSM/install.sh`), not a path the
                    # reader is expected to type verbatim. Skipping these is the
                    # one place this pin trusts prose, so it is deliberately the
                    # narrowest possible rule: a `$` that is not the plugin root.
                    continue
                elif raw.startswith("/"):
                    continue          # an absolute path is the author's problem, not ours
                else:
                    target = consumer / raw
                    how = "the consumer's project root"
                checked += 1
                if not target.exists():
                    missing.append(f"{rel} says `{raw}`, which resolves under "
                                   f"{how} to a file that does not exist")

        if checked == 0:
            bad("resolved zero command references — the extraction has drifted off the prose")
        elif missing:
            for msg in missing:
                bad(msg)
        else:
            ok(f"all {checked} script paths in the shipped instruction files "
               f"resolve for a fresh consumer")

        # ---- 4. a setting the shipped text names must have a slot ----------
        # "Set LOOPKIT_REMOTE in .loopkit/config.env" is only actionable if the
        # config a consumer receives has that line. This repo's own
        # .loopkit/config.env is tracked and does have it, which is exactly why
        # the gap was invisible from in here.
        example = installed / "templates" / "loopkit" / "config.env.example"
        if not example.is_file():
            bad("templates/loopkit/config.env.example is missing — consumers get no config template")
        else:
            example_text = example.read_text(encoding="utf-8")
            named = set()
            for doc in (installed / "agents").rglob("*.md"):
                named |= set(re.findall(r"\bLOOPKIT_[A-Z0-9_]+\b", doc.read_text(encoding="utf-8")))
            absent = sorted(k for k in named if k not in example_text)
            if absent:
                for k in absent:
                    bad(f"{k} is named in a shipped agent file but has no slot in config.env.example")
            elif named:
                ok(f"every LOOPKIT_* setting the agent files name ({', '.join(sorted(named))}) "
                   f"has a slot in the config consumers receive")
            else:
                ok("no LOOPKIT_* setting is named in the shipped agent files")

    print()
    if fails:
        print(f"SHIPPED PATHS: {len(fails)} FAILURE(S)")
        return 1
    print("SHIPPED PATHS: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
