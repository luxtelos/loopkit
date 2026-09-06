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

probe() {
  python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$1" \
    | CLAUDE_PROJECT_DIR="$ROOT" python3 "$H" >/dev/null 2>&1
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
ovr="$(python3 -c 'import json; print(json.dumps({"tool_name":"Bash","tool_input":{"command":"cat > specs/x.md"}}))' \
  | GOVERNANCE_EDIT_OK=1 CLAUDE_PROJECT_DIR="$ROOT" python3 "$H" >/dev/null 2>&1; echo $?)"
[ "$ovr" = 0 ] && echo "  ratified override still opens the door" || echo "  OVERRIDE BROKEN rc=$ovr"

echo "SUMMARY writes-leaked=$wfail reads-false-blocked=$rfail"
[ "$wfail" = 0 ] && [ "$rfail" = 0 ] && [ "$ovr" = 0 ]
