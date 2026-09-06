#!/usr/bin/env bash
# run-capped.sh — run a command, keep its full output on disk, print a cap.
#
# The single most implementable context-engineering rule: tool output over a
# couple of thousand tokens belongs in a file with a path and a short summary
# in context, not in the window. A PostToolUse hook cannot rewrite a tool
# result, so this is the honest half: wrap the command yourself.
#
#   run-capped.sh [--head N] [--tail N] -- <command...>
#   run-capped.sh [--head N] [--tail N] --shell -- '<one shell string>'
#
# Without --shell everything after `--` is an argv and runs as-is, whatever its
# length: `-- "/dir with space/prog"` is one program, never a shell string.
# WITH --shell the single argument runs under `bash -o pipefail -c` (the form
# hooks/offload_rewrite.py emits), so `'false | true'` exits 1 and the quoting
# inside it is the caller's, byte for byte. Arity used to decide this, which
# broke any one-word argv with a space in it — PR #7 review, 2026-09-07.
#
# Full output: .loopkit/scratch/<ts>-<cmd>.txt. Exit code: the command's, and
# pipefail is preserved: nothing here is a pipe, and the shell-string form
# sets it explicitly.

set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
HEAD=20; TAIL=20; SHELL_FORM=0
while [ $# -gt 0 ]; do
  case "$1" in
    --head) HEAD="$2"; shift 2 ;;
    --tail) TAIL="$2"; shift 2 ;;
    --shell) SHELL_FORM=1; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || { echo "usage: run-capped.sh [--head N] [--tail N] [--shell] -- <command...>" >&2; exit 2; }
mkdir -p "$ROOT/.loopkit/scratch"
slug="$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '-' | cut -c1-24)"
if [ "$SHELL_FORM" = 1 ]; then
  [ $# -eq 1 ] || { echo "run-capped.sh: --shell takes exactly one argument" >&2; exit 2; }
  set -- bash -o pipefail -c "$1"
fi
out="$ROOT/.loopkit/scratch/$(date +%Y%m%dT%H%M%S)-${slug}.txt"
"$@" > "$out" 2>&1
rc=$?
total="$(grep -c '' "$out" || true)"
if [ "${total:-0}" -le $((HEAD + TAIL)) ]; then
  cat "$out"
else
  head -n "$HEAD" "$out"
  echo "… [$((total - HEAD - TAIL)) lines omitted — full output: ${out#$ROOT/}]"
  tail -n "$TAIL" "$out"
fi
echo "run-capped: exit $rc, $total lines, ${out#$ROOT/}"
exit $rc
