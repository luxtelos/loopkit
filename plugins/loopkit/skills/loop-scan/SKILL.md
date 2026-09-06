---
name: loop-scan
description: Deterministic status of what is in flight — open PRs, their real blockers, loop-owned issues, and the local triage backlog GitHub cannot see. Use for "what's open", "what can be merged", "what needs me".
---

# loop-scan

One command:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/loop-scan.py"
```

Two API calls total, whatever the repo size — one `gh pr list`, one
`gh issue list`. Never one call per item; a sweep nobody runs because it is slow
is worse than a coarse one they run daily.

Flags: `--prs-only`, `--repo owner/name`, `--limit N`, `--scope <lane|a,b,c>`.
Lanes come from `<project>/.loopkit/scopes.json`; the issue label the loop
owns is `LOOPKIT_ISSUE_LABEL` (default `loopkit`).

## Why this is a script and not a rule in CLAUDE.md

CLAUDE.md is _context_. It can say "check the PRs"; it cannot run `gh`. And
every line in it spends compliance budget on every turn, including the thousands
that have nothing to do with pull requests.

"Which PRs are open, which are green, which are blocked" is a **lookup, not a
judgement** — the `tool` layer in loop-assess's failure table: a derived value
the prompt should never compute. A model reconstructing this by hand gets a
different answer each time and cannot be checked.

## Reading the output

**`READY TO MERGE` is the only line to act on without opening anything.** It
means approved AND green AND not conflicting AND not draft. Everything else in
the summary is context for deciding what to work on.

The distinction matters: a PR was once reported as "approved, waiting on a human
merge" while its CI was red, because the summary asked _is it approved_ when the
useful question is _could I merge it right now_. `mergeable_now()` asks the
second one, and `scripts/test_loop_scan.py` pins that case.

**`green` means nothing reported failure — not that everything ran.** Steps
inside a job are sequential and fail fast, so a green rollup is consistent with
later steps never executing. Before trusting green on anything that matters,
read the job.

**`could not read` is not `none`.** The script says which it means. Never report
an auth or network failure as "no open PRs" — that is the same silent-zero shape
as a gated suite reporting success by running nothing.

**The local backlog is the half GitHub cannot see.** `state/triage.md` rows
never appear in any `gh` query. Forty unclassified findings sat there while the
loop reported itself busy watching one PR, which is exactly why this section
exists.

## After the scan

The scan changes nothing — it is a read. To act on what it found:

- rows at `new` → `bash "${CLAUDE_PLUGIN_ROOT}/scripts/loop-next.sh"`, then `loopkit:loop-tick`
- a single PR you are watching over many ticks →
  `bash "${CLAUDE_PLUGIN_ROOT}/scripts/loop-watch.sh" --pr <n>` is far cheaper than re-scanning
- something that needs a ruling → `inbox/needs-human.md`, with both options
  costed

Do not merge anything from a scan. The loop never merges.
