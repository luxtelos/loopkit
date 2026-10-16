#!/usr/bin/env bash
# stop-review-prove-red.sh — a pin that cannot fail is not a pin.
#
# Breaks each rule of hooks/stop_review.py in turn, in a SCRATCH COPY of the
# plugin (never the tracked file: see the 62943e3 note in
# tests/pins/stop-gate-prove-red.sh), and requires
# tests/pins/stop-review-loop.sh to fail on the exact case that guards it.
# Prints "RED, as required" per mutation, or "NOT RED" with the reason.
#
# usage: stop-review-prove-red.sh <worktree-root>

set -uo pipefail
W="${1:?usage: stop-review-prove-red.sh <worktree-root>}"
W="$(cd "$W" 2>/dev/null && pwd)" || { echo "no such worktree root: $1"; exit 2; }
PIN="$W/tests/pins/stop-review-loop.sh"
TRACKED_SUM="$(cksum < "$W/plugins/loopkit/hooks/stop_review.py")"

SKEL="$(mktemp -d "${TMPDIR:-/tmp}/stop-review-prove-red.XXXXXX")"
trap 'chmod -R u+w "$SKEL" 2>/dev/null; rm -rf "$SKEL" 2>/dev/null || true' EXIT
# The whole plugin, not just hooks/: the hook calls scripts/ticks.py through
# its own location to record verdicts.
mkdir -p "$SKEL/plugins"
cp -R "$W/plugins/loopkit" "$SKEL/plugins/loopkit" || { echo "could not stage a copy"; exit 2; }
HOOK="$SKEL/plugins/loopkit/hooks/stop_review.py"
WIRING="$SKEL/plugins/loopkit/hooks/hooks.json"
cp "$HOOK" "$SKEL/hook.pristine"; cp "$WIRING" "$SKEL/wiring.pristine"
restore() { cp "$SKEL/hook.pristine" "$HOOK"; cp "$SKEL/wiring.pristine" "$WIRING"; }

# The unmutated copy must be green, or every RED below means nothing.
base_out="$(bash "$PIN" "$SKEL" 2>&1)"; base_rc=$?
if [ "$base_rc" != 0 ]; then
  echo "  BASELINE NOT GREEN — the unmutated copy fails, so no mutation result is meaningful"
  printf '%s\n' "$base_out" | grep -E '^  FAIL' | head -5
  exit 1
fi
echo "  baseline green"

MUTATED=0
mutate() {  # <file> <old> <new>
  python3 - "$1" "$2" "$3" <<'PY'
import sys
from pathlib import Path
p, old, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
s = p.read_text()
if old not in s:
    print(f"  MUTATION DID NOT APPLY: {old[:70]}"); sys.exit(3)
p.write_text(s.replace(old, new, 1))
PY
  MUTATED=$?
}

check() {  # <mutation name> <label of the pin case that must go FAIL>
  local name="$1" want="$2" out rc
  if [ "$MUTATED" != 0 ]; then
    echo "  NOT RED — MUTATION IS STALE, not the pin: $name"; restore; return
  fi
  out="$(bash "$PIN" "$SKEL" 2>&1)"; rc=$?
  if [ "$rc" != 0 ] && printf '%s\n' "$out" | grep -qF "  FAIL $want"; then
    echo "  RED, as required — $name"
  else
    echo "  NOT RED (rc=$rc) — the pin cannot catch: $name"
  fi
  restore
}

mutate "$HOOK" 'if payload.get("stop_hook_active") is True:  # MUT-ACTIVE' 'if False:  # MUT-ACTIVE'
check "MUT-ACTIVE: ignore stop_hook_active" "C1 stop_hook_active true allows despite unfinished code"

mutate "$HOOK" 'if live and said_waiting:  # MUT-WAIT' 'if live:  # MUT-WAIT'
check "MUT-WAIT-PROSE: any running task is a free pass, no marker needed" "C2 running task but no LOOPKIT:WAITING marker is refused"

mutate "$HOOK" 'if live and said_waiting:  # MUT-WAIT' 'if False:  # MUT-WAIT'
check "MUT-WAIT-OFF: no waiting pass at all" "C2 running background task plus LOOPKIT:WAITING allows"

mutate "$HOOK" 'if status in TERMINAL:' 'if False:'
check "MUT-TERMINAL: a finished task still counts as in flight" "C2 LOOPKIT:WAITING with only finished tasks is refused"

mutate "$HOOK" 'if before.get(path) != h:  # MUT-SCOPE' 'if True:  # MUT-SCOPE'
check "MUT-SCOPE: every dirty file is this turn's (the wrong-folder bug)" "C3 pre-existing untracked code, a dirty file and a nested worktree are not this turn's"

mutate "$HOOK" 'if head and head0 != head:' 'if False:'
check "MUT-COMMITTED: commits made during the turn are invisible" "C3 a code file committed during the turn counts"

mutate "$HOOK" 'if rec_head != head:  # MUT-EV-HEAD' 'if False:  # MUT-EV-HEAD'
check "MUT-EV-HEAD: evidence from another HEAD is accepted" "C6 evidence recorded at another HEAD is refused"

mutate "$HOOK" 'if w > now + 5 or now - w > max_age:  # MUT-EV-TIME' 'if False:  # MUT-EV-TIME'
check "MUT-EV-TIME: evidence of any age is accepted" "C6 evidence written a day ago is refused"

mutate "$HOOK" '    if code:
        return "invalid", "evidence lists code' '    if False:
        return "invalid", "evidence lists code'
check "MUT-EV-CODE: evidence may list code files" "C6 evidence that lists a code file is refused"

mutate "$HOOK" 'if scope == "tree" and ev_status == "valid":  # MUT-EV-SCOPE' 'if ev_status == "valid":  # MUT-EV-SCOPE'
check "MUT-EV-SCOPE: a claim overrides git's measurement" "C7 with a turn record, evidence cannot override code git sees changed"

mutate "$HOOK" '        elif jv == "REJECT":
            verdict, message = "BLOCK", reject_reason' '        elif jv == "REJECT":
            verdict, message = "ALLOW", reject_reason'
check "MUT-NO-GATE: a REJECT from the judge lets the stop through" "C8 control: unfinished code with no background work is refused"

mutate "$HOOK" 'return ("PASS" if rc == 0 else "REJECT")' 'return ("PASS" if True else "REJECT")'
check "MUT-REVIEW-CMD: a failing review command is ignored" "C9 a failing review command blocks with its output"

mutate "$HOOK" 'verdict = "ALLOW"  # MUT-SECOND' 'verdict = "BLOCK" if "REJECT" in message else "ALLOW"  # MUT-SECOND'
check "MUT-SECOND: the judge's verdict on a second firing blocks" "J2 a second firing with stop_hook_active never blocks, whatever the judge says"

mutate "$HOOK" '"--disallowedTools", "Bash",  # MUT-JUDGE-BASH' '"--allowedTools", "Bash",  # MUT-JUDGE-BASH'
check "MUT-JUDGE-BASH: the judge is given Bash" "J1 the judge has no Bash and runs with hooks off"

mutate "$HOOK" 'if nested():  # MUT-NESTED' 'if False:  # MUT-NESTED'
check "MUT-NESTED: the stop review runs inside its own judge (recursion)" "J6 inside the judge's own session the stop review stands down (no recursion)"

mutate "$HOOK" 'if jv == "PASS":  # MUT-JUDGE-PASS' 'if False:  # MUT-JUDGE-PASS'
check "MUT-JUDGE-PASS: a PASS verdict is ignored" "J4 a PASS verdict allows the first stop"

mutate "$HOOK" 'p = subprocess.run(["git", "-C", str(root), *args], input=stdin,' 'p = subprocess.run(["bash", "-c", 'git -C "$0" "$@"', str(root), *args], input=stdin,'
check "MUT-NEEDS-BASH: the hook shells out through bash to run git" "C11 with no bash on PATH, a valid evidence file is accepted"

mutate "$WIRING" 'stop_review.py\" turn-start' 'stop_review.py\" turn-begin'
check "MUT-WIRING: turn-start not wired" "C10 Stop is decided by a command hook and turn-start is wired"

if [ "$(cksum < "$W/plugins/loopkit/hooks/stop_review.py")" != "$TRACKED_SUM" ]; then
  echo "  TRACKED FILE CHANGED — this harness wrote to the tree it must only read"
  exit 1
fi
