#!/usr/bin/env bash
# loop-commit-finishes-a-merge.sh — a stopped merge must be completable through
# the wrapper, and completing it must not re-open the shared-index hole.
#
# WHY THIS PIN EXISTS. loop-commit.sh always ran `git commit -- <paths>`. With a
# merge in progress git answers "fatal: cannot do a partial commit during a
# merge." and exits 128. A bare `git commit` is refused by
# require_commit_lock.py. So a merge that stopped on a conflict had no allowed
# way to finish, and branches sat stuck on a one-file conflict.
#
# A merge commit publishes a whole tree, which is the thing the pathspec form
# was written to prevent, so each way that could go wrong has its own check:
#
#   M   the ordinary stuck merge completes (two parents, both sides' work)
#   R1  a foreign staged file present mid-merge is REFUSED, not swept in
#   RC  a foreign `git add` DURING the commit (from inside a slow pre-commit
#       hook) does not reach the merge commit, and stays staged afterwards.
#       The first fix checked the shared index and then committed it; git
#       re-reads a whole index after the pre-commit hook, so this swept.
#   R2  an unresolved path is REFUSED, the commit is not attempted
#   R3  naming the wrong paths is REFUSED; naming none is a usage error
#   R4  no merge in progress: only the named path is published (the control)
#   R5  cherry-pick: refused with a reason; revert and rebase stop: unchanged
#   CM  a named conflicted file still holding conflict markers is REFUSED
#   DIR a directory or `.` named on a merge is REFUSED (it would exempt all
#       under it)
#   XT  a merge run with -X theirs is refused until its files are named, then
#       completes with their working-tree content
#   D   a conflict resolved with `git rm` completes
#   L   the helper refuses to run outside the driver lock
#   X   the pin can fail: a helper that commits the shared index is caught
#
# R4 and the revert/rebase half of R5 pin behaviour that was already right, so
# they are green before the fix too.
#
# Every fixture is a throwaway repo under $TMPDIR. Nothing here reads or writes
# the tracked tree.
#
# usage: bash tests/pins/loop-commit-finishes-a-merge.sh [<repo-root> [<loop-commit.sh>]]

set -uo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
REPO="$(cd "$REPO" && pwd)"
LC="${2:-$REPO/plugins/loopkit/scripts/loop-commit.sh}"
[ -f "$LC" ] || { echo "FAIL no loop-commit.sh at $LC"; exit 2; }

# Hermetic: nothing inherited may steer the wrapper or git. An inherited
# LOOPKIT_DRIVER_LOCK would make the wrapper skip the lock; an inherited
# CLAUDE_PROJECT_DIR or GIT_DIR would point it at a real repo; a global
# core.hooksPath or commit.gpgsign would make the fixture depend on the machine.
for v in $(env | sed -n 's/^\(LOOP[A-Z_]*\)=.*/\1/p; s/^\(CLAUDE_PROJECT_DIR\)=.*/\1/p; s/^\(GIT_[A-Z_]*\)=.*/\1/p'); do
  unset "$v"
done
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
# No editor, ever: a commit that falls through to one would hang the suite.
export GIT_EDITOR=: EDITOR=: VISUAL=:

fails=0
ok()  { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails+1)); }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-merge-pin.XXXXXX")"
trap 'find "$WORK" -mindepth 1 -delete 2>/dev/null; rmdir "$WORK" 2>/dev/null || true' EXIT

n=0
g() { git -C "$W" "$@"; }

# A repo with one conflict waiting: CHANGELOG.md differs on both sides,
# clean.txt changes on `side` only (git merges it alone), other.txt is untouched.
mk() {
  n=$((n+1)); W="$WORK/fx$n"; mkdir -p "$W/sub"
  git -c init.defaultBranch=main init -q "$W" >/dev/null 2>&1
  g config user.email t@t; g config user.name t; g config commit.gpgsign false
  printf 'base\n' > "$W/CHANGELOG.md"; printf 'x\n' > "$W/other.txt"
  printf 'c\n' > "$W/clean.txt"; printf 's\n' > "$W/sub/deep.txt"; printf 'gone\n' > "$W/gone.txt"
  g add CHANGELOG.md other.txt clean.txt sub/deep.txt gone.txt; g commit -qm base
  g checkout -qb side
  printf 'side\n' > "$W/CHANGELOG.md"; printf 'c2\n' > "$W/clean.txt"; printf 's-side\n' > "$W/sub/deep.txt"
  g commit -qam side
  g checkout -q main
  printf 'main\n' > "$W/CHANGELOG.md"; printf 's-main\n' > "$W/sub/deep.txt"
  g commit -qam main
}
# Stop a merge on its conflicts and resolve the two conflicted files on disk.
stopped_merge() {
  mk; g merge side >/dev/null 2>&1
  [ -f "$W/.git/MERGE_HEAD" ] || { bad "fixture: the merge did not stop on a conflict"; return 1; }
  printf 'main\nside\n' > "$W/CHANGELOG.md"; printf 's-both\n' > "$W/sub/deep.txt"
}
# run <wrapper> <args...>: from the fixture root; sets RC, OUT (stdout+stderr).
run() { local lc="$1"; shift; OUT="$(cd "$W" && bash "$lc" "$@" 2>&1)"; RC=$?; }
parents() { g rev-list --parents -1 HEAD | wc -w | tr -d ' '; }   # 3 = a merge commit
still_merging() { [ -f "$W/.git/MERGE_HEAD" ]; }

echo "M  a stopped merge completes through the wrapper"
stopped_merge && {
  run "$LC" -m "merge side" -- CHANGELOG.md sub/deep.txt
  [ "$RC" = 0 ] && ok "exit 0" || bad "exit $RC, wanted 0: $(printf '%s' "$OUT" | head -2 | tr '\n' ' ')"
  [ "$(parents)" = 3 ] && ok "HEAD is a merge commit with two parents" || bad "HEAD is not a two-parent merge commit"
  still_merging && bad "MERGE_HEAD is still there" || ok "the merge is no longer in progress"
  [ "$(g show HEAD:CHANGELOG.md 2>/dev/null)" = "$(printf 'main\nside')" ] && ok "the named resolution is in the commit" || bad "the resolution is not in the commit"
  [ "$(g show HEAD:clean.txt 2>/dev/null)" = c2 ] && ok "the file git merged by itself is in the commit, unnamed" || bad "the auto-merged file is missing"
  [ -z "$(g status --porcelain --untracked-files=no)" ] && ok "nothing is left staged or modified" || bad "the tree is not clean after the merge commit"
  [ -z "$(ls "$W/.git" | grep loopkit-merge-index)" ] && ok "no private index is left behind" || bad "a private index file was left in .git"
}

echo "M  ... and from a subdirectory, with paths relative to it"
stopped_merge && {
  OUT="$(cd "$W/sub" && bash "$LC" -m "merge side" -- ../CHANGELOG.md deep.txt 2>&1)"; RC=$?
  { [ "$RC" = 0 ] && [ "$(parents)" = 3 ]; } && ok "completes from sub/ naming ../CHANGELOG.md and deep.txt" || bad "exit $RC from a subdirectory: $(printf '%s' "$OUT" | head -2 | tr '\n' ' ')"
}

# r1 <wrapper> — sets VERDICT to "refused" or "swept" for a foreign staged file.
# Not run in $(...): a subshell would lose the fixture counter.
r1() {
  stopped_merge || return 1
  printf 'not yours\n' > "$W/other.txt"; g add other.txt          # another process's staged edit
  printf 'new\n' > "$W/foreign-new.txt"; g add foreign-new.txt    # ... and its new file
  printf 'sp\n' > "$W/a b.txt"; g add "a b.txt"                   # ... and one with a space
  local before; before="$(g rev-parse HEAD)"
  run "$1" -m "merge side" -- CHANGELOG.md sub/deep.txt
  if [ "$(g rev-parse HEAD)" != "$before" ] && g show --name-only --format= -m HEAD | grep -qE '^(other|foreign-new)\.txt$'; then
    VERDICT=swept
  elif [ "$(g rev-parse HEAD)" = "$before" ]; then
    VERDICT=refused
  else
    VERDICT=committed-without-it
  fi
}
echo "R1 a foreign staged file mid-merge is refused, not swept into the merge commit"
VERDICT=none; r1 "$LC"; verdict="$VERDICT"
[ "$verdict" = refused ] && ok "no commit was made" || bad "foreign staged files: $verdict"
OUT="$(cd "$W" && bash "$LC" -m "merge side" -- CHANGELOG.md sub/deep.txt 2>&1)"; RC=$?
[ "$RC" = 3 ] && ok "exit 3" || bad "exit $RC, wanted 3"
case "$OUT" in *other.txt*foreign-new.txt*|*foreign-new.txt*other.txt*) ok "both foreign paths are named in the refusal" ;; *) bad "the refusal does not name the foreign paths: $(printf '%s' "$OUT" | head -3 | tr '\n' ' ')" ;; esac
printf '%s\n' "$OUT" | grep -qx '  a b.txt' && ok "a path with a space is listed as one path" || bad "'a b.txt' was split in the refusal"
{ printf '%s\n' "$OUT" | grep -qx '  other.txt' && printf '%s\n' "$OUT" | grep -qx '  foreign-new.txt'; } \
  && ok "every listed path is on its own indented line" || bad "the refusal list is not one indented path per line"
still_merging && ok "the merge is still in progress, to be finished properly" || bad "MERGE_HEAD is gone after a refusal"
g restore --staged other.txt foreign-new.txt "a b.txt" 2>/dev/null
run "$LC" -m "merge side" -- CHANGELOG.md sub/deep.txt
{ [ "$RC" = 0 ] && [ "$(parents)" = 3 ] && [ "$(g show HEAD:other.txt)" = x ]; } \
  && ok "once the foreign files are unstaged the same call completes, without them" || bad "exit $RC after unstaging the foreign files"

# rc_race <wrapper> — the pre-commit hook sleeps (lint-staged stand-in) and, in
# the middle, a FOREIGN process stages f.txt in the shared index. `env -u
# GIT_INDEX_FILE` is what makes it foreign: it uses the default index, as any
# other process in the worktree would. Sets RC_LINE to: <wrapper rc>
# <foreign add rc> <f.txt in commit: yes/no> <f.txt still staged: yes/no>
# <parents>. Not run in $(...), like r1.
rc_race() {
  stopped_merge || return 1
  printf 'foreign\n' > "$W/f.txt"
  printf '#!/bin/sh\nsleep 1\nenv -u GIT_INDEX_FILE git -C "%s" add f.txt\necho $? > "%s/.git/foreign-add-rc"\nsleep 1\nexit 0\n' "$W" "$W" > "$W/.git/hooks/pre-commit"
  chmod +x "$W/.git/hooks/pre-commit"
  local rc
  OUT="$(cd "$W" && bash "$1" -m "merge side" -- CHANGELOG.md sub/deep.txt 2>&1)"; rc=$?
  RC_LINE="$(printf '%s %s %s %s %s' "$rc" "$(cat "$W/.git/foreign-add-rc" 2>/dev/null || echo none)" \
    "$(g cat-file -e HEAD:f.txt 2>/dev/null && echo yes || echo no)" \
    "$(g diff --cached --name-only | grep -qx f.txt && echo yes || echo no)" "$(parents)")"
}
echo "RC a foreign git add during the pre-commit hook does not reach the merge commit"
RC_LINE="none none none none none"; rc_race "$LC"
read -r rc_w rc_f rc_in rc_staged rc_par <<EOF_RC
$RC_LINE
EOF_RC
[ "$rc_f" = 0 ] && ok "the foreign add really happened, mid-commit (rc 0, so this check is not vacuous)" || bad "the foreign add did not run or failed (rc $rc_f); the race was not exercised"
[ "$rc_w" = 0 ] && [ "$rc_par" = 3 ] && ok "the merge still completed (exit 0, two parents)" || bad "wrapper exit $rc_w, parents $rc_par"
[ "$rc_in" = no ] && ok "f.txt is NOT in the merge commit" || bad "f.txt was SWEPT into the merge commit"
[ "$rc_staged" = yes ] && ok "f.txt is still staged afterwards: not swept, not lost" || bad "the foreign staged f.txt was lost from the shared index"

echo "R2 an unresolved path is refused and the commit is not attempted"
stopped_merge && {
  before="$(g rev-parse HEAD)"
  printf '<<<<<<< HEAD\ns-main\n=======\ns-side\n>>>>>>> side\n' > "$W/sub/deep.txt"   # left unresolved, not named
  run "$LC" -m "merge side" -- CHANGELOG.md
  [ "$RC" = 3 ] && ok "exit 3" || bad "exit $RC, wanted 3"
  case "$OUT" in *"conflicted paths were not named"*sub/deep.txt*) ok "the unresolved path is named" ;; *) bad "the refusal does not name sub/deep.txt: $(printf '%s' "$OUT" | head -3 | tr '\n' ' ')" ;; esac
  { [ "$(g rev-parse HEAD)" = "$before" ] && still_merging; } && ok "HEAD did not move and the merge is still in progress" || bad "a commit was made with a path unresolved"
}
stopped_merge && {
  before="$(g rev-parse HEAD)"
  OUT="$(cd "$W/sub" && bash "$LC" -m "merge side" -- deep.txt 2>&1)"; RC=$?   # CHANGELOG.md at the root: not named
  { [ "$RC" = 3 ] && [ "$(g rev-parse HEAD)" = "$before" ]; } && ok "from a subdirectory, an unresolved path at the root is still seen" || bad "exit $RC from sub/ with the root conflict unnamed"
}

echo "R3 naming the wrong paths is refused; naming none is a usage error"
stopped_merge && {
  before="$(g rev-parse HEAD)"
  g add CHANGELOG.md sub/deep.txt             # resolved and staged, but then NOT named:
  run "$LC" -m "merge side" -- other.txt
  [ "$RC" = 3 ] && ok "exit 3 when the resolved paths are not the named ones" || bad "exit $RC, wanted 3"
  case "$OUT" in *CHANGELOG.md*) ok "the unnamed resolution is listed" ;; *) bad "the refusal does not list CHANGELOG.md: $(printf '%s' "$OUT" | head -3 | tr '\n' ' ')" ;; esac
  [ "$(g rev-parse HEAD)" = "$before" ] && ok "HEAD did not move" || bad "a commit was made"
  run "$LC" -m "merge side" -- CHANGELOG.md sub/deep.txt no-such-file.txt
  { [ "$RC" = 3 ] && [ "$(g rev-parse HEAD)" = "$before" ]; } && ok "a path nobody knows is refused, not skipped" || bad "exit $RC for a path that names nothing"
  run "$LC" -m "merge side" --
  { [ "$RC" = 2 ] && [ "$(g rev-parse HEAD)" = "$before" ]; } && ok "no paths at all is still a usage error (exit 2)" || bad "exit $RC with no paths, wanted 2"
}

echo "R4 control: with no merge in progress only the named path is published"
mk
printf 'mine\n' > "$W/clean.txt"
printf 'not yours\n' > "$W/other.txt"; g add other.txt
run "$LC" -m "ordinary" -- clean.txt
[ "$RC" = 0 ] && ok "exit 0" || bad "exit $RC: $(printf '%s' "$OUT" | head -2 | tr '\n' ' ')"
[ "$(g show --name-only --format= HEAD)" = clean.txt ] && ok "the commit holds exactly the named path" || bad "the commit holds: $(g show --name-only --format= HEAD | tr '\n' ' ')"
[ "$(parents)" = 2 ] && ok "it is an ordinary one-parent commit" || bad "not a one-parent commit"
[ "$(g diff --cached --name-only)" = other.txt ] && ok "the foreign staged file is still staged, untouched" || bad "the foreign staged file did not stay staged"
case "$OUT" in *"committed "*"  clean.txt"*) ok "the report line is unchanged" ;; *) bad "the report changed: $OUT" ;; esac

echo "R5 other operations in progress"
mk; g cherry-pick side >/dev/null 2>&1
if [ -f "$W/.git/CHERRY_PICK_HEAD" ]; then
  printf 'main\nside\n' > "$W/CHANGELOG.md"; printf 's-both\n' > "$W/sub/deep.txt"; before="$(g rev-parse HEAD)"
  run "$LC" -m "pick" -- CHANGELOG.md sub/deep.txt
  [ "$RC" = 3 ] && ok "cherry-pick: exit 3" || bad "cherry-pick: exit $RC, wanted 3"
  case "$OUT" in *"cherry-pick is in progress"*"cherry-pick --abort"*) ok "cherry-pick: the refusal says why and names the way out" ;; *) bad "cherry-pick: no clear refusal: $(printf '%s' "$OUT" | head -2 | tr '\n' ' ')" ;; esac
  { [ "$(g rev-parse HEAD)" = "$before" ] && [ -f "$W/.git/CHERRY_PICK_HEAD" ] && [ -n "$(g ls-files -u)" ]; } \
    && ok "cherry-pick: nothing committed, nothing staged, still in progress" || bad "cherry-pick: the refusal changed the repo"
else
  bad "fixture: the cherry-pick did not stop on a conflict"
fi
mk; printf 'main2\n' > "$W/CHANGELOG.md"; g commit -qam main2; g revert --no-edit HEAD~1 >/dev/null 2>&1
if [ -f "$W/.git/REVERT_HEAD" ]; then
  printf 'reverted\n' > "$W/CHANGELOG.md"; printf 'not yours\n' > "$W/other.txt"; g add other.txt
  run "$LC" -m "revert" -- CHANGELOG.md
  { [ "$RC" = 0 ] && [ "$(g show --name-only --format= HEAD)" = CHANGELOG.md ] && g diff --cached --name-only | grep -qx other.txt; } \
    && ok "revert: unchanged — only the named path is published, the foreign file stays staged" || bad "revert: exit $RC, commit holds: $(g show --name-only --format= HEAD | tr '\n' ' ')"
else
  bad "fixture: the revert did not stop on a conflict"
fi
mk; g checkout -q side; g rebase main >/dev/null 2>&1
if [ -d "$W/.git/rebase-merge" ] || [ -d "$W/.git/rebase-apply" ]; then
  printf 'rebased\n' > "$W/CHANGELOG.md"
  run "$LC" -m "rebase stop" -- CHANGELOG.md
  { [ "$RC" = 0 ] && [ "$(g show --name-only --format= HEAD)" = CHANGELOG.md ] && [ "$(parents)" = 2 ]; } \
    && ok "rebase stop: unchanged — an ordinary commit of only the named path" || bad "rebase stop: exit $RC, commit holds: $(g show --name-only --format= HEAD | tr '\n' ' ')"
else
  bad "fixture: the rebase did not stop on a conflict"
fi

echo "CM a named conflicted file that still holds conflict markers is refused"
stopped_merge && {
  before="$(g rev-parse HEAD)"
  printf '<<<<<<< HEAD\nmain\n=======\nside\n>>>>>>> side\n' > "$W/CHANGELOG.md"
  run "$LC" -m "merge side" -- CHANGELOG.md sub/deep.txt
  { [ "$RC" = 3 ] && [ "$(g rev-parse HEAD)" = "$before" ] && still_merging; } && ok "exit 3, nothing committed" || bad "exit $RC: a file with conflict markers was committed"
  case "$OUT" in *"conflict markers"*CHANGELOG.md*) ok "the marked file is named" ;; *) bad "no marker refusal: $(printf '%s' "$OUT" | head -2 | tr '\n' ' ')" ;; esac
  printf 'Title\n=======\nmain\nside\n' > "$W/CHANGELOG.md"    # a setext heading is not a marker
  run "$LC" -m "merge side" -- CHANGELOG.md sub/deep.txt
  [ "$RC" = 0 ] && ok "a lone ======= line (a heading underline) is not mistaken for a marker" || bad "exit $RC on a heading underline: $(printf '%s' "$OUT" | head -2 | tr '\n' ' ')"
}

echo "DIR on a merge, a directory or . is refused as a named path"
stopped_merge && {
  before="$(g rev-parse HEAD)"
  printf 'f\n' > "$W/sub/foreign.txt"; g add sub/foreign.txt
  run "$LC" -m "merge side" -- CHANGELOG.md sub
  { [ "$RC" = 3 ] && [ "$(g rev-parse HEAD)" = "$before" ]; } && ok "naming sub/ is refused" || bad "exit $RC naming a directory"
  case "$OUT" in *"is a directory"*) ok "the refusal says files only" ;; *) bad "no directory refusal: $(printf '%s' "$OUT" | head -2 | tr '\n' ' ')" ;; esac
  run "$LC" -m "merge side" -- .
  { [ "$RC" = 3 ] && [ "$(g rev-parse HEAD)" = "$before" ]; } && ok "naming . is refused" || bad "exit $RC naming ."
}

echo "XT a merge run with -X theirs completes once its files are named"
mk; g merge --no-commit -X theirs side >/dev/null 2>&1
if [ -f "$W/.git/MERGE_HEAD" ]; then
  before="$(g rev-parse HEAD)"
  run "$LC" -m "merge side" -- clean.txt
  { [ "$RC" = 3 ] && [ "$(g rev-parse HEAD)" = "$before" ]; } && ok "not naming the files -X resolved is refused" || bad "exit $RC"
  case "$OUT" in *CHANGELOG.md*sub/deep.txt*"-X"*) ok "the refusal lists them and mentions -X" ;; *) bad "unclear -X refusal: $(printf '%s' "$OUT" | head -4 | tr '\n' ' ')" ;; esac
  run "$LC" -m "merge side" -- CHANGELOG.md sub/deep.txt
  { [ "$RC" = 0 ] && [ "$(parents)" = 3 ] && [ "$(g show HEAD:CHANGELOG.md)" = side ] && [ "$(g show HEAD:sub/deep.txt)" = s-side ]; } \
    && ok "named, it completes with the -X theirs content" || bad "exit $RC: $(printf '%s' "$OUT" | head -3 | tr '\n' ' ')"
else
  bad "fixture: git merge --no-commit -X theirs left no merge in progress"
fi

echo "D  a conflict resolved by deleting the file completes"
mk
g checkout -q side; g rm -q gone.txt; g commit -qm "side deletes gone.txt"; g checkout -q main
printf 'changed\n' > "$W/gone.txt"; g commit -qam "main changes gone.txt"
g merge side >/dev/null 2>&1
printf 'main\nside\n' > "$W/CHANGELOG.md"; printf 's-both\n' > "$W/sub/deep.txt"
g rm -q gone.txt 2>/dev/null                    # the resolution: take the deletion, already staged
run "$LC" -m "merge side" -- CHANGELOG.md sub/deep.txt gone.txt
{ [ "$RC" = 0 ] && [ "$(parents)" = 3 ] && ! g cat-file -e HEAD:gone.txt 2>/dev/null; } \
  && ok "the deletion is in the merge commit" || bad "exit $RC: $(printf '%s' "$OUT" | head -3 | tr '\n' ' ')"

echo "L  the check refuses to run outside the driver lock"
HELPER="$(dirname "$LC")/loop-commit-merge.sh"
if [ -f "$HELPER" ]; then
  stopped_merge && {
    OUT="$(cd "$W" && bash "$HELPER" -- CHANGELOG.md sub/deep.txt 2>&1)"; RC=$?
    { [ "$RC" = 3 ] && [ -n "$(g ls-files -u)" ]; } && ok "exit 3, and it staged nothing" || bad "exit $RC outside the lock, or it staged the paths anyway"
  }
else
  bad "no loop-commit-merge.sh beside the wrapper"
fi

echo "X  this pin can fail: a helper that commits the shared index is caught"
if [ -f "$HELPER" ]; then
  MUT="$WORK/mutant"; mkdir -p "$MUT"
  cp "$(dirname "$LC")"/loop-commit.sh "$(dirname "$LC")"/driver_lock.py "$MUT/"
  # Stage the named paths in the SHARED index and commit it whole: no check.
  printf '#!/usr/bin/env bash\na=()\nwhile [ "$1" != -- ]; do a+=("$1"); shift; done; shift\ngit add -- "$@" && git commit -m mutant ${a[@]+"${a[@]}"}\n' > "$MUT/loop-commit-merge.sh"
  VERDICT=none; r1 "$MUT/loop-commit.sh"; verdict="$VERDICT"
  [ "$verdict" = swept ] && ok "R1 catches it ($verdict)" || bad "the mutant was not caught: R1 said '$verdict'"
  RC_LINE="none none none none none"; rc_race "$MUT/loop-commit.sh"
  read -r _ _ m_in _ _ <<EOF_M
$RC_LINE
EOF_M
  [ "$m_in" = yes ] && ok "RC catches it (f.txt swept)" || bad "RC did not catch the mutant (f.txt in commit: $m_in)"
else
  bad "no helper to mutate"
fi

echo
[ "$fails" = 0 ] && echo "PASS loop-commit finishes a merge" || echo "FAIL $fails check(s)"
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
