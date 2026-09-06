---
description: Lay LoopKit's files into this project (state/, inbox/, specs/, the three contracts, .loopkit/ config, CLAUDE.md rules). Idempotent — never overwrites.
allowed-tools: Bash(bash *), Read
---

Run the initialiser and show its output verbatim:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/loopkit-init.sh"
```

Then read the three contracts it created or kept — `FILES.md`, `TOOLS.md`,
`COMMANDS.md` — with the Read tool. A gate blocks work tools until you have.

Finish by telling the user, in three lines at most: what was created, what was
kept, and that `.loopkit/config.env` (copied from the example) is where the
project's test, lint and build commands go. Do not fill it in for them; the
commands are theirs to name.
