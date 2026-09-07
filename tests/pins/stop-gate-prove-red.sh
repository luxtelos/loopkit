#!/usr/bin/env bash
# stop-gate-prove-red.sh — a pin that cannot fail is not a pin.
#
# Break each half of the branch-shape fix in turn and require
# tests/pins/stop-gate-branch-shapes.sh to say so, then restore. Without this,
# the pin could silently stop exercising anything (a mistyped marker, a scenario
# that no longer builds) and would go on reporting ALL PASS forever — which is
# the same "check that cannot fail" defect the gate itself was fixed for.
#
# usage: stop-gate-prove-red.sh <worktree-root>

set -uo pipefail
W="${1:?usage: stop-gate-prove-red.sh <worktree-root>}"
W="$(cd "$W" 2>/dev/null && pwd)" || { echo "no such worktree root: $1"; exit 2; }
PIN="$W/tests/pins/stop-gate-branch-shapes.sh"

# MUTATE A COPY, NEVER THE TRACKED FILE.
#
# This script used to edit $W/plugins/loopkit/hooks/stop_gate.sh in place and
# restore it from a backup afterwards. The window is short — a second or two per
# mutation — and that is exactly long enough. On 2026-09-07 this pin was running
# inside a background suite while a `git add plugins/loopkit/hooks/stop_gate.sh`
# ran in the foreground, and the MUT-NAME mutation went into the commit. The
# working tree was correct, the suite was green against the working tree, and
# the pushed branch carried a stop gate that could not read a detached HEAD —
# the very bug the commit claimed to fix.
#
# A restore-afterwards is not a guarantee, it is a race with anything else that
# reads the file. So nothing under $W is written at all now: the plugin is
# copied into a scratch skeleton and the mutations happen there. The whole
# plugin, not just the one file, because stop_gate.sh derives PLUGIN_ROOT from
# its own location and calls siblings through it.
TRACKED_SUM="$(cksum < "$W/plugins/loopkit/hooks/stop_gate.sh")"
SKEL="$(mktemp -d "${TMPDIR:-/tmp}/stop-gate-prove-red.XXXXXX")"
trap 'chmod -R u+w "$SKEL" 2>/dev/null; rm -rf "$SKEL" 2>/dev/null || true' EXIT
cp -R "$W/plugins" "$SKEL/plugins" || { echo "could not stage a plugin copy"; exit 2; }
GATE="$SKEL/plugins/loopkit/hooks/stop_gate.sh"
[ -f "$GATE" ] || { echo "no stop_gate.sh at $GATE"; exit 2; }
BAK="$SKEL/stop_gate.pristine"
cp "$GATE" "$BAK"
restore() { cp "$BAK" "$GATE"; }

MUTATED=0
mutate() { python3 - "$GATE" "$1" "$2" <<'PY'
import sys
from pathlib import Path
p, old, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
s = p.read_text()
if old not in s:
    print(f"  MUTATION DID NOT APPLY: {old[:60]}"); sys.exit(3)
p.write_text(s.replace(old, new, 1))
PY
    MUTATED=$?
    return $MUTATED
}

check() {  # <label> <expected substring in the pin's failure output>
    local label="$1" want="$2" out rc
    # A mutation whose target text no longer exists means this case is STALE —
    # the code moved and nobody updated the mutation. Say that, rather than
    # "the pin cannot catch it": the two have opposite fixes, and reporting the
    # wrong one sends the next reader to rewrite a pin that is fine. This bit
    # once already, when gate_default_ref was rewritten to ask every remote.
    if [ "$MUTATED" != 0 ]; then
        echo "  NOT RED — MUTATION IS STALE, not the pin: $label"
        restore; return
    fi
    # The pin is read from the real tree (it is not what is being mutated) but
    # pointed at the SKELETON, so the gate it exercises is the copy.
    out="$(bash "$PIN" "$SKEL" 2>&1)"; rc=$?
    if [ "$rc" != 0 ] && printf '%s' "$out" | grep -q "$want"; then
        echo "  RED, as required — $label"
    else
        echo "  NOT RED (rc=$rc) — the pin cannot catch: $label"
    fi
    restore
}

# 1. Go back to asking for a branch name. Detached HEAD loses its base.
echo "MUT-NAME: resolve the base from the branch name again"
mutate 'base="$(git merge-base "$default_ref" HEAD 2>/dev/null || true)"' \
       'base=""; [ "$(git rev-parse --abbrev-ref HEAD)" != HEAD ] && base="$(git merge-base "$default_ref" HEAD 2>/dev/null || true)"'
check "detached HEAD reverts to working-tree-only" "detached HEAD"

# 2. Go back to a local-only trunk lookup. `develop` and CI checkouts lose it.
# The `production` shape is what carries this one: it is outside the fallback
# name list, so only the <remote>/HEAD lookup can resolve it.
echo "MUT-LOCALREF: look the trunk up in refs/heads only"
mutate '    for r in origin $remotes; do
        d="$(git symbolic-ref --quiet --short "refs/remotes/$r/HEAD" 2>/dev/null || true)"' \
       '    d=""; git show-ref --verify --quiet refs/heads/main && d=main
    for r in ; do'
check "a trunk not called 'main' loses its base" "default branch"

# 2b. Go back to hard-coding the remote name. A fork checkout whose upstream is
# called `upstream` loses its trunk — the gate then says "no trunk-shaped ref in
# this clone" about a clone that has one. One line, because that is all the bug
# ever was.
echo "MUT-ORIGINONLY: ask only a remote called 'origin'"
mutate 'remotes="$(git remote 2>/dev/null || true)"' 'remotes=""'
check "a remote not named 'origin' loses its base" "remote named 'upstream'"

# 3. Put the note back inside the SKIP branch, where a dirty tree never sees it.
echo "MUT-NOTE: only announce the missing base when the gate short-circuits"
mutate 'gate_scope_note "$GATE_BASE"' ':'
check "the silent fallback returns" "NO note"

# The rule this script now follows, asserted rather than trusted. Cheap, and it
# is the only thing standing between a future edit and another mutated commit.
if [ "$(cksum < "$W/plugins/loopkit/hooks/stop_gate.sh")" = "$TRACKED_SUM" ]; then
    echo "  ok — the tracked stop_gate.sh was never written to"
else
    echo "  NOT RED — THIS SCRIPT MODIFIED THE TRACKED stop_gate.sh. Restore it"
    echo "            from git before committing anything."
    exit 1
fi

echo "prove-red complete"
