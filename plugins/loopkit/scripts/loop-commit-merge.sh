#!/usr/bin/env bash
# loop-commit-merge.sh — the check that lets loop-commit.sh finish a merge.
#
# WHY. loop-commit.sh commits with a pathspec, `git commit -- <paths>`, so it
# can only publish what its caller named. Git refuses that form while a merge is
# in progress ("cannot do a partial commit during a merge"), and a bare
# `git commit` is refused by require_commit_lock.py. Between the two, a merge
# that stopped on a conflict could not be completed by any allowed path.
#
# A merge commit has to publish the WHOLE index: the paths git merged by itself
# are staged there and nobody "named" them. But "commit the whole index" is the
# exact hole the pathspec form was written to close — the index is shared by
# every process in the worktree, so a file somebody else staged would ride along
# under this caller's message.
#
# So the whole index is committed only after it is proven to hold nothing
# foreign. Git can say what the merge ALONE produces, without touching the index
# or the working tree: `git merge-tree --write-tree HEAD MERGE_HEAD`. Every path
# where the index differs from that tree is a path somebody changed by hand
# since the merge stopped. Each one must be covered by a path the caller named.
# One that is not is refused, by name, and nothing is committed.
#
#   index entry == what the merge alone produced   -> the merge's own work, fine
#   index entry differs, caller named it           -> the caller's resolution
#   index entry differs, caller did not name it    -> REFUSED (exit 3)
#
# This is not called directly. loop-commit.sh runs it inside the driver lock,
# and it refuses to run outside one: staging and checking are only one step if
# nobody else can stage in between.
#
# USAGE (from loop-commit.sh only)
#   loop-commit-merge.sh -- <path> [<path>...]
#
# EXIT: 0 the index is safe to commit whole; 3 refused, with the reason on
# stderr; git's own code if `git add` fails.

set -uo pipefail

refuse() {
  echo "loop-commit.sh: refusing to finish the merge: $1" >&2
  shift
  for line in "$@"; do echo "  $line" >&2; done
  echo "  Nothing was committed. The merge is still in progress." >&2
  exit 3
}

[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || refuse "no paths were named."
[ -n "${LOOPKIT_DRIVER_LOCK:-}" ] \
  || refuse "this check only means something inside the driver lock; commit through loop-commit.sh."

MERGE_HEAD_FILE="$(git rev-parse --git-path MERGE_HEAD)"
[ -f "$MERGE_HEAD_FILE" ] || refuse "no merge is in progress."
[ "$(grep -c . "$MERGE_HEAD_FILE")" = 1 ] \
  || refuse "more than one branch is being merged (an octopus merge)." \
            "What the merge alone would produce cannot be computed for that, so a" \
            "foreign staged file could not be told apart. Merge one branch at a time."

# Stage what the caller named, one path at a time. A conflict resolved by
# deleting the file and already staged with `git rm` is in neither the working
# tree nor the index, and `git add` errors on it — but it is a real resolution,
# so it is accepted when either side of the merge knows the path. A path nobody
# knows is a typo, and is refused rather than skipped.
for p in "$@"; do
  if [ -e "$p" ] || [ -L "$p" ] || git ls-files --error-unmatch -- "$p" >/dev/null 2>&1; then
    git add -- "$p" || exit $?
  elif git cat-file -e "HEAD:./$p" 2>/dev/null || git cat-file -e "MERGE_HEAD:./$p" 2>/dev/null; then
    :
  else
    refuse "'$p' is not in the working tree, the index, or either side of the merge." \
           "Name paths relative to the current directory."
  fi
done

unresolved="$(git ls-files -u | cut -f2 | LC_ALL=C sort -u)"
[ -z "$unresolved" ] || refuse "these paths are still unresolved:" $unresolved \
  "Resolve each one and name it after '--'."

# Exit 0 = clean merge, 1 = the merge has conflicts (the normal case here); the
# tree is printed on the first line either way. Anything else is a git too old
# for `merge-tree --write-tree` (before 2.38) or a merge it cannot replay, and
# without that tree there is no proof — so no commit.
auto="$(git merge-tree --write-tree HEAD MERGE_HEAD 2>/dev/null)"; mt_rc=$?
auto="$(printf '%s\n' "$auto" | sed -n '1p')"
{ [ "$mt_rc" -le 1 ] && git cat-file -e "$auto^{tree}" 2>/dev/null; } \
  || refuse "git could not compute what this merge alone produces" \
            "(\`git merge-tree --write-tree\` needs git 2.38 or newer, and two related histories)." \
            "Without it a foreign staged file cannot be told apart from the merge's own work."

index_tree="$(git write-tree 2>/dev/null)" || refuse "the index could not be written as a tree."

changed="$(git diff-tree -r --name-only "$auto" "$index_tree" | LC_ALL=C sort -u)"
named="$(git diff-tree -r --name-only "$auto" "$index_tree" -- "$@" | LC_ALL=C sort -u)"
foreign="$(LC_ALL=C comm -23 <(printf '%s\n' "$changed") <(printf '%s\n' "$named") | grep . || true)"

[ -z "$foreign" ] || refuse "the index holds changes that the merge did not make and you did not name:" $foreign \
  "If they are your resolution, name them after '--'." \
  "If they are not yours, another process staged them here; they must not ride in your merge commit."

exit 0
