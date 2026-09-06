#!/usr/bin/env bash
# precompact.sh — PreCompact hook: write the deterministic half of the summary
# to disk BEFORE the context is compacted, so the next window can read it back.
#
# Why a file and not stdout: a compaction hook cannot edit the summary the
# model writes, and whether its stdout reaches that summary is not something
# this plugin asserts. A file under .loopkit/session/<key>/ is read by
# session_start.sh on `compact`/`resume`, which is deterministic regardless.
# Fail-open: exit 0 always.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(dirname "$HERE")}"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

raw="$(cat 2>/dev/null || true)"
key="$(printf '%s' "$raw" | python3 -c 'import hashlib,json,sys
try:
    d=json.load(sys.stdin); s=str(d.get("session_id") or "")
except Exception:
    s=""
print(hashlib.sha256(s.encode()).hexdigest()[:16] if s else "unknown")' 2>/dev/null || echo unknown)"

dir="$ROOT/.loopkit/session/$key"
mkdir -p "$dir" 2>/dev/null || exit 0
python3 "$PLUGIN_ROOT/scripts/progress.py" snapshot --root "$ROOT" > "$dir/precompact.md" 2>/dev/null || true
python3 "$PLUGIN_ROOT/scripts/progress.py" append --root "$ROOT" --text "compaction: snapshot written to .loopkit/session/$key/precompact.md" >/dev/null 2>&1 || true
exit 0
