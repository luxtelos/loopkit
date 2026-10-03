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
    # Control case: commit message from a file, both spellings. `-F` is not
    # `-f` — the force-add rule is case-sensitive and only reads `git add`.
    ("commit -F alone", "git" " commit -F msg.txt"),
    ("commit --file= alone", "git" " commit --file=msg.txt"),
    ("uppercase -F inside an add is not force-add", "git" " add notes.md -F"),
    ("dotted path", "git" " add .claude/hooks/x.py"),
    ("./ prefix", "git" " add ./scripts/x.sh"),
    (".gitignore by name", "git" " add .gitignore"),
    ("read-only PR view", "gh" " pr view 2404 --json state"),
    ("prose mentioning it", 'echo "do not use git add -A"'),
    ("prose mentioning merge", 'echo "the loop never runs gh pr merge"'),
    # Push rules stop at a separator. Found 2026-10-02: a plain push chained
    # into `gh pr create` whose title said "-F ... -f" was refused.
    ("push then a PR title with -f",
     "git" " push -u origin fix/x 2>&1 | tail -2 && gh pr create --title 'a: -F no longer reads as -f'"),
    ("push then rm -f", "git" " push origin main; rm -f /tmp/x"),
    ("push then --delete elsewhere", "git" " push origin fix/a && gh api --method DELETE x --delete"),
    ("push then a colon elsewhere", "git" " push origin a && echo 'time :10'"),
    ("plain push -u", "git" " push -u origin fix/x"),
    ("branch name containing force", "git" " push origin fix-force-thing"),
    ("non-deleting src:dst refspec", "git" " push origin HEAD:refs/heads/fix/x"),
    ("--dry-run and --follow-tags", "git" " push --dry-run --follow-tags origin fix/x"),
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
    ("-f inside a flag cluster", "git" " add -vf x"),
    ("-f after an uppercase flag", "git" " add -Af x"),
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
    ("force push -f", "git" " push -f origin main"),
    ("force push --force-with-lease", "git" " push --force-with-lease origin x"),
    ("force push in a -uf cluster", "git" " push -uf origin x"),
    ("force push via +refspec", "git" " push origin +main"),
    ("force push via +src:dst", "git" " push origin +HEAD:main"),
    ("force push after &&", "cd x && git" " push -f origin main"),
    ("push --delete", "git" " push origin --delete fix/x"),
    ("push -d", "git" " push -d origin fix/x"),
    ("push :branch", "git" " push origin :fix/x"),
    # The vector that actually burned a token on 2026-09-07: a value inline on
    # an SSH command line, into the remote host's process table.
    ("inline token on an ssh command line", f"TOK={_GHO} ssh deploy@build-host 'echo $TOK'"),
    ("a doppler token inline", f"DOPPLER_TOKEN={_DP} doppler secrets"),
    ("a render token in a curl header", f"curl -H 'Authorization: Bearer {_RND}' https://api.render.com/v1/services"),
    ("a token glued to a word char", f"export MY_{_GHO}"),
    ("a token split on a line continuation", "ssh host TOK=gho_" + "V" * 10 + "\\\n" + "V" * 26),
    ("a percent-encoded token in a url", "curl 'https://h/?t=gho%5F" + "V" * 36 + "'"),
]

# Push cases ported verbatim from the downstream project copy's test file
# (independent review PASS in round 3, 2026-10-03). Keep them in step with
# that file.
PUSH_ALLOW = [
    # Shell-aware push check (review FAIL on #2965, 2026-10-03). The push rule
    # now splits the command the way the shell does, so these stay allowed.
    ("push then a PR title with -F and -f (double quotes)",
     "git" ' push -u origin fix/x && gh pr create --title "a: -F no longer reads as -f" --body "z -f"'),
    ("push newline rm -f", "git" " push origin fix/x\nrm -f /tmp/x"),
    ("push 2>&1 | tail", "git" " push origin fix/x 2>&1 | tail -2"),
    ("push -o ci.skip (o takes a value)", "git" " push -o ci.skip origin fix/x"),
    ("push --progress is not a prefix of --prune", "git" " push --progress origin fix/x"),
    ("push --porcelain and --no-verify", "git" " push --porcelain --no-verify origin fix/x"),
    ("push --set-upstream and --tags", "git" " push --set-upstream origin fix/x --tags"),
    ("branch name with -d- inside", "git" " push origin fix/add-d-flag"),
    ("src:dst same name", "git" " push origin main:main"),
    ("push -F is not a git push flag (case-sensitive)", "git" " push -F origin x"),
    ("comment with an apostrophe after a push", "git" " push origin fix/x  # it's our own branch"),
    ("commit message from a heredoc with an apostrophe, then push",
     "git" " commit -m \"$(cat <<'EOF'\nfix(hooks): don't (ever) block this\nEOF\n)\" && git" " push origin fix/x"),
    ("push of the current branch via $(git branch --show-current)",
     "git" " push -u origin $(git branch --show-current)"),
    # Round 2 (2026-10-03). A `$` word AFTER the remote is a refspec name; it
    # stays allowed (blocking it would refuse every `origin "$BRANCH"`).
    ("push origin \"$BRANCH\"", "git" ' push origin "$BRANCH"'),
    ("push -u origin ${BRANCH}", "git" " push -u origin ${BRANCH}"),
    # Unicode look-alike dashes are not flags: git reads `—f` as a remote
    # or refspec name and rejects it. Pinned so they never crash the hook.
    ("em dash f is not -f", "git" " push origin —f"),
    ("en dash f before the remote is not -f", "git" " push –f origin main"),
    ("en dash force is not --force", "git" " push origin main ––force"),
]
PUSH_BLOCK = [
    # Review FAIL on #2965 (2026-10-03): nine shapes dev blocked and the
    # separator fix let through. Each one really force-pushes or deletes.
    ('quoted "-f"', "git" ' push "-f" origin main'),
    ("quoted '--force'", "git" " push origin main '--force'"),
    ('quoted "--delete"', "git" ' push origin "--delete" fix/x'),
    ("; inside a quoted refspec then -f", "git" ' push origin "HEAD:refs/heads/a;b" -f'),
    ("& inside a quoted push-option then --force", "git" " push -o 'x&y' --force origin main"),
    ("| inside a quoted arg then --delete", "git" " push origin 'a|b' --delete fix/x"),
    ("2>&1 before -f", "git" " push 2>&1 -f origin main"),
    ("&>/dev/null before --force", "git" " push origin main &>/dev/null --force"),
    ("$(a; b) before -f", "git" " push $(echo x; echo y) -f origin main"),
    # Shapes the reviewer confirmed still block; keep them blocked.
    ("push $(echo -f)", "git" " push $(echo -f) origin main"),
    ("$(push --force) inside echo", "echo $(git" " push --force origin main)"),
    ("bash -c with a force push", 'bash -c "git' ' push -f origin main"'),
    ("env git push -f", "env git" " push -f origin main"),
    ("subshell (push -f)", "(git" " push -f origin main)"),
    ("push -f in the background", "git" " push -f origin main &"),
    ("|| then push -f", "false || git" " push -f origin main"),
    ("newline chain then push --delete", "cd x\ngit" " push --delete origin y"),
    ("push -fu", "git" " push -fu origin x"),
    ("push -vf", "git" " push -vf origin x"),
    ("second refspec +dev", "git" " push origin main +dev"),
    ("--force-with-lease=ref:sha", "git" " push --force-with-lease=main:abc123 origin main"),
    (">/dev/null 2>&1 before -d", "git" " push >/dev/null 2>&1 -d origin x"),
    # Misses dev also had, closed here because they were cheap.
    ("git -C dir push -f", "git" " -C dir push -f origin main"),
    ("git -c k=v push --force", "git" " -c core.x=y push --force origin main"),
    ('quoted "+main"', "git" ' push origin "+main"'),
    ('quoted ":x"', "git" ' push origin ":fix/x"'),
    ("abbreviated --forc", "git" " push --forc origin main"),
    ("abbreviated --del", "git" " push --del origin fix/x"),
    ("abbreviated --force-w=", "git" " push --force-w=main origin main"),
    ("--mirror (force plus delete)", "git" " push --mirror origin"),
    ("--prune (deletes remote refs)", "git" " push --prune origin refs/heads/*:refs/heads/*"),
    ("backslash-newline before -f", "git" " push origin main \\\n  -f"),
    ("ANSI-C quoted $'-f'", "git" " push $'-f' origin main"),
    ("brace expansion {-f,}", "git" " push origin main {-f,}"),
    ("a quoted ; alone does not end the command", "git" ' push -o ";" -f origin main'),
    ("bash heredoc running a force push", "bash <<EOF\ngit" " push -f origin main\nEOF"),
    # Fail closed: quoting the hook cannot read, on a command that names a push.
    ("unbalanced quote on a push", "git" ' push origin "main -f'),
    # Round 2 (2026-10-03): pushes built at run time by another program.
    ("eval of a force push", 'eval "git' ' push -f origin main"'),
    ("sh -c single-quoted force push", "sh -c 'git" " push -f origin main'"),
    ("bash -c double-quoted --force", 'bash -c "git' ' push --force origin main"'),
    ("xargs git push -f", "echo main | xargs git" " push -f origin"),
    ("env-var prefix GIT_DIR=x", "GIT_DIR=x git" " push -f origin main"),
    ("command git push -f", "command git" " push -f origin main"),
    ("backslash \\git push -f", "\\git" " push -f origin main"),
    # Fail closed: an unexpanded `$` word or substitution where an OPTION can
    # be (before the remote) may expand to -f; the hook cannot know.
    ("unexpanded $F right after push", "git" " push $F origin main"),
    ("unexpanded ${F} right after push", "git" " push ${F} origin main"),
    ("quoted \"$F\" right after push", "git" ' push "$F" origin main'),
    ("unexpanded $F after -u", "git" " push -u $F origin main"),
    ("substitution right after push", "git" " push $(git config loop.pushflag) origin main"),
    ("backtick right after push", "git" " push `cat flagfile` origin main"),
    # Nesting deep enough to blow Python's recursion limit must block, not crash.
    ("3000-deep $( ) around a push", "echo " + "$(" * 3000 + "git" " push origin x" + ")" * 3000),
]
MUST_ALLOW += PUSH_ALLOW
MUST_BLOCK += PUSH_BLOCK


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

# A crash must never fail open on a push (downstream review round 2,
# 2026-10-03): exit 1 lets the command RUN. Faults are forced by patching a
# library the hook calls before it is loaded. Unlike the downstream copy, a crash
# on a command with no push stays an ALLOW here (exit 0), as this file's
# docstring has always promised.
_BREAK_SHLEX = (
    "import shlex\n"
    "def _boom(*a, **k):\n"
    "    raise RuntimeError('forced by test')\n"
    "shlex.shlex = _boom\n"
)
_BREAK_RE = (
    "import re\n"
    "def _boom(*a, **k):\n"
    "    raise RuntimeError('forced by test')\n"
    "re.search = _boom\n"
)


def run_with_fault(cmd: str, prelude: str, env: dict) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    code = prelude + f"\nimport runpy\nrunpy.run_path({HOOK!r}, run_name='__main__')\n"
    p = subprocess.run([sys.executable, "-c", code], input=payload, capture_output=True, text=True, env=env)
    return p.returncode


with tempfile.TemporaryDirectory() as fault_root:
    env = dict(base_env, CLAUDE_PROJECT_DIR=fault_root)
    print("FORCED FAULTS (a crash must never fail open on a push):")
    for name, prelude, cmd, want in [
        ("tokeniser raises on a push -> block", _BREAK_SHLEX, "git" " push origin fix/x", 2),
        ("re.search raises on a push -> block", _BREAK_RE, "git" " push origin fix/x", 2),
        ("re.search raises, no push -> allow (a crash is an allow)", _BREAK_RE, "ls -la", 0),
    ]:
        got = run_with_fault(cmd, prelude, env)
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got} want={want}  {name}")

# The push guard must be linear: `\bgit\b[\s\S]*\bpush\b` once took 11.7 s on
# 48 KB of "git" words. Timed in-process, push guard only, as downstream.
print("PERF (push guard, in-process, limit 1.0s each):")
try:
    import importlib.util
    import time

    _spec = importlib.util.spec_from_file_location("_hook_under_test", HOOK)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _push_reason = _mod.push_danger_reason
except Exception as exc:
    _push_reason = None
    print(f"  FAIL  could not load push_danger_reason from the hook: {exc}")
    fails += 1
if _push_reason is not None:
    for name, cmd in [
        ("48 KB of 'git ' words, no push", "git " * 12000),
        ("48 KB of 'push git ' words", "push git " * 5400),
        ("50 KB heredoc commit message then a push",
         "git commit -F - <<'EOF'\n" + ("don't (stop) the git push -f talk\n" * 1500)
         + "EOF\n" + "git" " push origin fix/x"),
        ("48 KB quoted arg naming git push many times",
         'echo "' + ("git" " push origin x " * 2400) + '"'),
    ]:
        t0 = time.monotonic()
        _push_reason(cmd)
        dt = time.monotonic() - t0
        ok = dt < 1.0
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  {dt:.3f}s  {name}")

# The push rules are still switchable off BY NAME, one at a time. A disabled
# kind is skipped, not returned, so the other kind is still seen in the same
# command; a finding the guard cannot attribute blocks while either is on.
print("PUSH RULES ARE DISABLEABLE BY NAME:")
for disabled, cases in [
    ("git-force-push\n", [
        ("force allowed once git-force-push is off", "git" " push -f origin main", 0),
        ("delete still blocks", "git" " push origin --delete fix/x", 2),
        ("force then delete in one push: delete still seen", "git" " push -f origin main --delete x", 2),
        ("unreadable quoting still blocks (either rule)", "git" ' push origin "main -f', 2),
        ("$F before the remote still blocks (either rule)", "git" " push $F origin main", 2),
        # Independent review FAIL on f093809: a cluster was read only up to
        # its first f or d, so these deleted a branch with the delete rule on.
        ("-fd cluster: the -d is still seen", "git" " push -fd origin y", 2),
        ("-ufd cluster: the -d is still seen", "git" " push -ufd origin y", 2),
        ("-df cluster still blocks", "git" " push -df origin y", 2),
        ("$(echo -fd) still blocks", "git" " push $(echo -fd) origin y", 2),
    ]),
    ("git-push-delete\n", [
        ("delete allowed once git-push-delete is off", "git" " push origin --delete fix/x", 0),
        ("force still blocks", "git" " push -f origin main", 2),
        ("--prune is a delete", "git" " push --prune origin x", 0),
        ("--mirror is both: still blocks", "git" " push --mirror origin", 2),
        ("-df cluster: the -f is still seen", "git" " push -df origin y", 2),
        ("-fd cluster still blocks", "git" " push -fd origin y", 2),
        ("-uf still blocks (force only)", "git" " push -uf origin y", 2),
        ("-ud is a delete: allowed once git-push-delete is off", "git" " push -ud origin y", 0),
    ]),
    ("git-force-push\ngit-push-delete\n", [
        ("both off: force allowed", "git" " push -f origin main", 0),
        ("both off: delete allowed", "git" " push -d origin x", 0),
        ("both off: unreadable quoting allowed", "git" ' push origin "main -f', 0),
    ]),
]:
    with tempfile.TemporaryDirectory() as d_root:
        (Path(d_root) / ".loopkit").mkdir()
        (Path(d_root) / ".loopkit" / "block-disabled.txt").write_text(disabled)
        env = dict(base_env, CLAUDE_PROJECT_DIR=d_root)
        for name, cmd, want in cases:
            got = rc(cmd, env)
            ok = got == want
            fails += 0 if ok else 1
            print(f"  {'ok ' if ok else 'FAIL'}  rc={got} (want {want})  {name}")

# A push refusal names the rule, and never quotes a secret back.
print("A PUSH REFUSAL NAMES ITS RULE:")
with tempfile.TemporaryDirectory() as n_root:
    env = dict(base_env, CLAUDE_PROJECT_DIR=n_root)
    for name, cmd, needle in [
        ("force names git-force-push", "git" " push -f origin main", "[git-force-push]"),
        ("delete names git-push-delete", "git" " push -d origin x", "[git-push-delete]"),
    ]:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
        p = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True, env=env)
        ok = p.returncode == 2 and needle in p.stderr
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={p.returncode}  {name}")
    cmd = "git" f" push -f https://x:{_GHO}@github.com/o/r main"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    p = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True, env=env)
    out = p.stdout + p.stderr
    ok = p.returncode == 2 and _GHO not in out and _GHO[4:] not in out
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  rc={p.returncode}  a force push to a token URL never echoes the token")

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
