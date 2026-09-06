# Where LoopKit sits on the loop ladder

The Claude Code team's taxonomy sorts loops by what you hand over. Four rungs,
each nesting inside the next: turn-based hands off **the check**, goal-based
hands off **the stop condition**, time-based hands off **the trigger**,
proactive hands off **the prompt**. A proactive routine is a schedule wrapped
around a goal wrapped around a check, so the innermost one decides whether the
outer ones are worth anything.

This file records where this project actually stands, with the artefact that
proves each claim. Re-check it at each release; a rung claimed but not built is
the same failure mode as a gate that cannot fail.

| Rung | Built here? | The artefact | The gap |
| --- | --- | --- | --- |
| 1. Turn-based — the check | **Yes, and it is the whole thesis.** | `hooks/stop_gate.sh` runs the project's own test / lint / build commands from `.loopkit/config.env` and diffs the result against `state/known-test-failures.txt`; `skills/acceptance-review` + `references/judge.md`; a `reviewer` agent whose instructions differ from the `implementer`'s; `hooks/protect_tests.py` refuses a diff that deletes tests. | None. This is where the value is. |
| 2. Goal-based — the stop condition | **Structurally yes, per-run no.** | The Stop event carries two hooks: the deterministic gate above, and an `agent`-type evaluator that reads the diff and judges before the session may end. Acceptance is the EARS lines in `specs/<slug>.md`, which is exactly the "deterministic criteria beat vibes" rule. A consecutive-block counter warns before the runtime's own 8-block override. | The stop condition is fixed by the repo, not supplied per invocation. There is no `/goal`-shaped entry point where a caller states "this run ends when X". |
| 3. Time-based — the trigger | **Deliberately not shipped.** | `templates/COMMANDS.md` says the loop is human-pulled, not timer-pushed, and points at cron for `morning-triage.sh` only. `scripts/loop-next.sh` ends with `NEXT: CONTINUE \| WAIT \| IDLE`, which tells a caller whether a timer is even warranted — a wakeup only when something outside the loop must move first. | The trigger belongs to the host today. Nothing in the plugin owns a schedule, and a timer over a loop with no durable journal just repeats work after a crash. |
| 4. Proactive — the prompt | **No.** | — | Needs rung 3 plus a run that survives its own machine. |

## Why rungs 3 and 4 wait on the runtime, not on a timer

A cron line is an afternoon's work. What is missing is everything a trigger
implies once nobody is watching: a run identity, a journal written before each
side effect, a replay that does not call the model twice for a step already
recorded, and a store that two runners cannot corrupt. That is precisely M2 of
`docs/research/runtime-plan.md` — Provider, Store, Runner. Rung 3 without it
produces work faster than anyone can check it, which is the taxonomy's own
warning read from the other end.

The order therefore stands: finish the check (done), make the stop condition
addressable per run (M1's `Run` noun carries its own acceptance), then the
journal (M2), and only then the trigger.

## The two disciplines that apply at every rung

Both are already load-bearing here, which is why they are named rather than
aspired to.

- **A second agent with fresh context reviews.** The constitution forbids the
  agent that wrote code from approving it, and `hooks/block_dangerous.py`
  refuses merge and approve for every agent. On 2026-09-07 that separation paid
  for itself: a reviewer failed a pull request with three findings the author's
  own pins had passed, including a hook that silently dropped fields from every
  call it rewrote.
- **Watch the meter.** `scripts/run-capped.sh` and the offload hook keep large
  tool output out of the window; `hooks/count_approvals.py` counts approvals
  per session and warns past a threshold, because an approval nobody reads is
  not oversight.
