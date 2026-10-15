#!/usr/bin/env bash
# inbox-precedent-on-merge.sh — the inbox precedent check judges a MERGE commit
# too, and merging the trunk in does not re-judge the trunk's own inbox.
#
# WHY. Two features edit loop-commit.sh: the merge path (a stopped merge is
# finished from a private index, loop-commit-merge.sh) and the inbox precedent
# check (check-inbox-precedent.py, before anything is staged). The merge path
# exits the wrapper, so a check placed after it never sees a merge commit — and
# a merge commit publishes everything the merge brought in, named or not. The
# other way to get it wrong is the base: judged against the branch's old merge
# base, the trunk's sections arriving in the merge read as this branch's new
# work, and merging the trunk into a branch is refused for sections the trunk
# already holds. The gate's base folds MERGE_HEAD in to prevent that.
#
#   P1  merging the trunk, whose inbox holds a section with no precedent line,
#       completes — named inbox or not. (control: the ordinary merge path)
#   P2  a NEW bare section written while resolving the merge is refused
#       (exit 65): the merge is still in progress and HEAD has not moved
#   P3  merging a NON-trunk branch that added a bare section is refused even
#       when the inbox is not named, since the merge commit would publish it
#   P4  the same new section WITH a usable line completes as a merge commit
#   P5  an ordinary (no-merge) commit of a new bare section is still refused
#
# Then it proves it can fail: two mutants of a copy of the plugin.
#   MUT-AFTER  the check moved after the merge path   -> P2 and P3 go red
#   MUT-BASE   gate_base ignores MERGE_HEAD           -> P1 goes red
#
# Every fixture is a throwaway repo under $TMPDIR. Nothing here reads or writes
# the tracked tree.
#
# usage: bash tests/pins/inbox-precedent-on-merge.sh [<repo-root>]

set -uo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
REPO="$(cd "$REPO" && pwd)"
PLUGIN="$REPO/plugins/loopkit"
[ -f "$PLUGIN/scripts/loop-commit.sh" ] || { echo "FAIL no loop-commit.sh under $PLUGIN"; exit 2; }

# Hermetic, as in loop-commit-finishes-a-merge.sh.
for v in $(env | sed -n 's/^\(LOOP[A-Z_]*\)=.*/\1/p; s/^\(CLAUDE_PROJECT_DIR\)=.*/\1/p; s/^\(GIT_[A-Z_]*\)=.*/\1/p'); do
  unset "$v"
done
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_EDITOR=: EDITOR=: VISUAL=:

fails=0
ok()  { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails+1)); }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-inbox-merge-pin.XXXXXX")"
trap 'find "$WORK" -mindepth 1 -delete 2>/dev/null; rmdir "$WORK" 2>/dev/null || true' EXIT

n=0
g() { git -C "$W" "$@"; }

LINE='Precedent searched: grep -rn "merge" specs/ docs/adr/ → no ruling found'

# main: an inbox with one old section. feat branches off it. main then adds a
# section with NO precedent line (a trunk written before the rule) and edits
# CHANGELOG.md; feat edits CHANGELOG.md too, so `git merge main` stops.
mk() {
  n=$((n+1)); W="$WORK/fx$n"; mkdir -p "$W/inbox"
  git -c init.defaultBranch=main init -q "$W" >/dev/null 2>&1
  g config user.email t@t; g config user.name t; g config commit.gpgsign false
  printf '# Needs human\n\n## Old question (2026-09-01)\n\nold body\n' > "$W/inbox/needs-human.md"
  printf 'base\n' > "$W/CHANGELOG.md"
  g add inbox/needs-human.md CHANGELOG.md; g commit -qm base
  g checkout -qb feat
  printf 'feat\n' > "$W/CHANGELOG.md"; g commit -qam feat
  g checkout -q main
  printf '\n## Trunk question with no line (2026-10-01)\n\ntrunk body\n' >> "$W/inbox/needs-human.md"
  printf 'main\n' > "$W/CHANGELOG.md"; g commit -qam main
  g checkout -q feat
}
# Merge <ref> into feat; stop on the CHANGELOG conflict and resolve it on disk.
stopped_merge() {  # <ref>
  g merge "$1" >/dev/null 2>&1
  [ -f "$W/.git/MERGE_HEAD" ] || { bad "fixture: merging $1 did not stop on a conflict"; return 1; }
  printf 'feat\nmain\n' > "$W/CHANGELOG.md"
}
run() { local lc="$1"; shift; OUT="$(cd "$W" && bash "$lc" "$@" 2>&1)"; RC=$?; }
parents() { g rev-list --parents -1 HEAD | wc -w | tr -d ' '; }
still_merging() { [ -f "$W/.git/MERGE_HEAD" ]; }

# Each case sets one result word in a global, so the mutants can be judged by
# the same code: done (a merge/plain commit landed) | refused (exit 65, nothing
# moved) | other:<rc>.
verdict() {  # <before-sha> <want-merge 0|1>
  if [ "$RC" = 65 ] && [ "$(g rev-parse HEAD)" = "$1" ]; then
    if [ "$2" = 1 ] && ! still_merging; then V="other:merge-lost"; else V=refused; fi
  elif [ "$RC" = 0 ] && [ "$(g rev-parse HEAD)" != "$1" ]; then
    V=done
  else
    V="other:$RC"
  fi
}

p1() {  # <loop-commit.sh>  -> sets P1A P1B
  mk; stopped_merge main || { P1A=fixture; P1B=fixture; return; }
  local b; b="$(g rev-parse HEAD)"
  run "$1" -m "merge main" -- CHANGELOG.md; verdict "$b" 1; P1A="$V"
  [ "$V" = done ] && [ "$(parents)" != 3 ] && P1A="other:not-a-merge"
  mk; stopped_merge main || { P1B=fixture; return; }
  b="$(g rev-parse HEAD)"
  run "$1" -m "merge main" -- CHANGELOG.md inbox/needs-human.md; verdict "$b" 1; P1B="$V"
}
p2() {
  mk; stopped_merge main || { P2=fixture; return; }
  printf '\n## A new question asked mid-merge (2026-10-15)\n\nno line\n' >> "$W/inbox/needs-human.md"
  local b; b="$(g rev-parse HEAD)"
  run "$1" -m "merge main" -- CHANGELOG.md inbox/needs-human.md; verdict "$b" 1; P2="$V"
}
p3() {
  mk
  # `other` is a non-trunk branch off main that adds a bare section and edits
  # CHANGELOG.md, so merging it into feat stops on the conflict.
  g checkout -q -b other main
  printf '\n## A question from a side branch (2026-10-14)\n\nno line\n' >> "$W/inbox/needs-human.md"
  printf 'other\n' > "$W/CHANGELOG.md"; g commit -qam other
  g checkout -q feat
  stopped_merge other || { P3=fixture; return; }
  local b; b="$(g rev-parse HEAD)"
  run "$1" -m "merge other" -- CHANGELOG.md; verdict "$b" 1; P3="$V"
}
p4() {
  mk; stopped_merge main || { P4=fixture; return; }
  printf '\n## A new question asked mid-merge (2026-10-15)\n\n%s\n' "$LINE" >> "$W/inbox/needs-human.md"
  local b; b="$(g rev-parse HEAD)"
  run "$1" -m "merge main" -- CHANGELOG.md inbox/needs-human.md; verdict "$b" 1; P4="$V"
  [ "$V" = done ] && { [ "$(parents)" = 3 ] && g show HEAD:inbox/needs-human.md | grep -qF "$LINE"; } || { [ "$V" = done ] && P4="other:not-a-merge-with-the-line"; }
}
p5() {
  mk
  printf '\n## A plain new question (2026-10-15)\n\nno line\n' >> "$W/inbox/needs-human.md"
  local b; b="$(g rev-parse HEAD)"
  run "$1" -m "ask" -- inbox/needs-human.md; verdict "$b" 0; P5="$V"
}
all_cases() { p1 "$1"; p2 "$1"; p3 "$1"; p4 "$1"; p5 "$1"; }

LC="$PLUGIN/scripts/loop-commit.sh"
all_cases "$LC"
echo "P1 merging the trunk, whose inbox has a section with no line, completes"
[ "$P1A" = done ] && ok "inbox not named: merge commit made" || bad "inbox not named: $P1A"
[ "$P1B" = done ] && ok "inbox named: merge commit made" || bad "inbox named: $P1B"
echo "P2 a new bare section written while resolving is refused"
[ "$P2" = refused ] && ok "exit 65, HEAD unmoved, merge still in progress" || bad "$P2"
echo "P3 a bare section brought in from a non-trunk branch is refused, unnamed"
[ "$P3" = refused ] && ok "exit 65, HEAD unmoved, merge still in progress" || bad "$P3"
echo "P4 the same new section with a usable line completes"
[ "$P4" = done ] && ok "two-parent merge commit, with the line in it" || bad "$P4"
echo "P5 an ordinary commit of a new bare section is still refused"
[ "$P5" = refused ] && ok "exit 65, HEAD unmoved" || bad "$P5"

# --- prove it can fail -------------------------------------------------------
mutant() {  # <name>  -> echoes the path of a fresh plugin copy
  local d="$WORK/$1"; mkdir -p "$d"
  cp -R "$PLUGIN" "$d/loopkit"
  printf '%s' "$d/loopkit"
}

echo "MUT-AFTER the check moved after the merge path: P2 and P3 must go red"
M="$(mutant after)"
python3 - "$M/scripts/loop-commit.sh" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
start = s.index("if [ -f \"$ROOT/inbox/needs-human.md\" ]; then\n  INBOX_BASE=")
end = s.index("\nfi\n", start) + len("\nfi\n")
block = s[start:end]
s = s[:start] + s[end:]
anchor = "# Stage only what this caller named."
s = s.replace(anchor, block + "\n" + anchor, 1)
open(p, "w").write(s)
PY
all_cases "$M/scripts/loop-commit.sh"
{ [ "$P2" != refused ] && [ "$P3" != refused ]; } && ok "RED, as required — P2 '$P2', P3 '$P3'" || bad "NOT RED — P2 '$P2', P3 '$P3'"
{ [ "$P1A" = done ] && [ "$P5" = refused ]; } && ok "... and P1 / P5 stay green" || bad "the mutant broke an unrelated case: P1 '$P1A', P5 '$P5'"

echo "MUT-BASE gate_base ignores MERGE_HEAD: P1 must go red"
M="$(mutant base)"
python3 - "$M/hooks/stop_gate.sh" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
old = 'base="$(git merge-base "$default_ref" HEAD ${merge_head:+"$merge_head"} 2>/dev/null || true)"'
assert old in s, "mutation anchor not found"
s = s.replace(old, 'base="$(git merge-base "$default_ref" HEAD 2>/dev/null || true)"', 1)
open(p, "w").write(s)
PY
all_cases "$M/scripts/loop-commit.sh"
[ "$P1B" != done ] && ok "RED, as required — P1 (inbox named) '$P1B'" || bad "NOT RED — P1 (inbox named) '$P1B'"
{ [ "$P5" = refused ] && [ "$P2" = refused ]; } && ok "... and P2 / P5 stay green" || bad "the mutant broke an unrelated case: P2 '$P2', P5 '$P5'"

echo
[ "$fails" = 0 ] && echo "PASS inbox precedent is judged on a merge commit" || echo "FAIL $fails check(s)"
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
