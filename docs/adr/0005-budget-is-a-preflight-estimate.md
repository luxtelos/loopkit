# 0005 — The budget is a pre-flight estimate, not a reported count

Status: accepted (owner ruling, 2026-09-07)

## Context

`specs/loopkit-runtime.md` criterion 21 already required the budget to be
checked **before** each Provider call, never after. What it left open was the
*unit*: Provider calls, tokens reported in `usage`, or wall-clock seconds.

Criterion 23 pushed the answer toward reported tokens, requiring a Provider to
return `usage` with `0` — "never `null`, never a missing key" — when the
upstream API reports no count.

That is unsafe, and the repository already contained the proof. `inbox/needs-human.md`
reads a provider reporting `0` as having an **unlimited budget**. So under
criterion 23 a real zero and a fabricated zero are the same value with opposite
meanings: a Provider that cannot count reports `0`, the running total never
grows, the limit never trips, and an unattended loop keeps calling a paid API
while reporting itself comfortably inside budget.

`loop_metrics.py` had already reached the opposite conclusion independently — it
returns `None`, not `0`, when there is nothing to count. On this question the
specification was the outlier, not the code.

## Decision

**The budget is spent against an estimate we compute ourselves before the call,
not against a number the Provider hands back afterwards.**

1. Before each Provider call, estimate the request's tokens. Round **up**: the
   figure is a ceiling, never optimistic.
2. If `spent + estimate` would breach the limit, **the call is not made.**
   Journal it, warn, and stop.
3. Stopping asks a human: raise the limit and resume, or end the Run.
4. Where a Provider *does* report `usage`, record estimate against actual. That
   is reconciliation — it tells us how far off the ceiling runs — and it is not
   what the budget is spent against.

**Estimation stays in the standard library for now** (owner ruling): a
dependency-free approximation, accepted as over-cautious. Accurate tokenisers
(`tiktoken`, a vendor counting endpoint) become permissible at the **SDK half of
the roadmap — M5 onward** — where a package ships a dependency manifest and a
dependency is ordinary. The plugin must install anywhere; that constraint is
worth more than 10–20% of estimation accuracy.

## Consequences

- **A Provider that cannot report usage no longer breaks the budget.** It costs
  reconciliation accuracy, nothing more. This is the point of the decision.
- **The failure mode inverts, in the safe direction.** A ceiling that is too
  high stops a Run early. Annoying; recoverable by raising the limit. The
  alternative — a budget that silently never trips — is not recoverable, because
  the money is already spent.
- **Criterion 21's unit is settled:** estimated tokens, checked pre-call.
- **Criterion 23 stops being load-bearing.** `usage` must still be explicit
  rather than a fabricated `0`, or reconciliation compares against a lie — but
  nothing dangerous now rides on it.
- **A cost is accepted:** stdlib estimation over-estimates, so some Runs stop
  before they truly needed to. Deliberate, and revisited at M5.

## Alternatives rejected

- **Spend against the Provider's reported count.** Rejected: it trusts a number
  the Provider may be unable to produce, and the unsafe failure is silent.
- **Take a tokeniser dependency now.** Rejected by the owner: LoopKit installs
  anywhere today, and that property outranks estimation accuracy until the SDK
  milestone, where dependencies are normal.
- **Count Provider calls instead of tokens.** Rejected: one call can cost a
  hundred times another, so a call budget does not bound spend.
