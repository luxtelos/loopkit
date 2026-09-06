---
description: Status — open PRs with their real blockers, loop-owned issues, and the local triage backlog. Read-only.
allowed-tools: Bash(python3 *)
---

Run the scan and show the output verbatim:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/loop-scan.py" $ARGUMENTS
```

Then say, in two lines: what is READY TO MERGE (only that line is actionable
unread), and how many rows sit at `new`. Never merge anything from a scan.
