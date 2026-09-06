#!/usr/bin/env bash
# loopkit-init.sh — lay the loop's files into a project, idempotently.
#
# Creates what is missing and never overwrites what exists:
#   state/triage.md, state/known-test-failures.txt
#   inbox/needs-human.md
#   specs/                       (empty; the spec-writer skill fills it)
#   constitution.md FILES.md TOOLS.md COMMANDS.md docs/MUTATION_POLICY.md
#   .loopkit/{scopes.json,citations.json,block-patterns.txt,block-disabled.txt,protected.txt,config.env.example}
#   .gitignore line for .loopkit/config.env
#   a "LoopKit standing rules" block appended to CLAUDE.md (marker-guarded)
#
# usage: loopkit-init.sh [--project DIR] [--dry-run] [--profile commerce]
# Exit 0 on success. Prints CREATED / KEPT for every path so the run is auditable.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(dirname "$HERE")}"
TPL="$PLUGIN_ROOT/templates"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
DRY=0
PROFILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project) ROOT="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --profile) PROFILE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
cd "$ROOT"

say() { printf '%-8s %s\n' "$1" "$2"; }

put() {  # <template relpath> <destination relpath>
  local src="$TPL/$1" dst="$2"
  if [ -e "$dst" ]; then say KEPT "$dst"; return 0; fi
  [ -f "$src" ] || { say MISSING "$src (template)"; return 0; }
  if [ "$DRY" = 1 ]; then say CREATE "$dst (dry run)"; return 0; fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  say CREATED "$dst"
}

mkdir_p() {
  if [ -d "$1" ]; then say KEPT "$1/"; return 0; fi
  if [ "$DRY" = 1 ]; then say CREATE "$1/ (dry run)"; return 0; fi
  mkdir -p "$1"; say CREATED "$1/"
}

echo "LoopKit init in $ROOT"
echo

mkdir_p state
mkdir_p inbox
mkdir_p specs
mkdir_p docs
mkdir_p .loopkit

put triage.md                  state/triage.md
put known-test-failures.txt    state/known-test-failures.txt
put needs-human.md             inbox/needs-human.md
put constitution.template.md   constitution.md
put FILES.md                   FILES.md
put TOOLS.md                   TOOLS.md
put COMMANDS.md                COMMANDS.md
put MUTATION_POLICY.md         docs/MUTATION_POLICY.md
put loopkit/scopes.json        .loopkit/scopes.json
put loopkit/citations.json     .loopkit/citations.json
put loopkit/block-patterns.txt .loopkit/block-patterns.txt
put loopkit/block-disabled.txt .loopkit/block-disabled.txt
put loopkit/protected.txt      .loopkit/protected.txt
put loopkit/test-globs.txt     .loopkit/test-globs.txt
put loopkit/memory.json        .loopkit/memory.json
put loopkit/recall-triggers.txt .loopkit/recall-triggers.txt
put loopkit/config.env.example .loopkit/config.env.example

# .gitignore: the one file under .loopkit/ that may hold a webhook URL.
if [ -f .gitignore ] && grep -qxF '.loopkit/config.env' .gitignore; then
  say KEPT ".gitignore (.loopkit/config.env already ignored)"
elif [ "$DRY" = 1 ]; then
  say APPEND ".gitignore: .loopkit/config.env (dry run)"
else
  printf '\n# LoopKit: may hold a webhook URL\n.loopkit/config.env\n' >> .gitignore
  say APPENDED ".gitignore: .loopkit/config.env"
fi

# .prettierignore: a formatter must never rewrite the queue. Prettier strips
# the spaces around inline code in a table cell, which changes the `source`
# key, which makes every bridged row a duplicate on the next run.
if [ -f .prettierignore ] && grep -qxF 'state/triage.md' .prettierignore; then
  say KEPT ".prettierignore (state/triage.md already listed)"
elif [ "$DRY" = 1 ]; then
  say APPEND ".prettierignore: state/triage.md (dry run)"
else
  printf '# LoopKit: the queue is written by triage_state.py, never reformatted\nstate/triage.md\n' >> .prettierignore
  say APPENDED ".prettierignore: state/triage.md"
fi

# CLAUDE.md: append the standing-rules block once, marker-guarded.
if [ -f CLAUDE.md ] && grep -q 'loopkit:begin' CLAUDE.md; then
  say KEPT "CLAUDE.md (LoopKit block present)"
elif [ "$DRY" = 1 ]; then
  say APPEND "CLAUDE.md: LoopKit standing rules (dry run)"
else
  [ -f CLAUDE.md ] || printf '# CLAUDE.md — standing rules for every session\n' > CLAUDE.md
  cat "$TPL/CLAUDE.snippet.md" >> CLAUDE.md
  say APPENDED "CLAUDE.md: LoopKit standing rules"
fi

# --profile <name>: append the profile's config to the project's, marker-guarded
# so a second run is a no-op. Profiles are templates and checks, never code.
if [ -n "$PROFILE" ]; then
  PDIR="$TPL/profiles/$PROFILE"
  [ -d "$PDIR" ] || { echo "no such profile: $PROFILE (have: $(ls "$TPL/profiles" | tr '\n' ' '))" >&2; exit 2; }
  append_profile() {  # <profile file> <destination>
    local src="$PDIR/$1" dst="$2" marker="# loopkit-profile:$PROFILE:$1"
    [ -f "$src" ] || return 0
    if [ -f "$dst" ] && grep -qF "$marker" "$dst"; then say KEPT "$dst ($PROFILE profile present)"; return 0; fi
    if [ "$DRY" = 1 ]; then say APPEND "$dst <- profiles/$PROFILE/$1 (dry run)"; return 0; fi
    mkdir -p "$(dirname "$dst")"
    { printf '\n%s\n' "$marker"; cat "$src"; } >> "$dst"
    say APPENDED "$dst <- profiles/$PROFILE/$1"
  }
  append_profile block-patterns.txt  .loopkit/block-patterns.txt
  append_profile protected.txt       .loopkit/protected.txt
  append_profile recall-triggers.txt .loopkit/recall-triggers.txt
  append_profile constitution.$PROFILE.md constitution.md
  if [ -d "$PDIR/evals" ]; then
    mkdir -p "evals/$PROFILE"
    for f in "$PDIR"/evals/*; do put "profiles/$PROFILE/evals/$(basename "$f")" "evals/$PROFILE/$(basename "$f")"; done
  fi
fi

echo
echo "Next:"
echo "  1. Read FILES.md, TOOLS.md, COMMANDS.md (a gate blocks work tools until you do)."
echo "  2. Fill .loopkit/config.env from the example: test/lint/build commands, env wrapper, webhook."
echo "  3. Seed the test baseline once:  bash \"$PLUGIN_ROOT/scripts/test-regressions.sh\" --update-baseline"
echo "  4. Find work:                    /loopkit:morning-triage   (or bash \"$PLUGIN_ROOT/scripts/morning-triage.sh\")"
echo "  5. Tick:                         /loop work the loopkit backlog"
echo
echo "Guards active from the next tool call: block_dangerous, protect_governance (constitution.md, specs/), require_contracts."
