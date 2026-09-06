#!/usr/bin/env bash
# test-regressions.sh — fail only on NEW test failures.
#
# WHY: most real suites carry pre-existing failures. Proving "zero regressions"
# by running the suite twice (head + a stashed baseline) is slow, racy across
# worktrees, and gets skipped. Instead the known-failure set lives in
# state/known-test-failures.txt, committed, and ONE run diffed against it is
# the verdict. The goal is to shrink that file to zero, in the open.
#
# Usage:
#   test-regressions.sh                     # run suite, diff vs baseline
#   test-regressions.sh --update-baseline   # run suite, rewrite baseline
#   test-regressions.sh --from-json FILE    # skip the run, diff a jest/vitest JSON report
#   test-regressions.sh --from-list FILE    # skip the run, diff a plain list
#                                           # ("<file> :: <test name>" per line — for any runner)
#
# Baseline format (state/known-test-failures.txt), one entry per line:
#   <test file relpath> :: <full test name>   # exact known-failing test
#   <test file relpath> :: *                  # every failure in that file is known
#   # comment lines and blanks are ignored
#
# Env (or <project>/.loopkit/config.env):
#   LOOP_TEST_JSON_CMD   command producing a jest/vitest-shaped JSON report; the
#                        token {report} is replaced by the report path.
#                        default: npx vitest run --reporter=json --outputFile={report}
#   LOOP_TEST_BASELINE   baseline path (default state/known-test-failures.txt)
#   LOOP_ENV_WRAPPER     prefix for the run, e.g. "doppler run --project p --config c --"
#   LOOP_MIN_NODE        if set (e.g. 20), refuse to run on an older Node
#
# Exit codes: 0 = no new failures, 1 = new failures (listed on stdout),
#             2 = could not produce a test report.

set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT"
if [[ -f "$ROOT/.loopkit/config.env" ]]; then set -a; . "$ROOT/.loopkit/config.env"; set +a; fi

BASELINE="${LOOP_TEST_BASELINE:-$ROOT/state/known-test-failures.txt}"
# The default lives in its own variable: a `}` inside a ${VAR-default}
# closes the expansion at the first brace, so any SET value got a literal
# `}` appended (and the default only survived because `{report` + `}`
# happened to reassemble). Found by the placeholder pin.
DEFAULT_JSON_CMD='npx vitest run --reporter=json --outputFile={report}'
LOOP_TEST_JSON_CMD="${LOOP_TEST_JSON_CMD-$DEFAULT_JSON_CMD}"
LOOP_ENV_WRAPPER="${LOOP_ENV_WRAPPER-}"
LOOP_MIN_NODE="${LOOP_MIN_NODE-}"

UPDATE_BASELINE=0
JSON_REPORT=""
LIST_REPORT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --update-baseline) UPDATE_BASELINE=1; shift ;;
        --from-json) JSON_REPORT="$2"; shift 2 ;;
        --from-list) LIST_REPORT="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

# Plain `bash -c`, NOT `bash -lc`: a login shell re-sources the profile and can
# reset PATH to a system runtime, silently discarding the caller's — which is
# how a gate once ran a whole suite on the wrong Node for weeks.
run_in_env() {
    local command="$1"
    if [[ -n "$LOOP_ENV_WRAPPER" ]]; then
        local -a wrapper
        read -r -a wrapper <<< "$LOOP_ENV_WRAPPER"
        "${wrapper[@]}" bash -c "$command"
        return
    fi
    bash -c "$command"
}

# Probe through the exact execution path the suite will use, and fail fast:
# a too-old runtime fabricates "new" failures out of suites it cannot collect.
if [[ -n "$LOOP_MIN_NODE" ]]; then
    resolved="$(run_in_env 'node --version' 2>/dev/null | tail -n1)"
    major="${resolved#v}"; major="${major%%.*}"
    if ! [[ "$major" =~ ^[0-9]+$ ]] || (( major < LOOP_MIN_NODE )); then
        echo "FAIL: the test run would use Node '${resolved:-<none>}' — need >= $LOOP_MIN_NODE." >&2
        exit 2
    fi
    echo ">> Node check: suite will run on $resolved"
fi

CURRENT_FAILURES="$(mktemp "${TMPDIR:-/tmp}/current-failures.txt.XXXXXX")"

if [[ -n "$LIST_REPORT" ]]; then
    [[ -s "$LIST_REPORT" ]] || { echo "FAIL: list report $LIST_REPORT is missing or empty" >&2; exit 2; }
    grep -v '^\s*#' "$LIST_REPORT" | awk 'NF' | sort -u > "$CURRENT_FAILURES"
else
    if [[ -z "$JSON_REPORT" ]]; then
        [[ -n "$LOOP_TEST_JSON_CMD" ]] || { echo "FAIL: LOOP_TEST_JSON_CMD is empty and no report was given" >&2; exit 2; }
        # Honour TMPDIR explicitly (BSD mktemp -t ignores it) and keep the X's
        # trailing (BSD mktemp does not substitute infix X's).
        JSON_REPORT="$(mktemp "${TMPDIR:-/tmp}/test-report.json.XXXXXX")"
        # The placeholder lives in a variable: an escaped brace inside the
        # pattern of a ${var//pat/rep} expansion closes the expansion early,
        # and the report path came out with a trailing `}` for weeks.
        placeholder='{report}'
        cmd="${LOOP_TEST_JSON_CMD//"$placeholder"/$(printf '%q' "$JSON_REPORT")}"
        echo ">> Running the suite once (JSON report: $JSON_REPORT)"
        echo ">> $cmd"
        # The runner exits non-zero when any test fails — expected; the diff decides.
        run_in_env "$cmd" || true
    fi
    if [[ ! -s "$JSON_REPORT" ]]; then
        echo "FAIL: no JSON report produced at $JSON_REPORT" >&2
        exit 2
    fi
    python3 - "$JSON_REPORT" "$ROOT" > "$CURRENT_FAILURES" <<'PY'
import json, os, sys

report_path, root = sys.argv[1], sys.argv[2]
with open(report_path) as fh:
    report = json.load(fh)

lines = set()
for suite in report.get("testResults", []):
    name = suite.get("name", "")
    rel = os.path.relpath(name, root) if os.path.isabs(name) else name
    assertions = suite.get("assertionResults", [])
    failed = [a for a in assertions if a.get("status") == "failed"]
    for a in failed:
        lines.add(f"{rel} :: {a.get('fullName', a.get('title', '?'))}")
    # A file that failed to even run (import/setup error) has no failed
    # assertions but a failed suite status — record it at file level.
    if not failed and suite.get("status") == "failed":
        lines.add(f"{rel} :: <suite failed to run>")

for line in sorted(lines):
    print(line)
PY
    if [[ $? -ne 0 ]]; then
        echo "FAIL: could not parse the JSON report (expected jest/vitest shape: testResults[].assertionResults[])" >&2
        exit 2
    fi
fi

FAIL_COUNT="$(grep -c . "$CURRENT_FAILURES" || true)"
echo ">> Current failing tests: $FAIL_COUNT"

if [[ "$UPDATE_BASELINE" == "1" ]]; then
    mkdir -p "$(dirname "$BASELINE")"
    {
        echo "# known-test-failures.txt — the pre-existing failure baseline"
        echo "# Regenerate with: test-regressions.sh --update-baseline"
        echo "# Goal: shrink this file to zero (fix-or-retire sweep, tracked in state/triage.md)."
        echo "# Format: '<file> :: <full test name>' or '<file> :: *' (whole file known-bad)."
        cat "$CURRENT_FAILURES"
    } > "$BASELINE"
    echo ">> Baseline rewritten: $BASELINE ($FAIL_COUNT entries)"
    exit 0
fi

if [[ ! -f "$BASELINE" ]]; then
    echo ">> No baseline at $BASELINE — treating every failure as NEW."
    echo ">> Seed one with: test-regressions.sh --update-baseline"
    BASELINE=/dev/null
fi

python3 - "$CURRENT_FAILURES" "$BASELINE" <<'PY'
import sys

with open(sys.argv[1]) as fh:
    current = {l.strip() for l in fh if l.strip()}
with open(sys.argv[2]) as fh:
    baseline = {
        l.strip() for l in fh
        if l.strip() and not l.strip().startswith("#")
    }

wildcard_files = {b.split(" :: ")[0] for b in baseline if b.endswith(" :: *")}

new = sorted(
    c for c in current
    if c not in baseline and c.split(" :: ")[0] not in wildcard_files
)
current_files = {c.split(" :: ")[0] for c in current}
fixed = sorted(
    b for b in baseline
    if (b not in current and not b.endswith(" :: *"))
    or (b.endswith(" :: *") and b.split(" :: ")[0] not in current_files)
)

if fixed:
    print(f">> {len(fixed)} baseline entr(y/ies) no longer failing — shrink the baseline:")
    for f in fixed:
        print(f"   FIXED: {f}")

if new:
    print(f"FAIL: {len(new)} NEW test failure(s) not in the baseline:")
    for n in new:
        print(f"   NEW: {n}")
    sys.exit(1)

print(">> PASS: zero NEW failures (all current failures are in the known baseline).")
PY
exit $?
