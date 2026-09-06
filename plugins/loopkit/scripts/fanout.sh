#!/usr/bin/env bash
# fanout.sh — run one headless Claude per brief, each in its own worktree.
#
# The multi-agent lesson (anthropic.com/engineering/multi-agent-research-system):
# every brief needs an objective, an output format, tool guidance and
# boundaries; token use explains most of the variance; and a fan-out costs
# ~15x a single chat — so this wraps `claude -p` with the four-part brief
# template, scopes tools, caps turns, isolates each run in a git worktree, and
# writes one JSON result per brief. It never merges anything.
#
#   fanout.sh --briefs DIR [--allowed-tools "Read,Grep,Glob"] [--max-turns 30]
#             [--model NAME] [--run-id ID] [--keep-worktrees]
#
# Each DIR/*.md is one brief (templates/brief.md is the shape). Results land in
# state/fanout/<run-id>/<brief>.json; a summary line per brief is printed.
# Convergence: a brief whose worktree shows no diff is reported as
# "no change"; the caller decides whether that is done or stuck.
# Requires `claude` on PATH (a stub on PATH is enough for the selftest).

set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
BRIEFS=""; TOOLS="Read,Grep,Glob,Bash"; MAX_TURNS=30; MODEL=""; RUN_ID=""; KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --briefs) BRIEFS="$2"; shift 2 ;;
    --allowed-tools) TOOLS="$2"; shift 2 ;;
    --max-turns) MAX_TURNS="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --keep-worktrees) KEEP=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$BRIEFS" ] && [ -d "$BRIEFS" ] || { echo "usage: fanout.sh --briefs DIR" >&2; exit 2; }
command -v claude >/dev/null 2>&1 || { echo "fanout: claude not on PATH" >&2; exit 2; }
RUN_ID="${RUN_ID:-$(date +%Y%m%dT%H%M%S)}"
OUT="$ROOT/state/fanout/$RUN_ID"; mkdir -p "$OUT"
WT_BASE="$ROOT/.claude/worktrees/fanout-$RUN_ID"

n=0; ok=0
for brief in "$BRIEFS"/*.md; do
  [ -f "$brief" ] || continue
  name="$(basename "$brief" .md)"; n=$((n+1))
  wt="$WT_BASE-$name"
  if git -C "$ROOT" worktree add -q --detach "$wt" HEAD 2>/dev/null; then :; else wt="$ROOT"; fi
  args=(-p --output-format json --max-turns "$MAX_TURNS" --allowedTools "$TOOLS")
  [ -n "$MODEL" ] && args+=(--model "$MODEL")
  ( cd "$wt" && claude "${args[@]}" < "$brief" ) > "$OUT/$name.json" 2> "$OUT/$name.stderr" ; rc=$?
  changed="$(git -C "$wt" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$rc" = 0 ]; then ok=$((ok+1)); fi
  echo "FANOUT $name: rc=$rc changed_files=$changed result=state/fanout/$RUN_ID/$name.json"
  if [ "$wt" != "$ROOT" ] && [ "$KEEP" = 0 ]; then git -C "$ROOT" worktree remove --force "$wt" >/dev/null 2>&1 || true; fi
done
echo "FANOUT: $ok/$n briefs exited 0; results in state/fanout/$RUN_ID/ — nothing was merged."
[ "$n" -gt 0 ] || exit 2
exit 0
