---
description: Check the LoopKit install — hooks, scripts, project files, gate commands, the CLAUDE.md budget, skills, tool surface, duplicate hooks, model checkers.
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

The three linters (Anthropic's guidance as checks — none fails the doctor, read the notes):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-claude-md.py"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-skills.py" --plugin "${CLAUDE_PLUGIN_ROOT}"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-tools.py" --plugin "${CLAUDE_PLUGIN_ROOT}"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-duplicate-hooks.py" --plugin "${CLAUDE_PLUGIN_ROOT}"
```

Knowledge and the loop's numbers (n=0 is an honest answer; a bundle is optional):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/memory.py" status --line
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rulings-compile.py"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/loop-metrics.py"
```

Session memory (what the last gate recorded, and what a compaction would carry):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/progress.py" last --n 3
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/progress.py" files-modified --limit 8
```

The judge runner reads its six rules from judge.md at run time (no `claude` call here; six numbered lines is ok):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" --print-rules | grep -c '^[1-6]\. '
```

```bash
node "${CLAUDE_PLUGIN_ROOT}/skills/run-state-model/driver.mjs" doctor || echo "model checkers not installed — bash ${CLAUDE_PLUGIN_ROOT}/skills/run-state-model/install.sh"
```

A `--dry-run` line reading CREATE means the project has not been initialised:
suggest `/loopkit:init`. A `DUPLICATE` line means the project carries its own
copy of a plugin hook and both fire; the fix is deleting the project copy. Do
not fix anything from here; report and stop.
