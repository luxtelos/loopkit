#!/usr/bin/env python3
"""secrets.py — the one token-shape table, and the only place it is written.

WHY THIS FILE EXISTS AT ALL. On 2026-09-07 a `gho_` token leaked. PR #36 taught
`check-tools.py` the GitHub family and pinned it. But `check-tools.py` reads
`.mcp.json` and nothing else, and the token that actually leaked was never in a
`.mcp.json` — it was passed inline on an SSH command line, where it entered the
remote host's process table (readable by any user with `ps`) and was echoed
into a session transcript. It had to be revoked. So the same table now backs a
`PreToolUse` guard on Bash as well, and it is written HERE, once, because two
copies of one regex are two regexes by the end of the month.

THREE RULES THIS MODULE EXISTS TO KEEP

1. **Report by shape, never by value.** Every public function returns SHAPE
   NAMES (`"github"`, `"doppler"`), never the matched text. A guard that echoes
   what it caught puts the secret in the transcript — the same leak by a
   different route, and the exact failure mode of the pre-existing refusal
   message in `block_dangerous.py`, which printed the whole command back.
   `redact()` exists so any message that must quote a command can do so safely.

2. **A false positive gets the scanner switched off.** A scanner that is off
   catches nothing, so the must-not-catch half of `tests/pins/secret-shapes.py`
   is as load-bearing as the must-catch half. Two shapes were deliberately left
   out for this reason; see DELIBERATELY NOT MATCHED below.

3. **Every shape here has a pin.** `tests/pins/secret-shapes.py` fails if a
   shape in this table has no MUST_CATCH row, so a shape cannot be added
   without evidence that it is caught.

DELIBERATELY NOT MATCHED, and why — a scanner nobody trusts is worse than a
narrow one:

  * **A bare `Authorization: Bearer <opaque blob>`.** The only thing left to
    match on is "30-plus characters of base64-ish text", which is equally the
    shape of a docs placeholder (`Bearer YOUR_PERSONAL_ACCESS_TOKEN_HERE`), a
    content hash, a nonce, and an asset digest. It buys almost no coverage
    either: every bearer token an agent here actually holds already carries a
    distinctive prefix that IS in the table below — `gho_`, `sk-ant-`, `eyJ`,
    `dp.` — so the generic rule adds false-positive surface on top of a shape
    that is already caught.
  * **A bare 40-character AWS secret access key.** `[A-Za-z0-9/+=]{40}` is also
    the shape of every git SHA in the repository, so a bare rule would flag
    every `git show <sha>`. The key IS matched, but only in a labelled
    assignment (`aws_secret_access_key=...`), which a SHA never has.
  * **PostHog `phc_`.** It is a project API key designed to be embedded in
    client-side code and shipped to browsers. Flagging a value whose whole
    purpose is to be public trains people to ignore the scanner.
  * **Reversed text.** A reversed token is not a leak shape anybody produces by
    accident, and no exfiltration is prevented by catching it — an agent that
    wanted to evade this guard has cheaper options than `rev`. Decoding it
    would double the scan cost and widen the false-positive surface for no
    real-world case. Base64, percent- and `\\u`-encoding ARE decoded, because
    those three appear in ordinary config and URLs without anyone trying.
"""
from __future__ import annotations

import base64
import binascii
import re
import urllib.parse

# ---------------------------------------------------------------------------
# The table. (shape name, regex). The NAME is what every message prints, so it
# has to be meaningful on its own, with no value beside it to explain it.
#
# Every alternative uses non-capturing groups only: the combined pattern below
# relies on `match.lastgroup` to name the shape it hit, and a stray capturing
# group inside an alternative would steal that name.
#
# Lengths are the realistic body lengths of live tokens (36-40 for the GitHub
# family), not the shortest thing that could be one. Matching short bodies is
# how a scanner starts flagging prose.
# ---------------------------------------------------------------------------
SHAPES: list[tuple[str, str]] = [
    # --- present before this file existed (PR #36 and earlier) -------------
    ("stripe",        r"sk_(?:live|test)_[A-Za-z0-9]{8,}"),
    ("github",        r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("github_pat",    r"github_pat_[A-Za-z0-9_]{20,}"),
    ("slack",         r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    ("context7",      r"ctx7sk-[A-Za-z0-9-]{8,}"),
    ("aws_key_id",    r"AKIA[0-9A-Z]{16}"),
    ("anthropic",     r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("openai_proj",   r"sk-proj-[A-Za-z0-9_-]{20,}"),
    ("jwt",           r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),

    # --- this repo's own toolchain. These two are why the owner flagged the
    # gap: a Doppler or Render token on a command line is not hypothetical
    # here, it is Tuesday. -------------------------------------------------
    ("doppler",       r"dp\.(?:pt|st|ct|sa|scim|audit)\.[A-Za-z0-9_-]{20,}"),
    ("render",        r"rnd_[A-Za-z0-9]{20,}"),

    # --- the rest of the review's list ------------------------------------
    ("stripe_restricted", r"rk_(?:live|test)_[A-Za-z0-9]{8,}"),
    ("gitlab",        r"glpat-[A-Za-z0-9_-]{16,}"),
    ("google_api",    r"AIza[0-9A-Za-z_-]{35}"),
    ("npm",           r"npm_[A-Za-z0-9]{30,}"),
    ("huggingface",   r"hf_[A-Za-z0-9]{30,}"),
    # Legacy OpenAI. `sk-` then 48 alphanumerics with no dash, so it cannot
    # collide with `sk-ant-` or `sk-proj-`, whose dashes break the run.
    ("openai_legacy", r"sk-[A-Za-z0-9]{48}"),
    ("slack_app",     r"xapp-[A-Za-z0-9-]{16,}"),
    ("sendgrid",      r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),
    ("twilio",        r"SK[0-9a-f]{32}"),
    # Context-bound ON PURPOSE — see DELIBERATELY NOT MATCHED. The label is
    # what separates a key from a git SHA, so the label is required.
    ("aws_secret_key",
     r"(?i:aws[_-]?secret[_-]?access[_-]?key)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}"),
    ("private_key",   r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
]

# The combined pattern. Named groups turn a hit into a shape name without a
# second pass over the table.
#
# NOTE THE ABSENCE OF A LEADING `\b`. It used to be there and it was a plain
# bug: `\b` between two word characters does not exist, so `my_gho_<body>` and
# `TOKEN=gho_<body>`... the second is fine (`=` is not a word char) but the
# first was a silent miss. A token glued to a preceding word character is a
# leak, not a false positive, and there is no prose that gains a `gho_` plus
# twenty body characters by accident.
SECRET = re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in SHAPES))

# Guard rails so a hook on the hot path cannot become the slow thing in the
# session. A command line longer than this is not a command anybody typed.
MAX_SCAN_CHARS = 200_000
MAX_B64_BLOBS = 64

_B64_BLOB = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")


def shapes_in(text: str) -> list[str]:
    """Shape names present in `text` verbatim. Never returns matched values."""
    if not text:
        return []
    seen: list[str] = []
    for m in SECRET.finditer(text[:MAX_SCAN_CHARS]):
        if m.lastgroup and m.lastgroup not in seen:
            seen.append(m.lastgroup)
    return seen


def _decodings(text: str):
    """Yield the readings of `text` a scanner should also check.

    Only transforms that occur in ordinary config and URLs without anyone
    trying to hide anything. Each is independent, so one failing to decode
    never costs the others.
    """
    # A shell line continuation is not an evasion, it is what the shell will
    # actually run. Reconstructing it is reading the command correctly.
    if "\\\n" in text:
        yield text.replace("\\\n", "")

    # Percent-encoding: a token in a URL query string arrives this way on its
    # own, `_` written as `%5F`.
    if "%" in text:
        try:
            yield urllib.parse.unquote(text)
        except Exception:
            pass

    # `\uXXXX` inside a JSON string literal — how a token looks after any
    # JSON encoder that escapes non-ASCII, and after some that escape more.
    if "\\u" in text:
        try:
            yield text.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
        except Exception:
            pass

    # Base64: a token dropped into a config value or an auth header body.
    # Bounded on purpose — a base64 image blob is common and decoding many of
    # them is the one way this scan could get slow.
    for i, m in enumerate(_B64_BLOB.finditer(text)):
        if i >= MAX_B64_BLOBS:
            break
        blob = m.group(0)
        try:
            raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False)
        except (binascii.Error, ValueError):
            continue
        yield raw.decode("utf-8", "ignore")


def scan(text: str) -> list[str]:
    """Shape names in `text`, verbatim or under a plausible encoding.

    Returns names only. There is no variant of this function that returns the
    value, on purpose: a caller cannot leak what it was never handed.
    """
    if not text:
        return []
    text = text[:MAX_SCAN_CHARS]
    found = list(shapes_in(text))
    for variant in _decodings(text):
        for name in shapes_in(variant):
            if name not in found:
                found.append(name)
    return found


def redact(text: str) -> str:
    """`text` with every literal match replaced by its shape name.

    For messages that have to quote a command back at a human. It only redacts
    what it can SEE, so it is a safety net for the verbatim case, never a
    licence to print text that `scan()` has already flagged.
    """
    if not text:
        return text
    return SECRET.sub(lambda m: f"<{m.lastgroup} redacted>", text)


def advice(names: list[str]) -> str:
    """What to do instead. A guard that only says no gets worked around, and
    the workaround — a temp file left on disk, a token in an env file that gets
    committed — is usually worse than what it replaced."""
    shapes = ", ".join(names) if names else "a credential"
    return (
        f"This command carries a secret-looking literal ({shapes}) in its text. "
        "A value on a command line lands in the process table, where any user "
        "on the host can read it with `ps`, and in this session's transcript. "
        "That is exactly how the 2026-09-07 token was burned.\n"
        "Pass it without putting it in the command instead:\n"
        "  * pipe it on stdin      — `gh auth token | ssh host 'cat > ~/.tok'`\n"
        "  * let the child read the env — `ssh host 'echo $TOK'` with the value "
        "already set on the far side, never `TOK=<value> ssh ...`\n"
        "  * use a file the tool reads — `docker run --env-file`, `curl "
        "--config`, `psql --password-file`\n"
        "  * use the credential helper — `gh`, `doppler run --`, `op run --`\n"
        "If this is a placeholder or a documented example, put it in a file and "
        "reference it; do not rephrase the literal to get past this guard."
    )
