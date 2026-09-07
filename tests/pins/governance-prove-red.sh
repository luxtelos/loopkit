#!/usr/bin/env bash
# prove-red.sh — a pin that cannot fail is not a pin. Break each half of the
# governance fix in turn and require the pin to say so, then restore.
#
# MUTATE A COPY, NEVER THE TRACKED HOOK.
#
# This script used to edit the tracked plugins/loopkit/hooks/protect_governance.py
# in place and restore it from a backup. It is the third member of a class found
# on 2026-09-07 and the one that runs most often — tests/selftest.sh invokes it
# on every single run, so its window is open on every suite. The first member,
# stop-gate-prove-red.sh, put a mutated stop_gate.sh into commit 62943e3 when a
# concurrent `git add` landed inside exactly this window; the second,
# targets-prove-red.sh, was caught with a tracked spec fixture wrong on disk
# mid-run. A trap and a restore are not protection — they close the window
# afterwards, and afterwards is too late for anything that read the file during.
#
# So nothing under $W is written. plugins/ is copied into a scratch skeleton and
# the mutations happen to the copy — the whole plugin, not just the one file,
# because a hook may resolve siblings through its own location. The pin takes
# the hook's path as its argument, so pointing it at the copy is all it takes.
# usage: prove-red.sh <worktree-root>
set -uo pipefail
W="${1:?usage: prove-red.sh <worktree-root>}"
W="$(cd "$W" 2>/dev/null && pwd)" || { echo "no such worktree root: $1"; exit 2; }
PIN="$W/tests/pins/governance-shell-cases.sh"

TRACKED="$W/plugins/loopkit/hooks/protect_governance.py"
[ -f "$TRACKED" ] || { echo "no protect_governance.py at $TRACKED"; exit 2; }
TRACKED_SUM="$(cksum < "$TRACKED")"

SKEL="$(mktemp -d "${TMPDIR:-/tmp}/gov-prove-red.XXXXXX")"
trap 'chmod -R u+w "$SKEL" 2>/dev/null; find "$SKEL" -mindepth 1 -delete 2>/dev/null; rmdir "$SKEL" 2>/dev/null || true' EXIT
cp -R "$W/plugins" "$SKEL/plugins" || { echo "could not stage a plugin copy"; exit 2; }
HOOK="$SKEL/plugins/loopkit/hooks/protect_governance.py"
BAK="$SKEL/protect_governance.pristine"
cp "$HOOK" "$BAK"
restore() { cp "$BAK" "$HOOK"; }

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

# The rule this script now follows, asserted rather than trusted.
if [ "$(cksum < "$TRACKED")" = "$TRACKED_SUM" ]; then
  echo "  ok — the tracked protect_governance.py was never written to"
else
  echo "  NOT RED — THIS SCRIPT MODIFIED THE TRACKED protect_governance.py."
  echo "            Restore it from git before committing anything."
  exit 1
fi
