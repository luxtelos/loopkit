#!/usr/bin/env bash
# prove-red.sh — a pin that cannot fail is not a pin. Break each half of the
# governance fix in turn and require the pin to say so, then restore.
# usage: prove-red.sh <worktree-root>
set -uo pipefail
W="${1:?usage: prove-red.sh <worktree-root>}"
HOOK="$W/plugins/loopkit/hooks/protect_governance.py"
PIN="$W/tests/pins/governance-shell-cases.sh"
BAK="$(mktemp "${TMPDIR:-/tmp}/gov-hook.XXXXXX")"
cp "$HOOK" "$BAK"
restore() { cp "$BAK" "$HOOK"; }
trap restore EXIT

mutate() { python3 - "$HOOK" "$1" "$2" <<'PY'
import sys
from pathlib import Path
p, old, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
s = p.read_text()
if old not in s:
    print(f"  MUTATION DID NOT APPLY: {old[:50]}"); sys.exit(3)
p.write_text(s.replace(old, new, 1))
PY
}

echo "MUT-READS: stop exempting a filter without an in-place flag"
mutate 'if tool in INPLACE_ONLY and not any(INPLACE_FLAG.match(t) for t in toks):' 'if False:'
out="$(bash "$PIN" "$HOOK" 2>&1)"; rc=$?
if [ "$rc" != 0 ] && printf '%s' "$out" | grep -q 'FALSE BLOCK'; then
  echo "  RED, as required — $(printf '%s' "$out" | grep -c 'FALSE BLOCK') read shapes falsely blocked"
else
  echo "  NOT RED (rc=$rc) — the pin cannot catch this half"; fi
restore

echo "MUT-INLINE: remove the inline-prefix door"
mutate ' or inline_override(payload)' ''
out="$(bash "$PIN" "$HOOK" 2>&1)"; rc=$?
if [ "$rc" != 0 ] && printf '%s' "$out" | grep -q 'INLINE OVERRIDE BROKEN'; then
  echo "  RED, as required — the inline door is reported broken"
else
  echo "  NOT RED (rc=$rc) — the pin cannot catch this half"; fi
restore

echo "MUT-WRITES: stop matching Bash entirely"
mutate '"NotebookEdit", "Bash"}' '"NotebookEdit"}'
out="$(bash "$PIN" "$HOOK" 2>&1)"; rc=$?
if [ "$rc" != 0 ] && printf '%s' "$out" | grep -q 'LEAK'; then
  echo "  RED, as required — $(printf '%s' "$out" | grep -c 'LEAK') write shapes leaked"
else
  echo "  NOT RED (rc=$rc) — the pin cannot catch this half"; fi
restore

echo "RESTORED: $(bash "$PIN" "$HOOK" 2>&1 | tail -1)"
