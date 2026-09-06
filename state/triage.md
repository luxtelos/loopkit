# triage.md — the work queue

Items land here after discovery. The loop only implements unattended work when a
spec exists and the row is marked `spec-ready`. Status values:
`new, spec-draft, spec-ready, fixing, pr-open, blocked, inbox, done`.

`blocked` = waiting on a human ruling in `inbox/needs-human.md`; counted, never
polled, never served as a stage. Rows are written through `triage_state.py`,
never by hand — a finding containing `|` is escaped on write and unescaped on
read, and a hand edit breaks that.
| finding | source | priority | spec | status |
|---|---|---|---|---|
| Judge runner: judge.py runs a pairwise, position-swapped LLM judge via claude -p (references/judge.md made executable) | plan §0.3 judge-runner | medium |  | spec-draft |
| PreToolUse updatedInput offload: rewrite a large Bash result to .loopkit/scratch and return the path | plan §0.3 updatedInput-offload | medium |  | spec-draft |
| Post-compact probe harness: recall / artifact / continuation / decision probes after a compaction | plan §0.3 post-compact-probes | low |  | inbox |
| Adoption runbook for a project that already carries its own hooks: init, memory.json, duplicate hooks, rulings dry-run, ledger baseline | plan §adoption runbook | medium |  | pr-open |
| install.sh Linux path unexercised; selftest verified on node:22-bookworm — say so in GETTING-STARTED and pin a Linux run in CI | docs/GETTING-STARTED.md §linux | low |  | pr-open |
| Release 0.2.0: tag + GitHub release once PRs #2 #3 #4 are merged by the owner | release §0.2.0 | high |  | blocked |
| Submit to anthropics/claude-plugins-official once 0.2.0 is tagged | plan §distribution official-marketplace | medium |  | blocked |
| Bloom profile (six behaviour seeds + check-bloom.py) — waits on the owner's spend decision and a control-case run | docs/research/bloom-assessment.md | medium |  | blocked |
