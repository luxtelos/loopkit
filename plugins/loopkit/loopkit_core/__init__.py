"""loopkit_core — the portable half of LoopKit.

Nothing in this package may import Claude Code, read a hook payload, shell out
to the `claude` CLI or resolve a plugin-cache path. That is the whole point of
the package existing: `specs/loopkit-runtime.md` §Constraints says the core is
stdlib-only and its decisions are reproducible from `(queue, policy, journal)`
alone, so a second implementation in another language can be held to the same
`spec/fixtures/*.json`.

M2 part A moved these modules here unchanged except for imports:

  triage_state   the Queue over state/triage.md
  ticks          the Event journal over state/ticks.jsonl
  loop_next_pick the Counts + fan-out lookup
  inbox_to_triage the inbox -> Queue bridge
  loop_metrics   the ledger metrics (was scripts/loop-metrics.py; a dash is
                 not a legal identifier, so the module name gained an
                 underscore while the script path kept its dash)
  decide         NEW: the Stage precedence and the CONTINUE/WAIT/IDLE split,
                 lifted out of scripts/loop-next.sh

Every one of them is still importable at its old `plugins/loopkit/scripts/`
path, which is a thin shim over this package, so hooks, loop-next.sh, the
suite and other projects keep working unchanged.
"""

__all__ = [
    "decide",
    "inbox_to_triage",
    "loop_metrics",
    "loop_next_pick",
    "ticks",
    "triage_state",
]
