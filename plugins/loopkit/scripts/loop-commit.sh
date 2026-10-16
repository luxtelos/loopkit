#!/usr/bin/env bash
# loop-commit.sh — the ONLY way a loop process commits in a shared worktree.
#
# WHY. `git add` and `git commit` are two calls against ONE index, and the index
# is process-global per worktree. On 2026-09-07 a reviewer staged one file by
# name; between its add and its commit, a concurrent driver committed, swept the
# reviewer's file into an unrelated commit, and left the reviewer with "nothing
# added to commit". Naming files at `git add` time did not help, because
# `git commit` publishes the whole index.
#
# Two fixes, and this script is both of them at once:
#
#   1. The add and the commit happen inside ONE critical section, held on
#      <worktree>/.loopkit/driver.lock (see driver_lock.py). The kernel releases
#      it when this process ends, however it ends.
#   2. The commit uses the pathspec form, `git commit -- <paths>`, so even if
#      the lock is somehow not held, the commit can only publish the paths this
#      caller named. Belt, and separately, braces.
#
# The pathspec form does NOT stage new files and errors on a path git has never
# seen, which is why the `git add` step is still here and still comes first.
#
# USAGE
#   loop-commit.sh -m "subject" [-m "body"] -- <path> [<path>...]
#   loop-commit.sh -F msg.txt   [--no-verify] [--timeout 300] -- <path>...
#
# Every path is required to be named. There is deliberately no "commit
# everything" mode: that is the `git add -A` habit the constitution refuses.
#
# A MERGE IN PROGRESS is finished by the same call: name every path you
# resolved (files, not directories). See loop-commit-merge.sh for how the
# merge commit is built without committing the shared index, and what it checks.
#
# ONE CONTENT RULE RIDES ALONG. If the commit would publish
# inbox/needs-human.md, every NEW or CHANGED section in it must carry a usable
# `Precedent searched: <queries run> → <result>` line, judged against the same
# base as the stop gate, or the commit is refused before anything is staged (see check-inbox-precedent.py for the rule
# and for why it reads the file rather than the command that wrote it). It runs
# on a merge commit too. This is not a git hook, so --no-verify does not skip it.
#
# EXIT: git's own code; 2 on a usage error; 3 if an operation in progress was
# refused; 4 if a merge commit landed on a HEAD that moved; 65 if a new inbox
# section has no usable precedent line; 75 if the lock stayed busy.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMEOUT="${LOOPKIT_LOCK_TIMEOUT:-300}"

MSG_ARGS=()
GIT_FLAGS=()
PATHS=()
seen_sep=0

usage() {
  sed -n '2,43p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    -m) [ $# -ge 2 ] || usage; MSG_ARGS+=(-m "$2"); shift 2 ;;
    -F) [ $# -ge 2 ] || usage; MSG_ARGS+=(-F "$2"); shift 2 ;;
    --no-verify|-q|--quiet) GIT_FLAGS+=("$1"); shift ;;
    --author) [ $# -ge 2 ] || usage; GIT_FLAGS+=(--author "$2"); shift 2 ;;
    --timeout) [ $# -ge 2 ] || usage; TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage ;;
    --) seen_sep=1; shift; break ;;
    -*) echo "loop-commit.sh: unsupported flag '$1'" >&2; usage ;;
    *) echo "loop-commit.sh: put paths after '--'" >&2; usage ;;
  esac
done

[ "$seen_sep" = 1 ] || { echo "loop-commit.sh: missing '--' before the paths" >&2; usage; }
while [ $# -gt 0 ]; do PATHS+=("$1"); shift; done

[ "${#MSG_ARGS[@]}" -gt 0 ] || { echo "loop-commit.sh: no -m or -F message" >&2; usage; }
[ "${#PATHS[@]}" -gt 0 ] || { echo "loop-commit.sh: name at least one path" >&2; usage; }

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "${CLAUDE_PROJECT_DIR:-$PWD}")"

# Re-exec under the lock unless an ancestor already holds this worktree's lock.
# driver_lock.py exports LOOPKIT_DRIVER_LOCK for its child, and treats a second
# request for the same path as a no-op, so the check below is belt-and-braces
# against a deadlock rather than the only guard.
if [ -z "${LOOPKIT_DRIVER_LOCK:-}" ]; then
  exec python3 "$HERE/driver_lock.py" run --root "$ROOT" --timeout "$TIMEOUT" \
    --label "loop-commit" -- bash "${BASH_SOURCE[0]}" \
    "${MSG_ARGS[@]}" ${GIT_FLAGS[@]+"${GIT_FLAGS[@]}"} -- "${PATHS[@]}"
fi

# --- inside the critical section -------------------------------------------

# An operation in progress changes what a commit is, so it is decided first.
#   merge        git refuses the pathspec form ("cannot do a partial commit
#                during a merge"). loop-commit-merge.sh builds the merge commit
#                from a PRIVATE index — what the merge alone produced, plus the
#                named paths — so the shared index is never committed and a
#                file somebody else stages, even mid-commit, cannot ride along.
#   cherry-pick  git refuses the pathspec form here too, and the helper does
#                not handle one yet. Refused, exit 3, with the way out.
#   anything else (a revert, a rebase stop, nothing at all) is the ordinary
#                path below, unchanged: only the named paths are published.
if [ -f "$(git rev-parse --git-path CHERRY_PICK_HEAD 2>/dev/null)" ]; then
  echo "loop-commit.sh: a cherry-pick is in progress and this wrapper does not finish one." >&2
  echo "  git refuses a pathspec commit during a cherry-pick, and committing the whole" >&2
  echo "  index would publish whatever any other process staged here." >&2
  echo "  Either \`git cherry-pick --abort\`, or ask a human to finish it. Nothing was committed." >&2
  exit 3
fi

# The inbox rule, BEFORE staging, so a refusal leaves the index as it found it.
# The working file is what `git commit -- <paths>` is about to publish. The
# base is the stop gate's own (its merge base against the trunk), asked of the
# gate, so the two never disagree: judging against HEAD instead let a branch
# that had added a bare section before this rule existed commit the inbox
# again, while the gate refused the stop. HEAD only when the gate has no base.
# Any non-zero answer refuses, including "the check could not run": a check
# that fails open is a check that is not there.
#
# It runs AFTER the cherry-pick refusal (nothing is committed there, so there
# is nothing to judge) and BEFORE the merge path, so a merge commit is judged
# too. On a merge it judges what the merge commit PUBLISHES (--merge-commit):
# a named inbox is the working file; an unnamed one is the merged blob, since
# loop-commit-merge.sh commits what the merge produced for every path not
# named. Judging the working file there let a bare section through (refused,
# the line added to the working file only, the rerun passed, the merged blob
# committed bare) and refused merges over working-only edits never committed.
# The gate's base folds MERGE_HEAD in while a merge is in progress (see
# gate_base), so sections the merged-in side already shares with the trunk are
# not re-judged: merging the trunk into a branch must not refuse the trunk's
# own inbox.
#
# Only a line that names a commit is taken as the base. The gate may print a
# status line (its worktree-root notice) before the base, and a status line
# read as a ref would judge every section as new.
#
# With no inbox anywhere (not on disk, and on a merge in neither parent) there
# is nothing to judge, so the check is not started: a copy of this wrapper laid
# down without its sibling scripts, as the merge pin's mutant is, must still
# reach the merge path.
INBOX_MERGING=0
[ -f "$(git rev-parse --git-path MERGE_HEAD 2>/dev/null)" ] && INBOX_MERGING=1
HAS_INBOX=0
if [ -f "$ROOT/inbox/needs-human.md" ]; then
  HAS_INBOX=1
elif [ "$INBOX_MERGING" = 1 ] && { git cat-file -e "HEAD:inbox/needs-human.md" 2>/dev/null || git cat-file -e "MERGE_HEAD:inbox/needs-human.md" 2>/dev/null; }; then
  HAS_INBOX=1
fi
if [ "$HAS_INBOX" = 1 ]; then
  INBOX_BASE="$(CLAUDE_PROJECT_DIR="$ROOT" bash "$HERE/../hooks/stop_gate.sh" --print-base </dev/null 2>/dev/null || true)"
  INBOX_BASE="${INBOX_BASE##*$'\n'}"
  git rev-parse --verify --quiet "${INBOX_BASE:-none}^{commit}" >/dev/null 2>&1 || INBOX_BASE=""
  INBOX_MODE=()
  [ "$INBOX_MERGING" = 1 ] && INBOX_MODE=(--merge-commit)
  python3 "$HERE/check-inbox-precedent.py" --root "$ROOT" --base "${INBOX_BASE:-HEAD}" ${INBOX_MODE[@]+"${INBOX_MODE[@]}"} -- "${PATHS[@]}" || {
    echo "loop-commit.sh: nothing was staged or committed — fix inbox/needs-human.md and run this again" >&2
    [ "$INBOX_MERGING" = 1 ] && echo "  On a merge, an inbox you do not name is committed as the merge produced it; to commit your fix, name inbox/needs-human.md." >&2
    exit 65
  }
fi

if [ -f "$(git rev-parse --git-path MERGE_HEAD 2>/dev/null)" ]; then
  bash "$HERE/loop-commit-merge.sh" ${GIT_FLAGS[@]+"${GIT_FLAGS[@]}"} "${MSG_ARGS[@]}" -- "${PATHS[@]}"
  rc=$?
  if [ "$rc" = 0 ]; then
    echo "committed $(git rev-parse --short HEAD):"
    git show --name-only --format= HEAD | sed 's/^/  /'
  fi
  exit "$rc"
fi

# Stage only what this caller named. Never -A, never '.'.
git add -- "${PATHS[@]}" || exit $?

# The pathspec is what excludes every other process's staged work.
# `${a[@]+"${a[@]}"}`, not `"${a[@]}"`: bash 3.2 (still the system bash on
# macOS) treats an EMPTY array as unset under `set -u` and aborts. Found by
# test_driver_lock.py, which is the only reason this file was ever run with no
# optional flags.
git commit ${GIT_FLAGS[@]+"${GIT_FLAGS[@]}"} "${MSG_ARGS[@]}" -- "${PATHS[@]}"
rc=$?

if [ "$rc" = 0 ]; then
  # Say what actually landed. A green "I committed only my file" claim is not
  # verifiable after the fact if another process committed in between, so print
  # the truth from git rather than from intent.
  echo "committed $(git rev-parse --short HEAD):"
  git show --name-only --format= HEAD | sed 's/^/  /'
fi
exit "$rc"
