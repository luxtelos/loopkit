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
python3 -m py_compile "$P"/loopkit_core/*.py && ok "py_compile loopkit_core" || fail "py_compile loopkit_core"
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

# An ignore line init added once is init's LAST word on it. A project that
# deletes the line and keeps the marker has decided; the old check ("is the
# line absent?") could not tell that from a project that had never seen it, so
# init reversed the decision on every run — including on this repo, whose
# .loopkit/config.env is tracked on purpose.
echo "== init writes each ignore line once, then respects the project"
grep -qF 'loopkit:decided .loopkit/config.env' "$T/.gitignore" && ok "init leaves a decision marker in .gitignore" || fail "no decision marker in .gitignore"
grep -qF 'loopkit:decided state/triage.md' "$T/.prettierignore" && ok "init leaves a decision marker in .prettierignore" || fail "no decision marker in .prettierignore"
for pair in ".gitignore:.loopkit/config.env" ".prettierignore:state/triage.md"; do
  ig_f="$T/${pair%%:*}"; ig_l="${pair#*:}"
  grep -vxF "$ig_l" "$ig_f" > "$ig_f.tmp" || true; mv "$ig_f.tmp" "$ig_f"
  bash "$P/scripts/loopkit-init.sh" --project "$T" >/dev/null 2>&1
  if grep -qxF "$ig_l" "$ig_f"; then fail "init re-added $ig_l after the project removed it"; else ok "init respects a removed $ig_l (marker present)"; fi
  grep -vF "loopkit:decided $ig_l" "$ig_f" > "$ig_f.tmp" || true; mv "$ig_f.tmp" "$ig_f"
  bash "$P/scripts/loopkit-init.sh" --project "$T" >/dev/null 2>&1
  if grep -qxF "$ig_l" "$ig_f"; then ok "init re-adds $ig_l when no decision is on record"; else fail "init did not add $ig_l to a file carrying no marker"; fi
done
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
grep -q '^NEXT: CONTINUE' <<<"$out" && ok "loop-next says CONTINUE while rows are actionable (no wakeup)" || fail "NEXT line: $out"
grep -q '^BLOCKED: 1' <<<"$out" && ok "loop-next reports the blocked row" || fail "blocked row not reported"
grep -q 'Login page flicker | mobile' <<<"$out" && ok "a finding with a pipe survives the round trip" || fail "pipe in finding lost"
E="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-empty.XXXXXX")"; mkdir -p "$E/state"
python3 "$TS" ensure-schema --state "$E/state/triage.md" >/dev/null 2>&1 || true
out="$(CLAUDE_PROJECT_DIR="$E" bash "$P/scripts/loop-next.sh")"
grep -q '^NEXT: IDLE' <<<"$out" && ok "loop-next says IDLE on an empty queue (run triage, do not sleep)" || fail "IDLE line: $out"
python3 "$TS" upsert --state "$E/state/triage.md" --finding "Awaiting owner ruling on X" --source "inbox § X" --priority high --status blocked >/dev/null
out="$(CLAUDE_PROJECT_DIR="$E" bash "$P/scripts/loop-next.sh")"
grep -q '^NEXT: WAIT' <<<"$out" && ok "loop-next says WAIT when only a human ruling can move things" || fail "WAIT line: $out"
find "$E" -delete
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

# A citation checker that finds nothing must not print PASS. It also must not
# fail a project that legitimately cites no line numbers — so the verdict word
# changes and the exit code is the project's decision. And the scan has to
# reach where docs actually live: this repo's own run said "Checked 0
# citation(s) across 7 file(s) ... PASS" while docs/ and specs/ sat outside
# the scanned set.
echo "== citations: a run that verifies nothing says so"
CT="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-cite.XXXXXX")"
printf '# CLAUDE.md\nno citation in here at all\n' > "$CT/CLAUDE.md"
cite_out="$(python3 "$P/scripts/check-citations.py" --root "$CT" 2>&1)"; cite_rc=$?
case "$cite_out" in *EMPTY*) ok "zero citations reports EMPTY" ;; *) fail "zero citations did not report EMPTY: $cite_out" ;; esac
case "$cite_out" in *PASS*) fail "zero citations still printed PASS" ;; *) ok "zero citations never prints PASS" ;; esac
[ "$cite_rc" = 0 ] && ok "EMPTY exits 0 by default (a citation-free project is legitimate)" || fail "EMPTY exited $cite_rc by default"
expect_rc 1 "citations: --fail-on-empty turns EMPTY red" python3 "$P/scripts/check-citations.py" --root "$CT" --fail-on-empty
mkdir -p "$CT/.loopkit"
printf '{"allow_empty": false}\n' > "$CT/.loopkit/citations.json"
expect_rc 1 "citations: allow_empty=false turns EMPTY red" python3 "$P/scripts/check-citations.py" --root "$CT"
printf '{}\n' > "$CT/.loopkit/citations.json"
printf 'x\n' > "$CT/app.py"
mkdir -p "$CT/docs" "$CT/specs"
printf 'see `app.py:9`\n' > "$CT/docs/thing.md"
expect_rc 1 "citations: the default scan reaches docs/" python3 "$P/scripts/check-citations.py" --root "$CT"
printf 'nothing cited\n' > "$CT/docs/thing.md"
printf 'see `app.py:9`\n' > "$CT/specs/thing.md"
expect_rc 1 "citations: the default scan reaches specs/" python3 "$P/scripts/check-citations.py" --root "$CT"

# doctrine prints absolute script paths
out="$(echo '{"prompt":"/loop"}' | python3 "$P/hooks/loop_doctrine.py")"
grep -q "$P/scripts/loop-next.sh" <<<"$out" && ok "doctrine carries absolute paths" || fail "doctrine paths"
grep -q 'Never sleep unless something OUTSIDE the loop must move first' <<<"$out" && ok "doctrine rule 4: no wakeup while work is actionable" || fail "doctrine rule 4"

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

# --- 0.2.0-b: memory adapters ------------------------------------------------
echo "== memory: passive without memory.json"
B="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-bare.XXXXXX")"
( cd "$B" && git init -q . )
out="$(CLAUDE_PROJECT_DIR="$B" python3 "$P/scripts/memory.py" status --line)"
grep -q 'not configured' <<<"$out" && ok "memory.py: not configured without memory.json" || fail "memory passive: $out"
printf '{"session_id":"rc-bare-%s","tool_name":"Write","tool_input":{"file_path":"%s/db/migrations/V1.sql","content":"CREATE UNIQUE INDEX one ON t(a);"}}' "$$" "$B" | CLAUDE_PROJECT_DIR="$B" python3 "$P/hooks/require_recall.py" gate >/dev/null 2>&1
[ $? = 0 ] && ok "require_recall is passive without memory.json" || fail "require_recall must be passive without memory.json"
find "$B" -delete

echo "== memory: registry, adapters via PATH shims"
[ -f "$T/.loopkit/memory.json" ] && ok "init laid down .loopkit/memory.json" || fail "memory.json missing after init"
[ -f "$T/.loopkit/recall-triggers.txt" ] && ok "init laid down .loopkit/recall-triggers.txt" || fail "recall-triggers.txt missing"
mkdir -p "$T/bin" "$T/.mempalace/palace"
cat > "$T/bin/codebase-memory-mcp" <<'SHIM'
#!/usr/bin/env bash
# selftest shim: answers like the real CLI for the calls the adapter makes
tool="$2"; [ "$1" = "cli" ] || exit 1
case "$tool" in
  list_projects) printf '{"projects":[{"name":"shim-project","root_path":"%s","nodes":42,"edges":7}]}\n' "$SHIM_ROOT" ;;
  index_status)  printf '{"project":"shim-project","nodes":42,"edges":7,"status":"ready"}\n' ;;
  search_graph)  printf '{"total":1,"results":[{"name":"decideThing","qualified_name":"shim.decideThing","file_path":"lib/x.ts","start_line":12}],"has_more":false}\n' ;;
  trace_path)    printf '[{"name":"callerOfThing","file_path":"lib/y.ts","start_line":3}]\n' ;;
  get_code_snippet) printf '{"code":"function decideThing() { return 1 }"}\n' ;;
  detect_changes) printf '[{"name":"decideThing","file_path":"lib/x.ts"}]\n' ;;
  *) echo '{"error":"unknown tool"}'; exit 1 ;;
esac
SHIM
cat > "$T/bin/mempalace" <<'SHIM'
#!/usr/bin/env bash
# selftest shim for the mempalace CLI
args=("$@"); verb=""
for a in "${args[@]}"; do case "$a" in search|wake-up|mine|status) verb="$a"; break;; esac; done
case "$verb" in
  search)  echo "=== results ==="; echo "drawer: UNIQUE index on t(a) re-broke bug 1274 in June"; echo "drawer: one row per user is wrong: members hold one firm row plus one per client" ;;
  wake-up) echo "L0: shim palace"; echo "L1: 2 drawers" ;;
  status)  echo "MemPalace Status — 2 drawers" ;;
  mine)    echo "mined 1 file" ;;
  *) exit 1 ;;
esac
SHIM
chmod +x "$T/bin/codebase-memory-mcp" "$T/bin/mempalace"
export SHIM_ROOT="$T"
SHIMPATH="$T/bin:$PATH"
out="$(PATH="$SHIMPATH" python3 "$P/scripts/memory.py" status --no-cache --root "$T")"
grep -q 'graph=codebase-memory(42 nodes)' <<<"$out" && ok "graph adapter available through the shim (project matched by root_path)" || fail "graph status: $out"
grep -q 'memory=mempalace(2 drawers)' <<<"$out" && ok "memory adapter available through the shim" || fail "memory status: $out"
grep -q 'knowledge=DEGRADED(disabled' <<<"$out" && ok "knowledge reports disabled, not broken" || fail "knowledge status: $out"
out="$(PATH="$SHIMPATH" python3 "$P/scripts/memory.py" recall "unique index" --root "$T")"
grep -q '^ADAPTER: memory=mempalace \[available\]' <<<"$out" && grep -q 're-broke bug 1274' <<<"$out" && ok "recall returns the palace's facts with the banner" || fail "recall: $out"
out="$(PATH="$SHIMPATH" python3 "$P/scripts/memory.py" graph find decideThing --root "$T")"
grep -q 'lib/x.ts:12  shim.decideThing' <<<"$out" && ok "graph find goes through the CLI" || fail "graph find: $out"
out="$(PATH="$SHIMPATH" python3 "$P/scripts/memory.py" graph callers decideThing --root "$T")"
grep -q 'callerOfThing' <<<"$out" && ok "graph callers goes through trace_path" || fail "graph callers: $out"
out="$(PATH="$SHIMPATH" python3 "$P/scripts/memory.py" graph snippet shim.decideThing --root "$T")"
grep -q 'function decideThing' <<<"$out" && ok "graph snippet" || fail "graph snippet: $out"
out="$(PATH="$SHIMPATH" python3 "$P/scripts/memory.py" remember --title "period end is null on free to paid" --body "observed on 4 Sep; control case: paid firms unaffected" --tags billing,trap --root "$T")"
note="$(sed -n 's/^REMEMBERED: //p' <<<"$out")"
[ -n "$note" ] && [ -f "$T/$note" ] && grep -q '^valid_from:' "$T/$note" && grep -q 'tags: \[billing, trap\]' "$T/$note" && ok "remember writes a dated note ($note)" || fail "remember: $out"
nid="$(basename "$note" .md)"
out="$(PATH="$SHIMPATH" python3 "$P/scripts/memory.py" invalidate "$nid" --reason "fixed by the activation snapshot" --root "$T")"
grep -q "^valid_until:" "$T/$note" && grep -q 'INVALIDATED' "$T/$note" && ok "invalidate adds valid_until and keeps the note" || fail "invalidate: $out"
out="$(python3 "$P/scripts/memory.py" recall "period end null" --no-cache --root "$T" 2>/dev/null || python3 "$P/scripts/memory.py" recall "period end null" --root "$T")"
grep -q 'DEGRADED' <<<"$out" && grep -q 'invalidated' <<<"$out" && grep -q 'period end is null' <<<"$out" && ok "files fallback recalls the note (title, invalidation shown) when the palace is absent" || fail "files fallback: $out"
out="$(PATH="$SHIMPATH" python3 "$P/scripts/memory.py" --format concise graph find decideThing --root "$T")"
grep -q '^ADAPTER' <<<"$out" && ok "concise output carries the banner" || fail "concise banner"
out="$(bash "$P/hooks/session_start.sh")"
grep -q '^MEMORY:' <<<"$out" && ok "session start prints the MEMORY line when memory.json exists" || fail "session MEMORY line: $out"

echo "== require_recall: gate, mark, outage escape"
RSID="rc-$$-$RANDOM"
rq() {  # <want> <label> <json>  (gate mode)
  local want="$1" label="$2" json="$3"
  printf '%s' "$json" | python3 "$P/hooks/require_recall.py" gate >/dev/null 2>&1; local got=$?
  [ "$got" = "$want" ] && ok "$label (rc=$got)" || fail "$label (rc=$got, want $want)"
}
rq 2 "migration write blocked before recall" '{"session_id":"'"$RSID"'","tool_name":"Write","tool_input":{"file_path":"'"$T"'/db/migrations/V2.sql","content":"ALTER TABLE t ADD CONSTRAINT c CHECK (a > 0);"}}'
rq 2 "invariant SQL in a code file blocked before recall" '{"session_id":"'"$RSID"'","tool_name":"Write","tool_input":{"file_path":"'"$T"'/lib/schema.ts","content":"sql`CREATE UNIQUE INDEX ux ON t(a)`"}}'
rq 0 "prose about an invariant is not an invariant" '{"session_id":"'"$RSID"'","tool_name":"Write","tool_input":{"file_path":"'"$T"'/docs/notes.md","content":"we discussed the UNIQUE INDEX on t(a)"}}'
rq 0 "ordinary code write passes" '{"session_id":"'"$RSID"'","tool_name":"Write","tool_input":{"file_path":"'"$T"'/lib/a.ts","content":"export const x = 1"}}'
rq 2 "shell redirect into a migrations dir blocked" '{"session_id":"'"$RSID"'","tool_name":"Bash","tool_input":{"command":"cat > db/migrations/V3.sql <<EOF\nselect 1;\nEOF"}}'
rq 0 "shell redirect elsewhere passes" '{"session_id":"'"$RSID"'","tool_name":"Bash","tool_input":{"command":"echo hi > notes.txt"}}'
# No `\"` inside a printf FORMAT: bash printf rewrites it to `"`, which made this
# JSON invalid and the hook (correctly) ignored it — the pin failed on its own quoting.
printf '{"session_id":"%s","tool_name":"Bash","tool_input":{"command":"python3 %s/scripts/memory.py recall unique-index-t-a"}}' "$RSID" "$P" | python3 "$P/hooks/require_recall.py" mark
rq 0 "after a memory.py recall the migration write is allowed" '{"session_id":"'"$RSID"'","tool_name":"Write","tool_input":{"file_path":"'"$T"'/db/migrations/V2.sql","content":"ALTER TABLE t ADD CONSTRAINT c CHECK (a > 0);"}}'
RSID2="rc2-$$-$RANDOM"
printf '{"session_id":"%s","tool_name":"mcp__mempalace__mempalace_search","tool_input":{"query":"t"}}' "$RSID2" | python3 "$P/hooks/require_recall.py" mark
rq 0 "an MCP recall tool also opens the gate" '{"session_id":"'"$RSID2"'","tool_name":"Write","tool_input":{"file_path":"'"$T"'/db/migrations/V4.sql","content":"CREATE UNIQUE INDEX u ON t(b);"}}'
RSID3="rc3-$$-$RANDOM"
printf '{"session_id":"%s","tool_name":"Bash","tool_input":{"command":"ls -la"}}' "$RSID3" | python3 "$P/hooks/require_recall.py" mark
rq 2 "an unrelated Bash call does not count as recall" '{"session_id":"'"$RSID3"'","tool_name":"Write","tool_input":{"file_path":"'"$T"'/lib/schema.ts","content":"sql`CREATE UNIQUE INDEX ux ON t(a)`"}}'
key3="$(python3 -c 'import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])' "$RSID3")"
printf 'both memory CLIs time out since 14:10' > "${TMPDIR:-/tmp}/loopkit-recall-$key3/recall-unavailable"
rq 0 "documented outage unblocks a content-rule write" '{"session_id":"'"$RSID3"'","tool_name":"Write","tool_input":{"file_path":"'"$T"'/lib/schema.ts","content":"sql`CREATE UNIQUE INDEX ux ON t(a)`"}}'
rq 2 "documented outage never unblocks a real migration write" '{"session_id":"'"$RSID3"'","tool_name":"Write","tool_input":{"file_path":"'"$T"'/db/migrations/V5.sql","content":"select 1;"}}'
printf '{nope' | python3 "$P/hooks/require_recall.py" gate >/dev/null 2>&1; [ $? = 0 ] && ok "require_recall: junk input allows" || fail "require_recall must fail open"

echo "== governance guards the knowledge bundle when enabled"
python3 - "$T" <<'PYX'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1]) / ".loopkit" / "memory.json"
d = json.loads(p.read_text()); d["knowledge"]["enabled"] = True; p.write_text(json.dumps(d))
PYX
printf '{"tool_name":"Write","tool_input":{"file_path":"%s/knowledge/loop/x.md","content":"y"}}' "$T" | env -u GOVERNANCE_EDIT_OK python3 "$P/hooks/protect_governance.py" >/dev/null 2>&1; [ $? = 2 ] && ok "knowledge/ is guarded when enabled" || fail "knowledge guard"
printf '{"tool_name":"Write","tool_input":{"file_path":"%s/knowledge/loop/x.md","content":"y"}}' "$T" | KNOWLEDGE_EDIT_OK=1 python3 "$P/hooks/protect_governance.py" >/dev/null 2>&1; [ $? = 0 ] && ok "KNOWLEDGE_EDIT_OK=1 is the door" || fail "knowledge override"
python3 - "$T" <<'PYX'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1]) / ".loopkit" / "memory.json"
d = json.loads(p.read_text()); d["knowledge"]["enabled"] = False; p.write_text(json.dumps(d))
PYX

echo "== run-capped and the offload nudge"
out="$(cd "$T" && bash "$P/scripts/run-capped.sh" --head 3 --tail 2 -- seq 1 100)"
grep -q 'lines omitted' <<<"$out" && grep -q 'run-capped: exit 0, 100 lines' <<<"$out" && ok "run-capped keeps head+tail and writes the file" || fail "run-capped: $out"
( cd "$T" && bash "$P/scripts/run-capped.sh" -- false >/dev/null ); [ $? = 1 ] && ok "run-capped propagates the exit code" || fail "run-capped exit code"
big="$(python3 -c 'print("x"*9000)')"
err="$(printf '{"tool_name":"Bash","tool_input":{"command":"cat big.log"},"tool_response":"%s"}' "$big" | python3 "$P/hooks/offload_nudge.py" 2>&1 >/dev/null)"
grep -q 'run-capped.sh' <<<"$err" && ok "offload nudge fires past 8 KB" || fail "offload nudge: $err"
grep -q 'large_tool_output' "$T/.loopkit/metrics.jsonl" && ok "offload event counted in metrics.jsonl" || fail "metrics.jsonl"
err="$(printf '{"tool_name":"Bash","tool_input":{"command":"ls"},"tool_response":"small"}' | python3 "$P/hooks/offload_nudge.py" 2>&1 >/dev/null)"
[ -z "$err" ] && ok "offload nudge silent on small output" || fail "nudge on small output: $err"
for ev in require_recall.py offload_nudge.py; do grep -q "$ev" "$P/hooks/hooks.json" && ok "hooks.json wires $ev" || fail "hooks.json lacks $ev"; done

# --- 0.2.0-c: OKF + the knowledge layer -------------------------------------
echo "== knowledge: init (seed), verify, reindex, idempotent drain"
MEM="python3 $P/scripts/memory.py"
out="$($MEM knowledge init --root "$T" 2>&1)"
grep -q 'bundle now holds 10 concept' <<<"$out" && ok "knowledge init seeds ten concepts" || fail "init: $out"
[ -f "$T/knowledge/index.md" ] && [ -f "$T/knowledge/log.md" ] && ok "index.md and log.md generated" || fail "generated files missing"
grep -q '^knowledge/$' "$T/.prettierignore" && ok "bundle added to .prettierignore" || fail "prettierignore"
expect_rc 0 "verify: seeded bundle is conformant" $MEM knowledge verify --root "$T"
expect_rc 0 "reindex --check: index bytes are stable" $MEM knowledge reindex --check --root "$T"
log1="$(shasum "$T/knowledge/log.md")"
out="$($MEM knowledge init --root "$T" 2>&1)"
log2="$(shasum "$T/knowledge/log.md")"
[ "$log1" = "$log2" ] && grep -q 'bundle now holds 10 concept' <<<"$out" && ok "second init is a no-op (idempotency keys, convergent apply)" || fail "second init changed the log"
out="$($MEM status --no-cache --root "$T")"
grep -q 'knowledge=okf(10 concepts)' <<<"$out" && ok "status reports the bundle" || fail "status: $out"
out="$($MEM knowledge search "merge approve" --root "$T")"
grep -q 'never-merge-never-approve' <<<"$out" && ok "knowledge search finds a concept" || fail "search: $out"
out="$($MEM knowledge get /loop/the-stop-gate.md --root "$T")"
grep -q 'type: Gate' <<<"$out" && ok "knowledge get prints the concept" || fail "get: $out"
out="$($MEM recall "merge" --root "$T")"
grep -q '^CONCEPTS:' <<<"$out" && grep -q 'never-merge' <<<"$out" && ok "recall spans notes and concepts" || fail "recall+concepts: $out"

# Every probe below that expects a BLOCK runs under `env -u GOVERNANCE_EDIT_OK`.
# A suite run from a shell carrying the override otherwise reports success while
# measuring an open door — which it did on 2026-09-07, turning twelve blocked-write
# assertions green at once.
echo "== knowledge: guard, queue-source refusal, enqueue+drain a Trap"
printf '{"tool_name":"Write","tool_input":{"file_path":"%s/knowledge/loop/x.md","content":"y"}}' "$T" | env -u GOVERNANCE_EDIT_OK python3 "$P/hooks/protect_governance.py" >/dev/null 2>&1; [ $? = 2 ] && ok "hand edit of the bundle is refused after init" || fail "bundle guard"
out="$($MEM knowledge enqueue --target /rulings/bad.md --reason "cites a queue" --type Decision --title "bad" --sources inbox/needs-human.md --body "x" --root "$T" 2>&1)"; rc=$?
grep -q 'refused' <<<"$out" && [ "$rc" = 3 ] && ok "a queue as a source is refused at enqueue (rc=3)" || fail "queue source: rc=$rc $out"
out="$($MEM knowledge enqueue --target /billing/period-end-trap.md --reason "trap: period end null on free to paid" --type Trap --title "period_end is NULL after a free-to-paid activation" --tags "lane/billing,domain/billing" --sources constitution.md --body "Observed 4 Sep. Control case: paid firms unaffected." --root "$T" 2>&1)"; rc=$?
[ "$rc" = 0 ] && grep -q 'ENQUEUED rc=0' <<<"$out" && ok "enqueue a Trap with a lane tag" || fail "enqueue trap: rc=$rc $out"
expect_rc 0 "drain applies it" $MEM knowledge drain --root "$T"
[ -f "$T/knowledge/billing/period-end-trap.md" ] && grep -q 'type: Trap' "$T/knowledge/billing/period-end-trap.md" && ok "the Trap concept exists with its type" || fail "trap concept missing"
grep -q 'git-blob:' "$T/knowledge/billing/period-end-trap.md" && ok "source digest captured at apply" || fail "no digest captured"

echo "== knowledge: scoped trap injection in the tick doctrine"
out="$(printf '{"session_id":"traps-%s","prompt":"/loop work the billing lane --scope billing"}' "$RANDOM" | python3 "$P/hooks/loop_doctrine.py")"
grep -q "TRAPS recorded for lane 'billing'" <<<"$out" && grep -q 'period_end is NULL' <<<"$out" && ok "doctrine injects the lane's traps" || fail "traps: $out"
out="$(printf '{"session_id":"traps-%s","prompt":"/loop work the loopkit backlog"}' "$RANDOM" | python3 "$P/hooks/loop_doctrine.py")"
grep -q 'TRAPS recorded' <<<"$out" && fail "traps injected without a lane" || ok "no lane, no traps"
out="$(printf '{"session_id":"traps-%s","prompt":"/loop --scope auth"}' "$RANDOM" | python3 "$P/hooks/loop_doctrine.py")"
grep -q 'TRAPS recorded' <<<"$out" && fail "another lane got billing traps" || ok "another lane gets none of them"

echo "== rulings-compile: coverage"
out="$(python3 "$P/scripts/rulings-compile.py" --root "$T")"
grep -q 'RULINGS: 3/3' <<<"$out" && ok "the three seeded Invariant/Gate concepts are enforced (3/3)" || fail "coverage: $out"
$MEM knowledge enqueue --target /rulings/one-plan.md --reason "one plan per firm" --type Invariant --title "One plan per firm" --sources constitution.md --body "x" --root "$T" >/dev/null 2>&1
$MEM knowledge drain --root "$T" >/dev/null 2>&1
out="$(python3 "$P/scripts/rulings-compile.py" --root "$T")"
grep -q 'RULINGS: 3/4' <<<"$out" && grep -q 'NONE Invariant /rulings/one-plan.md' <<<"$out" && ok "an unenforced Invariant is listed (3/4)" || fail "coverage after: $out"
expect_rc 1 "--strict fails while a ruling is prose only" python3 "$P/scripts/rulings-compile.py" --root "$T" --strict
printf '#name one-plan-per-firm\n(?:\\A|[;&|]\\s*|\\n\\s*)stripe\\s+subscriptions\\s+create\\b\n' >> "$T/.loopkit/block-patterns.txt"
$MEM knowledge enqueue --target /rulings/one-plan.md --reason "now enforced" --type Invariant --title "One plan per firm" --enforced-by "block-pattern:one-plan-per-firm" --sources constitution.md --body "x" --root "$T" >/dev/null 2>&1
$MEM knowledge drain --root "$T" >/dev/null 2>&1
expect_rc 0 "--strict passes once the ruling names an existing named pattern" python3 "$P/scripts/rulings-compile.py" --root "$T" --strict
err="$(printf '{"tool_name":"Bash","tool_input":{"command":"stripe subscriptions create --customer cus_1"}}' | CLAUDE_PROJECT_DIR="$T" python3 "$P/hooks/block_dangerous.py" 2>&1 >/dev/null)"; rc=$?
[ "$rc" = 2 ] && grep -q 'ruling: one-plan-per-firm' <<<"$err" && ok "a named pattern blocks and names its ruling" || fail "named pattern: rc=$rc $err"

echo "== rulings-extract (dry run, then apply)"
printf '\n## RESOLVED 2026-09-01 — Grace-lane reactivation goes through a fresh checkout\n\nOwner ruled: reactivation from grace is a new checkout, never an un-cancel.\n' >> "$T/inbox/needs-human.md"
printf '\n## ~~Two SHALLs fire on undefined conditions~~ — RESOLVED 2026-08-31\n\nRewritten as EARS lines.\n\n## [superseded] 2026-07-23 (EOD) — Spec v3 SHIPPED: PR open for review\n\nStatus report.\n\n## Still open: the pricing anchor question\n\nNot ruled.\n' >> "$T/inbox/needs-human.md"
mkdir -p "$T/docs/adr"; printf '# ADR-001: Postgres-direct for new domains\n\n## Decision\n\nNew domains write to Postgres directly; the BI layer is read-only.\n' > "$T/docs/adr/ADR-001-postgres-direct.md"
out="$(python3 "$P/scripts/rulings-extract.py" --root "$T")"
grep -q 'Grace-lane reactivation' <<<"$out" && grep -q 'ADR-001' <<<"$out" && grep -q 'dry run' <<<"$out" && ok "extract finds the inbox ruling and the ADR, writes nothing" || fail "extract: $out"
[ -z "$(ls "$T/state/knowledge-mailbox/inbox" 2>/dev/null)" ] && ok "dry run enqueued nothing" || fail "dry run enqueued"
out="$(python3 "$P/scripts/rulings-extract.py" --root "$T" --apply)"
grep -qE 'ENQUEUED ([2-9]|[1-9][0-9])/\1' <<<"$out" && ok "apply enqueues one upsert per ruling (all of them)" || fail "apply: $out"
grep -q 'Grace-lane reactivation goes through a fresh checkout' <<<"$out" && ok "a RESOLVED heading's title survives its date's dashes" || fail "title: $out"
dry="$(python3 "$P/scripts/rulings-extract.py" --root "$T")"
grep -q 'Two SHALLs fire on undefined conditions' <<<"$dry" && ok "a struck-through heading with a trailing stamp is a ruling" || fail "struck: $dry"
grep -q 'Spec v3 SHIPPED: PR open for review' <<<"$dry" && ok "a [superseded]-tagged heading is a ruling, tag and date stripped" || fail "tagged: $dry"
grep -q 'Still open: the pricing anchor' <<<"$dry" && fail "an open heading was extracted as a ruling" || ok "an open heading is not a ruling"
expect_rc 0 "drain the rulings" $MEM knowledge drain --root "$T"
ls "$T"/knowledge/rulings/*.md | grep -q 'grace-lane' && ok "inbox ruling became a concept (no queue cited as source)" || fail "ruling concept missing"
grep -q 'resource: docs/adr/ADR-001-postgres-direct.md' "$T"/knowledge/rulings/adr-001*.md && ok "ADR ruling cites the ADR file as its source" || fail "ADR source missing"
expect_rc 0 "bundle still conformant after rulings" $MEM knowledge verify --root "$T"

echo "== ticks ledger and metrics"
[ -f "$T/state/ticks.jsonl" ] && grep -q '"event": "stage"' "$T/state/ticks.jsonl" && ok "loop-next recorded stage events" || fail "no stage events in ticks.jsonl"
grep -q '"event": "gate"' "$T/state/ticks.jsonl" && grep -q '"result": "PASS"' "$T/state/ticks.jsonl" && ok "stop gate recorded PASS and FAIL verdicts" || fail "no gate events"
grep -q '"event": "transition"' "$T/state/ticks.jsonl" && grep -q '"status": "spec-draft"' "$T/state/ticks.jsonl" && ok "triage update recorded the transition" || fail "no transition events"
out="$(python3 "$P/scripts/loop-metrics.py" --root "$T")"
grep -q '^  ticks' <<<"$out" && grep -q 'gate_pass_rate' <<<"$out" && grep -q 'n=' <<<"$out" && ok "loop-metrics prints every figure with its n=" || fail "metrics: $out"
out="$(python3 "$P/scripts/loop-metrics.py" --root "$B" 2>/dev/null || CLAUDE_PROJECT_DIR="$(mktemp -d)" python3 "$P/scripts/loop-metrics.py")"
grep -q 'n=0' <<<"$out" && ok "metrics on an empty ledger say n=0, never a number" || fail "empty metrics: $out"

# --- 0.2.0-d: profiles, fan-out, the override counter ----------------------
echo "== commerce profile"
expect_rc 0 "init --profile commerce (first run)" bash "$P/scripts/loopkit-init.sh" --project "$T" --profile commerce
expect_rc 0 "init --profile commerce (second run)" bash "$P/scripts/loopkit-init.sh" --project "$T" --profile commerce
[ "$(grep -c 'loopkit-profile:commerce:block-patterns.txt' "$T/.loopkit/block-patterns.txt")" = 1 ] && ok "profile patterns appended exactly once" || fail "profile marker count"
grep -q 'Commerce constraints (profile: commerce)' "$T/constitution.md" && ok "commerce constraints appended to constitution.md" || fail "constitution profile section"
[ -f "$T/evals/commerce/snapshot.template.json" ] && ok "snapshot template copied to evals/commerce/" || fail "snapshot template missing"
expect_rc 2 "unknown profile is refused" bash "$P/scripts/loopkit-init.sh" --project "$T" --profile nope
err="$(printf '{"tool_name":"Bash","tool_input":{"command":"stripe refunds create --amount 100 --live"}}' | CLAUDE_PROJECT_DIR="$T" python3 "$P/hooks/block_dangerous.py" 2>&1 >/dev/null)"; rc=$?
[ "$rc" = 2 ] && grep -qE 'ruling: no-(live-mode-flag|payout-refund-capture-from-shell)' <<<"$err" && ok "a live-mode refund from the shell is refused, naming its ruling" || fail "profile block: rc=$rc $err"
err="$(printf '{"tool_name":"Bash","tool_input":{"command":"echo sk_live_abcdefghijklmnop"}}' | CLAUDE_PROJECT_DIR="$T" python3 "$P/hooks/block_dangerous.py" 2>&1 >/dev/null)"; rc=$?
[ "$rc" = 2 ] && grep -q 'ruling: no-live-keys' <<<"$err" && ok "a live key literal is refused" || fail "live key: rc=$rc"
err="$(printf '{"tool_name":"Bash","tool_input":{"command":"stripe customers list --limit 3"}}' | CLAUDE_PROJECT_DIR="$T" python3 "$P/hooks/block_dangerous.py" 2>&1 >/dev/null)"; rc=$?
[ "$rc" = 0 ] && ok "a read-only test-mode call passes" || fail "read-only call blocked: $err"
printf '{"tool_name":"Write","tool_input":{"file_path":"%s/pricing/plans.json","content":"{}"}}' "$T" | env -u GOVERNANCE_EDIT_OK CLAUDE_PROJECT_DIR="$T" python3 "$P/hooks/protect_governance.py" >/dev/null 2>&1; [ $? = 2 ] && ok "pricing/ is protected under the profile" || fail "pricing guard"
expect_rc 0 "check-snapshot: the template passes" python3 "$P/scripts/check-snapshot.py" --root "$T" --strict
printf '{"id":"bad","transcript":[{"role":"user","content":"x"}],"state_before":{},"expected_state_after":{"steps":["call refund"]},"cap":1}' > "$T/evals/commerce/bad.json"
out="$(python3 "$P/scripts/check-snapshot.py" --root "$T")"
grep -q 'grades a PATH' <<<"$out" && ok "check-snapshot rejects path-graded expectations" || fail "snapshot path: $out"
expect_rc 1 "check-snapshot --strict fails on it" python3 "$P/scripts/check-snapshot.py" --root "$T" --strict
expect_rc 0 "check-skills --strict still passes with commerce-review" python3 "$P/scripts/check-skills.py" --root "$(mktemp -d)" --plugin "$P" --strict

echo "== fan-out with a stub claude"
mkdir -p "$T/briefs"
printf '# brief one\n\nObjective: list files.\n' > "$T/briefs/one.md"
printf '# brief two\n\nObjective: count lines.\n' > "$T/briefs/two.md"
cat > "$T/bin/claude" <<'STUB'
#!/usr/bin/env bash
# stub: consume the brief on stdin, echo a JSON result naming the flags it saw
brief="$(cat)"
printf '{"result":"ok","brief_bytes":%d,"args":"%s"}\n' "${#brief}" "$*"
STUB
chmod +x "$T/bin/claude"
out="$(cd "$T" && PATH="$T/bin:$PATH" bash "$P/scripts/fanout.sh" --briefs "$T/briefs" --run-id selftest --max-turns 3 --allowed-tools "Read,Grep")"
grep -q 'FANOUT: 2/2 briefs exited 0' <<<"$out" && ok "fan-out ran one claude per brief" || fail "fanout: $out"
[ -f "$T/state/fanout/selftest/one.json" ] && grep -q '"brief_bytes"' "$T/state/fanout/selftest/one.json" && ok "each brief has a JSON result" || fail "fanout results"
grep -q -- '--max-turns 3 --allowedTools Read,Grep' "$T/state/fanout/selftest/two.json" && ok "turns and tools are scoped per run" || fail "fanout flags: $(cat "$T/state/fanout/selftest/two.json")"
[ -z "$(git -C "$T" worktree list | grep fanout-selftest || true)" ] && ok "fan-out worktrees removed" || fail "worktrees left behind"
grep -q 'nothing was merged' <<<"$out" && ok "fan-out says it merged nothing" || fail "fanout merge line"

# --- criterion 33: prompt bytes per tick, against a real ceiling ------------
# The brief is the only free-text instruction a Run carries, so the bytes on a
# brief's stdin ARE that tick's prompt budget. Criterion 33 named this check
# before it existed: until 2026-09-07 the block above asserted only that
# `brief_bytes` was PRESENT, never that it was under anything, so the spec
# cited a ceiling no command applied. It is still a PROXY — a byte count
# cannot tell prose from a serialised Concept, so it catches growth and misses
# a smuggled instruction that stays under the cap — but a proxy that can fail.
BRIEF_CEILING=8192
brief_bytes_of() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["brief_bytes"])' "$1"; }
over=0
for r in "$T"/state/fanout/selftest/*.json; do
  b="$(brief_bytes_of "$r")"
  [ "$b" -le "$BRIEF_CEILING" ] || { over=$((over+1)); echo "   over: $(basename "$r") = $b bytes"; }
done
[ "$over" -eq 0 ] && ok "every brief is under the ${BRIEF_CEILING}-byte prompt ceiling (criterion 33)" || fail "$over brief(s) over the ${BRIEF_CEILING}-byte ceiling"

# The control case, measured through the SAME path. Without it the assertion
# above compares two ~40-byte briefs against 8192 and would pass however
# broken the comparison was — a gate that cannot fail is not a gate.
mkdir -p "$T/briefs-over"
python3 -c 'import sys; sys.stdout.write("# oversized brief\n\n" + "x" * 9000 + "\n")' > "$T/briefs-over/big.md"
(cd "$T" && PATH="$T/bin:$PATH" bash "$P/scripts/fanout.sh" --briefs "$T/briefs-over" --run-id ceiling --max-turns 1 --allowed-tools "Read") >/dev/null 2>&1
big="$(brief_bytes_of "$T/state/fanout/ceiling/big.json")"
[ "$big" -gt "$BRIEF_CEILING" ] && ok "the ceiling can fail: a ${big}-byte brief is measured as over ${BRIEF_CEILING}" || fail "control: oversized brief measured ${big}, not over ${BRIEF_CEILING}"

echo "== judge with a stub claude (specs/pairwise-judge-verdicts.md)"
J="$P/scripts/judge.py"; JD="$T/judge"; mkdir -p "$JD/bin"
printf 'alpha: the fix that reads the file\n' > "$JD/a.txt"
printf 'beta: the fix that streams the file\n' > "$JD/b.txt"
: > "$JD/empty.txt"
# stub: count calls, capture the prompt, answer per $JD/mode. `beta` prefers
# whichever position holds candidate B; `first` prefers position 1 (pure
# position bias); `noverdict` answers JSON with no VERDICT line; `fail` exits 1.
cat > "$JD/bin/claude" <<'STUB'
#!/usr/bin/env bash
D="$(cd "$(dirname "$0")/.." && pwd)"
prompt="$(cat)"
echo call >> "$D/calls"
printf '%s\n' "$prompt" > "$D/prompt.txt"
mode="$(cat "$D/mode")"
first="${prompt#*CANDIDATE FIRST}"; first="${first%%CANDIDATE SECOND*}"
case "$mode" in
  first)  v=FIRST; c=0.9 ;;
  beta)   case "$first" in *beta*) v=FIRST ;; *) v=SECOND ;; esac; c=0.9 ;;
  beta17) case "$first" in *beta*) v=FIRST ;; *) v=SECOND ;; esac; c=1.7 ;;
  noverdict) printf '{"type":"result","result":"I cannot decide."}\n'; exit 0 ;;
  fail) echo boom >&2; exit 1 ;;
esac
printf '{"type":"result","result":"FIRST: held - ran it\\nSECOND: not held - ran it\\nJUSTIFICATION: one reached the state\\nVERDICT: %s\\nCONFIDENCE: %s"}\n' "$v" "$c"
STUB
chmod +x "$JD/bin/claude"
judge_run() {  # <mode> <args...>; sets out/rc/calls, resets the call counter
  echo "$1" > "$JD/mode"; shift; rm -f "$JD/calls"
  out="$(PATH="$JD/bin:$PATH" python3 "$J" --root "$T" "$@" 2>&1)"; rc=$?
  calls=0; [ -f "$JD/calls" ] && calls="$(wc -l < "$JD/calls" | tr -d ' ')"
}
ticks_before="$(grep -c '"event": "judge"' "$T/state/ticks.jsonl" 2>/dev/null || true)"; ticks_before="${ticks_before:-0}"
judge_run first --criterion "WHEN x THEN y" --a "$JD/a.txt" --b "$JD/b.txt"
[ "$rc" = 0 ] && [ "$calls" = 2 ] && ok "judge calls claude exactly twice per criterion" || fail "judge calls=$calls rc=$rc: $out"
grep -q 'FINAL: TIE confidence 0.5' <<<"$out" && ok "position-biased judge → FINAL: TIE confidence 0.5" || fail "position bias: $out"
grep -q 'VERDICT (A-vs-B): A   VERDICT (B-vs-A): B' <<<"$out" && ok "both position verdicts printed in judge.md's shape" || fail "verdict shape: $out"
jl="$(grep -n '^JUSTIFICATION:' <<<"$out" | cut -d: -f1)"; vl="$(grep -n '^VERDICT (A-vs-B)' <<<"$out" | cut -d: -f1)"
[ -n "$jl" ] && [ -n "$vl" ] && [ "$jl" -lt "$vl" ] && ok "justification precedes the verdict lines" || fail "line order: $out"
grep -q '^CRITERION: WHEN x THEN y$' <<<"$out" && grep -q '^A: held' <<<"$out" && grep -q '^B: not held' <<<"$out" && ok "block carries CRITERION / A / B lines" || fail "block head: $out"
ticks_after="$(grep -c '"event": "judge"' "$T/state/ticks.jsonl" || true)"
[ "$((ticks_after - ticks_before))" = 1 ] && ok "one judge event appended to the ticks ledger" || fail "ticks judge events: $ticks_before → $ticks_after"
grep -q '"event": "judge", "final": "TIE", "confidence": "0.5"' "$T/state/ticks.jsonl" && ok "the event carries final and confidence" || fail "event fields: $(tail -1 "$T/state/ticks.jsonl")"
[ "$(grep -c 'do not prefer the longer' "$JD/prompt.txt")" = 1 ] && ok "the six rules reach the model from judge.md" || fail "rule missing from prompt"
[ "$(grep -c 'do not prefer the longer' "$J")" = 0 ] && ok "the rules are not in judge.py's source" || fail "rules embedded in judge.py"
judge_run beta --criterion "WHEN x THEN y" --a "$JD/a.txt" --b "$JD/b.txt" --judge opus --generator sonnet
grep -q 'FINAL: B confidence 0.9' <<<"$out" && ok "agreeing judge → FINAL: B with its confidence" || fail "always-B: $out"
judge_run beta17 --criterion "WHEN x THEN y" --a "$JD/a.txt" --b "$JD/b.txt"
grep -q 'FINAL: B confidence 1.0' <<<"$out" && ok "confidence 1.7 clamps to 1.0" || fail "clamp: $out"
judge_run beta --criterion "c1" --criterion "c2" --a "$JD/a.txt" --b "$JD/b.txt"
[ "$calls" = 4 ] && [ "$(grep -c '^CRITERION:' <<<"$out")" = 2 ] && ok "N=2 criteria → 2 blocks, 4 calls" || fail "N=2: calls=$calls $out"
judge_run beta --criterion c --a "$JD/a.txt" --b "$JD/b.txt" --judge opus --generator Opus
[ "$rc" = 3 ] && ! grep -q 'FINAL' <<<"$out" && [ "$calls" = 0 ] && ok "judge == generator → exit 3, no FINAL, no call" || fail "judge==generator: rc=$rc $out"
judge_run beta --criterion c --a "$JD/empty.txt" --b "$JD/b.txt"
[ "$rc" = 2 ] && ! grep -q 'FINAL' <<<"$out" && ok "empty candidate → exit 2, no FINAL" || fail "empty candidate: rc=$rc $out"
judge_run beta --criterion c --a "$JD/nope.txt" --b "$JD/b.txt"
[ "$rc" = 2 ] && ok "missing candidate path → exit 2" || fail "missing path: rc=$rc"
judge_run noverdict --criterion c --a "$JD/a.txt" --b "$JD/b.txt"
[ "$rc" = 2 ] && ! grep -q 'FINAL' <<<"$out" && ok "JSON without a verdict → exit 2, no FINAL" || fail "no verdict: rc=$rc $out"
judge_run fail --criterion c --a "$JD/a.txt" --b "$JD/b.txt"
[ "$rc" = 2 ] && ! grep -q 'FINAL' <<<"$out" && ok "claude non-zero → exit 2, no FINAL" || fail "claude fail: rc=$rc $out"
rawf="$(sed -n 's/.*raw responses in \(.*\.json\).*/\1/p' <<<"$out")"
[ -n "$rawf" ] && [ -f "$rawf" ] && grep -q '"stderr": "boom' "$rawf" && ok "raw response saved under state/judge/ for inspection" || fail "raw file: $rawf"
PY="$(command -v python3)"; mkdir -p "$JD/nobin"
out="$(PATH="$JD/nobin" "$PY" "$J" --root "$T" --criterion c --a "$JD/a.txt" --b "$JD/b.txt" 2>&1)"; rc=$?
[ "$rc" = 2 ] && ! grep -q 'FINAL' <<<"$out" && ok "claude absent from PATH → exit 2, no FINAL" || fail "claude absent: rc=$rc $out"
[ "$(ls "$T/state/judge" | wc -l | tr -d ' ')" -ge 9 ] && ok "every run left its own state/judge/<ts>-<pid>.json" || fail "judge raw files: $(ls "$T/state/judge")"
ticks_end="$(grep -c '"event": "judge"' "$T/state/ticks.jsonl" || true)"
[ "$((ticks_end - ticks_before))" = 4 ] && ok "only completed runs append a judge event (4 of 10)" || fail "ticks judge events at end: $ticks_before → $ticks_end"

echo "== stop gate: the eighth block is an override, so the seventh says so"
export LOOP_TEST_CMD="false" LOOP_LINT_CMD="true" LOOP_TYPECHECK_CMD="" LOOP_BUILD_CMD="" LOOP_TEST_JSON_CMD="false"
SG='{"session_id":"stopblocks-selftest-'$$'","hook_event_name":"Stop"}'
last=""
for i in 1 2 3 4 5 6 7; do last="$(printf '%s' "$SG" | bash "$P/hooks/stop_gate.sh" 2>&1 || true)"; done
grep -q 'OVERRIDE IMMINENT: this is consecutive block 7' <<<"$last" && ok "seventh consecutive block warns that the eighth is an override" || fail "override warning: $last"
grep -q '"event": "gate_override_imminent"' "$T/state/ticks.jsonl" && ok "override-imminent recorded in the ticks ledger" || fail "override event missing"
export LOOP_TEST_CMD="true" LOOP_TEST_JSON_CMD="true"
printf 'tests/a.test.ts :: adds\n' > "$T/state/known-test-failures.txt"
# The JSON command is a script file, so the value holds no quotes or braces
# except the placeholder: a `\"`-laden string built inside "$( … )" on bash
# 3.2 arrived with a stray `}` and looked like a substitution bug (it was not).
printf '#!/usr/bin/env bash\nprintf "{\\"testResults\\":[]}" > "$1"\n' > "$T/bin/fakereport"; chmod +x "$T/bin/fakereport"
out="$(printf '%s' "$SG" | LOOP_TEST_JSON_CMD="$T/bin/fakereport {report}" bash "$P/hooks/stop_gate.sh" 2>&1 || true)"
grep -q 'PASS: all gates passed' <<<"$out" && ok "a PASS resets the counter" || fail "reset pass: $out"
grep -q 'Running the suite once' <<<"$out" && ! grep -qE 'test-report\.json\.[A-Za-z0-9]+\}' <<<"$out" && ok "the {report} placeholder substitutes with no stray brace" || fail "placeholder: $out"
last="$(printf '%s' "$SG" | LOOP_TEST_CMD=false LOOP_TEST_JSON_CMD=false bash "$P/hooks/stop_gate.sh" 2>&1 || true)"
grep -q 'consecutive block 1)' "$T/state/progress.md" && ok "the count restarted at 1 after the PASS" || fail "counter did not reset: $(tail -2 "$T/state/progress.md")"
unset LOOP_TEST_CMD LOOP_LINT_CMD LOOP_TYPECHECK_CMD LOOP_BUILD_CMD LOOP_TEST_JSON_CMD
[ -f "$REPO/docs/research/watchlist.md" ] && grep -q 'ad-free' "$REPO/docs/research/watchlist.md" && ok "research watchlist states what is published" || fail "watchlist"

echo "== offload rewrite (PreToolUse updatedInput)"
O="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-offload.XXXXXX")"
mkdir -p "$O/.loopkit"
rw() {  # <command> → the hook's stdout (updatedInput JSON, or nothing); its stderr in $O/rw.err
  python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$1" \
    | CLAUDE_PROJECT_DIR="$O" python3 "$P/hooks/offload_rewrite.py" 2>"$O/rw.err"
}
newcmd() { python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["updatedInput"]["command"])'; }
out="$(rw 'git status')"; [ -z "$out" ] && ok "absent pattern file: git status passes through unchanged" || fail "absent file rewrote: $out"
n="$(grep -cvE '^[[:space:]]*(#|$)' "$P/templates/loopkit/offload-patterns.txt" || true)"
[ "$n" = 0 ] && ok "the shipped template is comments only" || fail "template has $n active lines"
cp "$P/templates/loopkit/offload-patterns.txt" "$O/.loopkit/offload-patterns.txt"
out="$(rw 'git diff --stat')"; [ -z "$out" ] && ok "comment-only file: git diff --stat passes through unchanged" || fail "comment-only file rewrote: $out"
printf '# noisy things\n(unclosed\n^printf\\b\n^false\\b\n^seq\\b\n' > "$O/.loopkit/offload-patterns.txt"
rt="printf '%s|%s|%s\\n' \"it's\" '\"q\"' \"\$USER\" | tr a-z A-Z"
raw="$(cd "$O" && bash -c "$rt")"
grep -qF "IT'S|\"Q\"|" <<<"$raw" && ok "round-trip fixture carries ' \" \$ and |" || fail "fixture: $raw"
out="$(rw "$rt")"; [ -n "$out" ] && ok "a matching command gets updatedInput" || fail "no updatedInput for a matching command"
new="$(newcmd <<<"$out")"
grep -qE "^bash '?[^ ]*run-capped\.sh'? --shell -- '" <<<"$new" && ok "rewrite is bash <plugin>/scripts/run-capped.sh --shell -- '<original>'" || fail "rewrite shape: $new"
wrapped="$(cd "$O" && bash -c "$new")"
[ "$(sed '$d' <<<"$wrapped")" = "$raw" ] && ok "wrapped stdout equals the raw run byte-for-byte" || fail "round-trip differs: raw=[$raw] wrapped=[$wrapped]"
grep -q 'run-capped: exit 0' <<<"$wrapped" && ok "wrapped run reports its exit line" || fail "no exit line: $wrapped"
out="$(rw "$new")"; [ -z "$out" ] && ok "an already-wrapped command is left unchanged" || fail "re-wrapped: $out"
out="$(rw 'printf %s <<EOF')"; [ -z "$out" ] && ok "a heredoc command is left unchanged" || fail "heredoc rewritten: $out"
out="$(rw $'printf a\nprintf b')"; [ -z "$out" ] && ok "a multi-line command is left unchanged" || fail "newline rewritten: $out"
out="$(rw '')"; [ -z "$out" ] && ok "an empty command is left unchanged" || fail "empty rewritten: $out"
new="$(rw 'false | true' | newcmd)"
( cd "$O" && bash -c "$new" >/dev/null 2>&1 ); rc=$?; [ "$rc" = 1 ] && ok "wrapped 'false | true' exits 1 (pipefail kept)" || fail "wrapped pipeline rc=$rc"
# PR #7 review: updatedInput REPLACES the whole input object, so every field the
# caller sent must come back or it is silently dropped from the executed call.
full="$(python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":"seq 1 5","description":"count","timeout":120000,"run_in_background":True}}))' \
  | CLAUDE_PROJECT_DIR="$O" python3 "$P/hooks/offload_rewrite.py" 2>/dev/null)"
kept="$(python3 -c 'import json,sys; u=json.load(sys.stdin)["hookSpecificOutput"]["updatedInput"]; print(",".join(sorted(u)))' <<<"$full")"
[ "$kept" = "command,description,run_in_background,timeout" ] && ok "updatedInput carries every input field, not just command" || fail "updatedInput dropped fields, kept: $kept"
bg="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["updatedInput"]["run_in_background"])' <<<"$full")"
[ "$bg" = "True" ] && ok "an unchanged field keeps its value through the rewrite" || fail "run_in_background became $bg"
# The header QUOTES the hooks reference. A grep for a phrase inside the same file
# can never fail — it passed twice on sentences that are not in the document at
# all. So diff the header's CITE block against the vendored excerpt, which is
# rewritten only by scripts/refresh-hooks-citation.sh from the fetched doc.
cite_hook="$(python3 -c '
import sys,pathlib
t=pathlib.Path(sys.argv[1]).read_text()
b=t.split("CITE-BEGIN\n",1)[1].split("CITE-END",1)[0]
print("\n".join(l.strip() for l in b.splitlines() if l.strip()))' "$P/hooks/offload_rewrite.py")"
cite_doc="$(python3 -c '
import sys,pathlib
t=pathlib.Path(sys.argv[1]).read_text()
b=t.split("## QUOTE-BEGIN\n",1)[1].split("## QUOTE-END",1)[0]
print("\n".join(l.strip() for l in b.splitlines() if l.strip()))' "$P/vendor/hooks-doc-excerpt.md")"
[ -n "$cite_hook" ] && [ "$cite_hook" = "$cite_doc" ] && ok "the header's quote is byte-identical to the vendored doc excerpt" || fail "header quote differs from vendor/hooks-doc-excerpt.md"
grep -q 'Replaces the entire input object' "$P/vendor/hooks-doc-excerpt.md" && ok "the vendored excerpt carries the replace-entire-object rule the hook relies on" || fail "vendored excerpt lost the rule"
grep -qE '^SHA-256 of the whole document at fetch time: [0-9a-f]{64}$' "$P/vendor/hooks-doc-excerpt.md" && ok "the excerpt records the source digest" || fail "excerpt has no source digest"
grep -q 'refresh-hooks-citation.sh' "$P/vendor/hooks-doc-excerpt.md" && [ -x "$P/scripts/refresh-hooks-citation.sh" ] && ok "the excerpt names an executable refresh path" || fail "no refresh path for the excerpt"
# --shell is explicit: arity must never decide
mkdir -p "$O/dir with space"; printf '#!/bin/sh\necho one-word-argv\n' > "$O/dir with space/prog"; chmod +x "$O/dir with space/prog"
one="$(cd "$O" && bash "$P/scripts/run-capped.sh" -- "$O/dir with space/prog" 2>&1)"
grep -q 'one-word-argv' <<<"$one" && ok "a one-word argv with a space runs as a program, not a shell string" || fail "one-word argv broke: $one"
sh_rc=0; ( cd "$O" && bash "$P/scripts/run-capped.sh" --shell -- 'false | true' >/dev/null 2>&1 ) || sh_rc=$?
[ "$sh_rc" = 1 ] && ok "--shell runs one string under pipefail" || fail "--shell rc=$sh_rc"
bad_rc=0; ( cd "$O" && bash "$P/scripts/run-capped.sh" --shell -- echo a b >/dev/null 2>&1 ) || bad_rc=$?
[ "$bad_rc" = 2 ] && ok "--shell with several arguments is refused, not guessed" || fail "--shell multi-arg rc=$bad_rc"
out="$(rw 'seq 1 3')"; errs="$(grep -c 'invalid pattern' "$O/rw.err" || true)"
[ -n "$out" ] && [ "$errs" = 1 ] && ok "an invalid regex line is skipped with one stderr line; a later line still matches" || fail "invalid line: out=[$out] stderr=$(cat "$O/rw.err")"
before="$( { [ -f "$O/.loopkit/metrics.jsonl" ] && grep -c offload_rewrite "$O/.loopkit/metrics.jsonl"; } || echo 0)"
out="$(rw 'seq 1 5')"
after="$(grep -c offload_rewrite "$O/.loopkit/metrics.jsonl" || true)"
[ "$after" = $((before + 1)) ] && ok "one offload_rewrite event per rewrite in metrics.jsonl ($before -> $after)" || fail "metrics: $before -> $after"
out="$(rw 'git status')"; [ -z "$out" ] && ok "control: git status still passes through with patterns loaded" || fail "control rewrote git status: $out"
python3 -c 'print("\n".join("^noisy%d\\b" % i for i in range(49)) + "\n^seq\\b")' > "$O/.loopkit/offload-patterns.txt"
timing="$(CLAUDE_PROJECT_DIR="$O" python3 -c '
import json, subprocess, sys, time
payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "seq 1 3"}})
t = time.time(); r = subprocess.run([sys.executable, sys.argv[1]], input=payload, capture_output=True, text=True)
print(int((time.time() - t) * 1000), "hit" if r.stdout else "miss")' "$P/hooks/offload_rewrite.py")"
set -- $timing
[ "$1" -lt 200 ] && [ "$2" = hit ] && ok "50-line pattern file: ${1} ms, last line matched" || fail "timing: $timing"
bd="$(grep -n 'block_dangerous.py' "$P/hooks/hooks.json" | head -1 | cut -d: -f1)"
orw="$(grep -n 'offload_rewrite.py' "$P/hooks/hooks.json" | head -1 | cut -d: -f1)"
[ -n "$orw" ] && [ "$orw" -gt "$bd" ] && ok "hooks.json wires offload_rewrite.py after block_dangerous.py" || fail "hooks.json order: block=$bd offload=$orw"
grep -q 'per-project choice' "$T/.loopkit/offload-patterns.txt" && ok "init laid down the offload-patterns template" || fail "init: offload-patterns.txt"
[ "$(grep -c 'loopkit-profile:commerce:offload-patterns.txt' "$T/.loopkit/offload-patterns.txt")" = 1 ] && ok "commerce profile appended its offload pattern exactly once" || fail "profile offload marker"
out="$(printf '{"tool_name":"Bash","tool_input":{"command":"stripe customers list --limit 3"}}' | CLAUDE_PROJECT_DIR="$T" python3 "$P/hooks/offload_rewrite.py")"
grep -q 'run-capped.sh' <<<"$out" && ok "under the commerce profile a payments listing runs capped" || fail "profile pattern: $out"
out="$(printf '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | CLAUDE_PROJECT_DIR="$T" python3 "$P/hooks/offload_rewrite.py")"
[ -z "$out" ] && ok "under the commerce profile git status is untouched" || fail "profile rewrote git status: $out"
find "$O" -delete

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

# --- M1 runtime spec pins (added 2026-09-07 after the PR #8 review) ---------
# Two classes of defect that review caught and no command would have:
#   1. a fixture whose expected output cannot be computed from the spec, so it
#      pins one implementation's printout rather than a cross-SDK contract;
#   2. a model assertion that passes because no reachable state can break it,
#      cited by a criterion as if it were evidence.
# Both pins are the gate now, so neither class can return silently.
echo "== M1 runtime spec: fixtures derive from the spec"
if [ -f "$REPO/tests/pins/fixture-derivable.py" ]; then
  fx_out="$(python3 "$REPO/tests/pins/fixture-derivable.py" 2>&1)"
  if [ $? = 0 ]; then
    ok "every conformance fixture derives from specs/loopkit-runtime.md"
  else
    fail "a conformance fixture does not derive: $(printf '%s' "$fx_out" | grep -E '^ +FAIL' | head -3 | tr '\n' ' ')"
  fi
else
  fail "tests/pins/fixture-derivable.py is missing"
fi

echo "== M1 runtime spec: every model assertion can fail"
if [ -f "$REPO/tests/pins/model-invariants-live.sh" ]; then
  mi_out="$(bash "$REPO/tests/pins/model-invariants-live.sh" 2>&1)"
  mi_rc=$?
  if [ "$mi_rc" != 0 ]; then
    fail "a model assertion is vacuous: $(printf '%s' "$mi_out" | grep -E '^ +FAIL' | head -3 | tr '\n' ' ')"
  elif printf '%s' "$mi_out" | grep -q 'skip model checkers not installed'; then
    # rc=0 here means "could not check", not "checked and fine" — so say which.
    # CI installs the engines and fails when this line appears; a laptop
    # without them gets a warning instead of a false green.
    ok "model invariants SKIPPED — engines absent, the runtime model is UNVERIFIED on this machine (CI installs them)"
  else
    ok "every assertion in loopkit-runtime.model.fizz is live under its mutation"
  fi
else
  fail "tests/pins/model-invariants-live.sh is missing"
fi

# NOTE: the pin that asserts CI installs the model checkers lives with the
# workflow change it checks, and no credential in this environment can push a
# workflow file (inbox/needs-human.md, 2026-09-07). Both are in
# docs/ci-model-engines.patch, to be applied by someone whose token carries the
# `workflow` scope. Until then the model invariants are proved on macOS only,
# and README.md says so rather than implying CI covers them.

# --- M2 core pins (added 2026-09-07 with plugins/loopkit/loopkit_core) --------
# M2 part A moved five modules into a package with no Claude Code dependency and
# lifted the stage precedence and the CONTINUE/WAIT/IDLE split out of bash. Three
# ways that goes wrong quietly, one pin each:
#   1. the package works and the old script paths silently stop working, so
#      hooks and other projects break at the next call rather than here;
#   2. `decide()` looks pure and reads the clock, which no same-second
#      double-call would ever catch;
#   3. the decision is right and the PRINTED lines changed, which every reader
#      of loop-next.sh depends on.
echo "== M2 core: the moved modules work through both doors"
csp_out="$(python3 "$REPO/tests/pins/core-shim-parity.py" 2>&1)"; csp_rc=$?
[ "$csp_rc" = 0 ] && ok "every moved module imports as loopkit_core.<name> and at its old script path" \
  || fail "core/shim parity: $(printf '%s' "$csp_out" | grep -E '^ +FAIL' | head -3 | tr '\n' ' ')"

echo "== M2 core: decide() is pure"
dp_out="$(python3 "$REPO/tests/pins/decide-pure.py" 2>&1)"; dp_rc=$?
[ "$dp_rc" = 0 ] && ok "decide() reads no clock, disk or socket, and is deterministic over 729 count vectors" \
  || fail "decide purity: $(printf '%s' "$dp_out" | grep -E '^ +FAIL' | head -3 | tr '\n' ' ')"

echo "== M2 core: decide() reproduces the fixtures' three-way split"
df_out="$(python3 "$REPO/tests/pins/decide-from-fixtures.py" 2>&1)"; df_rc=$?
[ "$df_rc" = 0 ] && ok "decide() matches every spec/fixtures case, driven from the fixture files" \
  || fail "decide vs fixtures: $(printf '%s' "$df_out" | grep -E '^ +FAIL' | head -3 | tr '\n' ' ')"

echo "== M2 core: loop-next.sh still prints what it printed before"
lnp_out="$(python3 "$REPO/tests/pins/loop-next-output-parity.py" 2>&1)"; lnp_rc=$?
[ "$lnp_rc" = 0 ] && ok "BACKLOG/BLOCKED/POLL/STAGE/TARGET/TARGETS/ACTION/NEXT/RULE byte-identical on three fixture queues" \
  || fail "loop-next output parity: $(printf '%s' "$lnp_out" | grep -E '^ +FAIL' | head -3 | tr '\n' ' ')"

# --- the repo's own gate config must source silently: an unquoted multi-word
# value (LOOP_TEST_CMD=bash tests/selftest.sh) RUNS the second word as a command
echo "== M2 io: provider and store pins run INSIDE this suite"
# Wired here because the spec's own Control case (§3) says both M1 pins run
# inside tests/selftest.sh "so neither can rot unnoticed by being a command
# nobody remembers to type". These 68 pins guard a conditional write, a runner
# lock and a credential redactor, so the rule applies with more force, not
# less. Before this, `grep provider tests/selftest.sh` returned nothing.
#
# Never read a pipeline's status here: grep exits non-zero on no match, and
# under `set -o pipefail` that would silently become the gate's verdict.
# Capture first, filter after.
prov_out="$(PYTHONPATH="$P" python3 -m loopkit_core.provider --selftest 2>&1)"; prov_rc=$?
printf '%s\n' "$prov_out" | grep '^FAIL' || true
[ "$prov_rc" = 0 ] && ok "provider.py pins ($(printf '%s' "$prov_out" | tail -1))" \
  || fail "provider.py pins: $(printf '%s' "$prov_out" | tail -1)"

# The store suite may LOUDLY SKIP its live-object-storage pins. It is asked for
# them only when LOOPKIT_S3_* is configured; either way the skip line names
# what went unverified, and that line is echoed here rather than left buried in
# captured output — a silent skip is this repo's named defect.
store_args="--selftest"
[ -n "${LOOPKIT_S3_BUCKET:-}" ] && store_args="--selftest --with-s3"
store_out="$(PYTHONPATH="$P" python3 -m loopkit_core.store $store_args 2>&1)"; store_rc=$?
printf '%s\n' "$store_out" | grep '^FAIL' || true
printf '%s\n' "$store_out" | grep '^ *SKIP' | sed 's/^ */  UNVERIFIED: /' | awk '!seen[$0]++' || true
[ "$store_rc" = 0 ] && ok "store.py pins ($(printf '%s' "$store_out" | grep -c '^PASS' || true) PASS)" \
  || fail "store.py pins: $(printf '%s' "$store_out" | grep '^FAIL' | head -1)"

# And those pins must be able to FAIL. Each mutation reintroduces, one at a
# time, one defect a hostile review of this branch found — including MUT-R1,
# which was GREEN across all sixteen of the original pins, and MUT-S3, which
# turns the store's only redaction function into `return text` and was GREEN
# across all twenty-seven store pins until the third review.
#
# The COUNT deliberately does not appear in this label. It said "six" while
# the script ran fourteen, which is exactly how a hardcoded number in a
# message becomes a lie nobody notices; the script's own last line is the
# only count printed.
m2red_out="$(python3 "$REPO/tests/pins/m2-prove-red.py" "$REPO" 2>&1)"; m2red_rc=$?
printf '%s\n' "$m2red_out" | grep 'NOT RED' || true
[ "$m2red_rc" = 0 ] && ok "every M2 mutation is provably catchable ($(printf '%s' "$m2red_out" | tail -1))" \
  || fail "M2 mutations: $(printf '%s' "$m2red_out" | tail -1)"

echo "== dogfood: .loopkit/config.env sources clean"
cfg_err="$( ( set -a; . "$REPO/.loopkit/config.env"; set +a ) 2>&1 >/dev/null )"
if [ -z "$cfg_err" ]; then ok "config.env sources with no stderr"; else fail "config.env sourcing printed: $cfg_err"; fi

# A release that does not bump the manifests reaches nobody: Claude Code caches
# a plugin per version string, so the old cache directory keeps being used.
# release.sh must refuse that, and this proves it refuses rather than warns.
echo "== release.sh refuses a version the manifests do not carry"
rel_out="$(cd "$REPO" && bash tools/release.sh 0.0.0-nonexistent --dry-run 2>&1 || true)"
case "$rel_out" in
  *"no '## 0.0.0-nonexistent' section in CHANGELOG.md"*) ok "release.sh stops before tagging an unwritten version" ;;
  *"says version"*) ok "release.sh refuses a manifest/version mismatch" ;;
  *) fail "release.sh did not refuse 0.0.0-nonexistent: $rel_out" ;;
esac
grep -q 'bump the manifest or nobody receives this release' "$REPO/tools/release.sh" && ok "release.sh carries the manifest-version guard" || fail "release.sh lost the manifest-version guard"
grep -q 'Updating LoopKit in a project that uses it' "$REPO/docs/GETTING-STARTED.md" && ok "the guide documents how consumers receive an update" || fail "no updating section in GETTING-STARTED.md"

# Every check must run BEFORE the summary. Two blocks added on 2026-09-07 landed
# below `exit` and printed after the verdict, so their fails could never count —
# the repo's own "a gate that cannot fail" class, committed into the gate itself.
# This check is structural: nothing that records a result may sit after the
# summary line.
echo "== the suite has no checks after its own verdict"
after="$(awk '/^\[ "\$fails" = 0 \] && echo "ALL PASS"/{f=1} f' "$0" | grep -cE '^[[:space:]]*(ok|fail)[[:space:]]' || true)"
[ "${after:-0}" = 0 ] && ok "no ok/fail/section call sits below the summary" || fail "$after check line(s) run after the verdict and cannot fail the suite"


echo "== protect_governance: blocks shell writes, never blocks reads"
gov_out="$(bash "$REPO/tests/pins/governance-shell-cases.sh" "$P/hooks/protect_governance.py" 2>&1)"; gov_rc=$?
printf '%s\n' "$gov_out" | grep -E '^  (LEAK|FALSE BLOCK|OVERRIDE BROKEN)' || true
[ "$gov_rc" = 0 ] && ok "every write shape blocked, every read shape allowed, override reachable inline" \
  || fail "governance shell cases: $(printf '%s' "$gov_out" | tail -1)"
# And the cases must be able to FAIL. Each half of the guard is broken in turn
# and the pin must say so — the first version of the override case passed
# whatever the hook did, because its probe never hit a protected path.
red_out="$(bash "$REPO/tests/pins/governance-prove-red.sh" "$REPO" 2>&1)"
red_n="$(printf '%s' "$red_out" | grep -c 'RED, as required' || true)"
notred="$(printf '%s' "$red_out" | grep -c 'NOT RED' || true)"
[ "$red_n" = 3 ] && [ "$notred" = 0 ] && ok "all three halves of the governance guard are provably catchable" \
  || fail "governance mutations: $red_n/3 red, $notred not red"

echo
[ "$fails" = 0 ] && echo "ALL PASS" || echo "$fails FAILURE(S)"
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
