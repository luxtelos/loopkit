---
name: memory
description: Consult and record memory through the loop's adapters — structural code graph, episodic notes/palace, curated knowledge — instead of grep or RAG. Use when a change touches a storage invariant, a table, a past decision, or "has this bitten us before"; use before any migration; use to record a finding that must survive the session.
---

# memory

One CLI, three kinds of memory, one banner that always says which path answered:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/memory.py" status --line
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/memory.py" recall "unique index on role_assignments"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/memory.py" graph callers createActiveRoleAssignment
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/memory.py" remember --title "period_end is NULL on free-to-paid" --body "…" --tags billing,trap
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/memory.py" invalidate 2026-09-06-period-end-is-null --reason "fixed by the activation snapshot"
```

Output starts with `ADAPTER: <kind>=<name> [available|DEGRADED: <reason>]`.
DEGRADED is not an error; it is the truth about the machine, and the reason
says how to fix it (index the repo, install the palace). The fallbacks —
grep for the graph, notes on disk for memory — answer in the same shape.

## The protocol (why the gate exists)

1. **Before a storage invariant** (a migration, UNIQUE / PRIMARY KEY /
   CONSTRAINT … CHECK / EXCLUDE): `recall` the table and column, then `graph
   callers` for every writer. `require_recall.py` blocks the write until one
   of these has run this session. A UNIQUE constraint is a claim about what
   ONE ROW MEANS; enumerate every row kind the predicate captures.
2. **Before answering about a past decision, ruling or incident**: `recall`
   first. A recalled fact carries its date; a guess does not.
3. **After a finding worth keeping**: `remember`. The note is the record; the
   palace is an index over it. Writes never block.
4. **When a fact changes**: `invalidate` the old note, then `remember` the
   new one. Never leave both alive without `valid_until` on the old.

Token-lean by default: `--format concise` caps at 30 lines and writes the
rest to `.loopkit/scratch/`; ask for `--format detailed` only when you will
read it all.

## Configuration

`.loopkit/memory.json` (laid down by `init`). Delete a section to disable that
kind; delete the file to make every memory gate passive. `graph.project`
names the indexed project when the tool indexed a different path (worktrees
index separately). `memory.bin` points at a `mempalace` outside `.venv/bin`.
Recall triggers live in `.loopkit/recall-triggers.txt`.

See `references/protocol.md` (read it) for the wake-up/diary shape and the
outage escape.

## Gotchas

- The graph index is keyed by project NAME, and a worktree is a different
  project from its parent. Set `graph.project` or index the worktree; a path
  passed as a name returns a false "not indexed".
- `mempalace` writes (drawers, diary, KG) are MCP-only. The CLI path is
  note-then-`mine`; the note on disk is the durable record, the mine is best
  effort.
- The outage escape (`$TMPDIR/loopkit-recall-<session>/recall-unavailable`,
  non-empty) applies to the content rule only. A real migration write waits
  for memory or a human; there is no flag for it.
- The Bash trigger matches a redirect into a migrations path even inside a
  heredoc that merely quotes one — the same act by another door cannot be
  told from prose at that layer. Write documentation with the Write tool.
- `status` is cached for 10 minutes because both CLIs take seconds to start;
  `--no-cache` after you index or install something.
- `recall` returning nothing is a result. Say "memory has nothing on X",
  never "X has never happened".
