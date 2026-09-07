# remote-gate-body.sh — the half of tools/remote-gate.sh that runs on the FAR
# SIDE, in the staged tree. Not executable on its own: `remote-gate.sh`
# prepends bash-quoted assignments for REF, SUITE and DIRTY and feeds the
# result to `bash -s` over ssh's stdin, which is why nothing here needs to
# survive nested quoting.
#
# It lives in its own file for one reason: so it can be run directly, on any
# machine, against a throwaway directory and a stub suite — no ssh, no docker,
# no network. See tests/pins/remote-gate-status.sh. A runner that has only ever
# been observed saying PASS has not been shown to be able to say anything else,
# and this one shipped saying PASS on a suite printing
# "FAILURE: 3 checks failed".
#
# It does NOT clone. The tree it runs in was rsynced here by the sender and
# already carries that machine's uncommitted work; cloning would replace it with
# pushed state, which is exactly the regression the sender's header describes.
#
#   REF        the sender's ref, informational — printed so two runs can be told
#              apart. Nothing is checked out here.
#   SUITE      the command that IS the gate
#   DIRTY      how many uncommitted changes the sender carried over (display)
#   WORKDIR    the staged tree (default `.`; docker sets -w, the bare-host
#              branch cds first, and the pin overrides it)
#   LOGDIR     where the run's logs go (default /tmp)
#   SKIP_APT   set to 1 to skip apt-get (the pin does; a container does not)

set -u
WORKDIR="${WORKDIR:-.}"
LOGDIR="${LOGDIR:-/tmp}"
mkdir -p "$LOGDIR"

if [ "${SKIP_APT:-0}" != "1" ]; then
    apt-get update -qq >/dev/null 2>&1
    # rsync is here for the suite, not for this script: the working-tree pin
    # stages a fixture with it, and a pin that skips when a tool is missing
    # reports success by running nothing.
    apt-get install -y -qq git python3 rsync >/dev/null 2>&1
fi

cd "$WORKDIR" 2>/dev/null || { echo "REMOTE GATE: no staged tree at '$WORKDIR'"; exit 91; }
WORKDIR="$(pwd)"
# The staged tree is owned by whoever rsynced it, so git refuses to read it
# until it is declared safe. Without this every pin that asks git for the repo
# root fails for a reason that has nothing to do with the change.
git config --global --add safe.directory "$WORKDIR" >/dev/null 2>&1 || true

echo "REF: ${REF:-?} -> $(git rev-parse --short HEAD 2>/dev/null || echo '?')  $(git log -1 --format=%s 2>/dev/null | cut -c1-60)"
echo "TREE: ${DIRTY:-?} uncommitted change(s) carried from the sender; $(git status --porcelain 2>/dev/null | wc -l | tr -d ' ') seen here"
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
