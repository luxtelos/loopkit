#!/usr/bin/env bash
# remote-gate-config.sh — LOOPKIT_REMOTE must have a reader, and must never
# take a working value away.
#
# WHY THIS PIN EXISTS. `.loopkit/config.env` grew LOOPKIT_REMOTE and
# LOOPKIT_SSH_KEY with a comment saying "set this and use tools/remote-gate.sh".
# Two things were wrong at once:
#
#   1. remote-gate.sh read only the environment and never sourced config.env, so
#      the slot the comment pointed at had no path to the one tool that uses the
#      variable. A config key nothing reads is decoration.
#   2. Eight scripts source config.env with `set -a`. An unconditional
#      LOOPKIT_REMOTE="" therefore OVERWROTE a correct exported value with
#      nothing. A key that only takes a setting away is worse than decoration.
#
# The rest of the file is read as `${VAR-default}`, where an explicit empty
# legitimately means "skip this step". These two keys carry no such contract, so
# they — and only they — are written `${VAR:-}`.
#
# usage: bash tests/pins/remote-gate-config.sh [<repo-root>]

set -uo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
CFG="$REPO/.loopkit/config.env"
RUNNER="$REPO/tools/remote-gate.sh"
[ -f "$CFG" ]    || { echo "FAIL no config.env at $CFG"; exit 2; }
[ -f "$RUNNER" ] || { echo "FAIL no remote-gate.sh at $RUNNER — this branch must carry the script its agent files point at"; exit 2; }

fails=0
ok()  { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails+1)); }

echo "== sourcing the config must not clobber an exported value"
# Exactly how stop_gate.sh and seven others read it: set -a, dot, set +a.
for key in LOOPKIT_REMOTE LOOPKIT_SSH_KEY; do
    got="$(env "$key=sentinel@example.invalid" bash -c '
        set -a; . "$1" >/dev/null 2>&1; set +a
        eval "printf %s \"\$$2\"" ' _ "$CFG" "$key")"
    [ "$got" = "sentinel@example.invalid" ] \
        && ok "$key survives a set -a source" \
        || bad "$key CLOBBERED to '${got}' — an empty config entry destroyed a working value"
done

echo "== sourcing the config must be safe when the value is unset"
err="$(env -u LOOPKIT_REMOTE -u LOOPKIT_SSH_KEY bash -c '
    set -u; set -a; . "$1"; set +a; printf ok' _ "$CFG" 2>&1)"
[ "$err" = "ok" ] && ok "sources cleanly with the keys unset, under set -u" \
                  || bad "sourcing with the keys unset printed: $err"

echo "== the key has a reader"
# Proven by observation, not by grep: the runner is asked to resolve its
# settings with the value present ONLY in a project config file, and must
# report it. LOOPKIT_GATE_DRY_RUN stops before any network call.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-gate-config.XXXXXX")"
trap 'find "$WORK" -mindepth 1 -delete 2>/dev/null; rmdir "$WORK" 2>/dev/null || true' EXIT
mkdir -p "$WORK/.loopkit"
printf 'LOOPKIT_REMOTE="${LOOPKIT_REMOTE:-from-config@example.invalid}"\nLOOPKIT_SSH_KEY="${LOOPKIT_SSH_KEY:-/keys/from-config}"\n' > "$WORK/.loopkit/config.env"

out="$(env -u LOOPKIT_REMOTE -u LOOPKIT_SSH_KEY CLAUDE_PROJECT_DIR="$WORK" LOOPKIT_GATE_DRY_RUN=1 \
        bash "$RUNNER" main 2>&1)"; rc=$?
if [ "$rc" != 0 ]; then
    bad "the runner failed with the value in config.env only (rc=$rc): $(printf '%s' "$out" | tail -2)"
else
    grep -q 'from-config@example.invalid' <<<"$out" \
        && ok "remote-gate.sh reads LOOPKIT_REMOTE from .loopkit/config.env" \
        || bad "remote-gate.sh ignored config.env: $out"
    grep -q '/keys/from-config' <<<"$out" \
        && ok "remote-gate.sh reads LOOPKIT_SSH_KEY from .loopkit/config.env" \
        || bad "LOOPKIT_SSH_KEY not read from config.env: $out"
fi

echo "== the environment still wins over the config file"
out="$(env LOOPKIT_REMOTE=from-env@example.invalid CLAUDE_PROJECT_DIR="$WORK" LOOPKIT_GATE_DRY_RUN=1 \
        bash "$RUNNER" main 2>&1)"
grep -q 'from-env@example.invalid' <<<"$out" \
    && ok "an exported LOOPKIT_REMOTE beats the config file" \
    || bad "the config file overrode the environment: $out"

echo "== the agent files must point at a script that exists on this branch"
# Three earlier hand-offs referenced this script while it existed in no merged
# branch, so each agent improvised its own ssh line and the results were not
# comparable. Re-creating that state inside the two files nobody can miss is
# the same defect, amplified.
for f in plugins/loopkit/agents/implementer.md plugins/loopkit/agents/reviewer.md; do
    if grep -q 'tools/remote-gate.sh' "$REPO/$f" 2>/dev/null; then
        [ -f "$REPO/tools/remote-gate.sh" ] \
            && ok "$(basename "$f") points at a script present on this branch" \
            || bad "$(basename "$f") sends the reader to a missing tools/remote-gate.sh"
    fi
done

echo
if [ "$fails" = 0 ]; then echo "REMOTE GATE CONFIG: ALL PASS"; else echo "REMOTE GATE CONFIG: $fails FAILURE(S)"; fi
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
