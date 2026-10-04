#!/usr/bin/env bash
# inbox-precedent-prove-red.sh — a pin that cannot fail is not a pin.
#
# tests/pins/inbox-precedent-gate.py tags every case with the half of the fix
# it exercises. This script breaks one half at a time and requires exactly
# that tag to go red — and requires a tag that should be untouched to stay
# green, because a mutation that turns EVERYTHING red proves only that the pin
# notices a missing file.
#
# One mutation per pre-mortem risk:
#
#   MUT-COMMIT       loop-commit.sh stops asking            -> [commit] red
#   MUT-GATE         stop_gate.sh stops asking              -> [gate] red
#   MUT-NEW          nothing is ever "new"                  -> [new] red
#   MUT-GRANDFATHER  everything is "new", history included  -> [grandfather] red
#   MUT-CLOSED       a RESOLVED stamp makes a section new   -> [grandfather] red
#   MUT-EMPTY        any line counts, whatever it says      -> [empty] red
#   MUT-SCOPE-LEAK   the named-paths filter never matches   -> [commit] red
#   MUT-SCOPE-WIDE   every commit is judged, named or not   -> [commit][control] red
#
# MUTATE A COPY, NEVER THE TRACKED FILE. Same rule, same reason, as
# governance-prove-red.sh and stop-gate-prove-red.sh: a restore-afterwards is a
# race with anything that reads the file during, and that race has already put
# a mutated gate into a commit once. plugins/ is copied to a scratch skeleton
# and the pin is pointed at the copy.
#
# usage: inbox-precedent-prove-red.sh <worktree-root>
set -uo pipefail
W="${1:?usage: inbox-precedent-prove-red.sh <worktree-root>}"
W="$(cd "$W" 2>/dev/null && pwd)" || { echo "no such worktree root: $1"; exit 2; }
PIN="$W/tests/pins/inbox-precedent-gate.py"

TRACKED=(plugins/loopkit/scripts/check-inbox-precedent.py plugins/loopkit/scripts/loop-commit.sh plugins/loopkit/hooks/stop_gate.sh)
for f in "${TRACKED[@]}"; do [ -f "$W/$f" ] || { echo "NOT RED — no $f to mutate"; exit 2; }; done
tracked_sum() { for f in "${TRACKED[@]}"; do cksum < "$W/$f"; done; }
TRACKED_SUM="$(tracked_sum)"

SKEL="$(mktemp -d "${TMPDIR:-/tmp}/inbox-precedent-prove-red.XXXXXX")"
trap 'chmod -R u+w "$SKEL" 2>/dev/null; find "$SKEL" -mindepth 1 -delete 2>/dev/null; rmdir "$SKEL" 2>/dev/null || true' EXIT
cp -R "$W/plugins" "$SKEL/plugins" || { echo "could not stage a plugin copy"; exit 2; }
cp -R "$W/plugins" "$SKEL/pristine" || { echo "could not stage a pristine copy"; exit 2; }
PLUGIN="$SKEL/plugins/loopkit"
restore() { cp "$SKEL/pristine/loopkit/$1" "$PLUGIN/$1"; }

mutate() {  # <file relative to the plugin> <old> <new>
python3 - "$PLUGIN/$1" "$2" "$3" <<'PY'
import sys
from pathlib import Path
p, old, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
s = p.read_text(encoding="utf-8")
if s.count(old) != 1:
    print(f"  MUTATION DID NOT APPLY ({s.count(old)} matches): {old[:60]}"); sys.exit(3)
p.write_text(s.replace(old, new, 1), encoding="utf-8")
PY
}

# <name> <file> <old> <new> <tag that must go red> <tag that must stay green>
prove() {
  local name="$1" file="$2" old="$3" new="$4" red="$5" green="$6" out rc n_red n_green
  echo "$name"
  if ! mutate "$file" "$old" "$new"; then echo "  NOT RED — the mutation did not apply"; restore "$file"; return; fi
  out="$(python3 "$PIN" "$PLUGIN" 2>&1)"; rc=$?
  restore "$file"
  n_red="$(printf '%s\n' "$out" | grep -E '^  FAIL ' | grep -cF "$red" || true)"
  n_green="$(printf '%s\n' "$out" | grep -E '^  FAIL ' | grep -cF "$green" || true)"
  if [ "$rc" != 0 ] && [ "$n_red" -gt 0 ] && [ "$n_green" = 0 ]; then
    echo "  RED, as required — $n_red $red case(s) failed, no $green case did"
  else
    echo "  NOT RED (rc=$rc, $red failures=$n_red, $green failures=$n_green) — the pin cannot tell this half apart"
  fi
}

C=scripts/check-inbox-precedent.py

prove "MUT-COMMIT: loop-commit.sh stops asking" scripts/loop-commit.sh \
  'python3 "$HERE/check-inbox-precedent.py" --root "$ROOT" --base HEAD -- "${PATHS[@]}" || {' \
  'true || {' \
  '[commit]' '[gate]'

prove "MUT-GATE: stop_gate.sh stops asking" hooks/stop_gate.sh \
  'if [[ -f "$ROOT/inbox/needs-human.md" ]]; then' \
  'if false; then' \
  '[gate][new]' '[commit]'

prove "MUT-NEW: nothing is ever new" "$C" \
  'refused.append((heading, why))' \
  'pass' \
  '[new]' '[grandfather]'

prove "MUT-GRANDFATHER: everything is new, history included" "$C" \
  'if allowance[heading] > 0:' \
  'if False:' \
  '[grandfather]' '[new]'

prove "MUT-CLOSED: a RESOLVED stamp makes an old section new" "$C" \
  'if is_closed(heading):' \
  'if False:' \
  '[grandfather]' '[new]'

prove "MUT-EMPTY: any line counts, whatever it says" "$C" \
  'why = judge_line(" ".join(parts))' \
  'why = ""' \
  '[empty]' '[grandfather]'

prove "MUT-SCOPE-LEAK: the named-paths filter never matches" "$C" \
  'if entry == inbox or entry[3:] == inbox:' \
  'if False:' \
  '[commit]' '[gate]'

prove "MUT-SCOPE-WIDE: every commit is judged, named or not" "$C" \
  'if args.pathspecs and not touched(args.inbox, args.pathspecs):' \
  'if False:' \
  '[commit][control]' '[gate]'

echo "RESTORED: $(python3 "$PIN" "$PLUGIN" 2>&1 | tail -1)"

# The rule this script follows, asserted rather than trusted.
if [ "$(tracked_sum)" = "$TRACKED_SUM" ]; then
  echo "  ok — no tracked file was written to"
else
  echo "  NOT RED — THIS SCRIPT MODIFIED A TRACKED FILE. Restore it from git before committing anything."
  exit 1
fi
