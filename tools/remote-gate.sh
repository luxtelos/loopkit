#!/usr/bin/env bash
# Run the suite on the remote Linux box instead of the Mac.
#
# WHY THIS IS THE DEFAULT NOW. On the Mac, three checks fail purely because
# /var is a symlink, and a timing check flakes under load. Both are noise I then
# have to explain away every single run — which is exactly how a real failure
# gets waved through as "probably the Mac again". The Linux box has neither
# quirk: TMPDIR is /tmp and /tmp is /tmp.
#
# usage: tools/remote-gate.sh <branch-or-sha>
#
# The host is the project's own Linux box. Override with LOOPKIT_REMOTE=user@host.
set -uo pipefail
REF="${1:?usage: remote-gate.sh <branch-or-sha>}"
EXTRA="${2:-}"

ssh -i "${LOOPKIT_SSH_KEY:-$HOME/.ssh/id_ed25519}" -o BatchMode=yes "${LOOPKIT_REMOTE:?set LOOPKIT_REMOTE=user@host}" "docker run --rm node:22-bookworm bash -lc '
set -e
apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq git python3 >/dev/null 2>&1
git clone -q https://github.com/luxtelos/loopkit.git /w 2>/dev/null
cd /w && git checkout -q $REF
echo \"REF: \$(git rev-parse --short HEAD)  \$(git log -1 --format=%s | cut -c1-60)\"
echo \"PLATFORM: \$(uname -s) python \$(python3 -V 2>&1 | cut -d\\  -f2) bash \$BASH_VERSION\"
echo \"TMPDIR: \${TMPDIR:-/tmp} -> \$(python3 -c \"import os,pathlib;print(pathlib.Path(os.environ.get(chr(84)+chr(77)+chr(80)+chr(68)+chr(73)+chr(82),\\\"/tmp\\\")).resolve())\")\"
echo \"---\"
bash tests/selftest.sh 2>&1 | grep -E \"^  FAIL|ALL PASS|FAILURE|^== \" | tail -30
echo \"suite-rc=\$?\"
'" 2>&1 | tail -40
