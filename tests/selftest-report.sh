#!/usr/bin/env bash
# selftest-report.sh <report.json> — run tests/selftest.sh and write a
# jest-shaped JSON report (testResults[].assertionResults[]) so the plugin's
# own stop gate can diff it against state/known-test-failures.txt.
set -uo pipefail
OUT="${1:?usage: selftest-report.sh <report.json>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$(mktemp "${TMPDIR:-/tmp}/selftest-log.XXXXXX")"
bash "$REPO/tests/selftest.sh" > "$LOG" 2>&1; rc=$?
python3 - "$LOG" "$OUT" "$rc" <<'PY'
import json, re, sys
log, out, rc = sys.argv[1], sys.argv[2], int(sys.argv[3])
results = []
for line in open(log, encoding="utf-8", errors="replace"):
    m = re.match(r"^\s{2}(ok|FAIL)\s+(.*)$", line.rstrip())
    if m:
        results.append({"status": "passed" if m.group(1) == "ok" else "failed", "fullName": m.group(2).strip()[:200], "title": m.group(2).strip()[:200]})
suite_failed = rc != 0 or any(r["status"] == "failed" for r in results)
json.dump({"testResults": [{"name": "tests/selftest.sh", "status": "failed" if suite_failed else "passed", "assertionResults": results}]}, open(out, "w"), indent=1)
print(f"selftest-report: {len(results)} assertions, {sum(r['status']=='failed' for r in results)} failed, rc={rc}")
PY
exit $rc
