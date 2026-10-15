#!/usr/bin/env python3
"""evidence-gate.py — a finding moves forward only on evidence it wrote down.

THE INCIDENT (2026-10-15, described generically): a loop built and reviewed a
fix on a one-source cause. A response-field name and a URL parameter were taken
as proof that a user had a pending invite; both were copies of one stale flag.
Nobody read the identity provider or the database rows. Rules saying "check
several sources" existed as context, and nothing enforced them. The loop's own
note that a plain sign-in did not loop was the contradiction it ignored.

This pin runs the recorder (`triage_state.py`) the way a tick does and asserts
the BEHAVIOUR of the gate described in docs/adr/0008-evidence-gate.md and the
draft spec state/2026-10-15-spec-draft-evidence-gate.md. Every case carries the
criterion it proves, `[E3]` and so on, so the mutation harness
(`evidence-gate-prove-red.sh`) can require that breaking one half of the gate
turns that half's case red.

Fixtures are scratch projects under $TMPDIR. Nothing here writes the tracked
tree, and the inherited environment is scrubbed: an outer loop's LOOP_* /
LOOPKIT_* / CLAUDE_PROJECT_DIR must not steer the recorder under test, and git
is told not to sign, in case a future case commits.

usage: python3 tests/pins/evidence-gate.py [<repo root to test>]
       (default: the checkout this file lives in)
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE_ROOT = pathlib.Path(__file__).resolve().parents[2]
ROOT = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE_ROOT
PLUGIN = ROOT / "plugins" / "loopkit"
CLI = PLUGIN / "scripts" / "triage_state.py"
SNAPSHOT = PLUGIN / "scripts" / "source-snapshot.py"
TEMPLATE = PLUGIN / "templates" / "loopkit" / "sources.json"

HEADER = ("| finding | source | priority | spec | status |\n"
          "|---|---|---|---|---|\n")


def clean_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("LOOP_") or k.startswith("LOOPKIT_")
                   or k == "CLAUDE_PROJECT_DIR" or k.startswith("GIT_CONFIG"))}
    env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "commit.gpgsign",
                "GIT_CONFIG_VALUE_0": "false", "GIT_CONFIG_NOSYSTEM": "1"})
    env.update(extra or {})
    return env


# ---------------------------------------------------------------- assessments

GOOD_SOURCES = """## Sources

| class    | system of record  | evidence |
|----------|-------------------|----------|
| identity | identity provider | read: user record for the account, dev |
| data     | database          | read: role rows and the profile row, dev |
| runtime  | logs              | n/a: the claim is about stored state, not timing |
| derived  | app output        | read: response field and URL parameter (worth zero) |
"""

GOOD_DNF = """## Does not fit

- a plain sign-in did not loop in the reproduction
  explained: the plain path never reads the stale flag; only the redirect does
"""

NONE_DNF = """## Does not fit

- none — re-read the reproduction notes and the role rows; nothing contradicts
"""


def assessment(sources: str | None = GOOD_SOURCES, dnf: str | None = GOOD_DNF,
               row: str = "src/one") -> str:
    parts = ["# Assessment — fixture", "", f"Row source: {row}", "",
             "## Observed", "", "a behaviour", ""]
    if sources is not None:
        parts += [sources, ""]
    if dnf is not None:
        parts += [dnf, ""]
    parts += ["## Classification", "", "code", ""]
    return "\n".join(parts)


def project(text: str | None = None, *, status: str = "new",
            rows: str | None = None, name: str = "fixture-assessment.md",
            sources_json: str | None = None) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp(prefix="evidence-gate-"))
    (root / "state").mkdir()
    rows = rows if rows is not None else f"| a thing | src/one | high |  | {status} |\n"
    (root / "state" / "triage.md").write_text("# triage\n\n" + HEADER + rows)
    if text is not None:
        (root / "state" / name).write_text(text)
    if sources_json is not None:
        (root / ".loopkit").mkdir()
        (root / ".loopkit" / "sources.json").write_text(sources_json)
    return root


def run(root, *args, env=None, cwd=None):
    return subprocess.run(
        [sys.executable, str(CLI), *args, "--state", str(root / "state" / "triage.md")],
        capture_output=True, text=True, env=clean_env(env), cwd=cwd or str(root))


def move(root, status, *extra, source="src/one", env=None):
    return run(root, "update", "--source", source, "--status", status, *extra, env=env)


def status_of(root, source="src/one"):
    r = run(root, "list", "--format", "json")
    try:
        rows = json.loads(r.stdout or "[]")
    except ValueError:
        return f"<unparseable: {r.stderr.strip()[:120]}>"
    for row in rows:
        if row["source"] == source:
            return row["status"]
    return None


def ledger(root):
    p = root / "state" / "ticks.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


fails = 0


def check(tag, label, condition, detail=""):
    global fails
    print(f"  {'ok  ' if condition else 'FAIL'} [{tag}] {label}")
    if not condition:
        fails += 1
        if detail:
            print(f"       {detail.strip()[:400]}")


def refused(tag, label, root, status="spec-draft", *extra, start="new"):
    """A refusal is three facts, not one: non-zero exit, the row did not move,
    and nothing was recorded for the transition it refused."""
    before = (root / "state" / "triage.md").read_bytes()
    r = move(root, status, *extra)
    out = r.stdout + r.stderr
    check(tag, f"REFUSED: {label}", r.returncode != 0, out)
    check(tag, "  ...the row did not move", status_of(root) == start, f"status={status_of(root)}")
    check(tag, "  ...state/triage.md is byte-identical",
          (root / "state" / "triage.md").read_bytes() == before)
    check(tag, "  ...no transition and no override event was recorded",
          not [e for e in ledger(root) if e.get("event") in ("transition", "sources-override")],
          json.dumps(ledger(root)))
    return out


def allowed(tag, label, root, status="spec-draft", *extra, env=None):
    r = move(root, status, *extra, env=env)
    check(tag, f"ALLOWED: {label}", r.returncode == 0, r.stdout + r.stderr)
    check(tag, "  ...and the row moved", status_of(root) == status, f"status={status_of(root)}")
    return r


# ============================================================ must be REFUSED

print("== refused: the assessment does not carry the evidence")

out = refused("E3", "one source (identity read, everything else n/a)", project(assessment(
    sources="""## Sources

| class    | system of record  | evidence |
|---|---|---|
| identity | identity provider | read: user record, dev |
| data     | database          | n/a: did not look |
""")))
check("E3", "  ...and the refusal names how many systems it counted", "1 independent" in out, out)

refused("E4", "two reads of the SAME system (two identity rows)", project(assessment(
    sources="""## Sources

| class    | system of record  | evidence |
|---|---|---|
| identity | identity provider | read: user record, dev |
| identity | identity provider | read: the same user's roles in the provider, dev |
""")))

refused("E4", "two classes the project maps to ONE system count once", project(assessment(
    sources="""## Sources

| class   | system of record | evidence |
|---|---|---|
| data    | database         | read: role rows, dev |
| reports | database         | read: the nightly count table, dev |
"""), sources_json=json.dumps({"classes": {
    "data": {"system": "primary database"},
    "reports": {"system": "Primary  Database"},
    "derived": {"system": "app output", "counts": False}}})))

refused("E5", "the incident's own evidence: a response field and a URL parameter (derived, worth zero)",
        project(assessment(sources="""## Sources

| class   | system of record | evidence |
|---|---|---|
| derived | app output       | read: the response field says the invite is pending |
| derived | app output       | read: the URL parameter says the account is solo |
| code    | code graph + git | read: the handler that sets both, at the deployed commit |
""")))

refused("E1", "no `## Sources` section at all", project(assessment(sources=None)))

refused("E2", "an EMPTY `## Sources` section (a fabricated zero)", project(assessment(
    sources="## Sources\n\n")))

refused("E2", "a `## Sources` table with a header and no rows", project(assessment(
    sources="## Sources\n\n| class | system of record | evidence |\n|---|---|---|\n")))

refused("E2", "`Live state: none` with no reason", project(assessment(
    sources="## Sources\n\nLive state: none —\n")))

refused("E6", "the snapshot's placeholders left in (`read: <what, env>`)", project(assessment(
    sources="""## Sources

| class    | system of record  | evidence |
|---|---|---|
| identity | identity provider | read: <what, env> |
| data     | database          | read: <what, env> |
""")))

refused("E6", "a class the project never configured is refused, not ignored", project(assessment(
    sources="""## Sources

| class    | system of record  | evidence |
|---|---|---|
| identity | identity provider | read: user record, dev |
| data     | database          | read: role rows, dev |
| hunch    | my own reading    | read: I am fairly sure, dev |
""")))

refused("E6", "rows inside an HTML comment are invisible to the gate", project(assessment(
    sources="""## Sources

<!--
| class    | system of record  | evidence |
|---|---|---|
| identity | identity provider | read: user record, dev |
| data     | database          | read: role rows, dev |
-->
""")))

out = refused("E9", "an observation that does not fit, left unexplained", project(assessment(
    dnf="""## Does not fit

- a plain sign-in did not loop in the reproduction
- the profile row was written months before the invite
  explained: profile rows are written at first sign-in, not at invite
""")))
check("E9", "  ...and the refusal quotes the unexplained item", "plain sign-in" in out, out)

refused("E9", "`explained:` with nothing after it", project(assessment(
    dnf="## Does not fit\n\n- a plain sign-in did not loop\n  explained:\n")))

refused("E8", "no `## Does not fit` section", project(assessment(dnf=None)))

refused("E8", "an empty `## Does not fit` section", project(assessment(
    dnf="## Does not fit\n\nNothing here.\n")))

refused("E10", "`none` beside a real item is a contradiction in itself", project(assessment(
    dnf="## Does not fit\n\n- none — checked\n- a plain sign-in did not loop\n  explained: it does not read the flag\n")))

refused("E10", "a bare `none` with no word on what was checked", project(assessment(
    dnf="## Does not fit\n\n- none\n")))

refused("E11", "no assessment declares the row", project(assessment(row="src/other")))

two = project(assessment())
(two / "state" / "second-assessment.md").write_text(assessment())
out = refused("E11", "two assessments declare the row and none was picked", two)
check("E11", "  ...and the refusal lists both", "second-assessment.md" in out
      and "fixture-assessment.md" in out, out)

p = project(assessment())
(p / "state" / "other-assessment.md").write_text(assessment(row="src/other"))
refused("E11", "--assessment naming a file that declares a DIFFERENT row", p, "spec-draft",
        "--assessment", str(p / "state" / "other-assessment.md"))

for target in ("spec-ready", "pr-open"):
    refused("E1", f"the gate bites {target} too, not only spec-draft",
            project(assessment(sources=None), status="fixing"), target, start="fixing")

p = project(assessment(sources=None))
before = (p / "state" / "triage.md").read_bytes()
r = run(p, "upsert", "--source", "src/one", "--finding", "a thing", "--priority", "high",
        "--status", "spec-draft")
check("E1", "REFUSED: upsert is gated too (a gate on one verb is a signposted detour)",
      r.returncode != 0 and status_of(p) == "new", r.stdout + r.stderr)

refused("E13", "--override-sources with an EMPTY reason", project(assessment(sources=None)),
        "spec-draft", "--override-sources", "")
refused("E13", "--override-sources with a reason that says nothing", project(assessment(sources=None)),
        "spec-draft", "--override-sources", " - ")

refused("E17", "a sources.json that is not JSON fails CLOSED", project(assessment(),
        sources_json="{ this is not json"))

# ============================================================ must be ALLOWED

print("== allowed: the evidence is on the page")

allowed("E3", "two systems read, every contradiction explained", project(assessment()))

p = project(assessment(), status="fixing")
allowed("E3", "spec-ready with the same assessment", p, "spec-ready")

allowed("E7", "`Live state: none — <why>` with no reads at all", project(assessment(
    sources="## Sources\n\nLive state: none — a typo in a doc; no system holds state about it\n",
    dnf=NONE_DNF)))

allowed("E10", "`- none — <what was checked>` alone", project(assessment(dnf=NONE_DNF)))

p = project(assessment())
(p / "state" / "second-assessment.md").write_text(assessment())
allowed("E11", "--assessment picks one of two declaring files", p, "spec-draft",
        "--assessment", "state/second-assessment.md")

# The override: allowed, loud, and recorded where a commit will carry it.
p = project(assessment(sources=None))
reason = "owner ruled in the PR thread: hotfix | read the logs after"
r = allowed("E12", "a REASONED --override-sources", p, "spec-draft", "--override-sources", reason,
            env={"LOOPKIT_AGENT": "tick-agent"})
check("E12", "  ...says so loudly on stderr", "OVERRIDE" in r.stderr, r.stderr)
ev = [e for e in ledger(p) if e.get("event") == "sources-override"]
check("E12", "  ...writes a sources-override event with the reason to the ledger",
      bool(ev) and "owner ruled" in ev[0].get("reason", ""), json.dumps(ledger(p)))
tri = (p / "state" / "triage.md").read_text()
check("E12", "  ...AND writes the reason into state/triage.md, the file the tick commits",
      "## Evidence-gate overrides" in tri and "owner ruled in the PR thread" in tri, tri)
check("E12", "  ...naming the row, the status and the agent",
      "src/one" in tri.split("## Evidence-gate overrides")[-1]
      and "spec-draft" in tri.split("## Evidence-gate overrides")[-1]
      and "tick-agent" in tri.split("## Evidence-gate overrides")[-1], tri)
check("E12", "  ...and the queue still parses with a `|` in the reason",
      status_of(p) == "spec-draft", f"status={status_of(p)}")
r2 = move(p, "spec-ready", "--override-sources", "second override, same row")
tri2 = (p / "state" / "triage.md").read_text()
check("E12", "  ...a second override appends under the same heading, never a second heading",
      r2.returncode == 0 and tri2.count("## Evidence-gate overrides") == 1
      and "second override" in tri2 and "owner ruled" in tri2, tri2)

p = project(assessment(sources=None))
r = run(p, "update", "--source", "src/nope", "--status", "spec-draft",
        "--override-sources", "a real reason")
check("E14", "an override on a transition that fails anyway records NOTHING",
      r.returncode != 0 and "Evidence-gate overrides" not in (p / "state" / "triage.md").read_text()
      and not [e for e in ledger(p) if e.get("event") == "sources-override"],
      r.stdout + r.stderr + json.dumps(ledger(p)))

p = project(assessment())
r = move(p, "spec-draft", "--override-sources", "not needed here")
check("E12", "an override the evidence did not need is a no-op, not a record",
      r.returncode == 0 and "Evidence-gate overrides" not in (p / "state" / "triage.md").read_text()
      and not [e for e in ledger(p) if e.get("event") == "sources-override"],
      r.stdout + r.stderr)

# The control case: everything that is not a commitment to a cause stays cheap.
for target in ("fixing", "blocked", "inbox", "done", "new"):
    p = project(None, status="spec-ready" if target != "new" else "inbox")
    r = move(p, target)
    check("E15", f"control: --status {target} needs no assessment",
          r.returncode == 0 and status_of(p) == target, r.stdout + r.stderr)

# The verdict gate is untouched by passing evidence.
p = project(assessment(), status="fixing")
r = move(p, "pr-open")
check("E16", "evidence alone does not open pr-open: the verdict gate still refuses",
      r.returncode != 0 and "verdict --state" in (r.stdout + r.stderr), r.stdout + r.stderr)
run(p, "verdict", "--source", "src/one", "--result", "PASS", "--by", "reviewer")
allowed("E16", "evidence plus a recorded PASS opens pr-open", p, "pr-open")

# The skill's own A3 template, copied verbatim, must NOT pass: a template whose
# placeholders count as reads would hand every lazy assessment two sources.
skill = (PLUGIN / "skills" / "loop-assess" / "SKILL.md").read_text()
block = skill.split("Write to `state/<date>-<slug>-assessment.md`:", 1)[-1]
block = block.split("```markdown", 1)[-1].split("```", 1)[0] if "```markdown" in block else ""
check("E6", "the loop-assess A3 template carries Row source, ## Sources and ## Does not fit",
      all(m in block for m in ("Row source:", "## Sources", "## Does not fit")), block[:300])
refused("E6", "the A3 template pasted unfilled", project(
    block.replace("Row source: <the triage row's source, exactly as in state/triage.md>",
                  "Row source: src/one")))

# ============================================================ the snapshot

print("== source-snapshot.py writes the skeleton and tells the truth about tools")

p = project(None)
(p / ".mcp.json").write_text(json.dumps({"mcpServers": {
    "fixture-idp": {"command": "idp-mcp", "env": {"API_KEY": "fixture-value-must-not-print"}}}}))
r = subprocess.run([sys.executable, str(SNAPSHOT), "--project", str(p), "--finding",
                    "a user sees a pending invite; their role rows say otherwise"],
                   capture_output=True, text=True, env=clean_env(), cwd=str(p))
snap = r.stdout
check("E18", "the snapshot runs", r.returncode == 0, r.stdout + r.stderr)
check("E18", "it writes a `## Sources` table with a row for every default class",
      "## Sources" in snap and all(f"| {c} " in snap for c in
                                   ("identity", "data", "runtime", "code", "memory", "tracker", "derived")),
      snap)
check("E18", "an MCP server it cannot probe is `unknown`, never `connected`",
      "fixture-idp" in snap and "unknown" in snap and "connected" not in snap.replace("not connected", ""),
      snap)
check("E18", "it prints the server NAME and never its configuration",
      "fixture-value-must-not-print" not in snap and "idp-mcp" not in snap
      and "fixture-value-must-not-print" not in r.stderr, snap + r.stderr)
check("E19", "the finding's nouns are matched to classes (identity: invite, user; data: rows)",
      any("identity" in l and "invite" in l for l in snap.splitlines())
      and any(l.startswith("| data") and "rows" in l for l in snap.splitlines()), snap)
q = project(assessment(sources=snap.split("### ")[0]))
refused("E18", "an assessment that pastes the snapshot unfilled is refused", q)

r = subprocess.run([sys.executable, str(SNAPSHOT), "--project", str(p), "--format", "json"],
                   capture_output=True, text=True, env=clean_env(), cwd=str(p))
try:
    data = json.loads(r.stdout)
    statuses = {t["status"].split()[0] for t in data["tools"]}
except Exception:  # noqa: BLE001 — any shape problem is the failure being reported
    statuses = set()
check("E18", "every tool status is one of answering / not-on-path / not-answering / unknown / session-reported",
      bool(statuses) and statuses <= {"answering", "not-on-path", "not-answering", "unknown",
                                      "session-reported"}, r.stdout + r.stderr)

try:
    tpl = json.loads(TEMPLATE.read_text())["classes"]
    sys.path.insert(0, str(PLUGIN))
    from loopkit_core import evidence  # noqa: E402
    same = tpl == evidence.DEFAULT_CLASSES
except Exception as exc:  # noqa: BLE001
    same, tpl = False, str(exc)
check("E18", "the template sources.json is exactly the built-in default", same, str(tpl)[:300])

print()
print("EVIDENCE GATE PINS PASS" if fails == 0 else f"{fails} FAILURE(S)")
raise SystemExit(1 if fails else 0)
