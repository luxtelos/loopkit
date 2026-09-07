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
# EXIT: git's own code; 2 on a usage error; 75 if the lock stayed busy.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMEOUT="${LOOPKIT_LOCK_TIMEOUT:-300}"

MSG_ARGS=()
GIT_FLAGS=()
PATHS=()
seen_sep=0

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
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
