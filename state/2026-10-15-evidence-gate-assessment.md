# Assessment — a fix was built and reviewed on a one-source cause (2026-10-15)

Row source: owner request 2026-10-15 §evidence-gate

## Observed

A loop classified a finding, wrote a fix and had it reviewed, and every step
rested on one source: a response-field name and a URL parameter read as proof
that "a user has a pending invite". Nothing in LoopKit asked which system holds
the truth about an invite, and nothing refused the transition to `spec-draft`
or `pr-open` when only one had been read.

## Evidence

- **The incident, as recorded by the operator.** Three later reads — the
  identity provider's user record, the user's role rows, the profile row —
  showed no invite at all, only a stale profile flag that the response field
  and the URL parameter were both copied from. Merged alone, the fix would have
  turned a harmless redirect loop into a consent prompt that deletes data. The
  loop's own note that a plain sign-in did not loop in its reproduction was
  already on the page and was never reconciled.
- **The tool, read at `origin/main` (`77203a7`, plugin 0.2.4).**
  `loopkit_core/triage_state.py` gates exactly one transition:
  `GATED_STATUSES = {"pr-open"}`, and `check_gate` reads only verdict events
  from `state/ticks.jsonl`. A row moves `new → spec-draft → spec-ready` with no
  check of any kind. Reproduced in a scratch project: `update --status
  spec-draft` on a row with no assessment file exits 0.
- **The skill.** `skills/loop-assess/SKILL.md` A1 asks "what is observably
  true, what did I run, could it have failed". It never asks *which system of
  record* the claim lives in, or how many of them were read. The A3 template has
  no sources section and no place for an observation that does not fit.
- **The rules existed.** The operator's memory already carried two rules of the
  form "read several sources before a claim about live state". They were
  context, loaded and not enforced, and they lost to speed.

Could the check have failed? Yes: the same scratch reproduction with a row
moved to `pr-open` and no verdict is refused (exit 2), which proves the scratch
project exercises the real gate and that the gate simply does not look at
evidence.

## Control case

A finding with **no live-state claim** — a typo in a doc, a refactor, a lint
rule — must still move through the pipeline without inventing reads it does not
need. The gate must accept an explicit `Live state: none — <why>` and must not
fire on `fixing`, `blocked`, `inbox` or `done`. And the existing verdict gate on
`pr-open` must behave exactly as before: no verdict, refused; PASS from a
different agent, allowed; `--override-verdict` with a reason, allowed and
recorded.

## Sources

| class    | system of record | evidence |
|----------|------------------|----------|
| identity | identity provider | n/a: no claim here is about a user, role or tenant of this repo |
| data     | database          | n/a: LoopKit keeps no database; its queue is a markdown file covered by `code` |
| runtime  | logs / analytics  | n/a: the claim is about what a CLI accepts, reproduced directly, not about production behaviour |
| code     | code graph + git  | read: `triage_state.py` gate and `loop-assess` A1/A3 at `origin/main` 77203a7; scratch reproduction of `update --status spec-draft` exit 0 |
| memory   | memory            | read: the operator's memory notes on the incident and on the two earlier "several sources" rules, 2026-10-15 |
| tracker  | tracker           | read: open PRs and issues on the repository, 2026-10-15 — no issue covers this; PR #58 and PR #61 overlap files and version |

Tools reachable this session (probed by hand; the snapshot script is what this
finding builds): `git` answering, `gh` answering and authenticated, `python3`
answering. MCP servers: several configured; which are connected is visible to
the session, not to a script — reported `unknown` by `source-snapshot.py`.

## Does not fit

- PR #58 already adds a "precedent searched" gate for decisions routed to the
  inbox, which looks like the same idea.
  explained: it gates a *question to a human* (was the ruling already made?),
  not a *claim about the system*. The two compose; neither covers the other.
- The operator's rules said "check several sources" and the loop still built
  on one.
  explained: that is the finding itself — a rule held as context is not a gate.
  It is the reason this lands in `triage_state.py`, not in another paragraph.

## Classification

`tool` + `process`, routed as code. The reflex resisted is a third paragraph in
`loop-assess` saying "check several sources" — that exists already, in spirit,
and lost. A derived check (how many independent systems did this assessment
read?) belongs in a deterministic function, and the place every pipeline
transition already converges is `triage_state.py`. Not `decision`: the owner
asked for the gate in plain words on 2026-10-15. Not `prompt`: the instruction
is fine; its enforcement is missing.

## Route

`spec-writer` (draft at `state/2026-10-15-spec-draft-evidence-gate.md`, since
`specs/` is governance-guarded and the owner ratifies wording) → ADR 0008 →
implementer on `feat/evidence-gate` → reviewer.

## Not a code problem

- **Whether a reviewer believes a `read:` line** is not something a parser can
  decide. The gate makes the agent *name* two systems of record; it cannot
  prove the agent read them. That stays the reviewer's job and the ADR says so.
- **Whether the gate may stop a hotfix** beyond the reasoned override is a
  product decision, not a code one. Escalated in the PR body with both options
  costed.
