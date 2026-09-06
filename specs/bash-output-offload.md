# Bash output offload through `updatedInput`

- Row: `plan §0.3 updatedInput-offload` · Assessment: `state/2026-09-06-plan-remainder-assessment.md §2`
- Outcome: a Bash command known to be noisy is rewritten before it runs so that
  its full output lands in a file and only a capped head/tail plus the path
  reaches the context window — without the agent remembering to wrap it.

## Scope

In: a PreToolUse hook (`hooks/offload_rewrite.py`, matcher `Bash`) that returns
`hookSpecificOutput.updatedInput` wrapping the command in the existing
`scripts/run-capped.sh`; a per-project pattern file `.loopkit/offload-patterns.txt`;
one metrics event. Out: changing `run-capped.sh`; any rewrite of non-Bash tools;
size-based decisions (a PreToolUse hook cannot see output size).

## Constraints

Fail-open everywhere: a hook error or an unreadable pattern file MUST leave the
command untouched. Passive without the pattern file. **Precondition for
`spec-ready`:** the hook header cites the line of the Claude Code hooks
reference that defines `updatedInput` for PreToolUse; if the field does not
exist for Bash, this spec is void and the row returns to `inbox`.

## Acceptance (EARS)

1. WHEN `.loopkit/offload-patterns.txt` is absent or contains only blank and
   `#` lines, the hook SHALL emit no `updatedInput` (exit 0, empty stdout).
2. WHEN the command matches a pattern line (Python `re.search`), the hook SHALL
   emit `updatedInput.command` = `bash <plugin>/scripts/run-capped.sh -- <original>`
   with the original preserved byte-for-byte inside a single-quoted form.
   Check: round-trip a command containing `'`, `"`, `$` and `|` through the
   rewrite and `run-capped.sh`, compare stdout to the unwrapped run.
3. IF the command already starts with `bash <…>/run-capped.sh`, THEN the hook
   SHALL leave it unchanged (idempotent under re-application).
4. IF the command contains a heredoc marker (`<<`) or a newline, THEN the hook
   SHALL leave it unchanged. Check: heredoc fixture → no `updatedInput`.
5. WHEN a rewrite is emitted, the wrapped run SHALL preserve the original exit
   code and `pipefail`. Check: the existing `run-capped.sh` pin, re-run through
   the rewritten form (`false | true` → 1).
6. IF a pattern line is not a valid regex, THEN the hook SHALL treat that line
   as no match and continue with the others (fail-open, never a block).
7. WHEN a rewrite is emitted, the hook SHALL append one `offload_rewrite` event
   to `.loopkit/metrics.jsonl`. Check: count before/after.
8. The hook SHALL complete in under 200 ms for a 50-line pattern file. Check:
   `time` in the selftest with a 50-line fixture.

## Edge cases

- Boundary: empty command → unchanged; a 1-line pattern file → works; a pattern
  that matches every command (`.`) is the user's choice and is honoured.
- Error: pattern file unreadable (permissions) → unchanged, one stderr line;
  `run-capped.sh` missing from the plugin → unchanged, one stderr line.
- Concurrency: stateless per call; the metrics append is a single-line
  `O_APPEND` write. Retry after partial success: not applicable.

## Control case

`git status`, `git diff --stat` and every command in the selftest's own scratch
runs pass through unchanged with the default (absent) pattern file, and the
batch-b `run-capped.sh` pin stays green.
