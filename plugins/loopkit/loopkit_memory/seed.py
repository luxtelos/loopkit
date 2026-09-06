"""seed — give a fresh project its first knowledge: the loop's own doctrine.

`memory.py knowledge init` enables the knowledge adapter, creates the bundle
and mailbox, enqueues the concepts below (each citing the project's own copy of
constitution.md and the contracts, so digests are captured against files that
exist), drains, and reindexes. Idempotent: the actor's idempotency keys make a
second run a no-op.

Every Gate and Invariant here carries `enforced_by`, so rulings-compile.py can
prove from day one that a ruling has a gate.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import load

CONCEPTS: list[dict] = [
    {"target": "/loop/separation-of-powers.md", "type": "Doctrine", "title": "The agent that wrote it never approves it",
     "description": "A different agent, with different instructions, judges; a fresh model decides done against an explicit stop condition; the evaluator assumes broken until proven otherwise.",
     "tags": ["domain/loop", "origin/constitution"], "sources": ["constitution.md", "COMMANDS.md"],
     "body": "## The claim\n\n> The system SHALL use a different agent to evaluate code than the one that generated it. The system SHALL decide \"done\" with a fresh model judging an explicit stop condition. The evaluator SHALL default to doubt.\n\n## Why\n\nSelf-enhancement bias is a measured effect, not a character flaw. Separating the generator from the judge is the cheapest control that survives pressure.\n\n## Enforced by\n\nThe implementer and reviewer agents carry different instructions; `block_dangerous.py` refuses `gh pr merge` and `gh pr review --approve` for every agent.\n"},
    {"target": "/loop/never-merge-never-approve.md", "type": "Invariant", "title": "The loop never merges and never approves",
     "description": "Merge and approve are human acts; every agent is refused them at the hook layer.",
     "tags": ["domain/loop", "origin/constitution"], "sources": ["constitution.md"],
     "enforced_by": [{"hook": "block_dangerous.py"}],
     "body": "## The claim\n\n> The system SHALL open pull requests but SHALL NEVER auto-merge them, and SHALL NEVER approve its own work.\n\n## Why\n\nA rule stated in four files and enforced in none is the weakest kind of rule. The hook makes this one as real as the destructive-command floor.\n\n## Enforced by\n\n`block_dangerous.py`: patterns `gh-pr-merge`, `gh-pr-approve`, `gh-api-merge`.\n"},
    {"target": "/loop/human-authority-and-the-open-door.md", "type": "Doctrine", "title": "Anything uncertain goes through the door, with both options costed",
     "description": "Findings with no reproduction, several live root causes, no owning module, or an unruled product decision route to inbox/needs-human.md, carrying the whole context and the cost of each option.",
     "tags": ["domain/loop", "origin/constitution"], "sources": ["constitution.md", "FILES.md"],
     "body": "## The claim\n\n> WHEN a finding has no reproduction steps, more than one plausible root cause, no owning module, or would encode a decision nobody has ruled on, the system SHALL route it to inbox/ and SHALL NOT open a PR. WHEN the system escalates, it SHALL carry the whole context and the cost of each option.\n\n## Why\n\nAn approval button is not oversight. A decision the human can make from the escalation alone, without a lookup, is the only kind that stays a decision when the human is tired.\n\n## Enforced by\n\nThe loop-assess classifier (`decision` layer) and the inbox template; `blocked` rows are counted and never polled.\n"},
    {"target": "/loop/determinism-boundary.md", "type": "Doctrine", "title": "Anything deterministic logic can decide is decided by a script",
     "description": "Which stage is next, which PRs are green, whether a citation still points where it claims: lookups live in scripts, never in a prompt.",
     "tags": ["domain/loop", "origin/constitution"], "sources": ["constitution.md", "COMMANDS.md"],
     "body": "## The claim\n\n> Anything deterministic logic can solve SHALL be solved by deterministic logic (hooks, scripts, gates), never handed to a probabilistic model.\n\n## Why\n\nA model re-deriving a lookup gets a different answer each time and nobody can check it. A real loop once spent forty ticks re-checking one PR while forty findings sat unclassified; `loop-next.sh` cannot drift.\n\n## Enforced by\n\n`loop-next.sh`, `loop-scan.py`, `loop-watch.sh`, `check-citations.py`.\n"},
    {"target": "/loop/spec-is-the-source-of-truth.md", "type": "Doctrine", "title": "The spec is the truth; code is its build output",
     "description": "specs/ holds EARS acceptance criteria; implementation waits for a validated spec; specs/ and constitution.md are guarded.",
     "tags": ["domain/loop", "origin/constitution"], "sources": ["constitution.md", "FILES.md"],
     "body": "## The claim\n\n> The spec in specs/ SHALL be the source of truth; code is its build output. The system SHALL NOT begin implementation until the relevant spec is validated.\n\n## Why\n\nAcceptance written after the code is written to pass. Each EARS line names the command that checks it, so the reviewer and the Stop hook grade against something that could fail.\n\n## Enforced by\n\n`protect_governance.py` guards `specs/` and `constitution.md` (`GOVERNANCE_EDIT_OK=1` is the visible door).\n"},
    {"target": "/loop/prove-claims-not-just-changes.md", "type": "Doctrine", "title": "Done means the command that proved it and the line that shows it",
     "description": "A green result from a check that could not have failed is not evidence; zero regressions means the baseline diff passed.",
     "tags": ["domain/loop", "origin/constitution"], "sources": ["constitution.md"],
     "body": "## The claim\n\n> Every \"done\" claim SHALL name the command that proved it and the line of output that shows it. \"Zero regressions\" SHALL mean the regression diff passes against the committed baseline.\n\n## Why\n\nGated suites, quoted globs and pipes through `tail` have each produced green while running nothing. The question that catches all of them: what would this look like if the code were broken?\n\n## Enforced by\n\n`stop_gate.sh` (the Stop hook) and `test-regressions.sh`.\n"},
    {"target": "/loop/the-stop-gate.md", "type": "Gate", "title": "The stop gate runs the tests before done is allowed",
     "description": "Precheck, regression diff, lint, typecheck, build; red rejects the stop.",
     "tags": ["domain/loop", "origin/constitution"], "sources": ["COMMANDS.md"],
     "enforced_by": [{"hook": "stop_gate.sh"}],
     "body": "## The claim\n\nA turn that changed code cannot end until the project's own checks pass. Every command is the project's (`.loopkit/config.env`); an explicitly empty command skips its step, so a gate that cannot pass is never quietly switched off.\n\n## Enforced by\n\n`stop_gate.sh`, wired as the Stop hook; verdicts land in `state/progress.md` and `state/ticks.jsonl`.\n"},
    {"target": "/loop/the-recall-gate.md", "type": "Gate", "title": "A storage invariant waits until memory was consulted",
     "description": "Migrations, uniqueness and key constraints in code, and shell writes into a migrations directory are blocked until a recall ran this session.",
     "tags": ["domain/loop", "origin/post-mortem"], "sources": ["FILES.md"],
     "enforced_by": [{"hook": "require_recall.py"}],
     "body": "## The claim\n\nA fact recorded in June cannot defend itself in July if nothing reads it. Before a write that creates a storage invariant, the loop must consult memory: the graph for every writer of the column, the notes for whether this has bitten before.\n\n## Enforced by\n\n`require_recall.py`, with rules in `.loopkit/recall-triggers.txt` and an audited outage escape for content-rule writes only.\n"},
    {"target": "/loop/signs-not-chats.md", "type": "Doctrine", "title": "Every human correction lands as an artifact in the same session",
     "description": "A ruling that stays in the chat is re-asked; it becomes a rule, a hook pattern, a script, a runbook, a test pin or a concept before the session ends.",
     "tags": ["domain/loop", "origin/constitution"], "sources": ["constitution.md"],
     "body": "## The claim\n\n> WHEN a human corrects the loop, the correction SHALL land as a durable artifact in the same session. Signs, not chats: kill the class of error, not the instance.\n\n## Why\n\nThe re-ask rate is the metric this loop is judged by. A ruling with an enforcing artifact (`enforced_by`) cannot be forgotten under pressure; one without is a reminder.\n\n## Enforced by\n\n`rulings-compile.py` reports every Invariant/Gate concept without an existing artifact.\n"},
    {"target": "/loop/memory-routing.md", "type": "Contract", "title": "Which memory holds what",
     "description": "state/ for the loop, inbox/ for humans, specs/ for truth, notes and the palace for facts, the bundle for rulings; derivable from code means query, never author.",
     "tags": ["domain/loop", "origin/contract"], "sources": ["FILES.md", "TOOLS.md"],
     "body": "## The claim\n\nA fact filed in the wrong layer is a fact lost. `FILES.md` is the routing table; `memory.py status` says which adapters are live and which are degraded.\n\n## Enforced by\n\n`require_contracts.py` blocks work tools until FILES.md, TOOLS.md and COMMANDS.md are read.\n"},
]


def seed(root: Path, *, by: str = "process:loopkit/seed", fmt: str = "concise") -> int:
    cfg_path = root / ".loopkit" / "memory.json"
    if not cfg_path.exists():
        print("knowledge init: no .loopkit/memory.json — run /loopkit:init first")
        return 2
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.setdefault("knowledge", {"adapter": "okf", "bundle": "knowledge"})["enabled"] = True
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    kn = load(root).get("knowledge")
    if not hasattr(kn, "enqueue"):
        print(f"knowledge init: adapter unavailable ({kn.available()[1]})")
        return 2
    kn.bundle.mkdir(parents=True, exist_ok=True)
    enq = 0
    for c in CONCEPTS:
        sources = [{"id": Path(s).stem, "resource": s, "title": s} for s in c["sources"] if (root / s).is_file()]
        fm = {"type": c["type"], "title": c["title"], "description": c["description"], "tags": c["tags"], "status": "draft"}
        if c.get("enforced_by"):
            fm["enforced_by"] = c["enforced_by"]
        rc, out = kn.enqueue(op="upsert", reason=f"seed: {c['title'][:150]}", by=by, target=c["target"],
                             payload={"frontmatter": fm, "sources": sources}, body=c["body"])
        if rc == 0:
            enq += 1
        else:
            print(f"  enqueue failed rc={rc} for {c['target']}: {out[:200]}")
    rc, _ = kn.drain()
    rc2, _ = kn.reindex()
    # a formatter must never touch the bundle or the mailbox
    ign = root / ".prettierignore"
    lines = ign.read_text(encoding="utf-8").splitlines() if ign.exists() else []
    added = []
    for entry in (f"{kn.bundle.relative_to(root)}/", f"{kn.mailbox.relative_to(root)}/"):
        if entry not in lines:
            lines.append(entry)
            added.append(entry)
    if added:
        ign.write_text("\n".join(lines) + "\n", encoding="utf-8")
    n = len(kn.concepts())
    print(f"KNOWLEDGE: enabled; enqueued {enq}/{len(CONCEPTS)}; drain rc={rc}; reindex rc={rc2}; "
          f"bundle now holds {n} concept(s) at {kn.bundle.relative_to(root)}/")
    if rc == 6:
        print("  something dead-lettered — see inbox/needs-human.md")
    if added:
        print(f"  .prettierignore: added {', '.join(added)}")
    print("  next: python3 <plugin>/scripts/rulings-compile.py   (which rulings have a gate)")
    return 0 if rc in (0, 6) else rc
