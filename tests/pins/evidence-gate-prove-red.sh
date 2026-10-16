#!/usr/bin/env bash
# evidence-gate-prove-red.sh — every half of the evidence gate must be able to fail.
#
# tests/pins/evidence-gate.py asserts the gate's behaviour. A pin that passes
# against a gate that no longer checks anything is the "check that cannot fail"
# defect this whole repo keeps meeting, so each mutation below breaks ONE half
# of the gate — in a scratch copy of the plugin — and requires the pin to go red
# on that half's own line. A mutation whose target text has moved is reported
# STALE, not "uncatchable": the two have opposite fixes.
#
# Nothing under <worktree-root> is written. Each mutation gets its own copy of
# plugins/ (the pin takes the root to test as its argument), and the tracked
# files the mutations target are checksummed before and after.
#
# usage: evidence-gate-prove-red.sh <worktree-root>
# prints one line per mutation: "RED, as required — …", "NOT RED …" or "STALE …".

set -uo pipefail
W="${1:?usage: evidence-gate-prove-red.sh <worktree-root>}"
W="$(cd "$W" 2>/dev/null && pwd)" || { echo "no such worktree root: $1"; exit 2; }
PIN="$W/tests/pins/evidence-gate.py"
EV="plugins/loopkit/loopkit_core/evidence.py"
TS="plugins/loopkit/loopkit_core/triage_state.py"
SNAP="plugins/loopkit/scripts/source-snapshot.py"

# An outer loop's environment must not steer the gate under test.
for v in $(env | sed -n 's/^\(LOOP_[A-Za-z0-9_]*\)=.*/\1/p; s/^\(LOOPKIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done
unset CLAUDE_PROJECT_DIR
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false

sums() { cksum "$W/$EV" "$W/$TS" "$W/$SNAP" "$PIN"; }
BEFORE="$(sums)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/evidence-gate-prove-red.XXXXXX")"
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK" 2>/dev/null || true' EXIT

# id | file | expected FAIL line (fixed string) | old text | new text
# Fields are separated by a line holding only "@@". Kept as data so a reader
# sees every mutation and its required red line side by side.
MUTATIONS="$WORK/mutations.txt"
cat > "$MUTATIONS" <<'EOF'
MIN-ONE
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E3] REFUSED: one source
@@
MIN_INDEPENDENT = 2
@@
MIN_INDEPENDENT = 1
@@@@
COUNT-ROWS-NOT-SYSTEMS
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E4] REFUSED: two reads of the SAME system
@@
        if counts and key not in systems:
            systems.append(key)
@@
        if counts:
            systems.append(key)
@@@@
DERIVED-COUNTS
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E5] REFUSED: identity plus a derived value is ONE system
@@
        counts = spec.get("counts", True) is not False
@@
        counts = True
@@@@
MISSING-SOURCES-PASSES
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E1] REFUSED: no `## Sources` section at all
@@
    problems += check_sources(src[0] if src else None, classes)[0]
@@
    problems += check_sources(src[0], classes)[0] if src else []
@@@@
EMPTY-SECTION-PASSES
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E2] REFUSED: an EMPTY `## Sources` section
@@
    rows = source_rows(body)
@@
    rows = source_rows(body)
    if not rows and live_none is None and not problems:
        return [], []
@@@@
LIVE-NONE-WITHOUT-REASON
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E2] REFUSED: `Live state: none` with no reason
@@
            if has_substance(m.group(1)):
                live_none = m.group(1).strip()
@@
            if True:
                live_none = (m.group(1) or "").strip()
@@@@
LIVE-NONE-IGNORED
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E7] ALLOWED: `Live state: none — <why>` with two non-live systems
@@
    if live_none is None and not live:
@@
    if not live:
@@@@
LIVE-NONE-WAIVES-TWO-SYSTEMS
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E7] REFUSED: `Live state: none — <why>` with NO reads
@@
    if len(systems) < MIN_INDEPENDENT:
@@
    if live_none is None and len(systems) < MIN_INDEPENDENT:
@@@@
LIVE-READ-NOT-REQUIRED
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E23] REFUSED: code + memory on a LIVE-state finding
@@
    if live_none is None and not live:
@@
    if False:
@@@@
DERIVED-COUNTS-AS-LIVE
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E23] REFUSED: a derived value never satisfies the live requirement
@@
        if counts and spec.get("live") is True and key not in live:
@@
        if (spec.get("live") is True or not counts) and key not in live:
@@@@
UNKNOWN-CLASS-IGNORED
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E6] REFUSED: a class the project never configured
@@
        if cls not in classes:
            problems.append(
@@
        if cls not in classes:
            continue
            problems.append(
@@@@
PLACEHOLDER-COUNTS
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E6] REFUSED: the snapshot's placeholders
@@
    bare = _PLACEHOLDER.sub("", text)
@@
    bare = text
@@@@
COMMENTS-COUNT
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E6] REFUSED: rows inside an HTML comment
@@
    text = _COMMENT.sub("", text)
@@
    text = text
@@@@
UNEXPLAINED-PASSES
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E9] REFUSED: an observation that does not fit
@@
        if not any(has_substance(r) for r in reasons):
@@
        if False:
@@@@
MISSING-DNF-PASSES
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E8] REFUSED: no `## Does not fit` section
@@
    problems += check_does_not_fit(dnf[0] if dnf else None)
@@
    problems += check_does_not_fit(dnf[0]) if dnf else []
@@@@
NONE-BESIDE-ITEMS
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E10] REFUSED: `none` beside a real item
@@
        if len(items) > 1:
@@
        if False:
@@@@
AMBIGUOUS-TAKES-FIRST
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E11] REFUSED: two assessments declare the row
@@
    if len(hits) > 1:
@@
    if False:
@@@@
EXPLICIT-ANY-FILE
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E11] REFUSED: --assessment naming a file
@@
        if want not in declared_rows(text):
@@
        if False:
@@@@
FAIL-OPEN-CONFIG
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E17] REFUSED: a sources.json that is not JSON
@@
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} could not be read as JSON
@@
    except (OSError, ValueError) as exc:
        return copy.deepcopy(DEFAULT_CLASSES)
        raise ValueError(f"{path} could not be read as JSON
@@@@
ONLY-SPEC-DRAFT-GATED
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E1] REFUSED: the gate bites spec-ready
@@
EVIDENCE_GATED_STATUSES = frozenset({"spec-draft", "spec-ready", "pr-open"})
@@
EVIDENCE_GATED_STATUSES = frozenset({"spec-draft"})
@@@@
UPSERT-UNGATED
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E1] REFUSED: upsert is gated too
@@
                # nothing behind — not even the verdict gate's override event.
                pending = check_evidence(state_path, args.source, args.status,
                                         args.assessment, args.override_sources,
                                         args.approved_by)
@@
                # nothing behind — not even the verdict gate's override event.
                pending = None
@@@@
REFUSAL-LEAVES-A-TRACE
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
...no transition and no override event was recorded
@@
                # refusal leaves no trace of the verdict gate either.
                pending = check_evidence(
@@
                # refusal leaves no trace of the verdict gate either.
                _record_transition(state_path, args.source, args.status)
                pending = check_evidence(
@@@@
OVERRIDE-NOT-COMMITTED
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E12]   ...AND writes the reason into state/triage.md
@@
    write_table(state, table)
    return (
        f"OVERRIDE: '{record['source']}'
@@
    return (
        f"OVERRIDE: '{record['source']}'
@@@@
OVERRIDE-WITHOUT-REASON
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E13] REFUSED: --override-sources with a reason that says nothing
@@
    if override is not None and not ev.has_substance(override):
@@
    if override is not None and not override:
@@@@
OVERRIDE-RECORDED-BEFORE-WRITE
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E14] an override on a transition that fails anyway
@@
                _record_transition(state_path, args.source, args.status)
                update_row(
@@
                _record_transition(state_path, args.source, args.status)
                if pending:
                    record_sources_override(state_path, pending)
                update_row(
@@@@
SNAPSHOT-CLAIMS-CONNECTED
@@
plugins/loopkit/scripts/source-snapshot.py
@@
FAIL [E18] an MCP server it cannot probe
@@
            status = "unknown (configured in .mcp.json
@@
            status = "connected (configured in .mcp.json
@@@@
SNAPSHOT-LEAKS-CONFIG
@@
plugins/loopkit/scripts/source-snapshot.py
@@
FAIL [E18] it prints the server NAME and never its configuration
@@
    return sorted(str(k) for k in servers) if isinstance(servers, dict) else []
@@
    return sorted(f"{k} {json.dumps(v)}" for k, v in servers.items()) if isinstance(servers, dict) else []
@@@@
APPROVER-OPTIONAL
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E20] REFUSED: an override with NO named approver
@@
        who = ev.approver_problems(approved_by, acting_identities(), root)
@@
        who = []
@@@@
APPROVED-BY-ALONE
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E20] REFUSED: --approved-by without --override-sources
@@
    if approved_by is not None and override is None:
@@
    if False:
@@@@
APPROVER-NOT-RECORDED
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E20]   ...the committed record names the human approver
@@
            f"(approved by {record['approved_by']}; run by {agent}; the gate said: "
@@
            f"(run by {agent}; the gate said: "
@@@@
AGENT-SHAPE-IGNORED
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E21] REFUSED: an override whose approver looks like an agent or process
@@
    if hits or "[bot]" in who.lower() or "@" in who or _LONG_DIGITS.search(who):
@@
    if False:
@@@@
SELF-APPROVAL
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E21] REFUSED: an approver equal to the acting agent
@@
        if ident and who.lower() == norm(ident).lower():
@@
        if False:
@@@@
ALLOWLIST-IGNORED
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E21] REFUSED: with .loopkit/approvers.txt present
@@
    allow = approvers_allowlist(root)
@@
    allow = None
@@@@
GRANDFATHER-ROWS-WITHOUT-ASSESSMENT
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E24] REFUSED: an in-flight fixing row with NO assessment
@@
    if not hits:
        return None, [f"no assessment under
@@
    if not hits:
        return None, []
        return None, [f"no assessment under
@@@@
FILLER-COUNTS
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E6] REFUSED: a placeholder read is not a read: `read: TODO`
@@
    return any(w not in FILLER_WORDS and len(w) >= 2 for w in words)
@@
    return True
@@@@
EXPLAINED-ANYWHERE
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E9] REFUSED: `explained:` buried inside the observation line
@@
        reasons = [m.group(1) for m in (_EXPLAINED.match(l.strip()) for l in item.splitlines()[1:]) if m]
@@
        reasons = [m.group(1) for m in (re.search(r"explained\s*:(.*)$", l, re.I) for l in item.splitlines()) if m]
@@@@
ALLOWLIST-SKIPS-SELF-CHECK
@@
plugins/loopkit/loopkit_core/evidence.py
@@
FAIL [E21] REFUSED: an allowlisted name that is also the acting LOOPKIT_AGENT
@@
    for label, ident in (acting or {}).items():
@@
    for label, ident in ({} if approvers_allowlist(root) is not None else (acting or {})).items():
@@@@
GIT-IDENTITY-IGNORED
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E21] REFUSED: an approver equal to the commit identity
@@
    names = ("LOOPKIT_AGENT", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME")
@@
    names = ("LOOPKIT_AGENT",)
@@@@
DIRTY-APPROVERS-TRUSTED
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E25] layer 2: a python-heredoc write to approvers.txt
@@
        dirty = uncommitted_config(root, ".loopkit/approvers.txt")
        if dirty:
            raise ValueError(dirty)
@@
        pass  # mutation: dirty approvers.txt trusted
@@@@
DIRTY-SOURCES-TRUSTED
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E25] an UNCOMMITTED sources.json
@@
    dirty = uncommitted_config(root, ".loopkit/sources.json")
    if dirty:
@@
    dirty = ""
    if dirty:
@@@@
STAGED-COUNTS-AS-COMMITTED
@@
plugins/loopkit/loopkit_core/triage_state.py
@@
FAIL [E25] staged but not committed is still refused
@@
        dirty = subprocess.run(git + ["status", "--porcelain", "--", rel],
@@
        dirty = subprocess.run(git + ["diff", "--name-only", "--", rel],
@@@@
GOVERNANCE-APPROVERS-UNPROTECTED
@@
plugins/loopkit/hooks/protect_governance.py
@@
FAIL [E25] layer 1: a Write to .loopkit/approvers.txt
@@
    (re.compile(r"(?:^|/)\.loopkit/approvers\.txt$"),
@@
    (re.compile(r"(?:^|/)\.loopkit/approvers\.txt-never$"),
@@@@
GOVERNANCE-SOURCES-UNPROTECTED
@@
plugins/loopkit/hooks/protect_governance.py
@@
FAIL [E25] layer 1: a Write to .loopkit/sources.json
@@
    (re.compile(r"(?:^|/)\.loopkit/sources\.json$"),
@@
    (re.compile(r"(?:^|/)\.loopkit/sources\.json-never$"),
EOF

# Split the data into one directory per mutation: id, file, want, old, new.
python3 - "$MUTATIONS" "$WORK" <<'PY'
import sys
from pathlib import Path
src, work = Path(sys.argv[1]), Path(sys.argv[2])
blocks = src.read_text().split("\n@@@@\n")
for n, block in enumerate(blocks):
    parts = block.strip("\n").split("\n@@\n")
    assert len(parts) == 5, f"mutation block {n} has {len(parts)} fields"
    d = work / f"mut-{n:02d}"
    d.mkdir()
    for name, value in zip(("id", "file", "want", "old", "new"), parts):
        (d / name).write_text(value)
print(len(blocks))
PY

run_one() {  # <mutation dir> -> writes <dir>/result
  local d="$1" skel out rc
  skel="$d/root"
  mkdir -p "$skel" && cp -R "$W/plugins" "$skel/plugins" || { echo "STALE (could not stage a copy) — $(cat "$d/id")" > "$d/result"; return; }
  if ! python3 - "$skel/$(cat "$d/file")" "$d/old" "$d/new" <<'PY'
import sys
from pathlib import Path
target, old, new = Path(sys.argv[1]), Path(sys.argv[2]).read_text(), Path(sys.argv[3]).read_text()
s = target.read_text()
if s.count(old) != 1:
    sys.exit(3)
target.write_text(s.replace(old, new, 1))
PY
  then
    echo "NOT RED — MUTATION IS STALE, not the pin: $(cat "$d/id")" > "$d/result"; return
  fi
  out="$(python3 "$PIN" "$skel" 2>&1)"; rc=$?
  printf '%s\n' "$out" > "$d/pin.log"
  local oks; oks="$(printf '%s\n' "$out" | grep -c '^  ok' || true)"
  if [ "$rc" != 0 ] && printf '%s\n' "$out" | grep -qF -- "$(cat "$d/want")" && [ "$oks" -ge 50 ]; then
    echo "RED, as required — $(cat "$d/id") (its own line failed; $oks other checks still ran green)" > "$d/result"
  else
    echo "NOT RED (rc=$rc, ok=$oks) — the pin cannot catch: $(cat "$d/id")" > "$d/result"
  fi
}

# Four at a time: each mutation has its own copy, so they cannot collide, and
# the suite stays inside the silence watchdog.
dirs=("$WORK"/mut-*)
i=0
for d in "${dirs[@]}"; do
  run_one "$d" &
  i=$((i + 1))
  if [ $((i % 4)) = 0 ]; then wait; fi
done
wait
status=0
for d in "${dirs[@]}"; do
  cat "$d/result"
  grep -q '^RED, as required' "$d/result" || status=1
done

AFTER="$(sums)"
if [ "$BEFORE" != "$AFTER" ]; then
  echo "NOT RED — THE HARNESS WROTE TO THE TRACKED TREE (checksums changed)"
  exit 1
fi
echo "tracked files untouched (checksums identical before and after)"
exit $status
