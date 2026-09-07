# remote-gate-body.sh — the half of tools/remote-gate.sh that runs INSIDE the
# container. Not executable on its own: `remote-gate.sh` prepends bash-quoted
# assignments for REF, SUITE and REPO_URL and feeds the result to `bash -s`
# over ssh's stdin, which is why nothing here needs to survive nested quoting.
#
# It lives in its own file so it can be run directly, on any machine, with a
# stub suite — see tests/pins/remote-gate-status.sh. A runner that has only
# ever been observed saying PASS has not been shown to be able to say anything
# else, and this one shipped saying PASS on a suite printing
# "FAILURE: 3 checks failed".
#
#   REF        branch or sha to check out
#   SUITE      the command that IS the gate
#   REPO_URL   clone source
#   WORKDIR    where to clone (default /w; the pin overrides it)
#   LOGDIR     where the run's logs go (default /tmp)
#   SKIP_APT   set to 1 to skip apt-get (the pin does; a container does not)

set -u
WORKDIR="${WORKDIR:-/w}"
LOGDIR="${LOGDIR:-/tmp}"
mkdir -p "$LOGDIR"

if [ "${SKIP_APT:-0}" != "1" ]; then
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq git python3 >/dev/null 2>&1
fi

if ! git clone -q "$REPO_URL" "$WORKDIR" 2>"$LOGDIR/clone.err"; then
    echo "REMOTE GATE: clone of $REPO_URL failed"; cat "$LOGDIR/clone.err"; exit 90
fi
cd "$WORKDIR" || exit 91
if ! git checkout -q "$REF" 2>"$LOGDIR/checkout.err"; then
    echo "REMOTE GATE: no such ref '$REF'"; cat "$LOGDIR/checkout.err"; exit 92
fi

echo "REF: $(git rev-parse --short HEAD)  $(git log -1 --format=%s | cut -c1-60)"
echo "PLATFORM: $(uname -s) bash $BASH_VERSION"
echo "TMPDIR: ${TMPDIR:-/tmp} -> $(cd "${TMPDIR:-/tmp}" 2>/dev/null && pwd -P || echo "${TMPDIR:-/tmp}")"
echo "SUITE: $SUITE"
echo "---"

# The two lines that are the whole point of this file. Output goes to a FILE,
# and the status is read on the very next line, before anything can overwrite
# $?. The old shape was `suite | grep | tail` followed by `echo "suite-rc=$?"`,
# which reports TAIL's status: the reader exits early, the writer takes
# SIGPIPE, and a failing suite reported suite-rc=0.
#
# stdin is /dev/null because this script arrives ON stdin: `bash -s` is still
# reading it, and a suite that reads stdin would eat the rest of the script.
bash -c "$SUITE" </dev/null >"$LOGDIR/suite.log" 2>&1
suite_rc=$?

# Display only, and structurally incapable of changing the verdict: it filters
# the FILE (no pipe at all, so no SIGPIPE and no borrowed status), and the
# variable captured above is what exits.
grep -E '^  FAIL|ALL PASS|FAILURE|^== ' "$LOGDIR/suite.log" >"$LOGDIR/suite.filtered" 2>/dev/null || true
if [ -s "$LOGDIR/suite.filtered" ]; then
    tail -n 40 "$LOGDIR/suite.filtered"
else
    echo "(no summary lines matched; last 40 lines of raw output)"
    tail -n 40 "$LOGDIR/suite.log"
fi
echo "suite-rc=$suite_rc"
exit "$suite_rc"
