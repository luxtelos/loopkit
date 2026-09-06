# Vendored excerpt — Claude Code hooks reference

Source: https://code.claude.com/docs/en/hooks.md
Fetched: 2026-09-07
SHA-256 of the whole document at fetch time: c30a50b8192dadf4e6ba016e451685f57a6d1d2c360d268887a9a94022d29f3e
Refresh and re-verify: bash plugins/loopkit/scripts/refresh-hooks-citation.sh

This file exists so a quotation in this repo can be checked without a network
call. Two earlier headers in hooks/offload_rewrite.py quoted sentences that are
not in the document at all — a paraphrase from a summarising fetch, pasted as if
verbatim, twice. The selftest now diffs the header's quoted block against the
block below, so an invented sentence fails the suite rather than shipping.
Nothing here is edited by hand; the refresh script rewrites the whole file.

## QUOTE-BEGIN
`PreToolUse`: `updatedInput` directly under `hookSpecificOutput` replaces a tool's arguments before it runs. See [PreToolUse decision control](#pretooluse-decision-control)
Modifies the tool's input parameters before execution. Replaces the entire input object, so include unchanged fields alongside modified ones. Claude Code evaluates permission rules and a Bash command's [auto-background eligibility](/docs/en/tools-reference#background-commands) against the input your hook returns, not the input Claude sent. Combine with `"allow"` to auto-approve, or `"ask"` to show the modified input to the user. For `"defer"`, ignored
## QUOTE-END
