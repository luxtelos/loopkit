#!/usr/bin/env bash
# selftest.sh — prove the plugin works before it is published.
#
# Runs every hook and script test, then an end-to-end pass in a scratch git
# repo: init → triage rows → loop-next → regression diff → stop gate →
# citations → doctrine. Exit 1 on the first failure. No network, no real
# test suite, no model-checker install needed (the driver's selftest uses
# stub engines).
#
# usage: bash tests/selftest.sh

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
P="$REPO/plugins/loopkit"
fails=0
ok()   { echo "  ok   $1"; }
fail() { echo "  FAIL $1"; fails=$((fails+1)); }
expect_rc() {  # <want> <label> <cmd...>
  local want="$1" label="$2"; shift 2
  "$@" >/dev/null 2>&1; local got=$?
  if [ "$got" = "$want" ]; then ok "$label (rc=$got)"; else fail "$label (rc=$got, want $want)"; fi
}

echo "== syntax and manifests"
for f in "$P"/hooks/*.sh "$P"/scripts/*.sh "$P"/skills/run-state-model/install.sh; do
  bash -n "$f" && ok "bash -n $(basename "$f")" || fail "bash -n $f"
done
python3 -m py_compile "$P"/hooks/*.py "$P"/scripts/*.py && ok "py_compile" || fail "py_compile"
for j in "$P/hooks/hooks.json" "$P/.claude-plugin/plugin.json" "$REPO/.claude-plugin/marketplace.json" "$P"/templates/loopkit/*.json; do
  python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$j" && ok "json $(basename "$j")" || fail "json $j"
done
node --check "$P/skills/run-state-model/driver.mjs" && ok "node --check driver.mjs" || fail "driver.mjs syntax"

echo "== hook tests"
for t in test_block_dangerous test_loop_doctrine test_protect_governance; do
  expect_rc 0 "$t" python3 "$P/hooks/$t.py"
done

echo "== script tests"
expect_rc 0 "test_loop_next_pick" python3 "$P/scripts/test_loop_next_pick.py"
expect_rc 0 "test_loop_scan" python3 "$P/scripts/test_loop_scan.py"
expect_rc 0 "test_inbox_to_triage" python3 "$P/scripts/test_inbox_to_triage.py"

echo "== model-checker driver selftest (stub engines)"
expect_rc 0 "driver selftest" node "$P/skills/run-state-model/driver.mjs" selftest

echo "== end to end in a scratch repo"
T="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-selftest.XXXXXX")"
( cd "$T" && git init -q . && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init )
export CLAUDE_PROJECT_DIR="$T"
expect_rc 0 "init (first run)" bash "$P/scripts/loopkit-init.sh" --project "$T"
expect_rc 0 "init (second run, idempotent)" bash "$P/scripts/loopkit-init.sh" --project "$T"
n="$(grep -c 'loopkit:begin' "$T/CLAUDE.md")"; [ "$n" = 1 ] && ok "CLAUDE.md block appended exactly once" || fail "CLAUDE.md block count=$n"
grep -qxF '.loopkit/config.env' "$T/.gitignore" && ok ".gitignore ignores config.env" || fail ".gitignore"
for f in constitution.md FILES.md TOOLS.md COMMANDS.md state/triage.md inbox/needs-human.md .loopkit/scopes.json; do
  [ -f "$T/$f" ] && ok "created $f" || fail "missing $f"
done

# require_contracts: gated once the contracts exist, open before a Read marks them
payload='{"session_id":"selftest-'$$'","tool_name":"Bash","tool_input":{"command":"ls"}}'
echo "$payload" | python3 "$P/hooks/require_contracts.py" gate >/dev/null 2>&1; [ $? = 2 ] && ok "contracts gate blocks before read" || fail "contracts gate should block"
for c in FILES.md TOOLS.md COMMANDS.md; do
  echo '{"session_id":"selftest-'$$'","tool_name":"Read","tool_input":{"file_path":"'"$T/$c"'"}}' | python3 "$P/hooks/require_contracts.py" mark
done
echo "$payload" | python3 "$P/hooks/require_contracts.py" gate >/dev/null 2>&1; [ $? = 0 ] && ok "contracts gate opens after reads" || fail "contracts gate should open"
( cd "$(mktemp -d)" && echo "$payload" | CLAUDE_PROJECT_DIR="$PWD" python3 "$P/hooks/require_contracts.py" gate ) && ok "contracts gate is passive in an uninitialised repo" || fail "gate must not lock a bare repo"

# triage rows and the stage lookup
TS="$P/scripts/triage_state.py"
python3 "$TS" upsert --state "$T/state/triage.md" --finding "Invoice totals drift after refund" --source "GitHub #12" --priority high --status new >/dev/null
python3 "$TS" upsert --state "$T/state/triage.md" --finding "Login page flicker | mobile" --source "GitHub #13" --priority low --status new >/dev/null
python3 "$TS" upsert --state "$T/state/triage.md" --finding "Waiting on pricing ruling" --source "inbox § pricing" --priority high --status blocked >/dev/null
out="$(bash "$P/scripts/loop-next.sh")"
grep -q '^STAGE: new' <<<"$out" && ok "loop-next serves STAGE: new" || fail "loop-next: $out"
grep -q '^BLOCKED: 1' <<<"$out" && ok "loop-next reports the blocked row" || fail "blocked row not reported"
grep -q 'Login page flicker | mobile' <<<"$out" && ok "a finding with a pipe survives the round trip" || fail "pipe in finding lost"
out="$(bash "$P/scripts/loop-next.sh" --scope billing)"
grep -q '^TARGET: Invoice totals' <<<"$out" && ok "--scope billing picks the invoice row" || fail "scope pick: $out"
python3 "$TS" update --state "$T/state/triage.md" --source "GitHub #12" --status spec-draft >/dev/null
# Capture first, grep second: under pipefail, `script | grep -q` fails when grep
# exits on the first match and the script takes SIGPIPE on its next echo.
out="$(bash "$P/scripts/loop-next.sh")"
grep -q '^STAGE: spec-draft' <<<"$out" && ok "transition recorded, stage moves" || fail "update did not move the stage"

# inbox bridge
printf '\n## Decide the refund window (2026-01-01)\n\nText.\n\n## RESOLVED 2026-01-02 — old one\n\nText.\n' >> "$T/inbox/needs-human.md"
out="$(python3 "$P/scripts/inbox_to_triage.py" --inbox "$T/inbox/needs-human.md" --state "$T/state/triage.md")"
grep -q 'VERDICT: 1 finding' <<<"$out" && ok "bridge files the open heading, skips the RESOLVED one" || fail "inbox bridge"

# regression diff
printf 'tests/a.test.ts :: adds\ntests/b.test.ts :: *\n' > "$T/state/known-test-failures.txt"
printf 'tests/a.test.ts :: adds\ntests/b.test.ts :: anything\n' > "$T/cur.txt"
expect_rc 0 "regressions: baseline-covered failures pass" bash "$P/scripts/test-regressions.sh" --from-list "$T/cur.txt"
printf 'tests/c.test.ts :: brand new\n' > "$T/cur.txt"
expect_rc 1 "regressions: a NEW failure fails" bash "$P/scripts/test-regressions.sh" --from-list "$T/cur.txt"

# stop gate
expect_rc 0 "stop gate short-circuits with no code change" bash "$P/hooks/stop_gate.sh"
echo "x" > "$T/app.ts"
export LOOP_TEST_CMD="true" LOOP_LINT_CMD="true" LOOP_TYPECHECK_CMD="" LOOP_BUILD_CMD="true"
expect_rc 0 "stop gate passes with green commands" bash "$P/hooks/stop_gate.sh"
LOOP_LINT_CMD="false" LOOP_LINT_FALLBACK_CMD="" expect_rc 1 "stop gate fails on red lint" bash "$P/hooks/stop_gate.sh"
LOOP_TEST_CMD="false" expect_rc 1 "stop gate fails on red tests (baseline present, regressions script used)" env LOOP_TEST_JSON_CMD="false" bash "$P/hooks/stop_gate.sh"
unset LOOP_TEST_CMD LOOP_LINT_CMD LOOP_TYPECHECK_CMD LOOP_BUILD_CMD

# citations
printf '# CLAUDE.md\nSee `app.ts:1` and `app.ts:9`.\n' > "$T/CLAUDE.md"
expect_rc 1 "citations: a line past EOF fails" python3 "$P/scripts/check-citations.py" --root "$T"
printf '# CLAUDE.md\nSee `app.ts:1`.\n' > "$T/CLAUDE.md"
expect_rc 0 "citations: a real line passes" python3 "$P/scripts/check-citations.py" --root "$T"

# doctrine prints absolute script paths
out="$(echo '{"prompt":"/loop"}' | python3 "$P/hooks/loop_doctrine.py")"
grep -q "$P/scripts/loop-next.sh" <<<"$out" && ok "doctrine carries absolute paths" || fail "doctrine paths"

# no project-specific tokens leaked into the plugin
leaks="$(grep -rnEl --exclude-dir=__pycache__ --exclude='*.pyc' 'balancia|luxtelos|/Volumes/evm|dev_multi_iam|codecakes/adaptive' "$P" 2>/dev/null || true)"
[ -z "$leaks" ] && ok "no project-specific tokens in the plugin" || { fail "project tokens in: $leaks"; }

unset CLAUDE_PROJECT_DIR
find "$T" -delete

echo "== claude plugin validate"
if command -v claude >/dev/null 2>&1; then
  ( cd "$REPO" && claude plugin validate . ) && ok "claude plugin validate" || fail "claude plugin validate"
else
  echo "  skip claude CLI not on PATH"
fi

echo
[ "$fails" = 0 ] && echo "ALL PASS" || echo "$fails FAILURE(S)"
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
