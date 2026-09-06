#!/usr/bin/env bash
# run-capped.sh — run a command, keep its full output on disk, print a cap.
#
# The single most implementable context-engineering rule: tool output over a
# couple of thousand tokens belongs in a file with a path and a short summary
# in context, not in the window. A PostToolUse hook cannot rewrite a tool
# result, so this is the honest half: wrap the command yourself.
#
#   run-capped.sh [--head N] [--tail N] -- <command...>
#   run-capped.sh [--head N] [--tail N] -- '<one shell string>'
#
# Several words after `--` are an argv and run as-is. ONE word is a shell
# string (the form hooks/offload_rewrite.py emits) and runs under
# `bash -o pipefail -c`, so `'false | true'` exits 1 and quoting inside it is
# the caller's, byte for byte.
#
# Full output: .loopkit/scratch/<ts>-<cmd>.txt. Exit code: the command's, and
# pipefail is preserved: nothing here is a pipe, and the shell-string form
# sets it explicitly.

set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
HEAD=20; TAIL=20
while [ $# -gt 0 ]; do
  case "$1" in
    --head) HEAD="$2"; shift 2 ;;
    --tail) TAIL="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || { echo "usage: run-capped.sh [--head N] [--tail N] -- <command...>" >&2; exit 2; }
mkdir -p "$ROOT/.loopkit/scratch"
slug="$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '-' | cut -c1-24)"
if [ $# -eq 1 ]; then set -- bash -o pipefail -c "$1"; fi
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
