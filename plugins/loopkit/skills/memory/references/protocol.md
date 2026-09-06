# Memory protocol — the shapes

## Wake-up

The session-start line prints `MEMORY: graph=… memory=… knowledge=…`. On a
`resume`/`compact` start it also prints Files Modified and the last progress
line. `memory.py wakeup` gives the palace's own L0/L1 context when it is
available, or the three most recent notes when it is not.

## Note shape (`state/memory/<date>-<slug>.md`)

```
---
id: 2026-09-06-period-end-is-null-on-free-to-paid
valid_from: 2026-09-06
tags: [billing, trap]
supersedes: 2026-08-30-period-end-backfill      # optional
---

# period_end is NULL on free-to-paid

What was observed, the command that proved it, the control case, the fix
or the ruling. One fact per note. Cite immutable sources (a commit, a file
at a SHA), never a queue (inbox, triage).
```

`invalidate` adds `valid_until: <date>` and an `> INVALIDATED <date>: <why>`
trailer. Nothing is deleted; temporal questions ("what did we believe in
August?") stay answerable.

## Recall triggers (`.loopkit/recall-triggers.txt`)

Three rule kinds, one per line, prefix `path:`, `content:`, `bash:`:

- `path` — a regex over the file path (default: any `migrations/*.sql`).
  Always in scope; no outage escape.
- `content` — a regex over the written text (default: UNIQUE INDEX, UNIQUE(,
  PRIMARY KEY, CONSTRAINT … CHECK(, EXCLUDE USING, ADD CONSTRAINT). Code-like
  files only; Markdown and text are exempt.
- `bash` — a regex over a Bash command (default: a shell redirect, `tee`,
  `cp`, `mv` or `install` whose target is a migrations path). The same write
  by another door.

The defaults are in the template `init` copies; edit the project's copy.

## What counts as "memory consulted"

Any of, this session: an MCP call matching an adapter's pattern
(`mcp__mempalace__mempalace_search|kg_query|traverse`,
`mcp__codebase-memory(-mcp)?__search_graph|search_code|query_graph|trace_path`),
or a Bash command matching it (`mempalace … search`, `codebase-memory-mcp cli
search_graph …`, `memory.py recall|graph …`). The hook asks the registry; it
never names a server itself.

## Outage escape

Write the reason (non-empty) into
`$TMPDIR/loopkit-recall-<session-key>/recall-unavailable` and retry. It
unblocks content-rule writes only, and the reason is what an auditor reads
against the server logs. The session key is printed in the BLOCKED message.
