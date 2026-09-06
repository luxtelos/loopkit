#!/usr/bin/env bash
# session_start.sh — one line of orientation at SessionStart, nothing more.
#
# A SessionStart hook's stdout is added to the model's context. This prints a
# pointer, not the contracts themselves: injection is skimmed like a file is
# skimmed, and require_contracts.py enforces the read anyway. Absolute paths
# are printed because the Bash tool does not see ${CLAUDE_PLUGIN_ROOT}.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(dirname "$HERE")}"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

if [ ! -d "$ROOT/.loopkit" ]; then
  echo "LOOPKIT: this project is not initialised. Run /loopkit:init (or: bash \"$PLUGIN_ROOT/scripts/loopkit-init.sh\") to lay down state/, inbox/, specs/, .loopkit/ and the three contracts. Until then the loop hooks stay passive."
  exit 0
fi

echo "LOOPKIT: read FILES.md (memory routing), TOOLS.md (tool diet), COMMANDS.md (how work fires) before any work — a gate blocks work tools until you do. Scripts live at $PLUGIN_ROOT/scripts; the next stage is a lookup: bash \"$PLUGIN_ROOT/scripts/loop-next.sh\""
exit 0
