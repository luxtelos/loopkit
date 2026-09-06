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

# --- 0.2.0-a: the practice floor -------------------------------------------
echo "== protect_tests (a test count may never drop)"
mkdir -p "$T/tests" "$T/src"
printf 'it("a", () => {});\nit("b", () => {});\n' > "$T/tests/x.test.ts"
pt() {  # <want> <label> <json>
  local want="$1" label="$2" json="$3"
  printf '%s' "$json" | python3 "$P/hooks/protect_tests.py" >/dev/null 2>&1; local got=$?
  [ "$got" = "$want" ] && ok "$label (rc=$got)" || fail "$label (rc=$got, want $want)"
}
pt 2 "Edit removing a test is blocked" '{"tool_name":"Edit","tool_input":{"file_path":"'"$T"'/tests/x.test.ts","old_string":"it(\"a\", () => {});\nit(\"b\", () => {});","new_string":"it(\"a\", () => {});"}}'
pt 0 "Edit adding a test passes" '{"tool_name":"Edit","tool_input":{"file_path":"'"$T"'/tests/x.test.ts","old_string":"it(\"b\", () => {});","new_string":"it(\"b\", () => {});\nit(\"c\", () => {});"}}'
pt 0 "Edit renaming a test passes (same count)" '{"tool_name":"Edit","tool_input":{"file_path":"'"$T"'/tests/x.test.ts","old_string":"it(\"a\"","new_string":"it(\"alpha\""}}'
pt 2 "Write with fewer tests is blocked" '{"tool_name":"Write","tool_input":{"file_path":"'"$T"'/tests/x.test.ts","content":"it(\"only\", () => {});"}}'
pt 0 "Write to a non-test file passes" '{"tool_name":"Write","tool_input":{"file_path":"'"$T"'/src/a.ts","content":"x"}}'
pt 2 "rm of a test path is blocked" '{"tool_name":"Bash","tool_input":{"command":"rm tests/x.test.ts"}}'
pt 2 "git rm of a test path is blocked" '{"tool_name":"Bash","tool_input":{"command":"cd x && git rm -q tests/x.test.ts"}}'
pt 0 "rm of a source path passes" '{"tool_name":"Bash","tool_input":{"command":"rm src/a.ts"}}'
pt 0 "prose mentioning rm tests passes" '{"tool_name":"Bash","tool_input":{"command":"echo \"never rm tests/\""}}'
printf '{"features":[{"id":"f1","name":"login","passes":false},{"id":"f2","name":"billing","passes":false}]}' > "$T/state/features.json"
pt 0 "features.json: flipping passes is allowed" '{"tool_name":"Write","tool_input":{"file_path":"'"$T"'/state/features.json","content":"{\"features\":[{\"id\":\"f1\",\"name\":\"login\",\"passes\":true},{\"id\":\"f2\",\"name\":\"billing\",\"passes\":false}]}"}}'
pt 2 "features.json: renaming a feature is blocked" '{"tool_name":"Write","tool_input":{"file_path":"'"$T"'/state/features.json","content":"{\"features\":[{\"id\":\"f1\",\"name\":\"signin\",\"passes\":true},{\"id\":\"f2\",\"name\":\"billing\",\"passes\":false}]}"}}'
pt 2 "features.json: dropping a feature is blocked" '{"tool_name":"Write","tool_input":{"file_path":"'"$T"'/state/features.json","content":"{\"features\":[{\"id\":\"f1\",\"name\":\"login\",\"passes\":true}]}"}}'
TEST_EDIT_OK=1 pt 0 "TEST_EDIT_OK=1 is the visible door" '{"tool_name":"Bash","tool_input":{"command":"rm tests/x.test.ts"}}'
printf '{nope' | python3 "$P/hooks/protect_tests.py" >/dev/null 2>&1; [ $? = 0 ] && ok "protect_tests: junk input allows" || fail "protect_tests must fail open"

echo "== progress, precompact, resume"
out="$(python3 "$P/scripts/progress.py" files-modified --root "$T")"
grep -q '^app.ts$' <<<"$out" && ok "files-modified lists app.ts" || fail "files-modified: $out"
python3 "$P/scripts/progress.py" append --root "$T" --text "selftest event" >/dev/null
grep -q 'selftest event' "$T/state/progress.md" && ok "progress.md appended" || fail "progress append"
grep -q 'gate PASS' "$T/state/progress.md" && ok "stop gate recorded its PASS in progress.md" || fail "gate PASS not recorded"
echo '{"session_id":"selftest-compact","trigger":"manual"}' | bash "$P/hooks/precompact.sh"; [ $? = 0 ] && ok "precompact exits 0" || fail "precompact rc"
snap="$(ls "$T"/.loopkit/session/*/precompact.md 2>/dev/null | head -1)"
[ -n "$snap" ] && grep -q '^## Files Modified' "$snap" && grep -q 'app.ts' "$snap" && ok "precompact snapshot has Files Modified from git" || fail "precompact snapshot: $snap"
for sec in "Session Intent" "Decisions Made" "Current State" "Next Steps"; do grep -q "^## $sec" "$snap" && ok "snapshot section: $sec" || fail "snapshot missing $sec"; done
out="$(bash "$P/hooks/session_start.sh" resume)"
grep -q 'LOOPKIT RESUME' <<<"$out" && grep -q 'app.ts' <<<"$out" && ok "session_start resume prints Files Modified" || fail "resume: $out"

echo "== linters on fixtures"
F="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-lint.XXXXXX")"
printf '# CLAUDE.md\n- NEVER do a\n- ALWAYS do b\n- IMPORTANT: c\n- run the tests with npm run test before claiming done\n- this exact standing rule is duplicated between two files on purpose\n' > "$F/CLAUDE.md"
printf '# constitution\n- this exact standing rule is duplicated between two files on purpose\n' > "$F/constitution.md"
printf '{"scripts":{"test":"vitest"}}' > "$F/package.json"
out="$(python3 "$P/scripts/check-claude-md.py" --root "$F")"
grep -q 'emphasised lines' <<<"$out" && ok "check-claude-md flags multiple emphasised lines" || fail "emphasis: $out"
grep -q 'restates a rule already in constitution.md' <<<"$out" && ok "check-claude-md flags the duplicate" || fail "duplicate: $out"
grep -q 'restates a package.json script' <<<"$out" && ok "check-claude-md flags the derivable line" || fail "derivable: $out"
expect_rc 1 "check-claude-md --strict fails on the duplicate" python3 "$P/scripts/check-claude-md.py" --root "$F" --strict
printf '# CLAUDE.md\n- one rule\n' > "$F/CLAUDE.md"; rm -f "$F/constitution.md"
expect_rc 0 "check-claude-md --strict passes a clean file" python3 "$P/scripts/check-claude-md.py" --root "$F" --strict
expect_rc 0 "check-skills --strict passes the plugin's own skills" python3 "$P/scripts/check-skills.py" --root "$F" --plugin "$P" --strict
mkdir -p "$F/.claude/skills/bad"; printf '# no frontmatter\n' > "$F/.claude/skills/bad/SKILL.md"
expect_rc 1 "check-skills --strict fails a skill without frontmatter/Gotchas" python3 "$P/scripts/check-skills.py" --root "$F" --strict
printf '{"mcpServers":{"ghost":{"command":"./bin/server","args":["--token","ghp_0123456789abcdefghijklmnopqrstuv"]}}}' > "$F/.mcp.json"
printf '# TOOLS.md\n' > "$F/TOOLS.md"
out="$(python3 "$P/scripts/check-tools.py" --root "$F")"
grep -q 'no row in TOOLS.md' <<<"$out" && ok "check-tools: server without a row" || fail "tools row: $out"
grep -q 'relative path' <<<"$out" && ok "check-tools: relative command" || fail "tools relative: $out"
grep -q 'secret-looking literal' <<<"$out" && ! grep -q 'ghp_0123' <<<"$out" && ok "check-tools: secret flagged by key, value never printed" || fail "tools secret: $out"
expect_rc 1 "check-tools --strict fails" python3 "$P/scripts/check-tools.py" --root "$F" --strict
mkdir -p "$F/.claude"; printf '{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"python3 .claude/hooks/block_dangerous.py"}]}]}}' > "$F/.claude/settings.json"
out="$(python3 "$P/scripts/check-duplicate-hooks.py" --root "$F" --plugin "$P")"
grep -q 'DUPLICATE: .claude/settings.json wires block_dangerous.py' <<<"$out" && ok "check-duplicate-hooks finds the project copy" || fail "dup hooks: $out"
find "$F" -delete

echo "== approval fatigue"
# The id is computed ONCE: `'…'$$'…'` inside a "$(…)" substitution expands to
# an empty PID on macOS bash 3.2, so building it inline on both sides hashed
# two different sessions and the pin failed on the test's own quoting.
FSID="selftest-fatigue-$$-$RANDOM"
FRESH="selftest-fresh-$$-$RANDOM"
for i in $(seq 1 31); do printf '{"session_id":"%s","tool_name":"Bash","tool_input":{}}' "$FSID" | python3 "$P/hooks/count_approvals.py"; done
out="$(printf '{"session_id":"%s","prompt":"/loop"}' "$FSID" | python3 "$P/hooks/loop_doctrine.py")"
grep -q 'APPROVAL FATIGUE: 31' <<<"$out" && ok "doctrine names approval fatigue past the threshold" || fail "fatigue line missing: $out"
out="$(printf '{"session_id":"%s","prompt":"/loop"}' "$FRESH" | python3 "$P/hooks/loop_doctrine.py")"
grep -q 'APPROVAL FATIGUE' <<<"$out" && fail "fatigue line on a fresh session" || ok "no fatigue line on a fresh session"
out="$(printf '{"session_id":"%s","tool_name":"Bash","tool_input":{}}' "$FSID" | python3 "$P/hooks/count_approvals.py")"
[ -z "$out" ] && ok "count_approvals prints nothing (never decides a permission)" || fail "count_approvals printed: $out"

echo "== hook wiring"
for ev in PreCompact PermissionRequest; do
  python3 -c "import json,sys; h=json.load(open(sys.argv[1]))['hooks']; sys.exit(0 if sys.argv[2] in h else 1)" "$P/hooks/hooks.json" "$ev" && ok "hooks.json wires $ev" || fail "hooks.json lacks $ev"
done
python3 -c "import json,sys; h=json.load(open(sys.argv[1]))['hooks']['SessionStart']; sys.exit(0 if any(e.get('matcher')=='resume|compact' for e in h) else 1)" "$P/hooks/hooks.json" && ok "SessionStart resume|compact matcher present" || fail "SessionStart matcher"
grep -q 'protect_tests.py' "$P/hooks/hooks.json" && ok "protect_tests wired" || fail "protect_tests not wired"
[ -f "$T/.loopkit/test-globs.txt" ] && ok "init laid down .loopkit/test-globs.txt" || fail "test-globs.txt missing after init"

# The repo this came from is never named anywhere in this project (owner rule,
# 2026-09-06) — README, docs, tests and plugin alike. The plugin's own
# `luxtelos/loopkit` is the one org reference allowed.
leaks="$(grep -rnEil --exclude-dir=__pycache__ --exclude-dir=.git --exclude=.git --exclude-dir=worktrees --exclude='*.pyc' --exclude=selftest.sh 'balancia|balencia|/Volumes/evm|dev_multi_iam|codecakes/adaptive|adaptive-unified|accountingos' "$REPO" 2>/dev/null || true)"
[ -z "$leaks" ] && ok "no project-specific tokens anywhere in the repo" || { fail "project tokens in: $leaks"; }
orgs="$(grep -rnE --exclude-dir=__pycache__ --exclude-dir=.git --exclude=.git --exclude-dir=worktrees --exclude=selftest.sh 'luxtelos' "$REPO" 2>/dev/null | grep -v 'luxtelos/loopkit' || true)"
[ -z "$orgs" ] && ok "the only org reference is luxtelos/loopkit" || { fail "other org references: $orgs"; }

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

# --- the repo's own gate config must source silently: an unquoted multi-word
# value (LOOP_TEST_CMD=bash tests/selftest.sh) RUNS the second word as a command
section "dogfood: .loopkit/config.env sources clean"
cfg_err="$( ( set -a; . "$REPO_ROOT/.loopkit/config.env"; set +a ) 2>&1 >/dev/null )"
if [ -z "$cfg_err" ]; then ok "config.env sources with no stderr"; else fail "config.env sourcing printed: $cfg_err"; fi
