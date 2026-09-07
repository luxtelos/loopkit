#!/usr/bin/env python3
"""queue-never-reads-empty.py — the queue never reads SHORT.

THE INVARIANT, which is broader than this file's name: if the queue file plainly
holds N table rows, `triage_state.py list` returns N of them or it refuses. It
never returns fewer and exits 0. "Never reads empty" is the special case N>0,
M=0; the general case includes partial loss, which is worse, because a count
that is merely wrong still looks plausible.

WHY THE NAME UNDERSTATES IT. Renaming would be the honest thing, and the first
attempt did exactly that — `protect_tests.py` blocked the `git mv`, because
moving a test is a human decision in this repo. So the name stays and the claim
lives here instead. The name understates what is pinned, which is the safe
direction; the previous version overstated it, which is what the 2026-09-07
review failed the PR for.

HISTORY, because this bug has now been fixed three times and each fix named a
different CAUSE:
  2026-08-31  a padded header failed an exact string compare — 40 rows read as 0
  2026-09-07a an unknown or renamed column did the same; the guard covered only
              the case where NO header was found
  2026-09-07b a review built nine shapes; four still read as empty and a fifth
              lost one row of two
The parser now guards the EFFECT — fewer rows out than in, therefore refuse —
so a cause nobody has met yet is covered too. Every shape below is either a
shape that review built (marked [rv]) or one invented since, and each declares
an exact expected row count or REFUSE. Run from the repo root.
"""
import json, os, pathlib, subprocess, sys, tempfile

_ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=os.path.dirname(os.path.abspath(__file__)),
                       capture_output=True, text=True).stdout.strip()
assert _ROOT, "not inside a git checkout — cannot locate the repo root"
os.chdir(_ROOT)
CLI = "plugins/loopkit/scripts/triage_state.py"
d = pathlib.Path(tempfile.mkdtemp())

H5 = "| finding | source | priority | spec | status |\n| --- | --- | --- | --- | --- |\n"
H4 = "| finding | source | priority | status |\n| --- | --- | --- | --- |\n"
RA = "| a | src/a | high |  | new |\n"
RB = "| b | src/b | low |  | new |\n"

# (label, expected rows or "REFUSE", body)
CASES = [
    # --- the shapes the 2026-09-07 review built -----------------------------
    ("[rv] A header in the wrong ORDER", "REFUSE",
     "# T\n\n| source | finding | priority | spec | status |\n| --- | --- | --- | --- | --- |\n"
     "| src/a | a | high |  | new |\n| src/b | b | low |  | new |\n"),
    ("[rv] B duplicate column name", "REFUSE",
     "# T\n\n| finding | finding | priority | spec | status |\n| --- | --- | --- | --- | --- |\n"
     "| a | src/a | high |  | new |\n| b | src/b | low |  | new |\n"),
    # C and G are legal GitHub-flavoured markdown, so the fix READS them rather
    # than refusing. Refusing valid input would be a second defect wearing the
    # first one's uniform.
    ("[rv] C rows without outer pipes (valid GFM)", 2,
     "# T\n\n" + H5 + "a | src/a | high |  | new\nb | src/b | low |  | new\n"),
    ("[rv] D header appears twice (lost 1 of 2)", "REFUSE",
     "# T\n\n" + H5 + RA + "\n" + H5 + RB),
    ("[rv] E rows narrower than the header", "REFUSE",
     "# T\n\n" + H5 + "| a | src/a | high | new |\n| b | src/b | low | new |\n"),
    ("[rv] F rows wider than the header", "REFUSE",
     "# T\n\n" + H5 + "| a | src/a | high |  | new | x |\n| b | src/b | low |  | new | y |\n"),
    ("[rv] G blank line after the separator", 2, "# T\n\n" + H5 + "\n" + RA + RB),
    ("[rv] H renamed column", "REFUSE",
     "# T\n\n| finding | origin | priority | spec | status |\n| --- | --- | --- | --- | --- |\n"
     "| a | src/a | high |  | new |\n"),
    # --- shapes invented since, to test the invariant rather than the list ---
    ("4-column header, 5-cell rows", "REFUSE",
     "# T\n\n" + H4 + "| a | src/a | high |  | new |\n| b | src/b | low |  | new |\n"),
    ("a second table further down the file", "REFUSE",
     "# T\n\n" + H5 + RA + "\n## Other\n\n" + H5 + RB),
    ("padded header and padded rows", 2,
     "# T\n\n|  finding  |  source  |  priority  |  spec  |  status  |\n"
     "|-----------|----------|------------|--------|----------|\n"
     "|  a        |  src/a   |  high      |        |  new     |\n"
     "|  b        |  src/b   |  low       |        |  new     |\n"),
    ("blank line BETWEEN two rows", 2, "# T\n\n" + H5 + RA + "\n" + RB),
    ("prose section after the table", 2,
     "# T\n\n" + H5 + RA + RB + "\n## Notes\n\nsome prose here.\n"),
    ("a specimen table inside a code fence", 0,
     "# T\n\nExample:\n\n```\n" + H5 + RA + "```\n\nNothing yet.\n"),
    ("an escaped pipe inside a cell", 1,
     "# T\n\n" + H5 + "| fix A \\| B | src/a | high |  | new |\n"),
    ("CRLF line endings", 2, ("# T\n\n" + H5 + RA + RB).replace("\n", "\r\n")),
    ("one row piped, one row not", 2, "# T\n\n" + H5 + RA + "b | src/b | low |  | new\n"),
    ("a row missing its trailing pipe", 2, "# T\n\n" + H5 + RA + "| b | src/b | low |  | new\n"),
    ("an all-blank row among real rows", 2, "# T\n\n" + H5 + RA + "|  |  |  |  |  |\n" + RB),
    ("tabs around the cells", 2,
     "# T\n\n" + H5 + "|\ta\t|\tsrc/a\t|\thigh\t|\t\t|\tnew\t|\n"
     "|\tb\t|\tsrc/b\t|\tlow\t|\t\t|\tnew\t|\n"),
    ("no trailing newline on the last row", 1, "# T\n\n" + H5 + RA.rstrip("\n")),
    # --- the control cases: honestly empty must stay honestly empty ----------
    ("5-column table, 2 rows (control)", 2, "# T\n\n" + H5 + RA + RB),
    ("4-column table, 1 row (control)", 1, "# T\n\n" + H4 + "| a | src/a | high | new |\n"),
    ("genuinely empty (control)", 0, "# Triage\n\nNothing yet.\n"),
    ("header only, no rows (control)", 0, "# Triage\n\n" + H5),
]

fails = 0
refused_n = 0
for label, want, body in CASES:
    f = d / (str(abs(hash(label))) + ".md")
    f.write_bytes(body.encode("utf-8"))
    r = subprocess.run([sys.executable, CLI, "list", "--state", str(f), "--format", "json"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        got, refused_n = "REFUSE", refused_n + 1
    else:
        try:
            got = len(json.loads(r.stdout or "[]"))
        except Exception:
            got = "UNPARSEABLE OUTPUT"
    ok = got == want
    fails += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'} {label:44} want={str(want):7} got={str(got):7} exit={r.returncode}")
    if not ok and want != "REFUSE" and got == "REFUSE":
        print(f"       refused what it should read: {(r.stderr.strip().splitlines() or [''])[-1][:100]}")
    if not ok and want == "REFUSE":
        print("       SILENT SHORT READ — this is the defect, not a style point")

# The harness must not be able to report success by failing everything. On
# 2026-09-07 a bad repo-root path made every case exit 2, and the pin still
# printed `ok` lines, because a universal failure satisfies any check that
# expects failure. Controls that must PARSE make that impossible to hide.
controls = [c for c in CASES if not isinstance(c[1], str)]
if fails == 0 and refused_n >= len(CASES):
    print("HARNESS BROKEN: every case refused, including the controls that must "
          "parse — the ok lines above are a universal failure satisfying checks "
          "that expected failure, not evidence")
    raise SystemExit(1)
if not controls:
    print("HARNESS BROKEN: no control case that must parse")
    raise SystemExit(1)

print("QUEUE NEVER-READS-SHORT PINS PASS" if fails == 0 else f"{fails} FAILURE(S)")
raise SystemExit(1 if fails else 0)
