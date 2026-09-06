---
description: One cheap tick of a PR watch — did the PR move? Pass the PR number.
allowed-tools: Bash(bash *)
---

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/loop-watch.sh" --pr $ARGUMENTS
```

Read the VERDICT line, not the exit code. ACT or ESCALATE means read the PR
properly now; QUIET means do nothing and say so in one line; BASELINE means the
first run, treat as quiet.
