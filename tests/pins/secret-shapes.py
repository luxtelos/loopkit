#!/usr/bin/env python3
"""secret-shapes.py — every token shape the scanners must refuse, and every
ordinary string they must not.

WHY A LIST. On 2026-09-07 a `gho_` token leaked into a process table and a chat
window. The scanner did not match `gho_` — the shape `gh auth token` returns,
and therefore the likeliest token an agent on a developer machine ever holds.
Nothing had ever written down what the scanner was supposed to cover, so nobody
noticed the gap until it cost a rotation.

The MUST-MISS half matters as much: a scanner that flags ordinary prose gets
switched off, and a scanner that is off catches nothing.

FOUR THINGS ARE CHECKED, and each can fail on its own:

  1. Every shape is caught, through `check-tools.py`'s own `SECRET` — so this
     pin proves the SCANNER is armed, not merely that a regex in a library
     compiles.
  2. Nothing ordinary is caught.
  3. COVERAGE: every entry in `loopkit_core.secrets.SHAPES` has a MUST_CATCH
     row here. This is the rule that makes the pin load-bearing rather than
     decorative — a shape added to the table without a case fails this file, so
     "add the shape and pin it" is one step, not two, and cannot be half done.
  4. The encodings that hide a token in ordinary config are decoded, the one
     that is not real-world is documented as skipped, and — the property that
     matters most — the Bash guard's refusal NEVER contains the secret it
     caught. A guard that echoes what it found is the same leak by another
     route.
"""
import os, re, subprocess, sys
_ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=os.path.dirname(os.path.abspath(__file__)),
                       capture_output=True, text=True).stdout.strip()
assert _ROOT, "not inside a git checkout"
os.chdir(_ROOT)
sys.path.insert(0, "plugins/loopkit/scripts")
sys.path.insert(0, "plugins/loopkit")

import importlib.util
spec = importlib.util.spec_from_file_location("ct", "plugins/loopkit/scripts/check-tools.py")
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)

from loopkit_core import secrets as S  # the table itself

HOOK = "plugins/loopkit/hooks/block_dangerous.py"

# Bodies are the realistic length of a live token (36-40 for the GitHub
# family), not the shortest thing that could be one. Keyed BY SHAPE NAME so the
# coverage check below is exact rather than approximate.
MUST_CATCH = {
    # shape name          label                        sample
    "github":            ("GitHub OAuth (gh cli)",     "gho_" + "B" * 36),   # the one that leaked
    "github_pat":        ("GitHub fine-grained",       "github_pat_" + "F" * 30),
    "stripe":            ("Stripe live",               "sk_live_" + "G" * 24),
    "stripe_restricted": ("Stripe restricted",         "rk_live_" + "R" * 24),
    "slack":             ("Slack bot",                 "xoxb-" + "1" * 20),
    "slack_app":         ("Slack app-level",           "xapp-1-A0" + "2" * 20),
    "context7":          ("Context7",                  "ctx7sk-" + "i" * 20),
    "aws_key_id":        ("AWS access key id",         "AKIA" + "J" * 16),
    "anthropic":         ("Anthropic",                 "sk-ant-" + "k" * 30),
    "openai_proj":       ("OpenAI project",            "sk-proj-" + "L" * 30),
    "openai_legacy":     ("OpenAI legacy",             "sk-" + "M" * 48),
    "jwt":               ("JWT",                       "eyJ" + "m" * 24 + "." + "n" * 24),
    # This repo's own toolchain — not hypothetical here, these are the tokens
    # an agent in this repo holds on an ordinary day.
    "doppler":           ("Doppler",                   "dp.pt." + "d" * 40),
    "render":            ("Render",                    "rnd_" + "r" * 28),
    "gitlab":            ("GitLab PAT",                "glpat-" + "g" * 20),
    "google_api":        ("Google API key",            "AIza" + "o" * 35),
    "npm":               ("npm token",                 "npm_" + "p" * 36),
    "huggingface":       ("HuggingFace",               "hf_" + "q" * 34),
    "sendgrid":          ("SendGrid",                  "SG." + "s" * 22 + "." + "t" * 43),
    "twilio":            ("Twilio API key SID",        "SK" + "0123456789abcdef" * 2),
    # Context-bound on purpose: a bare 40-char blob is also every git SHA, so
    # the label is required. See the module docstring.
    "aws_secret_key":    ("AWS secret access key",     'aws_secret_access_key = "' + "u" * 40 + '"'),
    "private_key":       ("PEM private key block",     "-----BEGIN RSA PRIVATE KEY-----"),
}

# Variants WITHIN a shape. One regex covers a whole family, so the shape-keyed
# table above has one row for it — but the family members still each need a
# case, or widening a character class silently stops being tested. Every entry
# main pinned separately before the table existed lives on here.
VARIANTS = {
    "GitHub personal":         "ghp_" + "A" * 36,
    "GitHub user-to-server":   "ghu_" + "C" * 36,
    "GitHub server-to-server": "ghs_" + "D" * 36,
    "GitHub refresh":          "ghr_" + "E" * 36,
    "Stripe test":             "sk_test_" + "H" * 24,
    "Stripe restricted test":  "rk_test_" + "T" * 24,
    "Slack session (xoxs)":    "xoxs-" + "2" * 20,
    "Slack refresh (xoxr)":    "xoxr-" + "3" * 20,
    "Doppler service token":   "dp.st." + "e" * 40,
    "Doppler CLI token":       "dp.ct." + "f" * 40,
    "PEM EC private key":      "-----BEGIN EC PRIVATE KEY-----",
    "PEM plain private key":   "-----BEGIN PRIVATE KEY-----",
    "AWS secret, colon form":  'AWS_SECRET_ACCESS_KEY: "' + "v" * 40 + '"',
}

MUST_MISS = {
    "the word token":         "set the token in your environment before running",
    "a variable name":        "GH_TOKEN is read from the environment, never written down",
    "a short sha":            "commit ghp_ is not a token without a body",
    "prose about github":     "see github.com/settings/tokens to rotate it",
    "a redacted value":       "Authorization: Bearer <redacted>",
    # Added with the Bash guard. Each is a thing somebody types in this repo on
    # an ordinary day; any one of them firing gets the guard switched off.
    "a 40-hex git sha":       "git show 0123456789abcdef0123456789abcdef01234567",
    "a git range":            "git diff 769faba1e2c3d4e5f60718293a4b5c6d7e8f9012..HEAD",
    "an env-var reference":   'curl -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com',
    "a braced env ref":       'ssh host "echo ${DOPPLER_TOKEN}"',
    "a docs placeholder":     "Authorization: Bearer YOUR_PERSONAL_ACCESS_TOKEN_HERE",
    "an npx server spec":     "npx -y @modelcontextprotocol/server-github",
    "a uuid":                 "550e8400-e29b-41d4-a716-446655440000",
    "an absolute path":       "/opt/homebrew/lib/node_modules/@scope/server/dist",
    # A commit message ABOUT the guard must pass, or the guard blocks the very
    # commit that describes it. This is the lesson block_dangerous.py already
    # learned once, in its own docstring.
    "a commit msg about it":  "fix: the secret scanner did not know the gho_ token shape that leaked",
    "a PostHog project key":  "posthog.init('phc_" + "a" * 43 + "')",  # public by design
    "prose about a key file": "the deploy key lives in ~/.ssh/id_ed25519, never in the repo",
}

# Encodings. A token is caught under each of these because each one occurs in
# ordinary config and URLs without anybody trying to hide anything.
_TOK = "gho_" + "V" * 36
import base64 as _b64
ENCODINGS = {
    "glued to a word char":      "my_" + _TOK,                      # the leading-\b bug
    "base64 of the whole token": _b64.b64encode(_TOK.encode()).decode(),
    "percent-encoded":           "https://h/?t=gho%5F" + "V" * 36,
    r"\u-escaped in JSON":       '{"t":"gho\\u005F' + "V" * 36 + '"}',
    "split on a line continuation": "ssh host TOK=gho_" + "V" * 10 + "\\\n" + "V" * 26,
}
# Deliberately NOT decoded — stated so a reader knows it is a decision, not an
# oversight. A reversed token is not a shape anybody produces by accident, and
# an agent set on evading this guard has cheaper routes than `rev`.
SKIPPED_ENCODINGS = {"reversed": _TOK[::-1]}

fails = 0


def check(cond: bool, label: str) -> None:
    global fails
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        fails += 1


print("MUST CATCH")
for shape, (label, sample) in MUST_CATCH.items():
    check(bool(ct.SECRET.search(sample)), f"{label} [{shape}]")

print("MUST CATCH — family members inside a shape")
for label, sample in VARIANTS.items():
    check(bool(ct.SECRET.search(sample)), label)

print("MUST NOT CATCH (a noisy scanner gets switched off)")
for name, sample in MUST_MISS.items():
    check(not ct.SECRET.search(sample), name)

print("COVERAGE (a shape with no pin is a shape nobody proved)")
tabled = {n for n, _ in S.SHAPES}
pinned = set(MUST_CATCH)
check(not (tabled - pinned), f"every shape in the table is pinned (unpinned: {sorted(tabled - pinned) or 'none'})")
check(not (pinned - tabled), f"every pinned shape is in the table (stale: {sorted(pinned - tabled) or 'none'})")
# One table, one regex, two callers. Same OBJECT, not merely same behaviour:
# a second copy would pass a behaviour check today and drift by next month.
check(ct.SECRET is S.SECRET, "check-tools.py and the Bash guard share one regex object")

print("ENCODINGS (decoded because they happen, not to win a game)")
for name, sample in ENCODINGS.items():
    check(bool(S.scan(sample)), name)
for name, sample in SKIPPED_ENCODINGS.items():
    check(not S.scan(sample), f"{name} is deliberately NOT decoded (documented non-goal)")

print("THE GUARD REFUSES, AND NEVER PRINTS WHAT IT CAUGHT")
# The real leaking command shape: a token inline on an SSH command line, which
# is where it enters the remote host's process table.
LEAK = f"TOKEN={_TOK} ssh deploy@build-host 'echo $TOKEN'"
p = subprocess.run([sys.executable, HOOK], input='{"tool_name":"Bash","tool_input":{"command":%s}}' % __import__("json").dumps(LEAK),
                   capture_output=True, text=True)
out = p.stdout + p.stderr
check(p.returncode == 2, "an inline token on an ssh command line is refused (rc=2)")
check(_TOK not in out, "the token does not appear in the refusal")
check(_TOK[4:] not in out, "the token BODY does not appear in the refusal")
check(not S.scan(out), "no shape at all is matchable in the refusal")
check("github" in out, "the refusal names the SHAPE it caught")
check("stdin" in out, "the refusal says what to do instead")

# A command that trips an OLD pattern while ALSO carrying a secret must not
# reach the message that quotes the command back. This is the regression the
# pre-existing refusal message had: it printed `{cmd}` verbatim.
BOTH = f"git add -A && curl -H 'Authorization: Bearer {_TOK}' https://api.github.com"
p2 = subprocess.run([sys.executable, HOOK], input='{"tool_name":"Bash","tool_input":{"command":%s}}' % __import__("json").dumps(BOTH),
                    capture_output=True, text=True)
out2 = p2.stdout + p2.stderr
check(p2.returncode == 2, "a command that is both dangerous and secret-bearing is refused")
check(_TOK not in out2, "and its refusal does not print the secret either")

print("SECRET SHAPES PASS" if fails == 0 else f"{fails} FAILURE(S)")
raise SystemExit(1 if fails else 0)
