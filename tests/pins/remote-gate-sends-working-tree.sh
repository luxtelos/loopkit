#!/usr/bin/env bash
# remote-gate-sends-working-tree.sh — the gate must judge the tree you HAVE.
#
# WHY THIS PIN EXISTS. `tools/remote-gate.sh` was an add/add merge conflict with
# two working implementations. One staged a local clone and rsynced the CURRENT
# WORKING TREE over it; the other cloned the branch from GitHub inside the
# container. The conflict was resolved on grounds that were not about behaviour
# — file ownership, and which branch built on which — and the GitHub-clone
# version won. It gates PUSHED STATE ONLY. Run from a dirty worktree against the
# real host it printed:
#
#     WORKING TREE NOT SENT
#     suite-rc=0
#     REMOTE GATE: PASS (rc=0)
#
# A green verdict on work that was never sent, with nothing said about it. That
# is the same silent-scope-gap class the stop gate was being fixed for in the
# very PR that lost it, and NO TEST IN THE TREE COULD SEE IT — a human caught it
# by reading two files side by side. That is the real defect this pin closes:
# not the bad merge, but the fact that a bad merge here was invisible.
#
# It also pins the second capability that went with it: LOOPKIT_REMOTE_DOCKER=0,
# bare-host mode. Nothing referenced it, so nothing broke, so nothing noticed.
#
# HOW IT OBSERVES THIS OFFLINE. `LOOPKIT_GATE_STAGE_DIR=<dir>` tells the runner
# to build exactly what it would send, into <dir>, and stop before any network
# call. The pin then reads that directory. No ssh, no docker, no host.
#
# usage: bash tests/pins/remote-gate-sends-working-tree.sh [<repo-root>]
#   LOOPKIT_GATE_RUNNER=<path>   test a different runner than this repo's.
#     That slot is not decoration: it is how this pin was proven RED against the
#     GitHub-clone version before being declared green against the restored one.
#     A pin that has only ever been seen agree has not been shown able to
#     disagree.

set -uo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
REPO="$(cd "$REPO" && pwd)"   # a relative argument must not break the fixtures
RUNNER="${LOOPKIT_GATE_RUNNER:-$REPO/plugins/loopkit/scripts/remote-gate.sh}"
[ -f "$RUNNER" ] || { echo "FAIL no remote-gate.sh at $RUNNER"; exit 2; }

fails=0
ok()  { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails+1)); }

command -v rsync >/dev/null 2>&1 \
    || { echo "  FAIL rsync is not installed — this pin cannot verify the gate's staging"; exit 2; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-gate-tree-pin.XXXXXX")"
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK" 2>/dev/null || true' EXIT

# ---------------------------------------------------------------- fixture ----
# A project whose working tree and whose committed state DISAGREE in all three
# ways that matter: a file added, a file edited, a file removed. Pushed-only
# staging gets every one of them wrong.
PROJ="$WORK/project"
mkdir -p "$PROJ"
git -c init.defaultBranch=main init -q "$PROJ" >/dev/null 2>&1
printf 'committed\n'      > "$PROJ/committed.txt"
printf 'ORIGINAL\n'       > "$PROJ/tracked-edit.txt"
printf 'still here\n'     > "$PROJ/deleted-in-worktree.txt"
# `-c commit.gpgsign=false`: a fixture must not depend on the caller's commit
# signing. With signing on and the agent locked, the commit fails, the fixture
# has no branch, and every check below reports a defect that is not there.
git -C "$PROJ" -c user.email=t@t -c user.name=t add . >/dev/null 2>&1
git -C "$PROJ" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
    commit -q -m "fixture: the committed state" >/dev/null 2>&1
git -C "$PROJ" rev-parse --verify --quiet HEAD >/dev/null 2>&1 \
    || { echo "FAIL could not build the fixture repo (is commit signing forcing a prompt?)"; exit 2; }

# Now diverge the working tree from what is committed. Nothing is pushed —
# there is no remote at all, which is the ordinary state of a branch mid-work.
printf 'this file exists only on disk\n' > "$PROJ/uncommitted_proof.py"
printf 'EDITED-IN-WORKTREE\n'            > "$PROJ/tracked-edit.txt"
rm -f "$PROJ/deleted-in-worktree.txt"

# A stub ssh, first on PATH: this pin must never touch the network, and a runner
# that ignores the stage-only contract and reaches for the wire must fail fast
# rather than hang on a DNS timeout.
mkdir -p "$WORK/bin"
printf '#!/bin/sh\necho "stub ssh: this pin does not use the network" >&2\nexit 255\n' > "$WORK/bin/ssh"
chmod +x "$WORK/bin/ssh"

stage() {  # <run-id> [extra env assignments...] → echoes the stage dir
    local id="$1"; shift
    local dir="$WORK/stage-$id"
    env PATH="$WORK/bin:$PATH" \
        CLAUDE_PROJECT_DIR="$PROJ" \
        LOOPKIT_REMOTE="pin@example.invalid" \
        LOOPKIT_GATE_STAGE_DIR="$dir" \
        "$@" \
        bash "$RUNNER" main 'echo stub-suite' >"$WORK/out-$id.log" 2>&1
    printf '%s' "$dir"
}

echo "== the gate must send the WORKING TREE, not only pushed state"
S="$(stage tree)"
OUT="$WORK/out-tree.log"

if [ ! -d "$S" ] || [ -z "$(ls -A "$S" 2>/dev/null)" ]; then
    bad "the runner staged NOTHING — it gates pushed state only, so uncommitted work is never sent"
    bad "a file added in the working tree never reaches the gate"
    bad "an edit to a tracked file never reaches the gate"
    bad "a deletion in the working tree never reaches the gate"
    bad "the committed state never reaches the gate"
    bad ".git never reaches the gate — the pins that ask git for the repo root cannot run"
else
    [ -f "$S/uncommitted_proof.py" ] \
        && ok "an untracked new file reaches the staged tree" \
        || bad "uncommitted_proof.py is MISSING from the stage — uncommitted work is not gated"

    if [ -f "$S/tracked-edit.txt" ] && grep -q 'EDITED-IN-WORKTREE' "$S/tracked-edit.txt" 2>/dev/null; then
        ok "an edit to a tracked file reaches the staged tree (content, not just the name)"
    else
        bad "tracked-edit.txt carries '$(cat "$S/tracked-edit.txt" 2>/dev/null | head -1)' — the committed version, not the working tree's"
    fi

    # The one a copy-everything-in would get wrong. Without --delete the stage
    # keeps a file the developer removed, and the suite passes on a tree nobody
    # has.
    [ ! -e "$S/deleted-in-worktree.txt" ] \
        && ok "a deletion in the working tree is mirrored into the stage" \
        || bad "deleted-in-worktree.txt is still in the stage — the gate runs against a tree nobody has"

    # Controls. Staging the dirt must not cost the committed state or the repo.
    #
    # This control used to read `[ -f "$S/committed.txt" ]` and say "committed
    # files are still there". It could not fail. Every committed file that still
    # exists is also in the working tree, so a runner that clones passes it and
    # a runner that only copies the working tree passes it too — and its failure
    # message named a condition ("staging replaced the repo") it had no way to
    # detect. A pin with one vacuous row teaches that its other rows might be
    # vacuous as well.
    #
    # So ask the question that CAN have a wrong answer: is the committed state
    # still distinguishable from the uncommitted state on the far side? The
    # rival transport design is to commit the dirt in the stage and push it,
    # and that design passes every other row here while destroying this one —
    # HEAD would carry EDITED-IN-WORKTREE, so nothing downstream could tell what
    # was actually committed.
    head_of_edit="$(git -C "$S" show HEAD:tracked-edit.txt 2>/dev/null)"
    if [ "$head_of_edit" = "ORIGINAL" ]; then
        ok "control: the overlay left the committed history intact (HEAD still holds the pre-edit content)"
    else
        bad "the stage's HEAD:tracked-edit.txt reads '$head_of_edit', not 'ORIGINAL' — the runner committed the working tree instead of overlaying it, so committed and uncommitted are no longer distinguishable on the far side"
    fi
    [ -d "$S/.git" ] \
        && ok "control: .git is staged, so pins that ask git for the repo root still work" \
        || bad ".git is missing from the stage — every git-rooted pin breaks on the far side"
fi

echo "== and it must SAY what it gated"
# Narrow scope is defensible; SILENT scope is not. The reader of a verdict has
# to be told which tree it is about, and the count is what makes the line
# impossible to satisfy with a constant string.
grep -qi 'working tree' "$OUT" \
    && ok "the run announces that the working tree is part of what it gated" \
    || bad "nothing in the output names the working tree — the verdict's scope is silent"
grep -qE '3 uncommitted change\(s\)' "$OUT" \
    && ok "it reports how many uncommitted changes went with it (3)" \
    || bad "the uncommitted-change count is absent or wrong: $(grep -i 'SCOPE' "$OUT" | head -1)"

echo "== bare-host mode must survive (LOOPKIT_REMOTE_DOCKER=0)"
# The second capability lost in the same merge. Nothing referenced it, so
# nothing broke, so nothing noticed — which is the argument for pinning it
# rather than the argument against.
stage docker LOOPKIT_REMOTE_DOCKER=1 >/dev/null
stage bare   LOOPKIT_REMOTE_DOCKER=0 >/dev/null
grep -q 'PLAN CMD:.*docker run' "$WORK/out-docker.log" \
    && ok "the default plan runs in a container" \
    || bad "the default plan does not mention docker: $(grep 'PLAN CMD' "$WORK/out-docker.log" | head -1)"
if grep -q 'PLAN CMD' "$WORK/out-bare.log" && ! grep -q 'PLAN CMD:.*docker' "$WORK/out-bare.log"; then
    ok "LOOPKIT_REMOTE_DOCKER=0 plans a bare-host run, with no container"
else
    bad "LOOPKIT_REMOTE_DOCKER=0 is ignored — bare-host mode is gone: $(grep 'PLAN CMD' "$WORK/out-bare.log" | head -1)"
fi

echo
if [ "$fails" = 0 ]; then echo "REMOTE GATE TREE: ALL PASS"; else echo "REMOTE GATE TREE: $fails FAILURE(S)"; fi
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
