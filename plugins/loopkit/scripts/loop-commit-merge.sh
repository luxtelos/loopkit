#!/usr/bin/env bash
# loop-commit-merge.sh — how loop-commit.sh finishes a merge.
#
# WHY. loop-commit.sh commits with a pathspec, `git commit -- <paths>`, so it
# can only publish what its caller named. Git refuses that form while a merge is
# in progress ("cannot do a partial commit during a merge"), and a bare
# `git commit` is refused by require_commit_lock.py. Between the two, a merge
# that stopped on a conflict could not be completed by any allowed path.
#
# WHY NOT JUST COMMIT THE INDEX. A merge commit publishes a whole tree, and the
# shared index is the one thing in a worktree every process writes to. Checking
# the shared index and then committing it is not enough: for a whole-index
# commit git releases index.lock BEFORE the pre-commit hook and reads the index
# AGAIN after it, so a lockless `git add` from another process during the hook
# (seconds, with lint-staged) lands in the merge commit. The first version of
# this file did exactly that. require_commit_lock.py lets a bare `git add`
# through on purpose, so "everyone takes the driver lock" is not a premise.
#
# SO THE SHARED INDEX IS NEVER COMMITTED. The commit is built in a PRIVATE index
# that no other process knows the path of:
#
#   1. auto  = `git merge-tree --write-tree HEAD MERGE_HEAD`, what the merge
#      ALONE produces, computed from the two commits (no index, no work tree).
#   2. private index = auto, then each named path from the working tree.
#   3. `GIT_INDEX_FILE=<private> git commit` — a real `git commit`, so HEAD and
#      MERGE_HEAD are the parents, the project's hooks run and signing applies.
#      (`git commit-tree` would skip the hooks; that counts as --no-verify.)
#   4. In the shared index, ONLY the named paths are brought up to the new HEAD.
#      Every other entry is left alone: a foreign staged change stays staged.
#
# Before step 2, checks that refuse (exit 3, nothing committed, merge still in
# progress):
#
#   - a named path that is a directory or `.`, or that names nothing    files only
#   - a conflicted path that was not named                              unresolved
#   - a named conflicted file that still holds conflict markers
#   - a shared-index entry that differs from `auto` and was not named   foreign,
#     or the merge used an option (-X theirs, -s subtree) that merge-tree did
#     not replay: either way the commit would not be what the caller sees
#   - an octopus merge, unrelated histories, git older than 2.38        no proof
#
# The foreign check reads the shared index, so a change staged AFTER it is not
# seen — and does not need to be: it cannot reach the commit (step 2 never reads
# the shared index) and step 4 leaves it staged.
#
# A merge started with -X ours/-X theirs: name every file the option resolved.
# The refusal lists them; naming them commits their working-tree content.
#
# This is not called directly. loop-commit.sh runs it inside the driver lock,
# and it refuses to run outside one.
#
# USAGE (from loop-commit.sh only)
#   loop-commit-merge.sh <git commit args...> -- <path> [<path>...]
#
# EXIT: git commit's own code; 3 refused, with the reason on stderr; 4 the
# merge commit landed but HEAD had moved since the check.

set -uo pipefail

refuse() {  # <reason> [<line>...] — one line each, so a path with a space stays one path
  echo "loop-commit.sh: refusing to finish the merge: $1" >&2
  shift
  local arg line
  for arg in "$@"; do
    while IFS= read -r line; do [ -n "$line" ] && echo "  $line" >&2; done <<<"$arg"
  done
  echo "  Nothing was committed. The merge is still in progress." >&2
  exit 3
}
lines() { printf '%s\n' "$1" | sed '/^$/d'; }   # a newline list → one per line, no blanks
minus() { LC_ALL=C comm -23 <(lines "$1" | LC_ALL=C sort -u) <(lines "$2" | LC_ALL=C sort -u); }

COMMIT_ARGS=()
while [ $# -gt 0 ] && [ "$1" != "--" ]; do COMMIT_ARGS+=("$1"); shift; done
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || refuse "no paths were named."
[ -n "${LOOPKIT_DRIVER_LOCK:-}" ] \
  || refuse "this only runs inside the driver lock; commit through loop-commit.sh."

TOP="$(git rev-parse --show-toplevel 2>/dev/null)" || refuse "not inside a git work tree."
PREFIX="$(git rev-parse --show-prefix)"
GITDIR="$(cd "$(git rev-parse --git-dir)" && pwd)"

[ -f "$GITDIR/MERGE_HEAD" ] || refuse "no merge is in progress."
[ "$(grep -c . "$GITDIR/MERGE_HEAD")" = 1 ] \
  || refuse "more than one branch is being merged (an octopus merge)." \
            "What the merge alone would produce cannot be computed for that. Merge one branch at a time."
HEAD_BEFORE="$(git rev-parse --verify -q HEAD)" || refuse "HEAD does not resolve."
MERGE_HEAD="$(git rev-parse --verify -q MERGE_HEAD)" || refuse "MERGE_HEAD does not resolve."

# Named paths → paths from the top of the work tree. Files only: a directory or
# `.` would exempt everything under it, foreign files included.
for p in "$@"; do
  [ -n "$p" ] || refuse "an empty path was named."
  { [ -d "$p" ] && [ ! -L "$p" ]; } \
    && refuse "'$p' is a directory. On a merge, name each file you resolved, not a directory."
done
FULL="$(python3 -c '
import os, sys
pre = sys.argv[1]
for p in sys.argv[2:]:
    print(os.path.normpath(os.path.join(pre, p)))
' "$PREFIX" "$@")" || refuse "the named paths could not be resolved."
FULL_ARR=()
while IFS= read -r f; do FULL_ARR+=("$f"); done < <(lines "$FULL")
outside="$(lines "$FULL" | grep -E '^(\.|\.\.(/.*)?)$' || true)"
[ -z "$outside" ] || refuse "these named paths are not files inside this work tree:" "$outside"

cd "$TOP" || refuse "cannot enter the top of the work tree."

# Exit 0 = clean merge, 1 = conflicts (the normal case here); the tree is the
# first line either way, the conflicted paths follow. Anything else, or no tree,
# is a git too old for --write-tree (before 2.38) or a merge it cannot replay.
mt="$(git merge-tree --write-tree --name-only --no-messages HEAD MERGE_HEAD 2>/dev/null)"; mt_rc=$?
AUTO="$(lines "$mt" | sed -n '1p')"
{ [ "$mt_rc" -le 1 ] && [ -n "$AUTO" ] && git cat-file -e "$AUTO^{tree}" 2>/dev/null; } \
  || refuse "git could not compute what this merge alone produces" \
            "(\`git merge-tree --write-tree\` needs git 2.38 or newer, and two related histories)."
CONFLICTED="$(lines "$mt" | sed '1d' | LC_ALL=C sort -u)"

# Each named path must be a file somewhere: in the work tree, or (a deletion
# resolution) in the merge result or either side. A typo is refused, not skipped.
in_tree() {  # <tree-ish> <path> → true if a blob or submodule entry is there
  case "$(git ls-tree "$1" -- "$2" 2>/dev/null | awk '{print $2}')" in blob|commit) return 0 ;; esac
  return 1
}
while IFS= read -r f; do
  { [ -d "$f" ] && [ ! -L "$f" ]; } && refuse "'$f' is a directory. Name each file you resolved."
  [ -e "$f" ] || [ -L "$f" ] || in_tree "$AUTO" "$f" || in_tree HEAD "$f" || in_tree MERGE_HEAD "$f" \
    || refuse "'$f' is not in the working tree or either side of the merge." \
              "Name paths relative to the current directory."
done < <(lines "$FULL")

UNMERGED="$(git ls-files -u | cut -f2 | LC_ALL=C sort -u)"
unresolved="$(minus "$CONFLICTED"$'\n'"$UNMERGED" "$FULL")"
[ -z "$unresolved" ] || refuse "these conflicted paths were not named:" "$unresolved" \
  "Resolve each one and name it after '--'." \
  "(A merge run with -X ours/-X theirs resolved some of these for you: name them" \
  " too, and their working-tree content is what gets committed.)"

staged="$(git diff-index --cached --name-only "$AUTO" 2>/dev/null)" \
  || refuse "the shared index could not be compared with the merge result."
foreign="$(minus "$staged" "$FULL")"
[ -z "$foreign" ] || refuse "the index holds changes that the merge did not make and you did not name:" "$foreign" \
  "If they are your resolution, name them after '--'." \
  "If they came from a merge option (-X, -s subtree), name them, or have a human finish it." \
  "If they are not yours, another process staged them; they must not ride in your merge commit."

# --- the private index -----------------------------------------------------
PRIV="$GITDIR/loopkit-merge-index.$$"
trap 'rm -f "$PRIV"' EXIT
GIT_INDEX_FILE="$PRIV" git read-tree "$AUTO" || refuse "could not start a private index from the merge result."
while IFS= read -r f; do
  if [ -e "$f" ] || [ -L "$f" ] \
     || GIT_INDEX_FILE="$PRIV" GIT_LITERAL_PATHSPECS=1 git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
    GIT_INDEX_FILE="$PRIV" GIT_LITERAL_PATHSPECS=1 git add -- "$f" \
      || refuse "could not stage '$f' into the private index."
  fi   # else: gone from the work tree AND from the merge result — nothing to do
done < <(lines "$FULL")

# Markers are checked in the blob that will be committed, not in the file.
marked=""
while IFS= read -r f; do
  GIT_INDEX_FILE="$PRIV" git cat-file -e ":$f" 2>/dev/null || continue
  if GIT_INDEX_FILE="$PRIV" git cat-file -p ":$f" | LC_ALL=C grep -qIE '^(<<<<<<<|\|\|\|\|\|\|\||>>>>>>>)( |$)'; then
    marked="$marked$f"$'\n'
  fi
done < <(LC_ALL=C comm -12 <(lines "$FULL" | LC_ALL=C sort -u) <(lines "$CONFLICTED"$'\n'"$UNMERGED" | LC_ALL=C sort -u))
[ -z "$marked" ] || refuse "these files still hold conflict markers:" "$marked" "Finish resolving them first."

# A real commit, from the private index. Hooks see GIT_INDEX_FILE, exactly as
# they do for git's own `git commit -- <paths>` (which also uses a temp index).
GIT_INDEX_FILE="$PRIV" git commit ${COMMIT_ARGS[@]+"${COMMIT_ARGS[@]}"}
rc=$?
[ "$rc" = 0 ] || exit "$rc"   # the shared index was never touched; the merge is still in progress

# Bring only the named paths in the shared index up to the new HEAD. A lockless
# `git add` may hold index.lock for a moment, so retry before giving up.
synced=1
for _ in 1 2 3 4 5; do
  GIT_LITERAL_PATHSPECS=1 git reset -q HEAD -- "${FULL_ARR[@]}" >/dev/null 2>&1 && { synced=0; break; }
  sleep 0.2
done
[ "$synced" = 0 ] || {
  echo "loop-commit.sh: WARNING: the merge commit landed, but the shared index was not updated" >&2
  echo "  for the named paths (index.lock stayed busy). They will look staged until you run:" >&2
  echo "  git reset -q HEAD -- <the paths you named>" >&2
}

if [ "$(git rev-parse HEAD^1)" != "$HEAD_BEFORE" ] || [ "$(git rev-parse HEAD^2 2>/dev/null)" != "$MERGE_HEAD" ]; then
  echo "loop-commit.sh: the merge commit landed, but its parents are not the HEAD and MERGE_HEAD" >&2
  echo "  that were checked ($HEAD_BEFORE, $MERGE_HEAD). Something moved HEAD meanwhile;" >&2
  echo "  inspect $(git rev-parse --short HEAD) before building on it." >&2
  exit 4
fi
exit 0
