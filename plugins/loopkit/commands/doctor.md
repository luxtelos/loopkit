---
description: Check the LoopKit install — hooks, scripts, project files, gate commands, model checkers.
allowed-tools: Bash(bash *), Bash(python3 *), Bash(node *)
---

Run these and report each as ok / missing / broken, one line each, then stop:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/loopkit-init.sh" --dry-run
```

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/test_block_dangerous.py" | tail -1
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/test_loop_doctrine.py" | tail -1
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/test_protect_governance.py" | tail -1
```

```bash
grep -nE '^\s*LOOP_[A-Z_]+=' .loopkit/config.env 2>/dev/null || echo "no .loopkit/config.env — gate uses npm defaults"
```

```bash
node "${CLAUDE_PLUGIN_ROOT}/skills/run-state-model/driver.mjs" doctor || echo "model checkers not installed — bash ${CLAUDE_PLUGIN_ROOT}/skills/run-state-model/install.sh"
```

A `--dry-run` line reading CREATE means the project has not been initialised:
suggest `/loopkit:init`. Do not fix anything from here; report and stop.
