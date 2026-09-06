#!/usr/bin/env bash
# morning-triage.sh — manual trigger for a headless triage run.
#
# Deliberately NOT on a timer by default: a human decides when the loop wakes
# up. Run it from anywhere:
#
#   bash "$CLAUDE_PLUGIN_ROOT/scripts/morning-triage.sh"
#
# It starts a headless Claude Code session in the project, runs the
# loopkit:morning-triage skill (find + rank work, write state/triage.md), and
# appends everything to state/cron-triage.log so runs stay auditable. If you do
# want it on a timer, put THIS script in cron — never a bare `claude -p`, or
# the log line that says which run did what never lands.
#
# Optional: LOOPKIT_TRIAGE_PRECHECK — a command run (and logged, fail-open)
# before the session starts, e.g. a deploy-drift or health check.

set -euo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT"
if [ -f "$ROOT/.loopkit/config.env" ]; then set -a; . "$ROOT/.loopkit/config.env"; set +a; fi

# Headless launchers get a bare environment sometimes (cron, Shortcuts) —
# make sure the usual tool locations are reachable.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$PATH"

mkdir -p state
LOG="state/cron-triage.log"
echo "=== triage run started $(date -u +%Y-%m-%dT%H:%M:%SZ) by $(whoami) ===" >> "$LOG"

if [ -n "${LOOPKIT_TRIAGE_PRECHECK:-}" ]; then
  echo "--- precheck $(date -u +%Y-%m-%dT%H:%M:%SZ): $LOOPKIT_TRIAGE_PRECHECK ---" >> "$LOG"
  bash -c "$LOOPKIT_TRIAGE_PRECHECK" >> "$LOG" 2>&1 || \
    echo "precheck exited non-zero — see lines above; triage continues (fail-open)" >> "$LOG"
fi

# acceptEdits: the session may write state/ and specs/ drafts without
# prompting, but the deterministic hooks (dangerous-command, governance,
# stop gate) still apply. Capture the exit code instead of letting set -e
# abort, so the "finished" line always lands in the log.
rc=0
claude -p "/loopkit:morning-triage" --permission-mode acceptEdits >> "$LOG" 2>&1 || rc=$?

echo "=== triage run finished $(date -u +%Y-%m-%dT%H:%M:%SZ) (exit $rc) ===" >> "$LOG"
echo "Done (exit $rc). Tail of the log:"
tail -5 "$LOG"
exit "$rc"
