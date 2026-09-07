#!/usr/bin/env bash
# remote-gate.sh — run tests/selftest.sh on a Linux host and report its verdict.
#
# WHY IT EXISTS. The suite is developed on macOS but the project runs on Linux,
# and on macOS a handful of cases fail for reasons that are about the platform
# rather than about the change (/tmp is a symlink to /private/tmp, so a path a
# hook resolves and a path a test spells are different strings). An output that
# always needs explaining away is an output nobody reads, and a reviewer who has
# been taught to explain away three failures will explain away the fourth. So
# the verdict comes from Linux; macOS is used only to confirm bash 3.2
# compatibility.
#
# It was referenced by the hand-off instructions for PR #31 before it existed —
# three separate runs were told to use it and had to improvise an ssh line each
# time. Improvised gates are not comparable to each other, so here it is.
#
# WHAT IT SENDS. A real clone (so the pins that ask git for the repo root work)
# with the CURRENT working tree rsynced over it — the gate judges what is on
# disk, not only what is committed. Nothing is pushed and no credential leaves
# this machine: the transport is ssh with a key, and the remote never talks to
# GitHub.
#
# WHY A CONTAINER ON THAT HOST, not the host itself. Run bare on the gate host,
# three memory cases fail: `memory.py` finds a mempalace the host has installed,
# so the case that asserts behaviour "when the palace is absent" measures a
# machine where it is present. That is the macOS problem again with a different
# cause — a suite whose output has to be explained away. The container fixes the
# environment instead of the reader's expectations. Set LOOPKIT_REMOTE_DOCKER=0
# to run bare on the host, and then own those three failures out loud.
#
# usage: bash tools/remote-gate.sh [branch]      # branch is informational
# env:   LOOPKIT_REMOTE        user@host         (required)
#        LOOPKIT_REMOTE_KEY    ssh key           (default ~/.ssh/id_ed25519)
#        LOOPKIT_REMOTE_DIR    remote scratch dir (default .loopkit-gate)
#        LOOPKIT_REMOTE_DOCKER 1 (default) | 0
#        LOOPKIT_REMOTE_IMAGE  container image   (default node:22-bookworm)
set -uo pipefail

BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
REMOTE="${LOOPKIT_REMOTE:-}"
KEY="${LOOPKIT_REMOTE_KEY:-$HOME/.ssh/id_ed25519}"
RDIR="${LOOPKIT_REMOTE_DIR:-.loopkit-gate}"
USE_DOCKER="${LOOPKIT_REMOTE_DOCKER:-1}"
IMAGE="${LOOPKIT_REMOTE_IMAGE:-node:22-bookworm}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "$REMOTE" ]; then
  echo "remote-gate.sh: set LOOPKIT_REMOTE=user@host (the Linux gate host)" >&2
  exit 2
fi

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-gate.XXXXXX")"
# A trap and not a tidy-up at the end of the happy path: an interrupted gate
# that leaves a half-staged clone behind is how the next run measures a tree
# nobody wrote.
cleanup() { [ -n "${STAGE:-}" ] && [ -d "$STAGE" ] && rm -r -- "$STAGE" 2>/dev/null; return 0; }
trap cleanup EXIT

echo "== staging $BRANCH ($(git -C "$REPO" rev-parse --short HEAD))"
git clone -q --no-hardlinks "$REPO" "$STAGE/repo" || { echo "clone failed" >&2; exit 2; }
# The clone carries committed state; the rsync lays the working tree over it,
# so an uncommitted edit is gated rather than silently skipped.
# __pycache__ is excluded on BOTH hops, not just tidiness: a .pyc compiled by
# the host's Python for a different version is junk on the far side, and rsync
# --delete refuses to remove the non-empty directory it lands in, which failed
# the whole send rather than the suite.
EXCL=(--exclude '.git' --exclude 'node_modules' --exclude '__pycache__' --exclude '*.pyc')
rsync -a --delete --force "${EXCL[@]}" \
      "$REPO"/ "$STAGE/repo"/ || { echo "stage rsync failed" >&2; exit 2; }

echo "== sending to $REMOTE:$RDIR"
rsync -a --delete --force --exclude '__pycache__' --exclude '*.pyc' \
      -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i $KEY" \
      "$STAGE/repo"/ "$REMOTE:$RDIR"/ || { echo "send failed" >&2; exit 2; }

if [ "$USE_DOCKER" = 1 ]; then
  echo "== running tests/selftest.sh on $REMOTE in $IMAGE"
  RUN="docker run --rm -v \"\$HOME/$RDIR\":/w -w /w $IMAGE bash -c 'git config --global --add safe.directory /w >/dev/null 2>&1; uname -s; python3 -V; bash tests/selftest.sh'"
else
  echo "== running tests/selftest.sh on $REMOTE (bare host — expect 3 memory failures)"
  RUN="cd '$RDIR' && uname -s && python3 -V && bash tests/selftest.sh"
fi
# No pipeline here, on purpose. Piping a gate into grep/head/tail makes the
# FILTER's status the gate's verdict under `pipefail`, and grep exits non-zero
# when it matches nothing — that is a green filter standing over a red suite.
ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -i "$KEY" \
    "$REMOTE" "$RUN"
rc=$?
echo "== remote-gate exit $rc"
exit $rc
