#!/usr/bin/env bash
# stop-review-loop.sh — the Stop review blocks at most once, lets a waiting
# session pause, takes the main session's evidence, and judges only this turn.
#
# Spec draft: state/2026-10-15-spec-draft-stop-review-loop.md (criteria 1-10).
#
# The case this is built on: an agent-type Stop hook blocked a session that was
# correctly waiting on background agents more than 30 times, until Claude Code
# overrode it ("A hook blocked the turn from ending 9 consecutive times"). The
# hook never read `stop_hook_active` or `background_tasks`, graded untracked
# files that predated the turn, and could never pass when its own Bash was
# denied. Every case below drives hooks/stop_review.py with a real Stop payload
# on stdin, in a scratch git repo — never in the tracked tree.
#
# Each case prints "  ok   <label>" or "  FAIL <label>". The labels are what
# tests/pins/stop-review-prove-red.sh greps for, so keep them stable.
#
# usage: stop-review-loop.sh <worktree-root>

set -uo pipefail
W="${1:?usage: stop-review-loop.sh <worktree-root>}"
W="$(cd "$W" 2>/dev/null && pwd)" || { echo "no such worktree root: $1"; exit 2; }
P="$W/plugins/loopkit"
SR="$P/hooks/stop_review.py"
HOOKS_JSON="$P/hooks/hooks.json"

SCR="$(mktemp -d "${TMPDIR:-/tmp}/stop-review-pin.XXXXXX")"
trap 'rm -rf "$SCR" 2>/dev/null || true' EXIT
# Turn records live under TMPDIR; give this run its own, so a real session's
# records are never read and nothing is left behind.
export TMPDIR="$SCR/tmp"; mkdir -p "$TMPDIR"
unset CLAUDE_PROJECT_DIR LOOP_STOP_REVIEW_CMD LOOP_CODE_GLOBS LOOPKIT_EVIDENCE_MAX_AGE \
      LOOP_STOP_REVIEW_JUDGE LOOPKIT_STOP_REVIEW_NESTED

# THE JUDGE IS A STUB. The default judge is `claude -p`; no case here may call a
# real model. The stub records its argv, its environment and the prompt it was
# handed, counts its calls, and answers with $STUB_VERDICT (default: REJECT).
STUB_DIR="$SCR/judge"; mkdir -p "$STUB_DIR/calls"
cat > "$STUB_DIR/claude" <<'STUB'
#!/usr/bin/env bash
d="$(dirname "$0")/calls"; n=$(( $(ls "$d" | grep -c '\.argv$') + 1 ))
printf '%s\n' "$@" > "$d/$n.argv"
cat > "$d/$n.prompt"
printf 'NESTED=%s\nFILES=%s\n' "${LOOPKIT_STOP_REVIEW_NESTED:-}" "${LOOPKIT_TURN_CODE_FILES:-}" > "$d/$n.env"
case "${STUB_VERDICT:-reject}" in
  pass)    echo "Read the files. Criteria hold."; echo "VERDICT: PASS" ;;
  garbage) echo "I could not decide." ;;
  crash)   echo "boom" >&2; exit 3 ;;
  *)       echo "Criterion 2 has no test."; echo "VERDICT: REJECT stub judge: criterion 2 has no test" ;;
esac
STUB
chmod +x "$STUB_DIR/claude"
export LOOPKIT_JUDGE_CLAUDE="$STUB_DIR/claude"
judge_calls() { ls "$STUB_DIR/calls" | grep -c '\.argv$' || true; }

fails=0
ok()   { echo "  ok   $1"; }
bad()  { echo "  FAIL $1"; fails=$((fails+1)); }

mkrepo() {  # -> prints the path of a fresh repo with one committed code file
  # mktemp, not a counter: this runs inside $( ), so a counter never advances
  # in the caller and every case would share one repo (it did, once).
  local r; r="$(mktemp -d "$SCR/repo.XXXXXX")"
  mkdir -p "$r/.loopkit"
  (
    cd "$r" && git init -q . \
      && printf 'print(1)\n' > app.py && printf '# notes\n' > notes.md \
      && git add app.py notes.md \
      && git -c user.email=t@t -c user.name=t -c commit.gpgsign=false commit -q -m init
  ) >/dev/null 2>&1
  printf '%s' "$(cd "$r" && pwd -P)"
}

commit_all() {  # <repo> <msg> <paths...>
  local r="$1" m="$2"; shift 2
  (cd "$r" && git add -- "$@" && git -c user.email=t@t -c user.name=t -c commit.gpgsign=false commit -q -m "$m") >/dev/null 2>&1
}

payload() {  # <session> <cwd> <active true|false> <last message> <tasks json>
  python3 - "$@" <<'PY'
import json, sys
s, cwd, active, msg, tasks = sys.argv[1:6]
print(json.dumps({
    "session_id": s, "cwd": cwd, "hook_event_name": "Stop",
    "stop_hook_active": active == "true",
    "last_assistant_message": msg,
    "background_tasks": json.loads(tasks),
    "session_crons": [],
}))
PY
}

turn_start() {  # <session> <cwd>  — what UserPromptSubmit does at the start of a turn
  local out
  out="$(printf '{"session_id":"%s","cwd":"%s","hook_event_name":"UserPromptSubmit","prompt":"go"}' "$1" "$2" \
    | (cd "$2" && python3 "$SR" turn-start) 2>/dev/null)"
  TURN_OUT="$out"
}

# Runs the Stop hook; sets OUT, RC and VERDICT (ALLOW | BLOCK | JUNK).
stop() {  # <repo> <payload json>
  OUT="$(printf '%s' "$2" | (cd "$1" && python3 "$SR") 2>&1)"; RC=$?
  VERDICT="$(printf '%s' "$OUT" | python3 -c '
import json, sys
s = sys.stdin.read().strip()
if not s:
    print("ALLOW"); sys.exit()
try:
    d = json.loads(s)
except Exception:
    print("JUNK"); sys.exit()
print("BLOCK" if isinstance(d, dict) and d.get("decision") == "block" else "ALLOW")')"
  [ "$RC" = 0 ] || VERDICT="JUNK"
}

expect() {  # <ALLOW|BLOCK> <label> [substring the output must carry]
  local want="$1" label="$2" sub="${3:-}"
  if [ "$VERDICT" = "$want" ] && { [ -z "$sub" ] || printf '%s' "$OUT" | grep -qF -- "$sub"; }; then
    ok "$label"
  else
    bad "$label (verdict=$VERDICT rc=$RC want $want${sub:+ with '$sub'}) :: $(printf '%s' "$OUT" | tr '\n' ' ' | cut -c1-220)"
  fi
}

NOTASKS='[]'
RUNNING='[{"id":"task-7","type":"subagent","status":"running","description":"review","agent_type":"reviewer"}]'
FINISHED='[{"id":"task-7","type":"subagent","status":"completed","description":"review"}]'

echo "== C1 stop_hook_active: the second stop in a chain is never re-judged"
R="$(mkrepo)"; S="c1-$$"
turn_start "$S" "$R"
printf 'print(2)\n' > "$R/new_code.py"
stop "$R" "$(payload "$S" "$R" true "still working" "$NOTASKS")"
calls_before="$(judge_calls)"
expect ALLOW "C1 stop_hook_active true allows despite unfinished code" "does not block again"
stop "$R" "$(payload "$S" "$R" false "still working" "$NOTASKS")"
expect BLOCK "C1 control: same tree, stop_hook_active false, is refused" "new_code.py"

echo "== J the model judge is on by default, and blocks at most once per chain"
R="$(mkrepo)"; S="j-$$"; mkdir -p "$R/state"
turn_start "$S" "$R"
printf 'def half():\n    pass\n' > "$R/half.py"
calls_before="$(judge_calls)"
stop "$R" "$(payload "$S" "$R" false "Done." "$NOTASKS")"
expect BLOCK "J1 first stop with unfinished work: the judge runs and blocks once" "criterion 2 has no test"
[ "$(judge_calls)" -gt "$calls_before" ] && ok "J1 the judge actually ran on the first stop" || bad "J1 the judge actually ran on the first stop"
last="$STUB_DIR/calls/$(judge_calls)"
if grep -qx -- '--disallowedTools' "$last.argv" && grep -qx 'Bash' "$last.argv" \
   && ! sed -n '/^--allowedTools$/,/^--/p' "$last.argv" | grep -qx 'Bash' \
   && grep -qx 'disableAllHooks' <(grep -o 'disableAllHooks' "$last.argv"); then
  ok "J1 the judge has no Bash and runs with hooks off"
else
  bad "J1 the judge has no Bash and runs with hooks off :: $(tr '\n' ' ' < "$last.argv")"
fi
grep -q 'half.py' "$last.prompt" && grep -q 'turn-evidence.json' "$last.prompt" && grep -qx 'NESTED=1' "$last.env" \
  && ok "J1 the judge is handed the hook's file list and the evidence file, and runs nested" \
  || bad "J1 the judge is handed the hook's file list and the evidence file, and runs nested"
calls_before="$(judge_calls)"
stop "$R" "$(payload "$S" "$R" true "Still not done." "$NOTASKS")"
expect ALLOW "J2 a second firing with stop_hook_active never blocks, whatever the judge says" "recorded, not enforced"
[ "$(judge_calls)" -gt "$calls_before" ] && grep -q '"firing": "second"' "$R/state/ticks.jsonl" \
  && grep -q '"result": "REJECT"' "$R/state/ticks.jsonl" \
  && ok "J2 the second verdict is recorded in the ticks ledger" \
  || bad "J2 the second verdict is recorded in the ticks ledger"
calls_before="$(judge_calls)"
stop "$R" "$(payload "$S" "$R" false "Reviewer dispatched. LOOPKIT:WAITING" "$RUNNING")"
expect ALLOW "J3 waiting with background work allows, and the judge is not run" "task-7"
[ "$(judge_calls)" = "$calls_before" ] && ok "J3 no judge call while waiting" || bad "J3 no judge call while waiting"
STUB_VERDICT=pass stop "$R" "$(payload "$S" "$R" false "Done." "$NOTASKS")"
expect ALLOW "J4 a PASS verdict allows the first stop" "PASSED"
STUB_VERDICT=garbage stop "$R" "$(payload "$S" "$R" false "Done." "$NOTASKS")"
expect BLOCK "J5 no VERDICT line blocks once and asks for the reviewer" "loopkit:reviewer"
LOOPKIT_JUDGE_CLAUDE="$SCR/no-such-claude" stop "$R" "$(payload "$S" "$R" false "Done." "$NOTASKS")"
expect BLOCK "J5 no judge CLI blocks once and says why" "could not run"
LOOP_STOP_REVIEW_JUDGE=off stop "$R" "$(payload "$S" "$R" false "Done." "$NOTASKS")"
expect BLOCK "J5 judge switched off falls back to asking for the reviewer" "switched off"
calls_before="$(judge_calls)"
OUT="$(printf '%s' "$(payload "$S" "$R" false "Done." "$NOTASKS")" | (cd "$R" && LOOPKIT_STOP_REVIEW_NESTED=1 python3 "$SR") 2>&1)"; RC=$?
[ "$RC" = 0 ] && [ -z "$OUT" ] && [ "$(judge_calls)" = "$calls_before" ] \
  && ok "J6 inside the judge's own session the stop review stands down (no recursion)" \
  || bad "J6 inside the judge's own session the stop review stands down (no recursion) :: rc=$RC $OUT"
OUT="$(cd "$R" && LOOPKIT_STOP_REVIEW_NESTED=1 LOOP_TEST_CMD=false LOOP_LINT_CMD=false bash "$P/hooks/stop_gate.sh" </dev/null 2>&1)"; RC=$?
[ "$RC" = 0 ] && printf '%s' "$OUT" | grep -q "judge session" \
  && ok "J6 inside the judge's own session the test gate stands down too" \
  || bad "J6 inside the judge's own session the test gate stands down too :: rc=$RC $(printf '%s' "$OUT" | tail -1)"

echo "== C2 waiting on background work is an explicit signal, not prose"
R="$(mkrepo)"; S="c2-$$"
turn_start "$S" "$R"
printf 'print(3)\n' > "$R/half_done.py"
stop "$R" "$(payload "$S" "$R" false "Dispatched the reviewer. LOOPKIT:WAITING" "$RUNNING")"
expect ALLOW "C2 running background task plus LOOPKIT:WAITING allows" "task-7"
stop "$R" "$(payload "$S" "$R" false "I am waiting on the reviewer" "$RUNNING")"
expect BLOCK "C2 running task but no LOOPKIT:WAITING marker is refused" "LOOPKIT:WAITING"
stop "$R" "$(payload "$S" "$R" false "LOOPKIT:WAITING" "$NOTASKS")"
expect BLOCK "C2 LOOPKIT:WAITING with no task in flight is refused"
stop "$R" "$(payload "$S" "$R" false "LOOPKIT:WAITING" "$FINISHED")"
expect BLOCK "C2 LOOPKIT:WAITING with only finished tasks is refused"
stop "$R" "$(python3 -c 'import json,sys; print(json.dumps({"session_id":sys.argv[1],"cwd":sys.argv[2],"hook_event_name":"Stop","stop_hook_active":False,"last_assistant_message":"LOOPKIT:WAITING"}))' "$S" "$R")"
expect BLOCK "C2 a payload with no background_tasks field gets no waiting pass"

echo "== C3 scope: only this turn's changes are judged"
R="$(mkrepo)"; S="c3-$$"
printf 'x = 1\n' > "$R/other_work.py"                 # untracked, predates the turn
printf 'print("dirty before")\n' > "$R/app.py"         # tracked, dirty before the turn
(cd "$R" && git -c user.email=t@t -c user.name=t worktree add -q -b side nested-wt) >/dev/null 2>&1
printf 'y = 2\n' > "$R/nested-wt/inside_other_worktree.py"
turn_start "$S" "$R"
printf 'more notes\n' >> "$R/notes.md"
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect ALLOW "C3 pre-existing untracked code, a dirty file and a nested worktree are not this turn's"
printf 'print("changed in turn")\n' > "$R/app.py"
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect BLOCK "C3 a pre-dirty file changed again during the turn counts" "app.py"
printf 'print("dirty before")\n' > "$R/app.py"
printf 'z = 3\n' > "$R/committed_in_turn.py"; commit_all "$R" "turn work" committed_in_turn.py
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect BLOCK "C3 a code file committed during the turn counts" "committed_in_turn.py"
if printf '%s' "$OUT" | grep -qE 'other_work\.py|inside_other_worktree\.py'; then
  bad "C3 the refusal names files that are not this turn's"
else
  ok "C3 the refusal names only this turn's files"
fi

echo "== C4 a turn that changed no code passes"
R="$(mkrepo)"; S="c4-$$"
turn_start "$S" "$R"
[ -z "$TURN_OUT" ] && ok "C4 turn-start prints nothing (UserPromptSubmit stdout reaches the model)" \
  || bad "C4 turn-start printed: $TURN_OUT"
printf 'only prose\n' >> "$R/notes.md"
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect ALLOW "C4 a prose-only turn is allowed"

echo "== C5 main-session evidence file (no turn record: scope would be the whole tree)"
R="$(mkrepo)"; S="c5-$$"                       # no turn_start: the record is missing
printf 'x = 1\n' > "$R/someone_elses.py"       # untracked code that is not this session's
printf 'edited\n' >> "$R/notes.md"
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect BLOCK "C5 control: no turn record, untracked code, no evidence is refused" "turn-evidence.json"
(cd "$R" && python3 "$SR" write-evidence --changed-file notes.md) >/dev/null 2>&1
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect ALLOW "C5 a valid evidence file from write-evidence is accepted" "evidence"
HEAD_NOW="$(git -C "$R" rev-parse HEAD)"
python3 - "$R/.loopkit/turn-evidence.json" "$HEAD_NOW" <<'PY'
import json, sys, time
json.dump({"version": 1, "kind": "no-code-change", "head": sys.argv[2],
           "written_at": time.time(), "command": "git status --porcelain --untracked-files=all",
           "changed_files": ["notes.md"]}, open(sys.argv[1], "w"))
PY
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect ALLOW "C5 a hand-written evidence file in the documented schema is accepted"

echo "== C6 a stale evidence file is refused"
python3 - "$R/.loopkit/turn-evidence.json" <<'PY'
import json, sys, time
json.dump({"version": 1, "kind": "no-code-change", "head": "0" * 40,
           "written_at": time.time(), "command": "git status --porcelain",
           "changed_files": ["notes.md"]}, open(sys.argv[1], "w"))
PY
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect BLOCK "C6 evidence recorded at another HEAD is refused" "HEAD"
python3 - "$R/.loopkit/turn-evidence.json" "$HEAD_NOW" <<'PY'
import json, sys, time
json.dump({"version": 1, "kind": "no-code-change", "head": sys.argv[2],
           "written_at": time.time() - 86400, "command": "git status --porcelain",
           "changed_files": ["notes.md"]}, open(sys.argv[1], "w"))
PY
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect BLOCK "C6 evidence written a day ago is refused" "stale"
python3 - "$R/.loopkit/turn-evidence.json" "$HEAD_NOW" <<'PY'
import json, sys, time
json.dump({"version": 1, "kind": "no-code-change", "head": sys.argv[2],
           "written_at": time.time(), "command": "git status --porcelain",
           "changed_files": ["notes.md", "sneaky.py"]}, open(sys.argv[1], "w"))
PY
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect BLOCK "C6 evidence that lists a code file is refused" "sneaky.py"
printf 'not json' > "$R/.loopkit/turn-evidence.json"
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect BLOCK "C6 a malformed evidence file is refused"
(cd "$R" && python3 "$SR" write-evidence) >/dev/null 2>&1; we_rc=$?
[ "$we_rc" != 0 ] && ok "C6 write-evidence refuses when the tree's own changes include code" \
  || bad "C6 write-evidence wrote a no-code claim over a dirty code file"

echo "== C7 a measurement beats a claim"
R="$(mkrepo)"; S="c7-$$"
turn_start "$S" "$R"
printf 'print(9)\n' > "$R/real_change.py"
(cd "$R" && python3 "$SR" write-evidence --changed-file notes.md) >/dev/null 2>&1
stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect BLOCK "C7 with a turn record, evidence cannot override code git sees changed" "real_change.py"

echo "== C8 the control: no background work, real unfinished code, no evidence"
R="$(mkrepo)"; S="c8-$$"
turn_start "$S" "$R"
printf 'def todo():\n    raise NotImplementedError\n' > "$R/unfinished.py"
stop "$R" "$(payload "$S" "$R" false "All done!" "$NOTASKS")"
expect BLOCK "C8 control: unfinished code with no background work is refused" "REJECTED"

echo "== C9 LOOP_STOP_REVIEW_CMD: an in-hook judge the project configures"
LOOP_STOP_REVIEW_CMD='test -n "$LOOPKIT_TURN_CODE_FILES" && printf "%s" "$LOOPKIT_TURN_CODE_FILES" | grep -q unfinished.py' \
  stop "$R" "$(payload "$S" "$R" false "All done!" "$NOTASKS")"
expect ALLOW "C9 a passing review command allows, and it was told the turn's files"
LOOP_STOP_REVIEW_CMD='echo "REJECT: criterion 3 has no test"; exit 1' \
  stop "$R" "$(payload "$S" "$R" false "All done!" "$NOTASKS")"
expect BLOCK "C9 a failing review command blocks with its output" "criterion 3 has no test"

echo "== C11 the checker needs no Bash: the verdict comes from the hook's own git and the evidence file"
# The old agent hook's checker reported "Bash denied (don't-ask mode)" on every
# turn, and permission allow rules did not reach it. Here the hook runs with a
# PATH holding ONLY git and python3 — no bash, no sh — so any step that shells
# out fails. The evidence verdicts must still come out right.
NB="$SCR/nobash-bin"; mkdir -p "$NB"
ln -s "$(command -v git)" "$NB/git"; ln -s "$(command -v python3)" "$NB/python3"
R="$(mkrepo)"; S="c11-$$"
printf 'x = 1\n' > "$R/someone_elses.py"
printf 'edited\n' >> "$R/notes.md"
(cd "$R" && python3 "$SR" write-evidence --changed-file notes.md) >/dev/null 2>&1
nobash_stop() {  # like stop(), with no shell on PATH
  OUT="$(printf '%s' "$2" | (cd "$1" && env -i HOME="$HOME" TMPDIR="$TMPDIR" PATH="$NB" "$NB/python3" "$SR") 2>&1)"; RC=$?
  VERDICT="$(printf '%s' "$OUT" | python3 -c '
import json, sys
s = sys.stdin.read().strip()
if not s:
    print("ALLOW"); sys.exit()
try:
    d = json.loads(s)
except Exception:
    print("JUNK"); sys.exit()
print("BLOCK" if isinstance(d, dict) and d.get("decision") == "block" else "ALLOW")')"
  [ "$RC" = 0 ] || VERDICT="JUNK"
}
nobash_stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect ALLOW "C11 with no bash on PATH, a valid evidence file is accepted" "evidence accepted"
python3 - "$R/.loopkit/turn-evidence.json" <<'PY'
import json, sys, time
json.dump({"version": 1, "kind": "no-code-change", "head": "0" * 40,
           "written_at": time.time(), "command": "git status --porcelain",
           "changed_files": ["notes.md"]}, open(sys.argv[1], "w"))
PY
nobash_stop "$R" "$(payload "$S" "$R" false "done" "$NOTASKS")"
expect BLOCK "C11 with no bash on PATH, stale evidence is still refused" "someone_elses.py"

echo "== C10 fail-open on junk, and the wiring"
OUT="$(printf 'not json at all' | (cd "$SCR" && python3 "$SR") 2>&1)"; RC=$?
if [ "$RC" = 0 ] && ! printf '%s' "$OUT" | grep -q '"decision": *"block"'; then
  ok "C10 junk stdin allows (fail-open) with exit 0"
else
  bad "C10 junk stdin (rc=$RC): $OUT"
fi
if python3 - "$HOOKS_JSON" <<'PY'
import json, sys
h = json.load(open(sys.argv[1]))["hooks"]
stop = [x for e in h.get("Stop", []) for x in e.get("hooks", [])]
ups = [x for e in h.get("UserPromptSubmit", []) for x in e.get("hooks", [])]
assert not any(x.get("type") in ("agent", "prompt") for x in stop), "a model-judged Stop hook is still wired"
assert any("stop_review.py" in x.get("command", "") and x.get("type") == "command" for x in stop), "stop_review.py not wired on Stop"
assert any("stop_review.py" in x.get("command", "") and "turn-start" in x.get("command", "") for x in ups), "turn-start not wired on UserPromptSubmit"
PY
then ok "C10 Stop is decided by a command hook and turn-start is wired"; else bad "C10 Stop is decided by a command hook and turn-start is wired"; fi

echo
[ "$fails" = 0 ] && echo "stop-review-loop: ALL PASS" || echo "stop-review-loop: $fails FAILURE(S)"
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
