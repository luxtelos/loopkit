# Pairwise judge verdicts — `judge.py`

- Row: `plan §0.3 judge-runner` · Assessment: `state/2026-09-06-plan-remainder-assessment.md §1`
- Outcome: when a review must choose between two candidates, the position-swapped
  pairwise discipline in `skills/acceptance-review/references/judge.md` runs as a
  script with a deterministic verdict rule, so no agent hand-rolls it.

## Scope

In: one script, `plugins/loopkit/scripts/judge.py`, driving `claude -p` twice per
criterion; the verdict rule; the output shape; a ticks event. Out: the judge
prompt text (stays in `judge.md`, the script reads it); any scoring scale beyond
`held | not held | unverifiable`; retries.

## Constraints

stdlib only; `claude` invoked as a subprocess with `--output-format json`; the
selftest drives it with a stub `claude` on PATH exactly as `fanout.sh` is driven.

## Acceptance (EARS)

1. WHEN invoked as `judge.py --criterion "<EARS line>" --a <file> --b <file>`,
   the script SHALL call `claude -p` exactly twice — once with A in position 1,
   once with B in position 1 — and print one block in the output shape of
   `judge.md` (`CRITERION / A / B / JUSTIFICATION / VERDICT (A-vs-B) / VERDICT (B-vs-A) / FINAL`).
   Check: stub `claude` counts its invocations to a file → 2.
2. IF the two verdicts disagree, THEN `FINAL` SHALL be `TIE confidence 0.5`.
   Check: a stub that always prefers position 1 → `FINAL: TIE confidence 0.5`.
3. IF the two verdicts agree, THEN `FINAL` SHALL be that verdict with the
   judge's confidence clamped to `[0.5, 1.0]`. Check: a stub that always answers
   `B` → `FINAL: B`; a stub reporting confidence 1.7 → `1.0`.
4. WHEN `--judge <name>` equals `--generator <name>`, the script SHALL exit 3
   and print no `FINAL` line (judge ≠ generator). Check: exit code + grep.
5. The prompt sent to `claude` SHALL contain the six rules of `judge.md`
   verbatim, and the script source SHALL NOT contain them. Check: stub writes
   its stdin to a file; `grep -c "do not prefer the longer"` → 1 in the capture,
   0 in `judge.py`.
6. WHEN `--criterion` is given N times, the script SHALL print N blocks and make
   2N `claude` calls. Check: N=2 → 4 invocations.
7. WHEN a run completes, the script SHALL append one `judge` event to
   `state/ticks.jsonl` carrying `final` and `confidence`. Check: `ticks.py` count.
8. Justification SHALL precede both verdict lines in every block. Check: line order.

## Edge cases

- Boundary: an empty candidate file → exit 2, no `FINAL` line. One criterion
  is the minimum; there is no maximum (N blocks).
- Error: `claude` absent from PATH, or exiting non-zero, or returning JSON
  without a verdict → exit 2, no `FINAL` line, the raw response saved under
  `state/judge/<ts>-<pid>.json` for inspection. Malformed `--a`/`--b` path → exit 2.
- Concurrency: two runs at once write distinct `state/judge/<ts>-<pid>.json`
  files and append to the ticks ledger with single-line `O_APPEND` writes; no
  shared mutable file. Retry after partial success: not applicable — no retries.

## Control case

`fanout.sh` with the stub `claude` (selftest batch d) stays green, and the
selftest's total runtime does not grow by more than the two stub calls per pin.
