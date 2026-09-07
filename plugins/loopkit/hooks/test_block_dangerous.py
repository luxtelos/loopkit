#!/usr/bin/env python3
"""Exercise block_dangerous.py against cases held as data, not as shell literals.

The cases must live in a file: putting them on a command line means the
session's own PreToolUse hook inspects them and refuses to run the test — the
guard blocks its own test harness. Same class as the guard blocking the commit
that described it.

usage: python3 hooks/test_block_dangerous.py [path-to-hook]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("block_dangerous.py"))

# Synthetic bodies, assembled from parts, at the length a live token actually
# is. Never a real value in a tracked file — and assembled rather than written
# out so that a scanner reading THIS file reports a test fixture, not a find.
_GHO = "gho_" + "V" * 36          # the 2026-09-07 shape
_DP = "dp.pt." + "d" * 40         # Doppler: this repo's own toolchain
_RND = "rnd_" + "r" * 28          # Render: likewise

MUST_ALLOW = [
    ("named add then commit -F", "git" " add foo.txt && git commit -F msg.txt"),
    ("chained, -F flag", "git" " add a.md b.md && git commit -q -F /tmp/m.txt"),
    ("dotted path", "git" " add .claude/hooks/x.py"),
    ("./ prefix", "git" " add ./scripts/x.sh"),
    (".gitignore by name", "git" " add .gitignore"),
    ("read-only PR view", "gh" " pr view 2404 --json state"),
    ("prose mentioning it", 'echo "do not use git add -A"'),
    ("prose mentioning merge", 'echo "the loop never runs gh pr merge"'),
    # The secret guard's must-not-block half. Every one of these is something
    # somebody types here on an ordinary day; any one of them firing is how the
    # guard gets switched off, and a guard that is off catches nothing.
    ("an env-var reference", 'ssh host "echo $CODEX_GITHUB_PAT"'),
    ("a braced env ref", 'doppler run -- sh -c "echo ${DOPPLER_TOKEN}"'),
    ("a 40-hex git sha", "git" " show 0123456789abcdef0123456789abcdef01234567"),
    ("prose about the token that leaked", 'echo "the gho_ shape was the one that leaked"'),
    ("a credential helper, value never in the text", "gh" " auth token | ssh host 'cat > .tok'"),
    ("an env-file, value never in the text", "docker" " run --rm --env-file .env node:22-bookworm"),
]
MUST_BLOCK = [
    ("real force-add", "git" " add -f secret.env"),
    ("--force", "git" " add --force x"),
    ("add-all -A", "git" " add -A"),
    ("add-all --all", "git" " add --all"),
    ("add-all dot", "git" " add ."),
    ("add-all dot-slash", "git" " add ./"),
    ("add-all dot then semicolon", "git" " add .;"),
    ("add-all dot-slash then chain", "git" " add ./ && echo done"),
    ("add-all dot then pipe", "git" " add .| cat"),
    ("pr merge", "gh" " pr merge 2404"),
    ("self-approve", "gh" " pr review 2404 --approve"),
    ("api merge", "gh" " api -X PUT repos/o/r/pulls/12/merge"),
    ("force push", "git" " push --force origin main"),
    ("hard reset", "git" " reset --hard HEAD~3"),
    ("rm -rf", "rm" " -rf build/"),
    ("tool cache add", "git" " add .mempalace/"),
    # The vector that actually burned a token on 2026-09-07: a value inline on
    # an SSH command line, into the remote host's process table.
    ("inline token on an ssh command line", f"TOK={_GHO} ssh deploy@build-host 'echo $TOK'"),
    ("a doppler token inline", f"DOPPLER_TOKEN={_DP} doppler secrets"),
    ("a render token in a curl header", f"curl -H 'Authorization: Bearer {_RND}' https://api.render.com/v1/services"),
    ("a token glued to a word char", f"export MY_{_GHO}"),
    ("a token split on a line continuation", "ssh host TOK=gho_" + "V" * 10 + "\\\n" + "V" * 26),
    ("a percent-encoded token in a url", "curl 'https://h/?t=gho%5F" + "V" * 36 + "'"),
]


def rc(cmd: str, env: dict | None = None) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    p = subprocess.run(
        [sys.executable, HOOK], input=payload, capture_output=True, text=True, env=env
    )
    return p.returncode


fails = 0
base_env = dict(os.environ)
base_env.pop("CLAUDE_PROJECT_DIR", None)

# An empty project root: no .loopkit/, so only the built-ins apply.
with tempfile.TemporaryDirectory() as empty_root:
    env = dict(base_env, CLAUDE_PROJECT_DIR=empty_root)

    print("MUST ALLOW (rc must be 0):")
    for name, cmd in MUST_ALLOW:
        got = rc(cmd, env)
        ok = got == 0
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {name}")

    print("MUST BLOCK (rc must be 2):")
    for name, cmd in MUST_BLOCK:
        got = rc(cmd, env)
        ok = got == 2
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {name}")

    print("MUST NEVER CRASH (rc must be 0 on junk input):")
    for name, raw in [("malformed json", "{not json"), ("empty stdin", ""), ("no tool_input", '{"tool_name":"Bash"}')]:
        p = subprocess.run([sys.executable, HOOK], input=raw, capture_output=True, text=True, env=env)
        ok = p.returncode == 0
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={p.returncode}  {name}")

# A configured project root: extras block, disabled built-ins allow.
with tempfile.TemporaryDirectory() as cfg_root:
    lk = Path(cfg_root) / ".loopkit"
    lk.mkdir()
    (lk / "block-patterns.txt").write_text("# project extras\n\\bterraform\\s+destroy\\b\n(unbalanced\n")
    (lk / "block-disabled.txt").write_text("git-rebase\n")
    env = dict(base_env, CLAUDE_PROJECT_DIR=cfg_root)

    print("PROJECT CONFIG (.loopkit/):")
    cases = [
        ("extra pattern blocks", "terraform" " destroy -auto-approve", 2),
        ("broken extra pattern is skipped, gate still works", "git" " add -A", 2),
        ("disabled built-in allows", "git" " rebase main", 0),
        ("other built-ins still block", "git" " reset --hard", 2),
    ]
    for name, cmd, want in cases:
        got = rc(cmd, env)
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got} (want {want})  {name}")

# The property the whole secret branch exists for. A guard that prints what it
# caught has moved the secret from the process table into the transcript, which
# is not a fix, it is a different leak. Checked on the REFUSAL TEXT, not on the
# exit code, because the exit code cannot tell you this.
print("THE REFUSAL NEVER CONTAINS THE SECRET:")
for name, cmd, needle in [
    ("ssh inline token", f"TOK={_GHO} ssh h 'echo $TOK'", _GHO),
    ("doppler token", f"DOPPLER_TOKEN={_DP} doppler secrets", _DP),
    # Both dangerous AND secret-bearing: this must not reach the branch that
    # quotes the command back. It did until 2026-09-07.
    ("dangerous and secret-bearing", f"git add -A && curl -H 'Authorization: Bearer {_GHO}'", _GHO),
]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    p = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True, env=base_env)
    out = p.stdout + p.stderr
    # The body alone, too: a message could drop the prefix and still leak.
    ok = p.returncode == 2 and needle not in out and needle[4:] not in out
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  rc={p.returncode}  {name}")

# Switchable off like any other pattern — a reviewed change to a tracked file,
# which is the only alternative to people rephrasing commands around the guard.
with tempfile.TemporaryDirectory() as off_root:
    lk = Path(off_root) / ".loopkit"
    lk.mkdir()
    (lk / "block-disabled.txt").write_text("secret-on-command-line\n")
    env = dict(base_env, CLAUDE_PROJECT_DIR=off_root)
    print("THE SECRET CHECK IS DISABLEABLE (or it gets worked around):")
    got = rc(f"TOK={_GHO} ssh h 'echo $TOK'", env)
    ok = got == 0
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  disabled by name in block-disabled.txt")
    got = rc("git" " add -A", env)
    ok = got == 2
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  other guards still block")

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
