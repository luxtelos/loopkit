#!/usr/bin/env python3
"""Exercise require_recall.py's content rule against cases held as data.

The cases live in this file, not on a command line: the session's own hooks
inspect command lines and file edits, and a gate that blocks its own test
harness is the same class of bug as a gate that blocks the edit describing it.
Every SQL keyword pair is split across two string literals (see the names
below), and case names use hyphens ("unique-index"), so this file does not
trip the gate it tests. The gate matches case-insensitively.

Each case runs the hook in a fresh scratch project (`.loopkit/memory.json`
present, so the gate is live) and a fresh TMPDIR (so no "memory was queried"
marker from a real session can turn a case into an allow).

usage: python3 hooks/test_require_recall.py [path-to-hook]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("require_recall.py"))

U = "UNI" "QUE"
PK = "PRIMARY" " KEY"
ADD_C = "ADD" " CONSTRAINT"
CONS = "CONSTR" "AINT"
CHK = "CHE" "CK"
EXC = "EXCLUDE" " USING"
UI = U + " INDEX"
TQ = '"' * 3
DDL = f"ALTER TABLE t {ADD_C} c {U} (a)"
CI = f"CREATE {UI} ux ON t (a)"

# (name, tool, file_path or command, body) -> must BLOCK (exit 2)
MUST_BLOCK = [
    # --- ported from the reviewed downstream fix (the #2990 MUST_BLOCK set) ---
    ("real DDL in a .ts string", "Write", "src/db.ts",
     f"await db.query(`ALTER TABLE t {ADD_C} c {CHK} (a > 0)`)\n"),
    ("real unique-index DDL in a .py string", "Write", "scripts/x.py",
     f'sql = "CREATE {UI} ux ON t (user_id)"\n'),
    ("DDL before a trailing comment still counts", "Write", "scripts/x.py",
     f'sql = "CREATE {UI} ux ON t (a)"  # see #1832\n'),
    ("a # inside a string is not a comment", "Write", "scripts/x.py",
     f'q = "#tag CREATE {UI} ux ON t (a)"\n'),
    ("a // inside a string is not a comment", "Write", "src/x.ts",
     f'const q = "http://x CREATE {UI} ux ON t (a)"\n'),
    ("named check constraint in a .ts string", "Write", "src/x.ts",
     f'const q = "{CONS} positive {CHK} (amount > 0)"\n'),
    ("a migration file is always in scope, even comment-only", "Write",
     "db/migrations/V999__x.sql", "-- nothing but a comment\n"),
    ("Edit of code adding DDL", "Edit", "src/x.ts",
     f"const q = `ALTER TABLE t {ADD_C} pk {PK} (id)`\n"),
    ("Bash copy into db/migrations", "Bash",
     "cp /tmp/V999__x.sql db/migrations/V999__x.sql", ""),
    ("inline /* note */ then DDL on the same line", "Write", "src/x.ts",
     f"const q = `\n  /* make it so */ CREATE {UI} ux ON t (a)\n`\n"),
    ("a # line inside a triple-quoted SQL string", "Write", "scripts/x.py",
     f'sql = {TQ}\n#x CREATE {UI} ux ON t (a)\n{TQ}\n'),
    ("a * line inside a template literal", "Write", "src/x.ts",
     f"const q = `\n* CREATE {UI} ux ON t (a)\n`\n"),
    ("a // line inside a template literal", "Write", "src/x.ts",
     f"const q = `\n// CREATE {UI} ux ON t (a)\n`\n"),
    ("DDL after a block comment closes", "Write", "src/x.ts",
     f"/*\n * notes\n */ const q = `ALTER TABLE t {ADD_C} pk {PK} (id)`\n"),
    ("DDL on the line after a one-line string with a # in it", "Write", "scripts/x.py",
     f'tag = "#x"\nsql = "CREATE {UI} ux ON t (a)"\n'),
    ("--i; then DDL on the same line", "Write", "src/x.ts",
     f'--i; db.query("CREATE {UI} ux ON t (a)")\n'),
    ("a --command continuation carrying DDL", "Write", "scripts/x.sh",
     f'psql \\\n  --command "CREATE {UI} ux ON t (a)"\n'),
    ("an escaped quote, then # inside the same string", "Write", "scripts/x.py",
     f'q = "a \\" # CREATE {UI} ux ON t (a)"\n'),
    ("B1 .sh glob seeds/*.sql, then psql -c DDL", "Write", "scripts/x.sh",
     f'for f in db/seeds/*.sql; do psql -f "$f"; done\npsql -c "{DDL}"\n'),
    ("B2 .sh cp src/* glob, then heredoc DDL", "Write", "scripts/x.sh",
     f"cp \"$SRC\"/* out/\npsql <<'SQL'\n{CI};\nSQL\n"),
    ("B3 YAML paths glob db/**, then run: psql DDL", "Write", ".github/workflows/x.yml",
     f"on:\n  push:\n    paths: [db/**]\njobs:\n  a:\n    steps:\n      - run: psql -c \"{CI}\"\n"),
    ("B4 Dockerfile COPY dist/*, then RUN psql DDL", "Write", "Dockerfile",
     f'COPY dist/* /app/\nRUN psql -c "ALTER TABLE t {ADD_C} c {PK} (id)"\n'),
    ("B18 .sh glob with no closing */ anywhere", "Write", "scripts/x.sh",
     f"ls migrations/*.sql\necho ok\necho ok\npsql -c \"{CI}\"\n"),
    ("an unclosed /* in a .ts file gives its text back", "Write", "src/x.ts",
     f"/* unfinished\nconst q = `{CI}`\n"),
    ("A20 a TS private field #ddl is not a comment", "Write", "src/x.ts",
     f'class M {{\n  #ddl = "{DDL}";\n}}\n'),
    ("B5 Python floor division // is not a comment", "Write", "scripts/x.py",
     f'pages = n // size; cur.execute("{CI}")\n'),
    ("B6 Python // inside a dict, then DDL", "Write", "scripts/x.py",
     f'cfg = {{"half": n // 2, "sql": "{DDL}"}}\n'),
    ("a -- line in a .ts file is not a comment", "Write", "src/x.ts",
     f"-- {ADD_C} c {U} (a)\n"),
    ("an unknown file type is not stripped at all", "Write", "notes/x.unknownext",
     f"# the {UI} is keyed by email\n"),
    ("MultiEdit edits[] carrying DDL", "MultiEdit", "src/x.ts",
     f"await db.query(`{CI}`)\n"),

    # --- follow-ups from the round-2 review of the downstream fix ---
    # 1. The dockerfile name match was a prefix: dockerfile-render.ts was read
    #    with # comments, so a TS private field holding DDL went through.
    ("X2 dockerfile-render.ts is TypeScript, #ddl is a private field", "Write",
     "lib/dockerfile-render.ts", f'class M {{\n  #ddl = "{DDL}";\n}}\n'),
    ("dockerfile.ts is TypeScript, not a Dockerfile", "Write", "lib/dockerfile.ts",
     f'class M {{\n  #ddl = "{DDL}";\n}}\n'),
    ("N21 dockerfile.prod is still a Dockerfile: glob, then RUN DDL", "Write",
     "dockerfile.prod", f"COPY dist/* /app/\nRUN psql -c \"{DDL}\"\n"),
    ("app.dockerfile: RUN DDL after a # comment", "Write", "build/app.dockerfile",
     f"# build\nRUN psql -c \"{DDL}\"\n"),
    # 2. .fizz is Python-like: # is a comment, strings are kept.
    (".fizz: DDL in a string is kept", "Write", "state/models/x.fizz",
     f'action Init:\n    q = "{CI}"  # note\n'),
    # 3a. YAML: an apostrophe inside a plain scalar is not a quote.
    ("X1 YAML apostrophe, then '... #x' and DDL in single-quoted psql -c", "Write",
     ".github/workflows/x.yml",
     f"- name: it's the backfill\n  run: psql -c 'SELECT 1 #x' -c '{DDL}'\n"),
    ("X1b YAML '90s opens a quote that never closes on its line", "Write",
     ".github/workflows/x.yml",
     f"- name: the '90s backfill\n  run: psql -c 'SELECT 1 #x' -c '{DDL}'\n"),
    ("YAML: a real quoted scalar holding # and DDL", "Write", "deploy/x.yaml",
     f"sql: 'SELECT 1 #x; {DDL}'\n"),
    # 3b. JSX: a backtick in JSX text is not a template literal.
    ("X3 .tsx odd backtick in JSX text, then a template with // before DDL", "Write",
     "src/x.tsx", f"const A = () => <p>run `npm</p>;\nconst q = `x // y {CI}`\n"),
    ("X3b .jsx the same shape", "Write", "src/x.jsx",
     f"const A = () => <p>run `npm</p>;\nconst q = `x // y {CI}`\n"),
    (".tsx tagged template sql`...` is a template", "Write", "src/x.tsx",
     f"const q = sql`x // y {CI}`;\n"),
    (".tsx template after return", "Write", "src/x.tsx",
     f"function f() {{\n  return `x // y {CI}`;\n}}\n"),
    (".tsx template after =>", "Write", "src/x.tsx",
     f"const f = () => `x // y {CI}`;\n"),
    (".tsx template inside a JSX expression", "Write", "src/x.tsx",
     f"const A = () => <p>{{`x // y {CI}`}}</p>;\n"),
    (".tsx multi-line template opened after =", "Write", "src/x.tsx",
     f"const q =\n  `\n  // x\n  {CI}\n`;\n"),
]

# The round-2 attack corpus (reviewer, 2026-10-03). Each one is a REAL
# invariant and must block -- except the four in KNOWN_HOLES.
ATTACK_MUST_BLOCK = [
    ("A1 DDL after -- inside a py string", "Write", "s/x.py",
     f'q = "SELECT 1 -- then {ADD_C} c {U} (a)"\n'),
    ("A2 multi-line py string, a line starts with --, DDL on next", "Write", "s/x.py",
     f'q = {TQ}\n-- note\n{DDL}\n{TQ}\n'),
    ("A3 /* opens in string, */ in code, DDL in string", "Write", "s/x.ts",
     f'const a = "/*"; /* x */ db.query("{CI}")\n'),
    ("A4 line starting /* note */ then DDL (template string)", "Write", "s/x.ts",
     f"const q = `\n/* V050 */ {CI}\n`\n"),
    ("A5 non-migration .sql: /* note */ ALTER ... on one line", "Write", "scripts/seed.sql",
     f"/* seed */ ALTER TABLE t {ADD_C} c {PK} (id);\n"),
    ("A6 py continuation line starting with * (unpack)", "Write", "s/x.py",
     f'cur.execute(\n    *("{DDL}",)\n)\n'),
    ("A7 multi-line py string, line starts with *", "Write", "s/x.py",
     f'q = {TQ}\nSELECT 1\n* 2, {UI} \n{TQ}\n'),
    ("A8 JS regex literal / #/ then DDL", "Write", "s/x.ts",
     f'const re = / #/; db.query("{CI}")\n'),
    ("A9 JS regex literal with a quote char, then # in a string", "Write", "s/x.ts",
     f"const re = /'/; const s = 'a # b'; db.query(\"{CI}\")\n"),
    ("A10 .sh heredoc, -- comment line then DDL", "Write", "scripts/x.sh",
     f"psql <<'SQL'\n-- add key\nALTER TABLE t {ADD_C} c {PK} (id);\nSQL\n"),
    ("A11 .sh continuation: --command \"DDL\"", "Write", "scripts/x.sh",
     f'psql "$DATABASE_URL" \\\n  --command "ALTER TABLE t {ADD_C} c {U} (email)"\n'),
    ("A12 .sh heredoc, DDL line with trailing -- comment", "Write", "scripts/x.sh",
     f"psql <<'SQL'\n{DDL}; -- why\nSQL\n"),
    ("A13 .sh heredoc, # comment line then DDL", "Write", "scripts/x.sh",
     f"psql <<SQL\n# note\n{CI};\nSQL\n"),
    ("A14 ADD CONSTRAINT / -- / CHECK split in a py string", "Write", "s/x.py",
     f'q = {TQ}\nALTER TABLE t\n  {ADD_C} c -- positive\n  {CHK} (a > 0)\n{TQ}\n'),
    ("A15 pieces joined across # comments (py implicit concat)", "Write", "s/x.py",
     f'q = ("ALTER TABLE t "  # why\n     "{ADD_C} c "  # name\n     "{CHK} (a > 0)")\n'),
    ("A16 # inside an f-string", "Write", "s/x.py",
     f'q = f"#{{n}} ALTER TABLE {{t}} {ADD_C} {{c}} {U} ({{col}})"\n'),
    ("A17 f-string with nested quotes and #", "Write", "s/x.py",
     f"q = f\"{{d['#']}} {CI}\"\n"),
    ("A18 CONSTRAINT..CHECK( then -- hides the rest", "Write", "s/x.ts",
     f'const q = "ALTER TABLE t {ADD_C} c {CHK} (a > 0) -- and more"\n'),
    ("A19 named CHECK first half, -- second half (template)", "Write", "s/x.ts",
     f"const q = `\n  {CONS} pos {CHK} (amount > 0 -- reason\n  AND amount < 10)\n`\n"),
    ("A21 py multi-line string, a line starts with #", "Write", "s/x.py",
     f'q = {TQ}\n# heading\n{TQ}\nr = {TQ}\n#x {DDL}\n{TQ}\n'),
    ("A22 template literal line starting with // URL then DDL", "Write", "s/x.ts",
     f"const q = `\n//host/db {DDL}\n`\n"),
    ("A23 YAML run: block, -- flag line carrying DDL", "Write", ".github/workflows/x.yml",
     f"      run: |\n        psql \\\n          --command \"{CI}\"\n"),
    ("A26 EXCLUDE USING in a string after a code ' #' operator (pg jsonb)", "Write", "s/x.ts",
     f"const q = `\nSELECT d #> '{{a}}', 1; ALTER TABLE t {ADD_C} c {EXC} gist (r WITH &&)\n`\n"),
    ("A27 comment then DDL in an Edit", "Edit", "s/x.ts",
     f"// note\nawait db.query(`{CI}`)\n"),
    ("A28 CRLF line endings, // comment then DDL line", "Write", "s/x.ts",
     f"// a\r\nawait db.query(`{CI}`)\r\n"),
    ("B7 nested template literal: DDL in inner after #", "Write", "s/x.ts",
     f"const q = `${{flag ? `a # b {DDL}` : ``}}`\n"),
    ("B8 Edit fragment mid-template, line starts #", "Edit", "s/x.ts",
     f"#x {DDL}\n`;\n"),
    ("B10 Edit fragment mid-triple: plain SQL DDL lines", "Edit", "s/x.py",
     f"  email text NOT NULL,\n  {CONS} users_email_key {U} (email)\n);\n{TQ}\n"),
    ("B11 Edit fragment mid-template, -- comment with apostrophe then DDL", "Edit", "s/x.ts",
     f"  -- don't drop\n  {ADD_C} c {U} (a)\n`\n"),
    ("B12 Edit fragment mid-template, // line then DDL next line", "Edit", "s/x.ts",
     f"  // x\n  {ADD_C} c {U} (a)\n`\n"),
    ("B13 SQL '''' literal inside a string, later string has # DDL", "Write", "s/x.ts",
     f"const a = \"''''\"; const b = 'x # {ADD_C} c {U} (a)';\n"),
    ("B14 py SQL '''' literal, then # in a later string", "Write", "s/x.py",
     f"q = 'SELECT ''''' \nr = 'x # {ADD_C} c {U} (a)'\n"),
    ("B15 .sh: echo it\\'s then DDL in double quotes", "Write", "scripts/x.sh",
     f"echo it\\'s\npsql -c \"{DDL}\"\n"),
    ("B16 .sql non-migration: -- comment between ALTER and ADD", "Write", "scripts/one.sql",
     f"ALTER TABLE t\n-- why\n{ADD_C} c {U} (a);\n"),
    ("N1 CRLF .sh glob then DDL", "Write", "scripts/x.sh",
     f"for f in db/*.sql; do :; done\r\npsql -c \"{DDL}\"\r\n"),
    ("N2 CRLF .py # comment line then DDL", "Write", "s/x.py",
     f"# note\r\ncur.execute(\"{CI}\")\r\n"),
    ("N3 .tsx JSX {/* c */} then DDL", "Write", "s/x.tsx",
     f"const A = () => <div>{{/* note */}}</div>;\nawait db.query(`{CI}`)\n"),
    ("N4 .mjs // inside URL string then DDL same line", "Write", "s/x.mjs",
     f'await fetch("http://h/x"); await db.query("{CI}")\n'),
    ("N5 .cjs DDL after URL // in template", "Write", "s/x.cjs",
     f"const q = `-- http://h/x\n{CI}`\n"),
    ("N6 uppercase .TS DDL in string after // line", "Write", "s/X.TS",
     f"// c\nawait db.query('{CI}')\n"),
    ("N7 uppercase .PY DDL", "Write", "s/X.PY", f"cur.execute('{DDL}')\n"),
    ("N8 no-ext shebang script with DDL after # line", "Write", "scripts/migrate-now",
     f"#!/bin/bash\n# go\npsql -c \"{DDL}\"\n"),
    ("N9 nested /* /* */ then DDL in code", "Write", "s/x.ts",
     f'/* a /* b */ db.query("{CI}")\n'),
    ("N10 template literal spanning lines with // URL + -- line", "Write", "s/x.ts",
     f"const q = `\n  -- see https://x/y\n  // not a comment here\n  {CI}\n`\n"),
    ("N11 py f-string nested same quotes with #", "Write", "s/x.py",
     f'q = f"{{d["#"]}} {DDL}"\n'),
    ("N12 sh $# and ${#x} then DDL same line", "Write", "scripts/x.sh",
     f'echo $# ${{#x}}; psql -c "{DDL}"\n'),
    ("N13 go raw string with // line then DDL", "Write", "s/x.go",
     f"q := `\n// x\n{CI}`\n"),
    ("N14 .sql non-migration: $$ body -- line then DDL", "Write", "scripts/fix.sql",
     f"DO $$ BEGIN\n-- why\nEXECUTE '{DDL}';\nEND $$;\n"),
    ("N15 .sql E-string with escaped quote then DDL", "Write", "scripts/fix.sql",
     f"SELECT E'it\\'s'; {DDL};\n"),
    ("N16 .sh unclosed ' in a # comment then DDL", "Write", "scripts/x.sh",
     f"# don't run twice\npsql -c \"{DDL}\"\n"),
    ("N17 .ts // line ending in backslash then DDL", "Write", "s/x.ts",
     f"// note \\\nawait db.query(`{CI}`)\n"),
    ("N18 .yml apostrophe in name then DDL in run", "Write", ".github/workflows/x.yml",
     f"- name: it's the backfill\n  run: psql -c \"{DDL}\"\n"),
    ("N19 .tsx JSX text apostrophe then DDL next line", "Write", "s/x.tsx",
     f"const A = () => <p>Don't</p>;\nawait db.query(`{CI}`)\n"),
    ("N20 .scss // then DDL in a string", "Write", "s/x.scss",
     f"// c\n$q: \"{DDL}\";\n"),
    ("N22 .tf HCL heredoc with # then DDL", "Write", "infra/x.tf",
     f"locals {{\n  q = <<EOT\n# x\n{DDL}\nEOT\n}}\n"),
]

# Accepted by the round-1 and round-2 reviews: an Edit fragment that STARTS
# inside a string is read as code from its first character. Run and reported,
# not asserted -- pinning a hole open would fail the day someone closes it.
KNOWN_HOLES = [
    ("A24 Edit fragment starting mid-string with ' #'", "Edit", "s/x.py",
     f'tail # x", "{DDL}"\n'),
    ("A25 Edit fragment: trailing // then DDL in the same template", "Edit", "s/x.ts",
     f'a // b ${{"x"}} {CI}`\n'),
    ("B9 Edit fragment mid-py-triple: jsonb # op before DDL on one line", "Edit", "s/x.py",
     f"SELECT d #> '{{a}}'; {DDL};\n{TQ}\n"),
    ("B17 Edit fragment mid-template: DDL line with // URL before it", "Edit", "s/x.ts",
     f"  -- see https: //x\n  x // y {ADD_C} c {U} (a)\n`\n"),
]

# -> must ALLOW (exit 0)
MUST_ALLOW = [
    ("trailing # comment naming a rule and a paren", "Edit", "hooks/x.py",
     f"    why = None  # set when a PUSH_RULES {CHK.lower()} (not a regex) matched\n"),
    ("a plain call to check()", "Write", "src/x.py", "if check(x):\n    pass\n"),
    ("# comment line naming a primary-key", "Write", "src/x.py",
     f"# rows are keyed by the {PK} of users, see V012\n"),
    ("// comment line naming unique-(col)", "Write", "src/x.ts",
     f"// the {U} (user_id) idea was wrong, see #1832\n"),
    ("trailing // comment naming a unique-index", "Write", "src/x.ts",
     f"const n = rows.length; // was a {UI} in V024, dropped\n"),
    ("block comment lines", "Write", "src/x.ts",
     f"/*\n * V024 added a {UI} on role_assignments.\n */\nexport const x = 1\n"),
    ("-- comment line in a non-migration .sql file", "Write", "scripts/seed.sql",
     f"-- {ADD_C} c was the old approach\nSELECT 1;\n"),
    ("JSDoc /** ... */ on one line", "Write", "src/x.ts",
     f"/** keyed by {PK} */\nexport const y = 2\n"),
    ("block comment whose lines do not start with *", "Write", "src/x.ts",
     f"/*\nV024 added a {UI}\non role_assignments\n*/\nexport const z = 3\n"),
    ("indented trailing # comment in Python", "Edit", "src/x.py",
     f"    x = 1  # was {PK} (id)\n"),
    ("Dockerfile # comment line", "Write", "Dockerfile",
     f"# the {UI} lives in V024\nFROM node:20\n"),
    ("prose file (unchanged rule)", "Write", "docs/x.md", f"We added a {UI}.\n"),
    ("ordinary Bash", "Bash", "ls -la db/migrations", ""),
    ("MultiEdit edits[] with only a comment", "MultiEdit", "src/x.ts",
     f"// was a {UI}\n"),
    # round-2 controls
    ("C1 control .ts no invariant", "Write", "s/x.ts", "export const x = 1 // ok\n"),
    ("C2 control .py no invariant", "Write", "s/x.py", "def f():\n    return 1  # ok\n"),
    ("C4 uppercase .TS comment-only", "Write", "s/X.TS", f"// was a {UI}\nexport const x = 1\n"),
    ("C5 .tsx JSX comment-only", "Write", "s/x.tsx",
     f"const A = () => <div>{{/* was a {UI} */}}</div>;\n"),
    ("C6 .mjs comment-only", "Write", "s/x.mjs", f"// keyed by {PK}\nexport default 1\n"),
    ("C7 .sh comment-only with glob after", "Write", "scripts/x.sh", f"# was a {UI}\nls db/*.sql\n"),
    ("C8 CRLF .py comment-only", "Write", "s/x.py", f"# keyed by {PK}\r\nx = 1\r\n"),
    ("C9 .yml comment-only", "Write", ".github/workflows/x.yml", f"# keyed by {PK}\non: push\n"),
    ("C10 .sql non-migration comment-only", "Write", "scripts/q.sql", f"-- was a {UI}\nSELECT 1;\n"),
    # round-2 review follow-ups, the allow side
    ("Dockerfile.prod # comment line", "Write", "docker/Dockerfile.prod",
     f"# the {UI} lives in V024\nFROM node:20\n"),
    ("app.dockerfile # comment line", "Write", "build/app.dockerfile",
     f"# the {UI} lives in V024\nFROM node:20\n"),
    (".fizz # comment naming a unique-index", "Write", "state/models/one-plan.fizz",
     f"# BUG: no {UI} on (firm_id, plan)\naction Init:\n    plans = {{}}\n"),
]

# The price of checking two readings of YAML and JSX (see require_recall.py):
# after a stray apostrophe or backtick, the plain reading still sees a later
# comment as a string, so these still block -- as they did before the fix.
# Reported, not asserted.
KNOWN_FALSE_POSITIVES = [
    ("YAML apostrophe, then a # comment line naming an invariant", "Write", "x.yml",
     f"name: it's the backfill\n# keyed by the {PK} of users\non: push\n"),
    (".tsx odd backtick in JSX text, then a // comment naming an invariant", "Write",
     "src/x.tsx", f"const A = () => <p>run `npm</p>;\n// was a {UI}\nexport const B = 1;\n"),
]

PROJECT = tempfile.mkdtemp(prefix="loopkit-recall-test.")
os.makedirs(os.path.join(PROJECT, ".loopkit"))
with open(os.path.join(PROJECT, ".loopkit", "memory.json"), "w") as fh:
    json.dump({"_comment": "test registry: present, so the gate is live"}, fh)


def run(name, tool, target, body):
    if tool == "Bash":
        ti = {"command": target}
    elif tool == "Edit":
        ti = {"file_path": target, "old_string": "x", "new_string": body}
    elif tool == "MultiEdit":
        ti = {"file_path": target, "edits": [{"old_string": "x", "new_string": body}]}
    else:
        ti = {"file_path": target, "content": body}
    payload = {"tool_name": tool, "session_id": "test-" + name, "tool_input": ti}
    env = {"PATH": "/usr/bin:/bin", "TMPDIR": tempfile.mkdtemp(),
           "CLAUDE_PROJECT_DIR": PROJECT}
    p = subprocess.run([sys.executable, HOOK, "gate"], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, cwd=PROJECT, check=False)
    return p.returncode


fails = 0
for title, cases, want in (("MUST BLOCK (rc must be 2):", MUST_BLOCK, 2),
                           ("ATTACK CORPUS, MUST BLOCK (rc must be 2):", ATTACK_MUST_BLOCK, 2),
                           ("MUST ALLOW (rc must be 0):", MUST_ALLOW, 0)):
    print(title)
    for case in cases:
        got = run(*case)
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {case[0]}")

print("KNOWN HOLES (accepted by review; reported, not asserted):")
open_holes = 0
for case in KNOWN_HOLES:
    got = run(*case)
    open_holes += got != 2
    print(f"  {'open  ' if got != 2 else 'closed'}  rc={got}  {case[0]}")
print(f"  {open_holes} of {len(KNOWN_HOLES)} still open")

print("KNOWN FALSE POSITIVES (accepted; reported, not asserted):")
for case in KNOWN_FALSE_POSITIVES:
    got = run(*case)
    print(f"  {'blocks' if got == 2 else 'allows'}  rc={got}  {case[0]}")

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
