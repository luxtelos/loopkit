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
GATE="$W/plugins/loopkit/hooks/stop_gate.sh"
PIN="$W/tests/pins/stop-gate-branch-shapes.sh"
BAK="$(mktemp "${TMPDIR:-/tmp}/stop-gate.XXXXXX")"
cp "$GATE" "$BAK"
restore() { cp "$BAK" "$GATE"; }
trap 'restore; rm -f "$BAK"' EXIT

mutate() { python3 - "$GATE" "$1" "$2" <<'PY'
import sys
from pathlib import Path
p, old, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
s = p.read_text()
if old not in s:
    print(f"  MUTATION DID NOT APPLY: {old[:60]}"); sys.exit(3)
p.write_text(s.replace(old, new, 1))
PY
}

check() {  # <label> <expected substring in the pin's failure output>
    local label="$1" want="$2" out rc
    out="$(bash "$PIN" "$W" 2>&1)"; rc=$?
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
echo "MUT-LOCALREF: look the trunk up in refs/heads only"
mutate 'd="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"' \
       'd=""; git show-ref --verify --quiet refs/heads/main && d=main'
check "a trunk not called 'main' loses its base" "default branch"

# 3. Put the note back inside the SKIP branch, where a dirty tree never sees it.
echo "MUT-NOTE: only announce the missing base when the gate short-circuits"
mutate 'gate_scope_note "$GATE_BASE"' ':'
check "the silent fallback returns" "NO note"

echo "prove-red complete"
