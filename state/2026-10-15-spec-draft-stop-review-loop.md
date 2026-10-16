# DRAFT spec — the Stop review never loops on a waiting session

> DRAFT, not ratified. `specs/` is governance-protected; this text moves there
> only after the owner reads it. Until then it is the acceptance list the
> implementation and its pins are measured against.

- Assessment: `state/2026-10-15-stop-hook-loop-assessment.md` · Owner: "proceed" (2026-10-15)
- Outcome: the Stop review decides deterministically, blocks at most once per
  chain, lets a session that is waiting on background work pause, accepts the
  main session's evidence for a turn that changed no code, and judges only the
  turn's own changes.

## Scope

In: a command-type Stop hook `hooks/stop_review.py` that replaces the
agent-type Stop hook; a UserPromptSubmit hook `hooks/turn_start.py` that
records where the turn began; the evidence file `.loopkit/turn-evidence.json`
and its writer (`stop_review.py write-evidence`). Out: `hooks/stop_gate.sh`
(the test/lint gate) — unchanged; SubagentStop; `session_crons`.

## Constraints

The decision is a pure function of the Stop payload plus facts read from git
and two files. No model call decides whether a stop is blocked. Fail-open: an
unreadable payload or an internal error allows the stop and says so.

## Acceptance (EARS)

1. WHEN the Stop payload has `stop_hook_active: true`, the hook SHALL allow the
   stop whatever the working tree holds and whatever the judge says. IF code
   changed this turn, THEN the judge MAY run and its verdict SHALL be recorded
   (ticks ledger and output), never enforced. (Owner decision 2026-10-16.)
2. WHEN `background_tasks` lists at least one task not in a terminal state AND
   `last_assistant_message` contains `LOOPKIT:WAITING`, the hook SHALL allow the
   stop and name the tasks it is waiting on.
3. IF `background_tasks` lists running tasks but the message carries no
   `LOOPKIT:WAITING`, or the message carries it but no task is in flight, THEN
   the waiting pass SHALL NOT apply (the rest of the ladder decides).
4. WHEN a turn record exists for this session and root, the hook SHALL count as
   changed only files committed since the recorded HEAD, or dirty files that
   were clean at turn start or whose content changed since. Files dirty and
   unchanged since turn start, and untracked nested repositories or worktrees,
   SHALL NOT count.
5. WHEN no code file changed this turn, the hook SHALL allow the stop.
6. WHEN no turn record exists and the working tree holds code changes, the hook
   SHALL accept `.loopkit/turn-evidence.json` with `kind: no-code-change` only
   if its `head` equals the current HEAD, its `written_at` is no older than
   `LOOPKIT_EVIDENCE_MAX_AGE` seconds (default 900) and not in the future, its
   `command` is non-empty and its `changed_files` holds no code file.
7. IF the evidence file is stale (HEAD or timestamp does not match), malformed,
   or lists a code file, THEN the hook SHALL refuse and say why.
8. WHEN a turn record exists and git shows code changed this turn, the hook
   SHALL NOT let an evidence file override that measurement.
9. WHEN code changed this turn and none of 1–8 allowed the stop, the hook SHALL
   run the judge: `LOOP_STOP_REVIEW_CMD` if set (exit 0 = PASS), otherwise the
   model judge (on by default; owner decision 2026-10-16) with no Bash and
   hooks off, handed the turn's code files and the evidence file. PASS SHALL
   allow; REJECT SHALL block once; no verdict SHALL block once with a reason
   that lists the turn's code files and asks for the `loopkit:reviewer` agent.
9a. WHILE `LOOPKIT_STOP_REVIEW_NESTED=1` (inside the judge's own session), both
   Stop hooks SHALL stand down.
10. The control: no background work, no earlier block, real code changed this
    turn, no evidence → refused.

## Edge cases

- `background_tasks` absent (older Claude Code): no waiting pass; documented.
- Detached HEAD, empty repo: HEAD is read with `rev-parse`; an unborn HEAD
  makes evidence unverifiable, so it is refused.
- Turn record from another session or another worktree: keyed by session and
  root, never read across.
