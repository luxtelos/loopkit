#!/usr/bin/env bash
# loop-next.sh — decide which ONE pipeline stage this tick should advance.
#
# Why deterministic: picking the next stage is a lookup over state/triage.md,
# not a judgement call. Letting the model choose means it drifts — a real loop
# once spent ~40 consecutive ticks on a single `pr-open` row while 40 rows sat
# unclassified at `new`. A script cannot drift.
#
# Precedence, highest first:
#   1. pr-open    — polled cheaply, never a stage that consumes the tick.
#   2. fixing     — work already started; finish before starting more.
#   3. spec-ready — a validated spec is ready to implement.
#   4. spec-draft — a draft needs EARS criteria.
#   5. new        — unclassified. THIS is where the backlog is.
#   6. (empty)    — nothing actionable; run discovery.
#
# usage: loop-next.sh [--state state/triage.md] [--scope <lane>]
# Always exit 0. Read the STAGE line.
#
# --scope restricts the pick to one lane, using the SAME named scopes as
# loop-scan.py (<project>/.loopkit/scopes.json, or a comma list of terms).
# A scoped pick is still a lookup, not a judgement — it takes the first
# matching row in file order. If nothing matches, it says so and falls back to
# the unscoped pick rather than serving an empty stage.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
STATE="$ROOT/state/triage.md"
SCOPE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --state) STATE="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

[ -f "$STATE" ] || { echo "STAGE: error — no state file at $STATE (run /loopkit:init, or morning-triage to seed it)"; exit 0; }

# Parse via triage_state.py, never by hand. It splits on unescaped pipes and
# unescapes `\|` on read; a naive awk -F'|' silently DROPS any row whose finding
# text contains a pipe, and a miscount here looks exactly like an empty backlog.
SUMMARY="$(python3 "$HERE/triage_state.py" list --state "$STATE" --format json 2>/dev/null \
  | LOOPKIT_PROJECT_ROOT="$ROOT" python3 "$HERE/loop_next_pick.py" "$SCOPE")"

if [ -z "$SUMMARY" ] || [ "$(printf '%s' "$SUMMARY" | head -1)" = "ERR" ]; then
  echo "STAGE: error — could not parse $STATE via triage_state.py. Do not guess; fix the state file."
  exit 0
fi

IFS='|' read -r N_PR N_FIX N_RDY N_DRF N_NEW <<< "$(printf '%s' "$SUMMARY" | head -1)"
first_of() { printf '%s' "$SUMMARY" | tail -n +2 | awk -F'\t' -v s="$1" '$1==s{print $2; exit}'; }

echo "BACKLOG: new=$N_NEW spec-draft=$N_DRF spec-ready=$N_RDY fixing=$N_FIX pr-open=$N_PR"
NOTE="$(first_of note)"
[ -n "$NOTE" ] && echo "$NOTE"

# Rows waiting on a human ruling. Reported so they stay visible, and excluded
# from the poll below: a blocked row cannot move however often it is checked.
N_BLOCKED="$(first_of blocked)"
[ -n "$N_BLOCKED" ] && echo "BLOCKED: $N_BLOCKED row(s) awaiting a human ruling — not polled, not a stage. See inbox/needs-human.md."

# pr-open is a CHEAP POLL, not a stage that consumes the tick. Making it a
# stage is what starved the backlog. It is reported separately and the tick
# continues to real work unless the watcher actually says something moved.
if [ "$N_PR" -gt 0 ]; then
  echo "POLL: $N_PR pr-open row(s) — run $HERE/loop-watch.sh FIRST (cheap)."
  echo "      If it says ACT or ESCALATE, that preempts and becomes this tick."
  echo "      If it says QUIET, ignore it and do the work stage below."
fi

if   [ "$N_FIX" -gt 0 ]; then S="fixing";     A="run the reviewer agent + the stop gate on the in-flight change. PASS -> open PR, set pr-open. FAIL -> cite the criterion."
elif [ "$N_RDY" -gt 0 ]; then S="spec-ready"; A="hand to the implementer in its own worktree and branch. Set fixing."
elif [ "$N_DRF" -gt 0 ]; then S="spec-draft"; A="run the spec-writer skill to produce EARS criteria. Carry the control case in. Set spec-ready."
elif [ "$N_NEW" -gt 0 ]; then S="new";        A="run loop-assess Mode A: baseline, classify, route. code -> spec-draft; anything else -> inbox and set the row inbox."
else                          S="discover";   A="no actionable rows. Run morning-triage to find work."
fi

echo "STAGE: $S"
if [ "$S" != "discover" ]; then
  T="$(first_of "$S")"
  echo "TARGET: ${T:0:110}"
  # Fan-out: every row at this stage (capped in loop_next_pick.py). A tick
  # advances the STAGE — one implementer/reviewer per row, each in its own
  # worktree — not one row of it. The TARGET line above is the first of these.
  printf '%s' "$SUMMARY" | awk -F'\t' -v s="target:$S" '$1==s{printf "TARGETS: %s  [source: %s]\n", substr($2,1,100), $3}'
fi
echo "ACTION: $A"
echo "RULE: advance exactly ONE stage this tick — for EVERY row listed under TARGETS,"
echo "      in parallel worktrees — then update each row via"
echo "      python3 $HERE/triage_state.py update --state $STATE --source <row source> --status <s>, and stop."
echo "      One stage per tick keeps transitions attributable; one ROW per tick was never the rule."
