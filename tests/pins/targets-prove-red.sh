#!/usr/bin/env bash
# targets-prove-red.sh — a derivation that cannot disagree with a fixture is not
# a derivation. Perturb each fixture's `targets` and require the pin to object.
#
# This exists because the pin once carried a `targets_of()` that recomputed the
# key from a hand-written reading of a noun the spec had never defined, and
# agreed with every fixture by construction. Agreement is only evidence when
# disagreement was possible.
#
# MUTATE A COPY, NEVER THE TRACKED FIXTURES.
#
# This script used to copy each tracked spec/fixtures/0*.json to a `.bak`,
# rewrite the tracked path in place, and move the backup back afterwards. That
# is the exact shape that put a mutated stop_gate.sh into commit 62943e3 on
# 2026-09-07: tests/pins/stop-gate-prove-red.sh edited a tracked file in place
# and restored it, and a concurrent `git add` landed inside the window. The
# window here is a second or two per fixture — the same order, and long enough.
# A reviewer watching this script run caught a tracked fixture wrong on disk
# mid-run, carrying `"source": "gh#104-WRONG"`.
#
# A restore-afterwards is not a guarantee, it is a race with anything else that
# reads the file. There was no trap either, so an interrupt left a corrupted
# tracked fixture AND a stray .bak in the worktree.
#
# So nothing under the repo is written now. A scratch skeleton gets a copy of
# the fixtures, a copy of the pin (its REPO is Path(__file__).resolve().parents[2],
# so the copy must sit at <skel>/tests/pins/ for it to read <skel>/spec/fixtures)
# and a copy of plugins/, because the pin imports okf_bundle and knowledge_actor
# from the vendor directory under it. Copies, not symlinks: `.resolve()` would
# walk a symlinked pin straight back to the tracked tree, and an import through
# a symlinked plugins/ writes __pycache__ into it.
#
# usage: targets-prove-red.sh [repo-root]
set -uo pipefail
REPO="${1:-$(dirname "${BASH_SOURCE[0]}")/../..}"
REPO="$(cd "$REPO" 2>/dev/null && pwd)" || { echo "no such repo root: ${1:-}"; exit 2; }

# The rule this script now follows, asserted rather than trusted.
tracked_sums() { cksum "$REPO"/spec/fixtures/*.json 2>/dev/null; }
TRACKED_SUMS="$(tracked_sums)"
[ -n "$TRACKED_SUMS" ] || { echo "no fixtures under $REPO/spec/fixtures"; exit 2; }

SKEL="$(mktemp -d "${TMPDIR:-/tmp}/targets-prove-red.XXXXXX")"
trap 'chmod -R u+w "$SKEL" 2>/dev/null; find "$SKEL" -mindepth 1 -delete 2>/dev/null; rmdir "$SKEL" 2>/dev/null || true' EXIT

mkdir -p "$SKEL/spec" "$SKEL/tests/pins" || { echo "could not build the scratch skeleton"; exit 2; }
cp -R "$REPO/spec/fixtures" "$SKEL/spec/fixtures" || { echo "could not stage the fixtures"; exit 2; }
cp "$REPO/tests/pins/fixture-derivable.py" "$SKEL/tests/pins/" || { echo "could not stage the pin"; exit 2; }
cp -R "$REPO/plugins" "$SKEL/plugins" || { echo "could not stage a plugin copy"; exit 2; }

PIN="$SKEL/tests/pins/fixture-derivable.py"
red=0; notred=0

for f in "$SKEL"/spec/fixtures/0*.json; do
  case "$f" in *README*) continue ;; esac
  base="$(basename "$f")"
  n="$(python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
print(len(d.get('expected',{}).get('targets') or []))" "$f")"
  if [ "$n" = 0 ]; then echo "  skip $base — no targets to perturb"; continue; fi
  cp "$f" "$SKEL/pristine.json"
  python3 - "$f" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["expected"]["targets"][0]["source"] += "-WRONG"
json.dump(d, open(p, "w"), indent=2)
PY
  out="$(PYTHONDONTWRITEBYTECODE=1 python3 "$PIN" 2>&1)"; rc=$?
  if [ "$rc" != 0 ] && printf '%s' "$out" | grep -q 'targets: spec'; then
    red=$((red+1)); echo "  RED  $base — the pin objects to a wrong target"
  else
    notred=$((notred+1)); echo "  NOT RED  $base (rc=$rc) — the derivation is not biting"
  fi
  cp "$SKEL/pristine.json" "$f"
done

echo "TARGETS MUTATIONS red=$red not-red=$notred"

# Cheap, and it is the only thing standing between a future edit of this file
# and another mutated commit.
if [ "$(tracked_sums)" = "$TRACKED_SUMS" ]; then
  echo "  ok — the tracked spec/fixtures were never written to"
else
  echo "  NOT RED — THIS SCRIPT MODIFIED THE TRACKED spec/fixtures. Restore them"
  echo "            from git before committing anything."
  exit 1
fi
if ls "$REPO"/spec/fixtures/*.bak "$REPO"/spec/fixtures/*.pristine >/dev/null 2>&1; then
  echo "  NOT RED — THIS SCRIPT LEFT BACKUP FILES IN THE TRACKED TREE."
  exit 1
fi

[ "$red" -gt 0 ] && [ "$notred" = 0 ]
