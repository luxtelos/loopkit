#!/usr/bin/env bash
# governance-shell-cases.sh — protect_governance must block WRITES to ratified
# paths and never block READS of them.
#
# WHY BOTH HALVES. The first Bash-aware version of the guard collected every
# token after sed/perl/awk, so `sed -n '1,20p' <spec>` was blocked. A guard that
# blocks reading is worse than one that misses a write: people export the
# override permanently and it stops guarding anything. And the override itself
# was unreachable from a shell command, because an inline VAR=1 prefix lives in
# the command string and never reaches the hook's own environment.
#
# The case lists live in a FILE rather than inline in a shell command on purpose:
# a command containing the literal write shapes trips the very guard under test.
# usage: gov-cases.sh <path-to-protect_governance.py>
H="${1:?usage: gov-cases.sh <hook>}"
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-govcases.XXXXXX")"
mkdir -p "$ROOT/specs"

# `env -u GOVERNANCE_EDIT_OK` on every probe. Without it, running the suite from
# a shell that legitimately carries the override turns every blocked-write case
# green — writes-leaked=10 and the whole section reports success while measuring
# an open door. Observed 2026-09-07 while overriding the guard to edit a spec in
# the same command that ran the tests.
probe() {
  python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$1" \
    | env -u GOVERNANCE_EDIT_OK CLAUDE_PROJECT_DIR="$ROOT" python3 "$H" >/dev/null 2>&1
  echo $?
}

writes=(
  'cat > specs/x.md'
  'tee specs/x.md'
  'rm specs/x.md'
  'mv a specs/b.md'
  'cp a specs/b.md'
  "sed -i '' s/a/b/ specs/x.md"
  'perl -pi -e s/a/b/ specs/x.md'
  '> constitution.md'
  'git checkout specs/x.md'
  'git rm specs/x.md'
)
reads=(
  "sed -n '1,20p' specs/x.md"
  'awk /foo/ specs/x.md'
  'cat specs/x.md'
  'grep -r foo specs/'
  'git show HEAD:specs/x.md'
  'git diff specs/'
  'git log specs/x.md'
  'git status'
  'ls specs/'
  'perl -ne print specs/x.md'
  'head -5 specs/x.md'
)

wfail=0; rfail=0
echo "WRITES (want rc=2)"
for c in "${writes[@]}"; do
  rc="$(probe "$c")"
  [ "$rc" = 2 ] || { wfail=$((wfail+1)); printf '  LEAK rc=%s  %s\n' "$rc" "$c"; }
done
[ "$wfail" = 0 ] && echo "  all ${#writes[@]} write shapes blocked"

echo "READS (want rc=0)"
for c in "${reads[@]}"; do
  rc="$(probe "$c")"
  [ "$rc" = 0 ] || { rfail=$((rfail+1)); printf '  FALSE BLOCK rc=%s  %s\n' "$rc" "$c"; }
done
[ "$rfail" = 0 ] && echo "  all ${#reads[@]} read shapes allowed"

echo "OVERRIDE (want rc=0)"
# Two doors, and they are NOT the same door. The env var is set for the hook's
# own process. The inline prefix lives in the command string and reaches the hook
# only if it parses it -- that is the half that was broken, and testing only the
# env var is why the first version of this pin could not have caught it.
# Control for the controls: with neither door open this command must be REFUSED.
# Without this line the two probes below can both report success while the hook
# is simply not matching the path -- which is exactly what happened on the first
# version of this section.
mk() { python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$1"; }
# the same literal the writes list uses, so the probe really hits a protected path
WRITE_CMD="cat > specs/x.md"
env_ovr="$(mk "$WRITE_CMD" | GOVERNANCE_EDIT_OK=1 CLAUDE_PROJECT_DIR="$ROOT" python3 "$H" >/dev/null 2>&1; echo $?)"
inline_ovr="$(mk "GOVERNANCE_EDIT_OK=1 $WRITE_CMD" | env -u GOVERNANCE_EDIT_OK CLAUDE_PROJECT_DIR="$ROOT" python3 "$H" >/dev/null 2>&1; echo $?)"
ovr=0
no_door="$(mk "$WRITE_CMD" | env -u GOVERNANCE_EDIT_OK CLAUDE_PROJECT_DIR="$ROOT" python3 "$H" >/dev/null 2>&1; echo $?)"
if [ "$no_door" = 2 ]; then echo "  control: the write is refused with no door open"; else ovr=1; echo "  CONTROL BROKEN rc=$no_door -- the override probes below prove nothing"; fi
if [ "$env_ovr" = 0 ]; then echo "  env var opens the door"; else ovr=1; echo "  ENV OVERRIDE BROKEN rc=$env_ovr"; fi
if [ "$inline_ovr" = 0 ]; then echo "  inline prefix opens the door"; else ovr=1; echo "  INLINE OVERRIDE BROKEN rc=$inline_ovr"; fi

echo "SUMMARY writes-leaked=$wfail reads-false-blocked=$rfail"
[ "$wfail" = 0 ] && [ "$rfail" = 0 ] && [ "$ovr" = 0 ]
