#!/usr/bin/env python3
"""pr-open-needs-a-verdict.py — the recorder enforces the rule, not just prose.

THE RULE: "a row reaches pr-open only on a recorded PASS from a different
agent." Until 2026-09-07 that sentence lived in loop_doctrine.py, in
loop-tick/SKILL.md, and nowhere that could stop anything. The 2026-09-07 review
put it plainly: `triage_state.py update --status pr-open` succeeded with no
verdict recorded anywhere, so the rule could be violated by the very tool that
records rows. Three pins asserted the TEXT was present, which a future agent
satisfies by paraphrase. None asserted the BEHAVIOUR.

This one runs the recorder. Every case below is a command someone would
actually type, and the case that matters most is the OVERRIDE: a gate with no
visible door is a gate people route around in the dark.

Run from the repo root."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

_ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=os.path.dirname(os.path.abspath(__file__)),
                       capture_output=True, text=True).stdout.strip()
assert _ROOT, "not inside a git checkout — cannot locate the repo root"
os.chdir(_ROOT)
CLI = "plugins/loopkit/scripts/triage_state.py"

HEADER = ("| finding | source | priority | spec | status |\n"
          "|---|---|---|---|---|\n")


def project(rows="| a thing | src/one | high |  | fixing |\n"):
    """A scratch project laid out the way a real one is: state/triage.md, so
    the ledger lands at state/ticks.jsonl exactly where the gate looks."""
    root = pathlib.Path(tempfile.mkdtemp())
    (root / "state").mkdir()
    (root / "state" / "triage.md").write_text("# triage\n\n" + HEADER + rows)
    return root


def run(root, *args, env=None):
    e = dict(os.environ)
    e.pop("LOOPKIT_AGENT", None)
    e.update(env or {})
    return subprocess.run([sys.executable, CLI, *args, "--state", str(root / "state" / "triage.md")],
                          capture_output=True, text=True, env=e)


def status_of(root, source="src/one"):
    r = run(root, "list", "--format", "json")
    for row in json.loads(r.stdout or "[]"):
        if row["source"] == source:
            return row["status"]
    return None


def ledger(root):
    p = root / "state" / "ticks.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


fails = 0


def check(label, condition, detail=""):
    global fails
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        fails += 1
        if detail:
            print(f"       {detail.strip()[:400]}")


# 1. THE DEFECT ITSELF. No verdict anywhere, straight to pr-open.
p = project()
r = run(p, "update", "--source", "src/one", "--status", "pr-open")
check("update --status pr-open is REFUSED when no verdict is recorded",
      r.returncode != 0, r.stdout + r.stderr)
check("...and the row did not move", status_of(p) == "fixing", f"status={status_of(p)}")
check("...and the refusal says how to record one",
      "verdict --state" in (r.stdout + r.stderr), r.stdout + r.stderr)
check("...and the refused transition left NO transition event behind",
      not [e for e in ledger(p) if e.get("event") == "transition"
           and e.get("status") == "pr-open"],
      json.dumps(ledger(p)))

# 2. `upsert` must not be the signposted detour around `update`.
p = project()
r = run(p, "upsert", "--source", "src/one", "--finding", "a thing",
        "--priority", "high", "--status", "pr-open")
check("upsert --status pr-open is refused too (a gate on one verb is not a gate)",
      r.returncode != 0, r.stdout + r.stderr)

# 3. A recorded PASS opens the door.
p = project()
r = run(p, "verdict", "--source", "src/one", "--result", "PASS",
        "--by", "reviewer", "--evidence", "24 pins green")
check("a verdict can be recorded", r.returncode == 0, r.stdout + r.stderr)
check("...and it lands in state/ticks.jsonl as a verdict event",
      any(e.get("event") == "verdict" and e.get("result") == "PASS" for e in ledger(p)),
      json.dumps(ledger(p)))
r = run(p, "update", "--source", "src/one", "--status", "pr-open")
check("with a recorded PASS, pr-open is ALLOWED", r.returncode == 0, r.stdout + r.stderr)
check("...and the row moved", status_of(p) == "pr-open", f"status={status_of(p)}")

# 4. A FAIL closes it, and a later FAIL closes it again. Latest wins, not "any
#    PASS in history" — otherwise one PASS props the door open forever.
p = project()
run(p, "verdict", "--source", "src/one", "--result", "FAIL", "--by", "reviewer")
r = run(p, "update", "--source", "src/one", "--status", "pr-open")
check("a recorded FAIL refuses pr-open", r.returncode != 0, r.stdout + r.stderr)
run(p, "verdict", "--source", "src/one", "--result", "PASS", "--by", "reviewer")
run(p, "verdict", "--source", "src/one", "--result", "FAIL", "--by", "reviewer")
r = run(p, "update", "--source", "src/one", "--status", "pr-open")
check("PASS then FAIL refuses — the LATEST verdict wins, not any in history",
      r.returncode != 0, r.stdout + r.stderr)

# 5. A verdict for a DIFFERENT row must not unlock this one.
p = project("| a thing | src/one | high |  | fixing |\n| other | src/two | low |  | fixing |\n")
run(p, "verdict", "--source", "src/two", "--result", "PASS", "--by", "reviewer")
r = run(p, "update", "--source", "src/one", "--status", "pr-open")
check("a PASS on another row does not unlock this one", r.returncode != 0, r.stdout + r.stderr)

# 6. Self-approval, when the agents are identified. This is the constitution's
#    oldest rule and the one the gate can only check when told who is acting.
p = project()
run(p, "update", "--source", "src/one", "--status", "fixing", env={"LOOPKIT_AGENT": "implementer"})
run(p, "verdict", "--source", "src/one", "--result", "PASS", "--by", "implementer")
r = run(p, "update", "--source", "src/one", "--status", "pr-open",
        env={"LOOPKIT_AGENT": "implementer"})
check("the agent that wrote the code cannot pass its own work",
      r.returncode != 0 and "both did the work" in (r.stdout + r.stderr), r.stdout + r.stderr)
run(p, "verdict", "--source", "src/one", "--result", "PASS", "--by", "reviewer")
r = run(p, "update", "--source", "src/one", "--status", "pr-open",
        env={"LOOPKIT_AGENT": "implementer"})
check("...but a different agent's PASS is accepted", r.returncode == 0, r.stdout + r.stderr)

# 7. An unattributed PASS is not a PASS.
p = project()
lp = p / "state" / "ticks.jsonl"
lp.write_text(json.dumps({"event": "verdict", "source": "src/one", "result": "PASS"}) + "\n")
r = run(p, "update", "--source", "src/one", "--status", "pr-open")
check("a PASS that names nobody is refused", r.returncode != 0, r.stdout + r.stderr)

# 8. THE VISIBLE DOOR. A human deciding otherwise must have a way through that
#    leaves a mark. A gate with no door gets disabled; a door with no mark is a
#    hole.
p = project()
r = run(p, "update", "--source", "src/one", "--status", "pr-open",
        "--override-verdict", "owner ruled on the PR thread, 2026-09-07")
check("--override-verdict lets a human through", r.returncode == 0, r.stdout + r.stderr)
check("...and the row moved", status_of(p) == "pr-open", f"status={status_of(p)}")
check("...and it says so loudly on stderr", "OVERRIDE" in r.stderr, r.stderr)
ov = [e for e in ledger(p) if e.get("event") == "verdict-override"]
check("...and it is recorded in the ledger with the reason",
      bool(ov) and "owner ruled" in ov[0].get("reason", ""), json.dumps(ledger(p)))
r = run(p, "update", "--source", "src/one", "--status", "pr-open", "--override-verdict", "")
check("an override with an EMPTY reason is refused — that is the gate switched off",
      r.returncode != 0, r.stdout + r.stderr)

# 9. THE CONTROL CASE. The gate must bite pr-open and nothing else. Without
#    this, a gate that refused every transition would satisfy every check above.
p = project()
for target in ("spec-draft", "spec-ready", "fixing", "blocked", "inbox", "done"):
    r = run(p, "update", "--source", "src/one", "--status", target)
    check(f"control: --status {target} still passes with no verdict",
          r.returncode == 0, r.stdout + r.stderr)

print()
print("PR-OPEN GATE PINS PASS" if fails == 0 else f"{fails} FAILURE(S)")
raise SystemExit(1 if fails else 0)
