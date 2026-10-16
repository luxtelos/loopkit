# Assessment — the Stop review hook loops while the session waits (2026-10-15)

Mode A. Owner instruction: "proceed" (2026-10-15), in reply to building this fix.

## Observed

In a real session the main agent was correctly waiting on background agents.
The plugin's agent-type Stop hook blocked the stop more than 30 times. Each
block forced another reply, each reply fired the hook again, and Claude Code
finally ended the turn itself:

> A hook blocked the turn from ending 9 consecutive times — overriding and
> ending turn. For Stop/SubagentStop hooks, check stop_hook_active in the input
> and return success while it's true.

Two more symptoms in the same session: the checker graded untracked files in
the session's working directory that belonged to other work, and when its own
Bash or memory tools were denied it could never return PASS.

## Evidence

1. `grep -c 'stop_hook_active\|background_tasks' plugins/loopkit/hooks/hooks.json
   plugins/loopkit/hooks/stop_gate.sh` on `origin/main` (`ba7cf21`) prints `0`
   for both. Nothing in the Stop wiring reads the two fields that tell a hook
   "you already blocked once" and "the session is waiting on background work".
   This check could have failed: either word anywhere in the prompt or the
   script would have counted.
2. The agent hook's first instruction is to run
   `git diff --name-only HEAD` and `git ls-files --others --exclude-standard`
   over the whole working tree. Untracked files from before the turn, and an
   untracked nested worktree, are in that list by construction. Nothing records
   where the turn began, so there is no way for it to tell "this turn" apart.
3. Claude Code hooks reference (code.claude.com/docs/en/hooks), read today:
   - Stop input carries `stop_hook_active`, `last_assistant_message`,
     `background_tasks` and `session_crons`. `background_tasks` exists so hooks
     can tell "session is done" apart from "session is paused waiting for
     background work to wake it back up".
   - "All matching hooks run in parallel." A command hook placed before the
     agent hook cannot stop the agent hook from blocking. Any block wins.
   - Agent hooks receive the input through `$ARGUMENTS`, so the model *can* see
     `stop_hook_active`, but acting on it is the model's judgement, and the docs
     mark agent hooks experimental: "For production workflows, prefer command
     hooks."
   - Claude Code caps consecutive Stop continuations at 8, then overrides.
4. Whether the agent's Bash is allowed depends on the session's permission
   settings, so "run git first" is an instruction that can be impossible to
   follow. No fallback exists in the prompt.

## Control case

A turn that changed real code, with no background work in flight and no
earlier block in this chain, must still be refused until it is reviewed. A fix
that lets every stop through ends the loop by deleting the gate. The second
control: a session that has background tasks running but did not say it is
waiting (for example a perpetual log monitor) must not get a free pass.

## Classification

`tool`, with the code change landing in a hook. The reflex resisted is
`prompt`: adding "check stop_hook_active first" to the agent's prompt. That is
a derived value the prompt should never compute — the field is a boolean in the
payload, and a model reading it is a model that can misread it. Because hooks
run in parallel, the only way to make the decision deterministic is to take it
away from the model entirely: a command hook decides, and the model judgement
moves to a reviewer the session dispatches (or an in-hook command the project
configures).

## Route

hook + spec: `state/2026-10-15-spec-draft-stop-review-loop.md` (EARS draft;
`specs/` is governance-protected and takes the text after the owner reads it), then
`plugins/loopkit/hooks/stop_review.py` (replaces the agent-type Stop hook) and
`plugins/loopkit/hooks/turn_start.py` (records where the turn began), pinned in
`tests/pins/stop-review-loop.sh` with a mutation harness.

## Not a code problem

- `hooks/stop_gate.sh` (the test/lint gate) does not honour
  `stop_hook_active` either. It is left alone on purpose: a red test suite
  should keep blocking, and its consecutive-block counter already warns at 7.
  Its blocks also reset Claude Code's cap whenever the session calls a tool to
  fix the failure, which a waiting session does not do. If it is ever seen
  looping on a waiting session, give it the same waiting pass; that is an owner
  decision about the test gate, not part of this fix.
- RULED 2026-10-16 (owner): the model judge stays ON by default and may block
  only once per stop chain. Implemented by running the judge from the command
  hook. The original open question was whether the default "block once, then dispatch the reviewer" is acceptable in
  place of an in-hook model judge is a product decision. The fix keeps an
  in-hook judge possible through `LOOP_STOP_REVIEW_CMD`; the PR names the
  trade-off for the owner.
