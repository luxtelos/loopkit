#!/usr/bin/env bash
# remote-gate.sh — run the suite on a Linux host and report its verdict.
#
# WHY IT EXISTS. The suite is developed on macOS but the project runs on Linux,
# and on macOS a handful of cases fail for reasons that are about the platform
# rather than about the change (/tmp is a symlink to /private/tmp, so a path a
# hook resolves and a path a test spells are different strings), plus a
# wall-clock check that flakes under load. An output that always needs
# explaining away is an output nobody reads, and a reviewer who has been taught
# to explain away three failures will explain away the fourth. So the verdict
# comes from Linux; macOS is used only to confirm the bash 3.2 path still works.
#
# WHAT IT SENDS, AND WHY THAT IS THE WHOLE POINT. A real clone (so the pins that
# ask git for the repo root work) with the CURRENT WORKING TREE rsynced over it.
# The gate judges what is on disk, not only what is committed and pushed.
#
#   This file briefly stopped doing that. An add/add merge conflict was resolved
#   by keeping a version that cloned the branch from GitHub inside the container
#   — so it gated PUSHED state only. Run from a dirty worktree it printed
#   `REMOTE GATE: PASS (rc=0)` while the uncommitted work was never sent, and
#   said nothing about it. A verdict whose scope is silently narrower than the
#   reader's is the same defect class the stop gate was being fixed for, one
#   layer further out. tests/pins/remote-gate-sends-working-tree.sh is there so
#   the next conflict resolution cannot make that choice quietly.
#
# Nothing is pushed and no credential leaves this machine: the transport is ssh
# with a key, and the remote never talks to GitHub.
#
# WHY A CONTAINER ON THAT HOST, not the host itself. Run bare on the gate host,
# three memory cases fail: `memory.py` finds a mempalace the host has installed,
# so the case that asserts behaviour "when the palace is absent" measures a
# machine where it is present. That is the macOS problem again with a different
# cause — a suite whose output has to be explained away. The container fixes the
# environment instead of the reader's expectations. Set LOOPKIT_REMOTE_DOCKER=0
# to run bare on the host, and then own those three failures out loud.
#
# THE STATUS RULE, which this script got wrong once and must never get wrong
# again. Never read `$?` after a pipe, and never let a pipe be the last thing a
# gate does. `suite | grep | tail` reports TAIL's status: the reader exits
# early, the writer takes SIGPIPE, and a suite printing "FAILURE: 3 checks
# failed" yielded `suite-rc=0`. So: the suite writes to a FILE, its status is
# captured on the very next line, the FILE is filtered for display, and the
# captured status is what exits. Display and verdict are separate paths, and
# only one of them can fail.
#
# usage: bash tools/remote-gate.sh [ref] [suite-command]
#
#   ref     informational only — the working tree is what gets sent, so there is
#           no ref to check out on the far side. It is printed and carried into
#           the log so two runs can be told apart. Defaults to the current
#           branch (or `HEAD` when detached).
#
#   LOOPKIT_REMOTE         user@host of the Linux box            (required)
#   LOOPKIT_SSH_KEY        identity file           (default: ~/.ssh/id_ed25519)
#   LOOPKIT_IMAGE          container image           (default: node:22-bookworm)
#   LOOPKIT_REMOTE_DIR     remote scratch dir         (default: .loopkit-gate)
#   LOOPKIT_REMOTE_DOCKER  1 (default) | 0 to run bare on the host
#   LOOPKIT_GATE_SUITE / argument 2
#                          the command that IS the gate
#                          (default: bash tests/selftest.sh). Overridable so
#                          this script's verdict can be proven red as well as
#                          green — a runner that has only ever been seen say
#                          PASS has not been shown able to say anything else.
#   LOOPKIT_GATE_STAGE_DIR stage into this directory and STOP, without touching
#                          the network. Prints the scope banner and the run
#                          plan, and leaves the staged tree in place so you can
#                          see exactly what would have been sent. This is how
#                          tests/pins/remote-gate-sends-working-tree.sh observes
#                          the working-tree property offline.

set -uo pipefail

# Read the project's config, like every other tool that this file's settings
# live beside. Without this, .loopkit/config.env told you to set LOOPKIT_REMOTE
# for a script that only ever read the environment — a slot with no reader,
# pointing at the one tool that uses the variable. The file assigns these keys
# as `${VAR:-}`, so an entry left empty there cannot overwrite an exported one.
#
# This is read from the PROJECT being gated, never from the directory this
# script happens to sit in. `$(dirname "$0")/..` was correct only while the
# script lived inside the repo it gates; it now ships inside the plugin, where
# that expression points at the plugin cache. Ask git, or take
# CLAUDE_PROJECT_DIR when the harness set one.
REPO="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
if [ -f "$REPO/.loopkit/config.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO/.loopkit/config.env"
    set +a
fi

REF="${1:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)}"
SUITE="${2:-${LOOPKIT_GATE_SUITE:-bash tests/selftest.sh}}"
SSH_KEY="${LOOPKIT_SSH_KEY:-$HOME/.ssh/id_ed25519}"
IMAGE="${LOOPKIT_IMAGE:-node:22-bookworm}"
RDIR="${LOOPKIT_REMOTE_DIR:-.loopkit-gate}"
USE_DOCKER="${LOOPKIT_REMOTE_DOCKER:-1}"
STAGE_ONLY="${LOOPKIT_GATE_STAGE_DIR:-}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BODY="$HERE/remote-gate-body.sh"
[ -f "$BODY" ] || { echo "REMOTE GATE: missing $BODY" >&2; exit 2; }

REMOTE="${LOOPKIT_REMOTE:-}"
if [ -z "$REMOTE" ] && [ -z "$STAGE_ONLY" ] && [ "${LOOPKIT_GATE_DRY_RUN:-0}" != "1" ]; then
    echo "remote-gate.sh: set LOOPKIT_REMOTE=user@host (the Linux gate host)," >&2
    echo "                in the environment or .loopkit/config.env" >&2
    exit 2
fi

# LOOPKIT_GATE_DRY_RUN=1 resolves and prints the settings without touching the
# network. It is how tests/pins/remote-gate-config.sh proves the config file is
# genuinely read, offline and without a host — a key nothing reads is
# decoration, and the only way to know it is read is to observe it.
if [ "${LOOPKIT_GATE_DRY_RUN:-0}" = "1" ]; then
    echo "PROJECT:  $REPO"
    echo "REMOTE:   $REMOTE"
    echo "SSH_KEY:  $SSH_KEY"
    echo "IMAGE:    $IMAGE"
    echo "REF:      $REF"
    echo "SUITE:    $SUITE"
    echo "DOCKER:   $USE_DOCKER"
    echo "REMOTE GATE: DRY RUN (nothing executed)"
    exit 0
fi

# Checked here and not earlier: the dry run above must work anywhere, including
# a scratch directory that is not a repository, or the pin that proves the
# config file is read would need a git fixture to ask a question about config.
[ -d "$REPO/.git" ] || git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || {
    echo "REMOTE GATE: $REPO is not a git repository — nothing to stage" >&2; exit 2; }

# ---------------------------------------------------------------- staging ----
# A trap and not a tidy-up at the end of the happy path: an interrupted gate
# that leaves a half-staged clone behind is how the next run measures a tree
# nobody wrote. LOOPKIT_GATE_STAGE_DIR is exempt on purpose — its whole job is
# to leave the staged tree behind for inspection.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-remote-gate.XXXXXX")"
cleanup() { [ -n "${WORK:-}" ] && [ -d "$WORK" ] && rm -r -- "$WORK" 2>/dev/null; return 0; }
trap cleanup EXIT

if [ -n "$STAGE_ONLY" ]; then
    mkdir -p "$STAGE_ONLY" || { echo "REMOTE GATE: cannot create $STAGE_ONLY" >&2; exit 2; }
    STAGED="$STAGE_ONLY"
else
    STAGED="$WORK/repo"
fi

HEAD_SHA="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '(no commits)')"
DIRTY="$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"

echo "== staging $REF ($HEAD_SHA) from $REPO"
# Say what is being gated, every run. The version that silently gated pushed
# state only was wrong because it was silent at least as much as because it was
# narrow: a reader cannot notice a scope they are never told.
if [ "${DIRTY:-0}" -gt 0 ]; then
    echo "== SCOPE: committed state PLUS the working tree ($DIRTY uncommitted change(s) included)"
else
    echo "== SCOPE: committed state PLUS the working tree (tree is clean)"
fi

git clone -q --no-hardlinks "$REPO" "$STAGED" || { echo "clone failed" >&2; exit 2; }
# Match the sender's HEAD exactly, detached included, so `REF:` on the far side
# is the commit you are actually gating rather than whatever the clone's default
# branch happened to be.
if [ "$HEAD_SHA" != "(no commits)" ]; then
    git -C "$STAGED" checkout -q --detach "$(git -C "$REPO" rev-parse HEAD)" 2>/dev/null || true
fi

# The clone carries committed state; the rsync lays the working tree over it,
# so an uncommitted edit is gated rather than silently skipped. --delete is what
# makes a working-tree DELETION visible too — without it the stage would keep a
# file the developer removed, and the suite would pass on a tree nobody has.
#
# __pycache__ is excluded on BOTH hops, not just tidiness: a .pyc compiled by
# the host's Python for a different version is junk on the far side, and rsync
# --delete refuses to remove the non-empty directory it lands in, which failed
# the whole send rather than the suite.
EXCL=(--exclude '.git' --exclude 'node_modules' --exclude '__pycache__' --exclude '*.pyc')
rsync -a --delete --force "${EXCL[@]}" \
      "$REPO"/ "$STAGED"/ || { echo "stage rsync failed" >&2; exit 2; }

# ------------------------------------------------------------- the run plan --
# WORKDIR is left at its default `.` for both modes: docker gets `-w /w`, and
# the bare-host branch cds first. One value, no remote-side expansion needed.
if [ "$USE_DOCKER" = 1 ]; then
    PLAN="docker run --rm -i -v \"\$HOME/$RDIR\":/w -w /w $IMAGE bash -s"
    PLAN_LABEL="container $IMAGE on $REMOTE:$RDIR"
else
    PLAN="cd \"\$HOME/$RDIR\" && bash -s"
    PLAN_LABEL="bare host $REMOTE:$RDIR (no container — expect the 3 memory failures)"
fi
echo "== PLAN: $PLAN_LABEL"
echo "== PLAN CMD: $PLAN"

if [ -n "$STAGE_ONLY" ]; then
    echo "== STAGED AT: $STAGED"
    echo "REMOTE GATE: STAGED ONLY (nothing sent, nothing run)"
    exit 0
fi

# The container script is fed to `bash -s` over ssh's stdin, so nothing has to
# survive three layers of nested quoting. SUITE and REF are prepended as
# bash-quoted assignments (printf %q), which round-trips exactly because the far
# side is bash too.
#
# Plain `bash -c`, never `bash -lc`: a login shell re-sources the profile and
# can reset PATH to a system runtime, silently discarding the caller's. The gate
# this script runs for forbids `-lc` in its own `run_in_env`; a runner that
# breaks the rule it enforces is not a runner anybody should trust.
{
    printf 'REF=%q\n'   "$REF"
    printf 'SUITE=%q\n' "$SUITE"
    printf 'DIRTY=%q\n' "$DIRTY"
    cat "$BODY"
} > "$WORK/input.sh"

echo "== sending to $REMOTE:$RDIR"
rsync -a --delete --force --exclude '__pycache__' --exclude '*.pyc' \
      -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i $SSH_KEY" \
      "$STAGED"/ "$REMOTE:$RDIR"/ || { echo "send failed" >&2; exit 2; }

echo "== running the suite on $PLAN_LABEL"
ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -i "$SSH_KEY" \
    "$REMOTE" "$PLAN" <"$WORK/input.sh" >"$WORK/output.log" 2>&1
rc=$?

cat "$WORK/output.log"
if [ "$rc" -eq 0 ]; then
    echo "REMOTE GATE: PASS (rc=0)"
else
    echo "REMOTE GATE: FAIL (rc=$rc)"
fi
exit "$rc"
