---
name: morning-triage
description: Discovery — scan GitHub issues (and anything else the project names) for work, rank it, and append rows to state/triage.md at status `new`. Never modifies or closes existing rows.
---

# Skill: Morning Triage

## Job

Scan GitHub issues and any alert source the project names. Find work. Write to
`state/triage.md`.

## Algorithm

1. Fetch open GitHub issues labeled `bug`.
2. Fetch open GitHub issues labeled `enhancement`.
3. Override priority from a `Priority N` label when one is present, using this
   mapping. The label vocabulary and the column vocabulary are different, and
   until this table existed nothing said how one became the other:

   | Label        | `priority` column |
   | ------------ | ----------------- |
   | `Priority 1` | `critical`        |
   | `Priority 2` | `high`            |
   | `Priority 3` | `medium`          |
   | `Priority 4` | `low`             |

   If an issue carries more than one, take the most severe and note the clash in
   Warnings — two priority labels is a human disagreement, not something to
   average away.

4. With no `Priority N` label, fall back on the type label: `high` for `bug`,
   `medium` for `enhancement`. An issue with neither is `medium`, so nothing is
   silently dropped for want of a label.
5. Deduplicate by issue number before appending.
6. Append to `state/triage.md` with `status=new` and an empty `spec`, through
   the helper — never by hand-editing the table:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/triage_state.py" upsert --state state/triage.md \
     --finding "<title>" --source "GitHub #<n>" --priority <p> --status new
   ```

7. Bridge the inbox. Findings written as prose in `inbox/needs-human.md` are
   invisible to a loop that discovers from code; a real loop once reported
   itself idle in front of sixty-four open findings for exactly this reason.

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/inbox_to_triage.py"          # dry run
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/inbox_to_triage.py" --apply  # add the rows
   ```

   It is create-only: an existing row's status belongs to the loop, and the
   bridge never resets it.

## Output Format

Markdown table rows, one per finding. Columns:
`finding | source | priority | spec | status`

`priority` is one of `critical`, `high`, `medium`, `low` — the four values step
3 produces, and nothing else.

```markdown
| Report timeout on 50k transactions | GitHub #42  | high   |  | new |
| Sandbox credential refresh fails   | GitHub #156 | medium |  | new |
```

### Warnings

Anything the run surfaces that is not a row goes here, under its own heading
beneath the table, as one bullet each. The table has no column for it, and a
warning with nowhere to go is a warning that gets dropped:

```markdown
#### Warnings

- PR #2451 has no `Fixes #` line — the ticket it closes cannot be derived.
- Issue #42 carries both `Priority 1` and `Priority 3`; took `critical`.
```

Write the heading even when there is nothing under it, with `- none`. An absent
section reads the same whether the check ran clean or never ran at all.

## Environment

- `gh` authenticated (or `GH_TOKEN` set) for the GitHub API.

## Do NOT

- Modify existing rows (only append new findings).
- Close findings (leave that to the loop).
- Comment on issues or PRs unless the project has an idempotent script for it —
  a hand-written status comment is written twice the next morning.
