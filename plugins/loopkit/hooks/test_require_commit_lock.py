#!/usr/bin/env python3
"""Exercise require_commit_lock.py.

Cases are held as data and fed to the hook on stdin, never put on a command
line: the session's own PreToolUse hooks inspect command lines, and a guard
that blocks its own test harness is the same class of bug as a guard that
blocks the commit describing it.

usage: python3 hooks/test_require_commit_lock.py [path-to-hook]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("require_commit_lock.py"))

# Split literals so this file's own content never reads as a runnable command.
GIT = "git"
COMMIT = " commit"

MUST_BLOCK = [
    ("bare commit", f"{GIT}{COMMIT} -m 'x'"),
    ("add then commit", f"{GIT} add a.md && {GIT}{COMMIT} -m 'x'"),
    ("pathspec form is still two calls on a shared index",
     f"{GIT} add a.md && {GIT}{COMMIT} -m 'x' -- a.md"),
    ("after a cd", f"cd /tmp/x; {GIT}{COMMIT} -m 'x'"),
    ("with -C", f"{GIT} -C /tmp/x{COMMIT} -m 'x'"),
    ("with -c config", f"{GIT} -c user.name=t{COMMIT} -m 'x'"),
    ("newline separated", f"echo hi\n{GIT}{COMMIT} -F m.txt"),
    ("inside a subshell", f"({GIT}{COMMIT} -m 'x')"),
    # Heredoc controls: only a heredoc body that is DATA is skipped.
    ("a commit after the heredoc ends",
     f"cat > m.txt <<'EOF'\nsubject line\nEOF\n{GIT}{COMMIT} -F m.txt"),
    ("a commit on the operator's own line",
     f"cat > m.txt <<'EOF' && {GIT}{COMMIT} -F m.txt\nsubject line\nEOF"),
    ("a heredoc fed to bash is code",
     f"bash <<'EOF'\n{GIT} add a.md && {GIT}{COMMIT} -m 'x'\nEOF"),
    ("a heredoc piped into sh is code",
     f"cat <<'EOF' | sh\n{GIT}{COMMIT} -m 'x'\nEOF"),
    ("a here-string (<<<) opens no heredoc",
     f"grep x <<< 'EOF'\n{GIT}{COMMIT} -m 'x'"),
    ("a commit after two heredocs on one line",
     f"paste <<A <<'B'\none\nA\ntwo\nB\n{GIT}{COMMIT} -m 'x'"),
    # Independent review FAIL on e1924ff (2026-10-03): twelve commit shapes
    # main blocked and the first heredoc version let through. A `<<` that is
    # not a heredoc must open nothing...
    ('a quoted "<<EOF", then a commit on the next line',
     f'echo "<<EOF"\n{GIT}{COMMIT} -m x'),
    ("a single-quoted '<<EOF', then a commit", f"echo '<<EOF'\n{GIT}{COMMIT} -m x"),
    ("grep for '<<EOF', then a commit", f"grep -n '<<EOF' notes.md\n{GIT}{COMMIT} -m x"),
    ("a # comment naming <<EOF, then a commit", f"# write it with <<EOF\n{GIT}{COMMIT} -m x"),
    ("an arithmetic shift $((1<<X)), then a commit", f"echo $((1<<X))\n{GIT}{COMMIT} -m x"),
    ("an escaped \\<<EOF, then a commit", f"echo \\<<EOF\n{GIT}{COMMIT} -m x"),
    ("<<E\\OF (bash reads delimiter EOF), commit after it",
     f"cat > f <<E\\OF\nhi\nEOF\n{GIT}{COMMIT} -m x"),
    # ...and a body that a shell runs must stay visible, whatever word comes first.
    ("sudo bash <<EOF", f"sudo bash <<'EOF'\n{GIT}{COMMIT} -m x\nEOF"),
    ("env bash <<EOF", f"env bash <<'EOF'\n{GIT}{COMMIT} -m x\nEOF"),
    ("exec sh <<EOF", f"exec sh <<'EOF'\n{GIT}{COMMIT} -m x\nEOF"),
    ("cat <<EOF | sudo sh", f"cat <<'EOF' | sudo sh\n{GIT}{COMMIT} -m x\nEOF"),
    ("docker exec -i c sh <<EOF", f"docker exec -i c sh <<'EOF'\n{GIT}{COMMIT} -m x\nEOF"),
]

MUST_ALLOW = [
    ("through the wrapper", 'bash "$P/scripts/loop-commit.sh" -m "x" -- a.md'),
    ("through the lock directly",
     f'python3 "$P/scripts/driver_lock.py" run -- {GIT}{COMMIT} -m "x"'),
    ("staging alone is harmless", f"{GIT} add a.md b.md"),
    ("reads pass", f"{GIT} log --oneline -5"),
    ("status passes", f"{GIT} status --short"),
    ("show passes", f"{GIT} show --name-only HEAD"),
    # The guard must not block writing ABOUT the guard.
    ("prose mentioning it", f'echo "never run a bare {GIT}{COMMIT} here"'),
    ("a path that merely contains the word",
     f"{GIT} add docs/commit-policy.md"),
    # Found 2026-10-02: an issue body written through a heredoc said
    # "`git add x && git commit -F m` passes there". The `&&` inside the PROSE
    # read as a command position, and `gh issue create` was refused.
    ("heredoc prose (the 2026-10-02 case)",
     f"cat > /tmp/issue.md <<'EOF'\nSo `{GIT} add x && {GIT}{COMMIT} -F m` passes there.\nEOF\n"
     "gh issue create --title t --body-file /tmp/issue.md"),
    ("heredoc prose, unquoted delimiter",
     f"cat > b.md <<EOF\nstep two:\n{GIT}{COMMIT} -m 'x'\nEOF"),
    ("heredoc prose, <<- with a tab-indented terminator",
     f"cat > b.md <<-END\n\t{GIT}{COMMIT} -m 'x'\n\tEND"),
    ("prose in two heredocs on one line",
     f"paste <<A <<'B'\none; {GIT}{COMMIT} -m x\nA\ntwo && {GIT}{COMMIT} -m y\nB\necho done"),
    ('prose that only quotes "<<EOF"',
     f'echo "use <<EOF for {GIT}{COMMIT} messages"'),
    ("heredoc prose as a PR body",
     f"gh pr create --title t --body-file - <<'MSG'\n1. {GIT} add a.md\n2. {GIT}{COMMIT} -m x\nMSG"),
    ("inline door", f"LOOPKIT_COMMIT_UNLOCKED=1 {GIT}{COMMIT} -m 'x'"),
    ("inline door after another assignment",
     f"FOO=1 LOOPKIT_COMMIT_UNLOCKED=1 {GIT}{COMMIT} -m 'x'"),
]


def rc(command: str, env: dict) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    p = subprocess.run(
        [sys.executable, HOOK], input=payload, text=True,
        capture_output=True, env=env,
    )
    return p.returncode


fails = 0
base_env = {k: v for k, v in os.environ.items() if k != "LOOPKIT_COMMIT_UNLOCKED"}

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "proj"
    (root / ".loopkit").mkdir(parents=True)
    (root / ".loopkit" / "scopes.json").write_text("{}\n")   # init ran here
    env = dict(base_env, CLAUDE_PROJECT_DIR=str(root))

    print("MUST BLOCK (exit 2):")
    for name, cmd in MUST_BLOCK:
        got = rc(cmd, env)
        ok = got == 2
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {name}")

    print("MUST ALLOW (exit 0):")
    for name, cmd in MUST_ALLOW:
        got = rc(cmd, env)
        ok = got == 0
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got}  {name}")

    print("DOORS AND SCOPE:")
    checks = [
        ("env door opens it",
         rc(f"{GIT}{COMMIT} -m 'x'", dict(env, LOOPKIT_COMMIT_UNLOCKED="1")), 0),
    ]
    # A project that names the pattern in block-disabled.txt has decided.
    (root / ".loopkit" / "block-disabled.txt").write_text("commit-without-lock\n")
    checks.append(("block-disabled.txt switches it off", rc(f"{GIT}{COMMIT} -m 'x'", env), 0))
    (root / ".loopkit" / "block-disabled.txt").write_text("git-rebase\n")
    checks.append(("an unrelated disabled name leaves it on", rc(f"{GIT}{COMMIT} -m 'x'", env), 2))

    # No .loopkit/ means this is not a LoopKit project: stay entirely out of it.
    bare = Path(tmp) / "bare"
    bare.mkdir()
    checks.append(("passive in a non-LoopKit repo",
                   rc(f"{GIT}{COMMIT} -m 'x'", dict(base_env, CLAUDE_PROJECT_DIR=str(bare))), 0))

    # A `.loopkit/` directory ALONE is not a loop. Passive hooks (metrics,
    # session snapshots) create it in any repo a LoopKit-equipped session
    # touches; found in the wild on 2026-09-07, where the main checkout held
    # nothing but a metrics.jsonl and this gate was ready to refuse every
    # commit in it.
    touched = Path(tmp) / "touched"
    (touched / ".loopkit").mkdir(parents=True)
    (touched / ".loopkit" / "metrics.jsonl").write_text("{}\n")
    checks.append(("a hook-created .loopkit/ is not a loop",
                   rc(f"{GIT}{COMMIT} -m 'x'", dict(base_env, CLAUDE_PROJECT_DIR=str(touched))), 0))
    # ...but the queue is.
    (touched / "state").mkdir()
    (touched / "state" / "triage.md").write_text("| finding |\n")
    checks.append(("state/triage.md marks a real loop",
                   rc(f"{GIT}{COMMIT} -m 'x'", dict(base_env, CLAUDE_PROJECT_DIR=str(touched))), 2))

    for name, got, want in checks:
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'}  rc={got} (want {want})  {name}")

    print("FAIL-OPEN:")
    p = subprocess.run([sys.executable, HOOK], input="{nope", text=True,
                       capture_output=True, env=env)
    ok = p.returncode == 0
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'FAIL'}  rc={p.returncode}  junk stdin allows")

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
