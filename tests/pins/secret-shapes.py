#!/usr/bin/env python3
"""secret-shapes.py — every token shape check-tools.py must refuse.

WHY A LIST. On 2026-09-07 a `gho_` token leaked into a process table and a chat
window. The scanner did not match `gho_` — the shape `gh auth token` returns,
and therefore the likeliest token an agent on a developer machine ever holds.
Nothing had ever written down what the scanner was supposed to cover, so nobody
noticed the gap until it cost a rotation.

The MUST-MISS half matters as much: a scanner that flags ordinary prose gets
switched off, and a scanner that is off catches nothing.
"""
import os, re, subprocess, sys
_ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=os.path.dirname(os.path.abspath(__file__)),
                       capture_output=True, text=True).stdout.strip()
assert _ROOT, "not inside a git checkout"
os.chdir(_ROOT)
sys.path.insert(0, "plugins/loopkit/scripts")

import importlib.util
spec = importlib.util.spec_from_file_location("ct", "plugins/loopkit/scripts/check-tools.py")
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)

MUST_CATCH = {
    "GitHub personal":        "ghp_" + "A" * 36,
    "GitHub OAuth (gh cli)":  "gho_" + "B" * 36,   # the one that leaked
    "GitHub user-to-server":  "ghu_" + "C" * 36,
    "GitHub server-to-server":"ghs_" + "D" * 36,
    "GitHub refresh":         "ghr_" + "E" * 36,
    "GitHub fine-grained":    "github_pat_" + "F" * 30,
    "Stripe live":            "sk_live_" + "G" * 24,
    "Stripe test":            "sk_test_" + "H" * 24,
    "Slack bot":              "xoxb-" + "1" * 20,
    "Context7":               "ctx7sk-" + "i" * 20,
    "AWS access key id":      "AKIA" + "J" * 16,
    "Anthropic":              "sk-ant-" + "k" * 30,
    "OpenAI project":         "sk-proj-" + "L" * 30,
    "JWT":                    "eyJ" + "m" * 24 + "." + "n" * 24,
}

MUST_MISS = {
    "the word token":         "set the token in your environment before running",
    "a variable name":        "GH_TOKEN is read from the environment, never written down",
    "a short sha":            "commit ghp_ is not a token without a body",
    "prose about github":     "see github.com/settings/tokens to rotate it",
    "a redacted value":       "Authorization: Bearer <redacted>",
}

fails = 0
print("MUST CATCH")
for name, sample in MUST_CATCH.items():
    hit = bool(ct.SECRET.search(sample))
    print(f"  {'ok  ' if hit else 'FAIL'} {name}")
    if not hit:
        fails += 1

print("MUST NOT CATCH (a noisy scanner gets switched off)")
for name, sample in MUST_MISS.items():
    hit = bool(ct.SECRET.search(sample))
    print(f"  {'ok  ' if not hit else 'FAIL'} {name}")
    if hit:
        fails += 1

print("SECRET SHAPES PASS" if fails == 0 else f"{fails} FAILURE(S)")
raise SystemExit(1 if fails else 0)
