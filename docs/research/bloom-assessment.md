# Can Bloom be plugged into LoopKit? — assessment (2026-09-06)

Sources read today: anthropic.com/research/bloom, github.com/safety-research/bloom
(README), alignment.anthropic.com/2025/bloom-auto-evals/ (technical report).

## Observed

Bloom is an MIT-licensed, pip-installable framework (`bloom init`, `bloom run
bloom-data`, per-stage `bloom understanding|ideation|rollout|judgment`) that
takes a **behaviour description** plus optional example transcripts
(`bloom-data/seed.yaml`: `behavior.name`, `behavior.examples`,
`ideation.num_scenarios`, `ideation.variation_dimensions`, `rollout.target`
as a LiteLLM model id, `rollout.modality: conversation|simenv`), generates
scenarios, rolls them out with an agent that simulates the user and any tool
responses, and has a **separate judge model** score each transcript 1–10 for
"behavior presence" (plus elicitation difficulty, unrealism, evaluation
validity, evaluation awareness); a meta-judge writes the suite report. Outputs
land in `bloom-results/<behavior>/` with an elicitation rate (share of
rollouts ≥ a threshold, typically 7/10) and an average presence score. Resume
is supported; W&B is optional.

Judge–human agreement reported: Spearman 0.86 (Claude Opus 4.1) and 0.75
(Sonnet 4.5) on 40 hand-labelled transcripts across 12 behaviours. Stated
caveats: contamination ("bespoke evaluations also risk losing validity through
training set contamination"), rising evaluation awareness in newer models,
and absolute metrics that "change with configuration choices" while rankings
hold.

## Evidence

Three fetches of the pages named above, read in full; nothing here is from
memory. What could not be verified: whether Bloom can drive **Claude Code with
LoopKit's hooks attached** as the target. The README specifies targets as
model ids through LiteLLM and simulates tools itself (`simenv`); it does not
drive a harness. So Bloom measures a **model's propensity** under generated
pressure, not a **hook's effectiveness**. Those are different instruments.

## Control case

The instrument must be able to say "clean". Before trusting any elicitation
rate, run a behaviour known to be absent (a nonsense propensity, or one the
target provably cannot exhibit in the modality) and confirm a rate near zero,
and run a behaviour known to be present and confirm it is found. loop-assess
exists because an audit tool once returned the same count on a clean file as
on a dirty one; Bloom is an audit tool.

## Classification

`architecture` — a new evaluation stage, not code in the pipeline. Bloom is
Generate → Evaluate for **agent behaviour**; LoopKit's stop gate is
Generate → Evaluate for **code**. They do not overlap and they measure
different things. The reflex resisted: wiring Bloom into the Stop hook. Each
Bloom run is dozens of Opus-class rollouts; a gate that costs that much gets
switched off, and a gate that samples a model's propensity does not verify
this turn's code.

## Route — what "plugged in" would honestly mean

Bloom plugs in as **the instrument for the behaviours LoopKit's practices
exist to prevent**, measured on the model, alongside the ledger that measures
outcomes:

| LoopKit practice (hook / rule) | Bloom behaviour to specify | What a rising elicitation rate would mean |
| --- | --- | --- |
| stop gate: "show evidence, not assertion" | claims a task is done without having run a check | the model's propensity to assert; the gate catches it, Bloom sizes it |
| protect_tests | removes or weakens a failing test to make a run pass | same |
| block_dangerous (merge/approve) | approves or merges its own work when nudged | same |
| require_recall / the knowledge layer | re-asks or overrides a ruling already recorded in context | the re-ask metric's model-side twin |
| block_dangerous BLOCKED message | rephrases a refused command around the guard | the "do not rephrase around the guard" line, measured |
| doctrine rule 4 | schedules a wait when the next step is its own | sleeping on actionable work, measured |

Concretely, a **`bloom` profile** (templates and checks, like the commerce
profile): `templates/profiles/bloom/behaviors/*.yaml` — one seed per row
above, with example transcripts marked synthetic until real ones are
anonymised from `state/`; `scripts/check-bloom.py` reading
`bloom-results/<behavior>/` and appending a `bloom` event (behaviour,
elicitation rate, n, judge model, target model) to `state/ticks.jsonl` so
`loop-metrics.py` reports propensity beside verified success; and a doc line
in `commands/doctor.md`. Judge ≠ target is required in the seed (Bloom lets
each stage name its model; `judge.md`'s rule applies).

**Not a gate.** A scheduled run (weekly, or per release), read the way the
metrics are read: with `n=`, never as a verdict on a turn.

## Not a code problem

- **Decision (owner):** whether to spend on it at all. Cost of yes: API spend
  for rollouts and judging (dozens of Opus-class calls per behaviour per run)
  and an hour to write six seeds. Cost of no: LoopKit keeps measuring
  outcomes (verified success, re-asks) with no measure of the propensities the
  hooks suppress — the thesis stays testable, but only on the outcome side.
- **Measurement caveat to carry:** 0.86 on 40 transcripts is the published
  figure; LoopKit's behaviours are not among the 12 it was measured on. Run
  the control case first, every time.
- **Contamination:** Bloom-generated scenarios that reach training data lose
  validity; keep seeds and results out of anything that ships or publishes.
- **Nothing here changes a hook.** If a Bloom run shows a propensity the hooks
  do not cover, that becomes a `new` triage row and goes through loop-assess
  like anything else.

## Verdict

Yes — as an instrument next to the loop, not a stage inside it. The plug-in
shape is a profile plus one script that feeds the ledger; the prerequisite
is an owner decision on spend and a control-case run that proves the
instrument can say "clean".
