#!/usr/bin/env python3
"""Prove protect_tests.py honours an inline `TEST_EDIT_OK=1` prefix, per command,
and that nothing else about the guard moved.

Why this exists: the hook is its own process, so a prefix typed in a Bash command
never reached `os.getenv`. Its block message said "re-run with TEST_EDIT_OK=1"
and that door did not open. These cases pin the door (E1-E16) and the CONTROL
(E17-E19: a plain removal is still blocked, byte for byte, as in 0.2.5).

Every payload runs as a subprocess with CLAUDE_PROJECT_DIR set to a temp dir and
no TEST_EDIT_OK in the environment, unless the case sets it.

usage: python3 hooks/test_protect_tests.py [path-to-hook]
Run it against tests/fixtures/protect_tests_0.2.5.py to see the red side: the
E1 line (and the other inline cases) FAIL there, the control cases pass.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = sys.argv[1] if len(sys.argv) > 1 else str(HERE / "protect_tests.py")
OLD_HOOK = str(HERE.parent.parent.parent / "tests" / "fixtures" / "protect_tests_0.2.5.py")
T = "tests/a.test.ts"
LINE = f">> protect_tests: ALLOWED by TEST_EDIT_OK — {T}"

fails = 0
total = 0


def make_root(state: bool = True, with_test_file: bool = False) -> str:
    root = tempfile.mkdtemp(prefix="protect-tests-")
    if state:
        os.makedirs(os.path.join(root, "state"))
    if with_test_file:
        os.makedirs(os.path.join(root, "tests"), exist_ok=True)
        Path(root, T).write_text("it('one', () => {});\nit('two', () => {});\n", encoding="utf-8")
    return root


def run(payload, root: str, env_extra: dict | None = None, hook: str = HOOK, raw: str | None = None):
    env = dict(os.environ)
    env.pop("TEST_EDIT_OK", None)
    env["CLAUDE_PROJECT_DIR"] = root
    env.update(env_extra or {})
    data = raw if raw is not None else json.dumps(payload)
    p = subprocess.run([sys.executable, hook], input=data, capture_output=True, text=True, env=env)
    return p.returncode, p.stderr


def bash(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def ticks(root: str) -> list[dict]:
    p = Path(root, "state", "ticks.jsonl")
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails, total
    total += 1
    if not cond:
        fails += 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}" + (f"   [{detail}]" if detail and not cond else ""))


def rc_case(label: str, cmd: str, want: int, env_extra: dict | None = None) -> None:
    root = make_root()
    rc, err = run(bash(cmd), root, env_extra)
    check(f"{label}: rc={want}", rc == want, f"rc={rc} cmd={cmd!r}")


print("Honouring the prefix (E1-E5)")
root = make_root()
rc, err = run(bash(f"TEST_EDIT_OK=1 git rm {T}"), root)
# 0.2.5 also exits 0 here, but only because its matcher never looks past a leading
# VAR=value word (the same blindness lets `FOO=1 git rm <test>` through, see E10).
# So E1 asserts the override was HONOURED and ACKNOWLEDGED, which 0.2.5 never does.
check("E1 plain prefix on git rm: rc=0 and the override is acknowledged",
      rc == 0 and LINE in err.splitlines(), f"rc={rc} err={err!r}")
check("E3 stderr carries the ALLOWED line", LINE in err.splitlines(), err)
rows = ticks(root)
check("E4 exactly one test_edit_override tick", len(rows) == 1 and rows[0].get("event") == "test_edit_override", str(rows))
if rows:
    check("E4 tick carries command and paths", rows[0].get("command") == f"TEST_EDIT_OK=1 git rm {T}"
          and rows[0].get("paths") == [T] and "cwd" in rows[0] and "at" in rows[0], str(rows[0]))
root = make_root()
run(bash("TEST_EDIT_OK=1 git rm " + T + " # " + "x" * 400), root)
rows = ticks(root)
check("E4 command is cut to 200 characters", bool(rows) and len(rows[0].get("command", "")) <= 200, str(rows)[:120])
root = make_root()
fake = "ghp_FAKE" + "0123456789abcdefTOKENVALUE"
run(bash(f'GH_TOKEN={fake} OTHER="a b {fake}" TEST_EDIT_OK=1 git rm {T}'), root)
raw = Path(root, "state", "ticks.jsonl").read_text(encoding="utf-8") if ticks(root) else ""
check("tick never holds an assignment value other than TEST_EDIT_OK=1",
      bool(raw) and fake not in raw and "GH_TOKEN=***" in raw and "OTHER=***" in raw and "TEST_EDIT_OK=1" in raw, raw[:200])
rc_case("E2 assignment BEFORE the override",f"FOO=bar TEST_EDIT_OK=1 rm {T}", 0)
rc_case("E2 assignment AFTER the override", f"TEST_EDIT_OK=1 FOO=bar rm {T}", 0)
rc_case("E2 two extra assignments", f"A=1 TEST_EDIT_OK=1 B=2 rm {T}", 0)
rc_case("E2 quoted value in a neighbour assignment", f'FOO="a b" TEST_EDIT_OK=1 git rm {T}', 0)
rc_case("later command in a chain carries its own prefix", f"cd x && TEST_EDIT_OK=1 git rm {T}", 0)
rc_case("redirect 2>&1 is not a separator", f"TEST_EDIT_OK=1 git rm {T} 2>&1", 0)
rc_case("override on mv", f"TEST_EDIT_OK=1 mv {T} /tmp/x", 0)

root = make_root()
os.chmod(os.path.join(root, "state"), stat.S_IRUSR | stat.S_IXUSR)
rc, err = run(bash(f"TEST_EDIT_OK=1 git rm {T}"), root)
os.chmod(os.path.join(root, "state"), stat.S_IRWXU)
check("E5 unwritable state/: rc=0 and the line is still printed", rc == 0 and LINE in err.splitlines(), f"rc={rc} {err!r}")
root = make_root(state=False)
rc, err = run(bash(f"TEST_EDIT_OK=1 git rm {T}"), root)
check("E5 no state/ at all: rc=0, line printed, no ledger made",
      rc == 0 and LINE in err.splitlines() and not Path(root, "state").exists(), f"rc={rc} {err!r}")
root = make_root(state=False)
Path(root, "state").write_text("a file, not a directory", encoding="utf-8")
rc, err = run(bash(f"TEST_EDIT_OK=1 git rm {T}"), root)
check("E5 state is a file: rc=0, no traceback", rc == 0 and "Traceback" not in err, f"rc={rc} {err!r}")

print("Only the command it prefixes (E6-E12)")
for sep_label, sep in [("&&", " && "), (";", " ; "), ("||", " || "), ("|", " | "), ("&", " & "), ("newline", "\n")]:
    rc_case(f"E6 separator {sep_label}", f"TEST_EDIT_OK=1 git rm docs/x.md{sep}git rm {T}", 2)
rc_case("E7 prefix on the LATER command alone", f"git rm docs/x.md; TEST_EDIT_OK=1 git rm {T}", 0)
rc_case("E7 plus an unprefixed test removal BEFORE", f"git rm tests/b.test.ts; TEST_EDIT_OK=1 git rm {T}", 2)
rc_case("E7 plus an unprefixed test removal AFTER", f"TEST_EDIT_OK=1 git rm {T}; git rm tests/b.test.ts", 2)
rc_case("two prefixed commands, both allowed", f"TEST_EDIT_OK=1 git rm {T} && TEST_EDIT_OK=1 git rm tests/b.test.ts", 0)
rc_case("E8 echo with the text, then a removal", f'echo "TEST_EDIT_OK=1"; git rm {T}', 2)
rc_case("E8 text as an argument", f'git rm {T} "TEST_EDIT_OK=1"', 2)
rc_case("E8 text in a comment", f"git rm {T} # TEST_EDIT_OK=1", 2)
rc_case("E8 text in single quotes", f"echo 'TEST_EDIT_OK=1 git rm x'; git rm {T}", 2)
rc_case("E9 variable holds the word", f"V=TEST_EDIT_OK=1; $V git rm {T}", 2)
rc_case("E9 value from a variable", f"TEST_EDIT_OK=$X git rm {T}", 2)
rc_case("E9 value from a substitution", f"TEST_EDIT_OK=$(echo 1) git rm {T}", 2)
rc_case("E9 value from backticks", f"TEST_EDIT_OK=`echo 1` git rm {T}", 2)
for label, val in [("0", "0"), ("10", "10"), ("1x", "1x"), ("1.", "1."), ("true", "true"), ("empty", ""), ('"1"', '"1"'), ("'1'", "'1'")]:
    rc_case(f"E10 value {label}", f"TEST_EDIT_OK={val} git rm {T}", 2)
rc_case("E10 last assignment wins (1 then 0)", f"TEST_EDIT_OK=1 TEST_EDIT_OK=0 git rm {T}", 2)
rc_case("E10 last assignment wins (0 then 1)", f"TEST_EDIT_OK=0 TEST_EDIT_OK=1 git rm {T}", 0)
rc_case("E11 export", f"export TEST_EDIT_OK=1; git rm {T}", 2)
rc_case("E11 env", f"env TEST_EDIT_OK=1 git rm {T}", 2)

# Review of PR 67: six lines 0.2.5 BLOCKED and a first draft of this hook allowed,
# while the real shell ran the removal WITHOUT the override. Each must stay blocked.
# Fail closed: any command substitution, process substitution, heredoc or
# unbalanced quote voids every inline override on the line.
rc_case("R1 removal inside $( ) under an outer prefix", f"TEST_EDIT_OK=1 echo $(true; git rm {T} )", 2)
rc_case("R2 same with backticks", f"TEST_EDIT_OK=1 echo `true; git rm {T} `", 2)
rc_case("R3 same inside double quotes", f'TEST_EDIT_OK=1 echo "$(true; git rm {T} )"', 2)
rc_case("R4 escaped > then & backgrounds the echo", f"TEST_EDIT_OK=1 echo \\>& git rm {T}", 2)
rc_case("R5 heredoc body whose odd quote swallows a later removal",
        'cat <<EOF\nTEST_EDIT_OK=1 x"\nEOF\n' + f"git rm {T}" + '\ncat <<EOF\n"\nEOF', 2)
rc_case("R6 quoted ) inside $( ) then a removal", f'TEST_EDIT_OK=1 echo $(echo ")"; git rm {T} # "\n)', 2)
rc_case("R7 wrapped removal under a prefix is NOT honoured (as 0.2.5)", f"TEST_EDIT_OK=1 bash -c 'true; git rm {T} '", 2)
rc_case("R8 prefix on echo does not cover a removal quoted into it", f'TEST_EDIT_OK=1 echo "x; git rm {T}"', 2)
rc_case("R9 process substitution voids the line", f"TEST_EDIT_OK=1 git rm {T} <(true)", 2)
rc_case("R10 heredoc anywhere voids the line", f"TEST_EDIT_OK=1 git rm {T} <<EOF\nx\nEOF", 2)
rc_case("R11 unescaped >& is still a redirect, not a separator", f"TEST_EDIT_OK=1 git rm {T} >&2", 0)
rc_case("R11 &> is still a redirect", f"TEST_EDIT_OK=1 git rm {T} &> /dev/null", 0)
rc_case("E12 GOVERNANCE_EDIT_OK is not this door", f"GOVERNANCE_EDIT_OK=1 git rm {T}", 2)
rc_case("E12 LOOPKIT_COMMIT_UNLOCKED is not this door", f"LOOPKIT_COMMIT_UNLOCKED=1 git rm {T}", 2)
rc_case("unbalanced quote falls back to the 0.2.5 reading: still blocked", f'git rm {T} "x', 2)

print("Env-var path, as today (E13-E15)")
root = make_root(with_test_file=True)
rc, err = run(bash(f"git rm {T}"), root, {"TEST_EDIT_OK": "1"})
check("E13 env + Bash removal: rc=0", rc == 0, f"rc={rc}")
rows = ticks(root)
check("E15 env path prints the line and writes the tick",
      LINE in err.splitlines() and len(rows) == 1 and rows[0].get("event") == "test_edit_override" and rows[0].get("via") == "env",
      f"{err!r} {rows}")
write = {"tool_name": "Write", "tool_input": {"file_path": T, "content": "it('one', () => {});\n"}}
rc, err = run(write, root, {"TEST_EDIT_OK": "1"})
check("E13 env + Write that lowers the count: rc=0", rc == 0, f"rc={rc}")
edit = {"tool_name": "Edit", "tool_input": {"file_path": T, "old_string": "it('two', () => {});\n", "new_string": ""}}
rc, err = run(edit, root, {"TEST_EDIT_OK": "1"})
check("E13 env + Edit that removes an it(: rc=0", rc == 0, f"rc={rc}")
multi = {"tool_name": "MultiEdit", "tool_input": {"file_path": T, "edits": [{"old_string": "it('two', () => {});\n", "new_string": ""}]}}
rc, err = run(multi, root, {"TEST_EDIT_OK": "1"})
check("E13 env + MultiEdit that removes an it(: rc=0", rc == 0, f"rc={rc}")
for label, val in [("0", "0"), ("10", "10"), ("true", "true"), ("empty", "")]:
    root = make_root()
    rc, err = run(bash(f"git rm {T}"), root, {"TEST_EDIT_OK": val})
    check(f"E14 env TEST_EDIT_OK={label!r}: still blocked", rc == 2, f"rc={rc}")
root = make_root()
rc, err = run(bash(f"git rm {T}"), root)
check("E14 env unset: blocked", rc == 2, f"rc={rc}")

print("Edit and Write cannot carry a prefix (E16)")
for name, ti in [
    ("Write", {"file_path": T, "content": "it('one', () => {});\n"}),
    ("Edit", {"file_path": T, "old_string": "it('two', () => {});\n", "new_string": ""}),
    ("MultiEdit", {"file_path": T, "edits": [{"old_string": "it('two', () => {});\n", "new_string": ""}]}),
]:
    root = make_root(with_test_file=True)
    ti = dict(ti, command="TEST_EDIT_OK=1 true")
    rc, err = run({"tool_name": name, "tool_input": ti}, root)
    check(f"E16 {name} with a stray TEST_EDIT_OK=1 command key: rc=2", rc == 2, f"rc={rc}")

print("CONTROL: nothing else may move (E17-E19)")
for cmd in [f"git rm {T}", f"rm {T}", f"mv {T} /tmp/x", f"git mv {T} tests/b.test.ts", f"cd x && rm -f {T}"]:
    root = make_root()
    rc, err = run(bash(cmd), root)
    orc, oerr = run(bash(cmd), root, hook=OLD_HOOK) if os.path.exists(OLD_HOOK) else (2, err)
    check(f"E17 plain `{cmd}`: rc=2 and stderr identical to 0.2.5",
          rc == 2 and orc == 2 and err == oerr and err.startswith("BLOCKED [protect_tests]:"), f"rc={rc} new={err!r} old={oerr!r}")
    check("E17 no tick written for a block", ticks(root) == [])
for cmd in ["TEST_EDIT_OK=1 rm build/out.log", "rm lib/a.ts", "git rm docs/x.md", "mv a.ts b.ts"]:
    root = make_root()
    rc, err = run(bash(cmd), root)
    check(f"E18 `{cmd}`: rc=0, stderr empty, ledger untouched", rc == 0 and err == "" and ticks(root) == [], f"rc={rc} {err!r}")
root = make_root(with_test_file=True)
rc, _ = run({"tool_name": "Write", "tool_input": {"file_path": T, "content": "it('one', () => {});\n"}}, root)
check("E19 Write that lowers the count: rc=2", rc == 2, f"rc={rc}")
rc, _ = run({"tool_name": "Write", "tool_input": {"file_path": T, "content": "it('one', () => {});\nit('two', () => {});\nit('three', () => {});\n"}}, root)
check("E19 Write that only adds a test: rc=0", rc == 0, f"rc={rc}")
root = make_root()
Path(root, "state", "features.json").write_text(json.dumps({"features": [{"id": 1, "name": "a", "passes": False}]}), encoding="utf-8")
newf = json.dumps({"features": [{"id": 1, "name": "renamed", "passes": False}]})
rc, _ = run({"tool_name": "Write", "tool_input": {"file_path": "state/features.json", "content": newf}}, root)
check("E19 features.json change beyond `passes`: rc=2", rc == 2, f"rc={rc}")
okf = json.dumps({"features": [{"id": 1, "name": "a", "passes": True}]})
rc, _ = run({"tool_name": "Write", "tool_input": {"file_path": "state/features.json", "content": okf}}, root)
check("features.json flipping only `passes`: rc=0", rc == 0, f"rc={rc}")

print("Bad input and concurrency (E21-E22)")
root = make_root()
for label, raw in [
    ("not JSON", "this is not json"),
    ("JSON but not an object", "[1, 2]"),
    ("command missing", json.dumps({"tool_name": "Bash", "tool_input": {}})),
    ("command not a string", json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}})),
    ("command a list", json.dumps({"tool_name": "Bash", "tool_input": {"command": ["rm", T]}})),
    ("tool_input not an object", json.dumps({"tool_name": "Bash", "tool_input": "rm"})),
]:
    rc, err = run(None, root, raw=raw)
    check(f"E21 {label}: rc=0, no traceback", rc == 0 and "Traceback" not in err, f"rc={rc} {err!r}")
root = make_root()
with ThreadPoolExecutor(max_workers=12) as ex:
    res = list(ex.map(lambda i: run(bash(f"TEST_EDIT_OK=1 git rm tests/c{i}.test.ts"), root), range(12)))
tp = Path(root, "state", "ticks.jsonl")
lines = tp.read_text(encoding="utf-8").splitlines() if tp.exists() else []
parsed = []
for l in lines:
    try:
        parsed.append(json.loads(l))
    except Exception:
        pass
check("E22 twelve simultaneous overrides: all rc=0, twelve whole JSON lines",
      all(r[0] == 0 for r in res) and len(lines) == 12 and len(parsed) == 12, f"{len(lines)} lines, {len(parsed)} valid")

print("Wrapped removals do not get worse (E23)")
if os.path.exists(OLD_HOOK):
    for cmd in [
        f"bash -c 'git rm {T}'",
        f"TEST_EDIT_OK=1 bash -c 'git rm {T}'",
        f"sed -i '' 's/it(/xit(/' {T}",
        f"python3 -c \"import os; os.remove('{T}')\"",
        f"cat <<EOF\nrm {T}\nEOF",
        f"git rm docs/x.md && echo done",
    ]:
        root = make_root()
        rc, _ = run(bash(cmd), root)
        orc, _ = run(bash(cmd), root, hook=OLD_HOOK)
        check(f"E23 same rc as 0.2.5 ({orc}) for {cmd!r:.60}", rc == orc, f"new={rc} old={orc}")
else:
    check("E23 fixture of the 0.2.5 hook is present", False, OLD_HOOK)

print("Delivery (E24)")
repo = HERE.parent.parent.parent
vers = []
for m in (repo / "plugins/loopkit/.claude-plugin/plugin.json", repo / ".claude-plugin/marketplace.json"):
    mm = re.search(r'"version"\s*:\s*"([^"]+)"', m.read_text(encoding="utf-8"))
    vers.append(mm.group(1) if mm else "")
check("E24 both manifests carry the same version", len(set(vers)) == 1 and vers[0] != "", str(vers))
as_tuple = lambda v: tuple(int(x) for x in v.split(".")[:3]) if re.fullmatch(r"\d+\.\d+\.\d+", v) else (0, 0, 0)
check("E24 that version is higher than 0.2.5", as_tuple(vers[0]) > (0, 2, 5), str(vers))

print()
print(f"{total - fails}/{total} checks passed" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
