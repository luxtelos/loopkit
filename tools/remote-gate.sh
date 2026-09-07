#!/usr/bin/env bash
# Run the suite on the remote Linux box instead of the Mac.
#
# WHY THIS IS THE DEFAULT NOW. On the Mac, three checks fail purely because
# /var is a symlink, and a timing check flakes under load. Both are noise I then
# have to explain away every single run — which is exactly how a real failure
# gets waved through as "probably the Mac again". The Linux box has neither
# quirk: TMPDIR is /tmp and /tmp is /tmp.
#
# usage: tools/remote-gate.sh <branch-or-sha> [suite-command]
#
#   LOOPKIT_REMOTE    user@host of the Linux box (required)
#   LOOPKIT_SSH_KEY   identity file (default: ~/.ssh/id_ed25519)
#   LOOPKIT_IMAGE     container image (default: node:22-bookworm)
#   LOOPKIT_GATE_SUITE / argument 2
#                     the command that IS the gate (default: bash tests/selftest.sh).
#                     Overridable so this script's verdict can be proven red as
#                     well as green — a runner that has only ever been seen say
#                     PASS has not been shown to be able to say anything else.
#
# THE STATUS RULE, which this script got wrong once and must never get wrong
# again. Never read `$?` after a pipe, and never let a pipe be the last thing a
# gate does. `suite | grep | tail` reports TAIL's status: the reader exits
# early, the writer takes SIGPIPE, and a suite printing "FAILURE: 3 checks
# failed" yielded `suite-rc=0`. So: the suite writes to a FILE, its status is
# captured on the very next line, the FILE is filtered for display, and the
# captured status is what exits. Display and verdict are separate paths, and
# only one of them can fail.

set -uo pipefail

REF="${1:?usage: remote-gate.sh <branch-or-sha> [suite-command]}"
SUITE="${2:-${LOOPKIT_GATE_SUITE:-bash tests/selftest.sh}}"
REMOTE="${LOOPKIT_REMOTE:?set LOOPKIT_REMOTE=user@host}"
SSH_KEY="${LOOPKIT_SSH_KEY:-$HOME/.ssh/id_ed25519}"
IMAGE="${LOOPKIT_IMAGE:-node:22-bookworm}"
REPO_URL="https://github.com/${LOOPKIT_REPO:-luxtelos/loopkit}.git"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BODY="$HERE/remote-gate-body.sh"
[ -f "$BODY" ] || { echo "REMOTE GATE: missing $BODY" >&2; exit 2; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-remote-gate.XXXXXX")"
cleanup() { rm -f "$WORK/input.sh" "$WORK/output.log"; rmdir "$WORK" 2>/dev/null || true; }
trap cleanup EXIT

# The container script is fed to `bash -s` over ssh's stdin, so nothing has to
# survive three layers of nested quoting. REF and SUITE are prepended as
# bash-quoted assignments (printf %q), which round-trips exactly because the
# far side is bash too.
#
# Plain `bash -c`, never `bash -lc`: a login shell re-sources the profile and
# can reset PATH to a system runtime, silently discarding the caller's. The
# gate this script runs for forbids `-lc` in its own `run_in_env`; a runner that
# breaks the rule it enforces is not a runner anybody should trust.
{
    printf 'REF=%q\n' "$REF"
    printf 'SUITE=%q\n' "$SUITE"
    printf 'REPO_URL=%q\n' "$REPO_URL"
    cat "$BODY"
} > "$WORK/input.sh"

ssh -i "$SSH_KEY" -o BatchMode=yes "$REMOTE" \
    "docker run --rm -i $IMAGE bash -s" <"$WORK/input.sh" >"$WORK/output.log" 2>&1
rc=$?

cat "$WORK/output.log"
if [ "$rc" -eq 0 ]; then
    echo "REMOTE GATE: PASS (rc=0)"
else
    echo "REMOTE GATE: FAIL (rc=$rc)"
fi
exit "$rc"
