# Assessment — the five `new` rows from the 0.2 plan remainder (2026-09-06)

Mode A, one block per row. Baseline commands were run in this checkout on
branch `chore/dogfood-and-release` (main + dogfood files).

## 1. Judge runner — `plan §0.3 judge-runner`

**Observed:** `skills/acceptance-review/references/judge.md` states the pairwise,
position-swapped discipline as prose; nothing executes it (`ls plugins/loopkit/scripts | grep judge` → empty).
**Evidence:** the ls above; the selftest has no judge pin. Could it have failed? Yes — a file named judge.py would have shown.
**Control case:** `fanout.sh` with a stub `claude` on PATH (batch d pin) — the runner must reuse that stub pattern, and a stub that answers A in position 1 and B in position 1 (pure position bias) must yield `FINAL: TIE confidence 0.5`.
**Classification:** `code` — the reflex resisted is "the judge is an LLM, so it is a prompt problem"; the swap, the disagreement→TIE rule and the output shape are deterministic and belong in a script.
**Route:** spec-writer. **Not a code problem:** the judge prompt text stays in judge.md; the script reads it, never embeds it.

## 2. PreToolUse `updatedInput` offload — `plan §0.3 updatedInput-offload`

**Observed:** on this branch no hook returns `updatedInput` (`grep -rn updatedInput plugins/loopkit/hooks` → empty); batch b ships the honest half (`run-capped.sh` + a PostToolUse nudge past 8 KB).
**Evidence:** the grep; hooks.json read. A PreToolUse hook cannot see output size, so the rewrite must key on the command shape, not the result.
**Control case:** a short command (`git status`) must reach the shell unchanged; a rewritten command must preserve `pipefail` and the exit code (the run-capped pin).
**Classification:** `code`, with a measurement precondition: the spec must cite the hooks doc line that defines `hookSpecificOutput.updatedInput` for PreToolUse, and the pattern list lives in `.loopkit/offload-patterns.txt` (absent = passive).
**Route:** spec-writer. **Not a code problem:** which commands are "noisy" is a per-project choice — a config file, never a hardcoded list.

## 3. Post-compact probe harness — `plan §0.3 post-compact-probes`

**Observed:** `session_start.sh resume` output (Files Modified, last progress line, `git log -3`) is pinned by the selftest; whether the MODEL recalls after compaction is measured by nothing.
**Evidence:** selftest pin exists (batch a). A probe needs a session that compacts on demand; `claude -p` has no compaction trigger and `/compact` is interactive-only. `claude plugin eval` (early access) may drive sessions, unverified.
**Control case:** none can be named — that is the finding.
**Classification:** `measurement` — the reflex resisted is writing probe prompts that nothing can run and calling them a harness.
**Route:** inbox — re-check `claude plugin eval` capabilities on 2026-10-01; until a run can compact on demand, the deterministic SessionStart pin is the whole honest measurement.
**Not a code problem:** all of it.

## 4. Adoption runbook — `plan §adoption runbook`

**Observed:** a project that already carries its own hook copies double-fires with the plugin's (seen in this session: two doctrine blocks per prompt); nothing in the repo tells an adopter the order of operations.
**Evidence:** the duplicate doctrine blocks in the transcript; `docs/` holds only GETTING-STARTED and MUTATION_POLICY.
**Control case:** a fresh project (no hooks of its own) must still be served by GETTING-STARTED alone — the runbook is for the second case only.
**Classification:** `process` — mechanics, not behaviour. **Route:** runbook, `docs/ADOPTION.md`, written this tick.
**Not a code problem:** `doctor` already reports DUPLICATE (batch a); the runbook cites it rather than re-implementing it.

## 5. Linux path — `docs/GETTING-STARTED.md §linux`

**Observed:** `tests/selftest.sh` passed on `node:22-bookworm` (Python 3.11.2, Node 22, bash 5.2.15) on a remote docker host today; the guide says nothing about OS support and no CI runs the suite on any OS (`.github/workflows` absent).
**Evidence:** the container run's `ALL PASS` line with `selftest-rc=0`; `ls .github/workflows` → absent. Could it have failed? Yes — the run reads the script's own exit code, not a `tail`.
**Control case:** the macOS run (bash 3.2) must stay green; CI runs Linux only and must not be read as covering the bash-3.2 path.
**Classification:** `process` — a claim with no gate behind it. **Route:** a line in GETTING-STARTED plus `.github/workflows/selftest.yml` on ubuntu-latest, written this tick.
**Not a code problem:** `install.sh`'s Linux branch is still unexercised; the CI job runs the selftest, not the installer, and the guide says so.
