#!/usr/bin/env bash
# session_start.sh — orientation at SessionStart, nothing more.
#
# A SessionStart hook's stdout is added to the model's context. This prints a
# pointer, not the contracts themselves: injection is skimmed like a file is
# skimmed, and require_contracts.py enforces the read anyway. Absolute paths
# are printed because the Bash tool does not see ${CLAUDE_PLUGIN_ROOT}.
#
# With the argument `resume` (wired on the SessionStart matcher
# `resume|compact`) it also prints the deterministic half of session memory —
# Files Modified from git, the last progress line, the last commits — which is
# the part every compaction summary gets wrong when left to the model
# (anthropic.com/engineering/effective-harnesses-for-long-running-agents:
# "check cwd, read git log + progress file, pick the next feature").

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(dirname "$HERE")}"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
MODE="${1:-startup}"

if [ "$MODE" = "resume" ]; then
  total="$(python3 "$PLUGIN_ROOT/scripts/progress.py" files-modified --root "$ROOT" 2>/dev/null | grep -c . || true)"
  files="$(python3 "$PLUGIN_ROOT/scripts/progress.py" files-modified --root "$ROOT" --limit 8 2>/dev/null | tr '\n' ' ')"
  [ "${total:-0}" -gt 8 ] && files="$files(+$((total - 8)) more) "
  lastline="$(python3 "$PLUGIN_ROOT/scripts/progress.py" last --root "$ROOT" 2>/dev/null)"
  log="$(git -C "$ROOT" log --oneline -3 2>/dev/null | tr '\n' ';')"
  echo "LOOPKIT RESUME: files modified: ${files:-none}| last progress: ${lastline:-none} | recent commits: ${log:-none}"
  latest="$(ls -t "$ROOT"/.loopkit/session/*/precompact.md 2>/dev/null | head -1)"
  [ -n "$latest" ] && echo "LOOPKIT RESUME: pre-compaction snapshot at $latest (Session Intent / Files Modified / Decisions / Current State / Next Steps)"
  exit 0
fi

if [ ! -d "$ROOT/.loopkit" ]; then
  echo "LOOPKIT: this project is not initialised. Run /loopkit:init (or: bash \"$PLUGIN_ROOT/scripts/loopkit-init.sh\") to lay down state/, inbox/, specs/, .loopkit/ and the three contracts. Until then the loop hooks stay passive."
  exit 0
fi

if [ -f "$ROOT/.loopkit/memory.json" ]; then
  mem="$(timeout 12 python3 "$PLUGIN_ROOT/scripts/memory.py" status --line --root "$ROOT" 2>/dev/null || true)"
  [ -n "$mem" ] && echo "$mem — recall: python3 \"$PLUGIN_ROOT/scripts/memory.py\" recall \"<query>\""
fi
echo "LOOPKIT: read FILES.md (memory routing), TOOLS.md (tool diet), COMMANDS.md (how work fires) before any work — a gate blocks work tools until you do. Scripts live at $PLUGIN_ROOT/scripts; the next stage is a lookup: bash \"$PLUGIN_ROOT/scripts/loop-next.sh\""
exit 0
