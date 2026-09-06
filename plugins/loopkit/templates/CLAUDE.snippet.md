
<!-- loopkit:begin — managed by /loopkit:init; edit freely, the marker is only used to avoid a second copy -->

## LoopKit standing rules

- The loop is driven by state on disk, not by the chat. `state/triage.md` is
  the queue, `specs/` is the source of truth, `inbox/needs-human.md` is the
  door. Write findings to `state/`, not to the conversation.
- NEVER merge. NEVER approve your own work. NEVER delete history. NEVER stage
  everything — name the files. (All four are refused by the plugin's
  `block_dangerous.py`; the rule stands whether or not the hook catches a new
  spelling.)
- Which stage is next is a lookup: `loop-next.sh`, never a judgement. Advance
  exactly one stage per tick, for every row at it, record each transition keyed
  on `--source`, then stop.
- "Zero regressions" means `test-regressions.sh` passes against the committed
  baseline. Run tests AND lint before claiming done; "runs" is not "right".
- Anything you are less than confident about goes to `inbox/needs-human.md`
  with the whole context and the cost of both options — never into a PR.
- Every owner correction lands as a durable artifact (rule, hook pattern,
  script, runbook, memory) in the same session. Signs, not chats.
- Read `FILES.md`, `TOOLS.md` and `COMMANDS.md` before any work; a gate blocks
  work tools until you do.

<!-- loopkit:end -->
