#!/usr/bin/env bash
# loop-watch.sh — one cheap tick for a PR watch.
#
# Why this exists: a watch loop re-reads the same sources every tick. Measured
# on a real watch: the GitHub state check costs ~70 bytes; the chat-thread read
# costs ~9,000 and returns byte-identical text every time. Over ~40 quiet ticks
# that is ~360KB of re-reading for zero new information.
#
# So this script answers "did anything change?" deterministically and prints
# one line. The model only spends a full read when this says something moved.
# Deterministic logic solves deterministic problems.
#
# usage: loop-watch.sh --pr <number> [--repo owner/name] [--chat-every N] [--state FILE]
# exit 0 always; read the VERDICT line, not the exit code.
#
# Env: LOOPKIT_REPO (owner/name; else `gh repo view`), LOOPKIT_WATCH_STATE_DIR
# (default $TMPDIR or /tmp), GH_TOKEN for gh when not logged in.

set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
if [ -f "$ROOT/.loopkit/config.env" ]; then set -a; . "$ROOT/.loopkit/config.env"; set +a; fi

REPO="${LOOPKIT_REPO:-}"
PR=""
CHAT_EVERY=4
STATE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --pr) PR="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --chat-every|--slack-every) CHAT_EVERY="$2"; shift 2 ;;
    --state) STATE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -z "$PR" ] && { echo "usage: loop-watch.sh --pr <number>" >&2; exit 2; }
[ -z "$REPO" ] && REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
[ -z "$REPO" ] && { echo "VERDICT: ERROR — no repo. Set LOOPKIT_REPO=owner/name or pass --repo."; exit 0; }

# The baseline is PER PR and PER REPO, and the default is computed AFTER
# parsing because it depends on both. A single shared file once compared PR B
# against PR A's stored SHA — always different, so always "ACT" — and the
# write clobbered A's baseline so A false-fired next. A watch that cries ACT
# every time is worse than no watch: the whole point is a cheap quiet tick.
STATE="${STATE:-${LOOPKIT_WATCH_STATE_DIR:-${TMPDIR:-/tmp}}/loop-watch-$(printf '%s' "$REPO" | tr '/' '-')-${PR}.state}"

now="$(gh pr view "$PR" --repo "$REPO" \
        --json state,reviewDecision,mergedAt,headRefOid \
        --jq '"\(.state)|\(.reviewDecision)|\(.mergedAt//"-")|\(.headRefOid[0:8])"' 2>/dev/null)"

if [ -z "$now" ]; then
  echo "VERDICT: ERROR — could not read PR #$PR in $REPO (auth or network). Check before trusting a quiet tick."
  exit 0
fi

prev=""; ticks=0
if [ -f "$STATE" ]; then
  prev="$(sed -n '1p' "$STATE")"
  ticks="$(sed -n '2p' "$STATE" 2>/dev/null)"
  case "$ticks" in ''|*[!0-9]*) ticks=0 ;; esac
fi

if [ "$now" = "$prev" ]; then
  ticks=$((ticks + 1))
  changed="no"
else
  ticks=0
  changed="yes"
fi
printf '%s\n%s\n' "$now" "$ticks" > "$STATE"

IFS='|' read -r st rev merged head <<< "$now"
echo "PR#$PR  state=$st  review=$rev  merged=$merged  head=$head"

if [ "$changed" = "yes" ] && [ -n "$prev" ]; then
  echo "CHANGED from: $prev"
  echo "VERDICT: ACT — the watched PR moved. Read it properly and respond."
  exit 0
fi
if [ -z "$prev" ]; then
  echo "VERDICT: BASELINE — first run, nothing to compare. Treat as quiet."
  exit 0
fi

# The chat thread is the expensive source and the least likely to move. Poll
# it on a fraction of ticks rather than every one.
if [ "$CHAT_EVERY" -gt 0 ] && [ $((ticks % CHAT_EVERY)) -eq 0 ]; then
  echo "CHAT: poll due this tick (every $CHAT_EVERY quiet ticks)"
else
  echo "CHAT: skip (next poll in $(( CHAT_EVERY - (ticks % CHAT_EVERY) )) tick(s))"
fi

echo "QUIET for $ticks consecutive ticks"
if [ "$ticks" -ge 24 ]; then
  echo "VERDICT: ESCALATE — unchanged for $ticks ticks. A watch this quiet is not"
  echo "         producing information; chase the human or stop the loop."
else
  echo "VERDICT: QUIET — hold, no full read needed."
fi
