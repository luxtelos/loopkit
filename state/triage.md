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
| Judge runner: judge.py runs a pairwise, position-swapped LLM judge via claude -p (references/judge.md made executable) | plan §0.3 judge-runner | medium | specs/pairwise-judge-verdicts.md | done |
| PreToolUse updatedInput offload: rewrite a large Bash result to .loopkit/scratch and return the path | plan §0.3 updatedInput-offload | medium | specs/bash-output-offload.md | done |
| Post-compact probe harness: recall / artifact / continuation / decision probes after a compaction | plan §0.3 post-compact-probes | low |  | inbox |
| Adoption runbook for a project that already carries its own hooks: init, memory.json, duplicate hooks, rulings dry-run, ledger baseline | plan §adoption runbook | medium |  | done |
| install.sh Linux path unexercised; selftest verified on node:22-bookworm — say so in GETTING-STARTED and pin a Linux run in CI | docs/GETTING-STARTED.md §linux | low |  | done |
| Release 0.2.0: tag + GitHub release once PRs #2 #3 #4 are merged by the owner | release §0.2.0 | high |  | done |
| Submit to anthropics/claude-plugins-official once 0.2.0 is tagged | plan §distribution official-marketplace | medium |  | blocked |
| Bloom profile (six behaviour seeds + check-bloom.py) — waits on the owner's spend decision and a control-case run | docs/research/bloom-assessment.md | medium |  | blocked |
| M1 runtime spec: specs/loopkit-runtime.md (EARS), ADR-0001..0004, docs/DDD-ERD.md, spec/fixtures, loopkit-runtime.model.fizz | docs/research/runtime-plan.md §M1 | high |  | done |
| M2 loopkit-core: extract portable modules, lift stage+NEXT into loop_next_pick.decide, Provider (stub/OpenAI-compat/Anthropic), Store (fs/sqlite/s3), Runner, projection | docs/research/runtime-plan.md §M2 | high |  | blocked |
| M3 plugin becomes an adapter over loopkit-core; selftest unchanged | docs/research/runtime-plan.md §M3 | medium |  | blocked |
| M4 projections + protocols: knowledge project --schema, A2A agent card, AG-UI emitter behind a flag | docs/research/runtime-plan.md §M4 | medium |  | blocked |
| M5 loopkit-js from the conformance fixtures (packages/js) | docs/research/runtime-plan.md §M5 | low |  | blocked |
| M6 bench: prompt-driven vs policy-driven on one task set, ledger metrics | docs/research/runtime-plan.md §M6 | low |  | blocked |
| M1 spec review FAIL: scoping field is fiction (scopes/scope/tags vs okf.py's lane\|scope\|domain prefix), fixture 05 message lacks required enqueued_at, criterion 1 run_start contradicts all four fixtures, fixture 04 events ambiguous, counts undefined | PR #8 review §spec-defects | high |  | new |
| Model checker: EffectAtMostOnce is VACUOUS (deleting the convergence guard still passes; Commit's result==0 guard makes redelivery unreachable) and criterion 27's lease-guard claim is false | PR #8 review §vacuous-invariant | high |  | new |
| Criterion 33 cites a prompt-bytes-per-tick ceiling that does not exist anywhere in the repo — an unpinned proxy presented as a check | PR #8 review §phantom-proxy | high |  | new |
| protect_governance had no Bash matcher; shell writes into specs/ bypassed it entirely | PR #8 review §hook-gap | critical |  | pr-open |
| selftest called an undefined section() helper; three 'command not found' lines per run on both platforms | linux-validation 2026-09-07 | low |  | pr-open |
