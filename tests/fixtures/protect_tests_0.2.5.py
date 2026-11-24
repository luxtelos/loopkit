#!/usr/bin/env python3
"""protect_tests.py — strategic friction on the one thing agents most want to delete.

Anthropic's long-running-agent harness puts it in the imperative: "It is
unacceptable to remove or edit tests." A rule in a prompt is forgotten under
pressure; this hook is not. PreToolUse on Write|Edit|MultiEdit|Bash:

  * a Write/Edit to a TEST FILE that lowers its test count is blocked
    (adding tests, renaming, refactoring with the same count all pass);
  * a Bash `rm` / `git rm` / `mv` that names a test path is blocked;
  * if the project keeps `state/features.json` (a feature list the agent
    may only flip to passes), any edit that changes anything except a
    `passes` value is blocked.

Test files are matched by .loopkit/test-globs.txt (defaults below). Escape:
TEST_EDIT_OK=1 for a human-ratified deletion — visible in the transcript.
Exit 2 blocks; every other path is exit 0, including every parse error.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_GLOBS = [
    "tests/**", "test/**", "spec/**", "__tests__/**",
    "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/*_test.go", "**/*Test.java",
]
TEST_TOKEN = re.compile(
    r"\b(?:it|test|describe)\s*\(|\bdef\s+test_\w+|#\[test\]|\bfunc\s+Test\w+\s*\(|@Test\b",
)
DESTRUCTIVE = re.compile(r"(?:\A|[;&|]\s*|\n\s*)(?:rm|git\s+rm|mv|git\s+mv)\b([^\n;&|]*)", re.I)


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def load_globs(root: Path) -> list[str]:
    try:
        lines = (root / ".loopkit" / "test-globs.txt").read_text(encoding="utf-8").splitlines()
        globs = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
        return globs or DEFAULT_GLOBS
    except Exception:
        return DEFAULT_GLOBS


def rel(root: Path, path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        return str(p.resolve().relative_to(root.resolve())).replace(os.sep, "/")
    except Exception:
        return str(p).replace(os.sep, "/")


def is_test_path(root: Path, path: str, globs: list[str]) -> bool:
    r = rel(root, path)
    for g in globs:
        if fnmatch.fnmatch(r, g) or fnmatch.fnmatch("/" + r, "/" + g):
            return True
        # `tests/**` should also match a file directly under tests/
        if g.endswith("/**") and (r + "/").startswith(g[:-2]):
            return True
    return False


def test_count(text: str) -> int:
    return len(TEST_TOKEN.findall(text or ""))


def only_passes_changed(old: object, new: object) -> bool:
    """features.json rule: agents may flip `passes`; nothing else may change."""
    if isinstance(old, dict) and isinstance(new, dict):
        if set(old) != set(new):
            return False
        return all(k == "passes" or only_passes_changed(old[k], new[k]) for k in old)
    if isinstance(old, list) and isinstance(new, list):
        return len(old) == len(new) and all(only_passes_changed(a, b) for a, b in zip(old, new))
    return old == new


def block(msg: str) -> int:
    print(
        f"BLOCKED [protect_tests]: {msg}\n"
        "Tests are the loop's evidence; removing one is a human decision. If a person "
        "ratified it, re-run with TEST_EDIT_OK=1 so the override is visible.",
        file=sys.stderr,
    )
    return 2


def main() -> int:
    if os.getenv("TEST_EDIT_OK") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        return 0
    root = project_root()
    globs = load_globs(root)

    if tool == "Bash":
        cmd = str(ti.get("command") or "")
        for m in DESTRUCTIVE.finditer(cmd):
            try:
                tokens = shlex.split(m.group(1))
            except ValueError:
                tokens = m.group(1).split()
            for t in tokens:
                if t.startswith("-"):
                    continue
                if is_test_path(root, t, globs):
                    return block(f"`{cmd.strip()[:120]}` removes or moves a test path: {t}")
        return 0

    path = str(ti.get("file_path") or "")
    if not path:
        return 0
    r = rel(root, path)

    # features.json: only `passes` may change
    if r == "state/features.json" and tool in {"Write", "Edit", "MultiEdit"}:
        try:
            old = json.loads((root / r).read_text(encoding="utf-8")) if (root / r).exists() else None
        except Exception:
            old = None
        new_text = None
        if tool == "Write":
            new_text = str(ti.get("content") or "")
        elif tool == "Edit" and old is not None:
            cur = (root / r).read_text(encoding="utf-8")
            new_text = cur.replace(str(ti.get("old_string") or ""), str(ti.get("new_string") or ""), 1)
        if old is not None and new_text is not None:
            try:
                new = json.loads(new_text)
            except Exception:
                return block("state/features.json would no longer be valid JSON")
            if not only_passes_changed(old, new):
                return block("state/features.json: only a `passes` value may change; features are never added, removed or renamed by the loop")
        return 0

    if not is_test_path(root, path, globs):
        return 0

    if tool == "Write":
        new_text = str(ti.get("content") or "")
        try:
            old_text = (root / r).read_text(encoding="utf-8") if (root / r).exists() else ""
        except Exception:
            old_text = ""
        before, after = test_count(old_text), test_count(new_text)
        if old_text and after < before:
            return block(f"{r}: test count would drop {before} → {after}")
        return 0

    if tool == "Edit":
        before, after = test_count(str(ti.get("old_string") or "")), test_count(str(ti.get("new_string") or ""))
        if after < before:
            return block(f"{r}: this edit removes {before - after} test(s)")
        return 0

    if tool == "MultiEdit":
        edits = ti.get("edits") or []
        before = sum(test_count(str(e.get("old_string") or "")) for e in edits if isinstance(e, dict))
        after = sum(test_count(str(e.get("new_string") or "")) for e in edits if isinstance(e, dict))
        if after < before:
            return block(f"{r}: these edits remove {before - after} test(s)")
        return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
