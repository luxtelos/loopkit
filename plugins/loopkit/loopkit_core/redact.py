#!/usr/bin/env python3
"""redact.py — one escape-robust redactor, shared by provider.py and store.py.

WHY THIS MODULE EXISTS (third review of the same file, 2026-09-07)

Both HTTP clients in this package used to carry their OWN literal-substring
redactor:

    if secret in text:
        text = text.replace(secret, "***")

Any escaping defeats that. `http.client.putheader` raises
`ValueError("Invalid header value %r" % value)`, and `%r` renders a real
newline as the two characters backslash-n -- so the secret in the text is no
longer EQUAL to the secret in memory, `in` is False, `replace` never fires,
and the whole `Authorization: AWS4-HMAC-SHA256 Credential=<access key>...`
header reaches the operator's terminal. An access key picks up a trailing
newline from `AWS_ACCESS_KEY_ID=$(cat keyfile)` or an unquoted `.env` line, so
this is an ordinary deployment, not an exotic one. That leak survived two
reviews of `store.py` because the redactor itself had no pin.

THE PRIMARY DEFENCE IS STRUCTURAL AND NAMES NO ESCAPING SCHEME

Enumerating escapers would be the same defect one level up: "enumerate
known-bad, permit the unknown" is this repository's most-repeated bug, and a
list of escapers is exactly that list. So the load-bearing rule here is a
property instead:

    A character escaper transforms individual characters and leaves the
    characters that need no escaping alone.

Therefore every maximal run of `[A-Za-z0-9_.~-]` inside a secret survives
`repr` (of `str` AND of `bytes`), `json.dumps` in either `ensure_ascii` mode,
percent-encoding, XML/HTML entities, `shlex.quote`, `backslashreplace`,
`unicode_escape` and anything else of that family -- byte for byte, because
none of those schemes has any reason to touch an ASCII letter, digit, `_`,
`.`, `~` or `-`. Redacting those runs redacts the secret under an escaping
this module has never heard of, which is the only kind that matters.

`AKIA<key>\\n` therefore still loses `AKIA<key>` even when the newline in the
middle of the header value has become a backslash and an `n`.

THE SECOND LAYER IS ENUMERATED, AND ONLY FOR TRANSFORMS THAT DESTROY RUNS

Base64 and hex do not escape characters, they re-code the whole string, so no
run survives and the structural rule has nothing to hold on to. Those forms
are listed explicitly. Whole-string percent-encoding is listed too, cheaply,
even though the runs already cover it.

WHAT THIS DOES NOT COVER -- stated here so no reader has to assume

  * A secret with NO invariant run of `_MIN_RUN` characters (a credential that
    is all punctuation, or shorter than the floor) keeps only the weaker
    whole-string cover. `uncovered()` reports exactly which registered secrets
    are in that state, so a pin can assert the honest answer rather than a
    hoped-for one.
  * A transform that SPLITS a run rather than escaping a character -- header
    folding, hard line wrapping, or a hex/base64 fragment at an unaligned
    offset. Structure cannot help there; not composing untrusted exception
    text into a message in the first place is what covers it, and both callers
    do that as their own first line of defence. This redactor is the BACKSTOP
    for text neither module composed, never the only wall.
  * Case folding, and any encoding that is not UTF-8 on the wire.

Pinned by `store.py --selftest` (S9) and `provider.py --selftest` (P9), which
drive the property over escapers this module does not enumerate.

Python 3.9+, standard library only.
"""
from __future__ import annotations

import base64
import re
import urllib.parse
from typing import Iterable, List, Optional, Set

__all__ = ["Redactor", "invariant_runs", "MASK"]

MASK = "***"

# The floor a whole secret must clear before it is redacted at all. Below it,
# `replace()` would mangle ordinary prose for no security gain.
_MIN_SECRET = 4
# The floor an invariant RUN must clear. Same number for the same reason: a
# three-character run redacted everywhere turns messages into asterisks
# without hiding anything an attacker could use.
_MIN_RUN = 4

# Characters no character-escaping scheme has a reason to transform. Kept
# deliberately narrow: every character added here is a character some escaper
# might touch, which would silently weaken the structural rule above.
_RUN = re.compile(r"[A-Za-z0-9_.~-]{%d,}" % _MIN_RUN)


def invariant_runs(secret: str) -> List[str]:
    """The parts of `secret` that survive character-escaping unchanged."""
    if not isinstance(secret, str):
        return []
    return _RUN.findall(secret)


def _recoded_forms(secret: str) -> Set[str]:
    """Whole-string re-codings, where no run survives to be matched."""
    forms: Set[str] = set()
    try:
        raw = secret.encode("utf-8", "surrogatepass")
    except Exception:  # pragma: no cover - defensive; never fail a scrub
        return forms
    try:
        standard = base64.b64encode(raw).decode("ascii")
        urlsafe = base64.urlsafe_b64encode(raw).decode("ascii")
        forms.update({standard, standard.rstrip("="), urlsafe, urlsafe.rstrip("=")})
        forms.add(raw.hex())
        forms.add(raw.hex().upper())
        forms.add(urllib.parse.quote(secret, safe=""))
    except Exception:  # pragma: no cover - defensive
        return forms
    return {form for form in forms if len(form) >= _MIN_SECRET}


class Redactor:
    """Scrubs known secrets out of anything on its way to a human.

    Matching is by invariant run first (see the module docstring), so a secret
    that has been repr-escaped, JSON-escaped or percent-encoded on its way into
    an exception message is still caught.
    """

    def __init__(self, secrets: Iterable[Optional[str]] = ()) -> None:
        self._secrets: List[str] = []
        self._needles: List[str] = []
        for secret in secrets:
            self.add(secret)

    def add(self, secret: Optional[str]) -> None:
        if not isinstance(secret, str) or len(secret) < _MIN_SECRET:
            return
        if secret in self._secrets:
            return
        self._secrets.append(secret)
        needles = {secret} if len(secret) >= _MIN_SECRET else set()
        needles.update(run for run in invariant_runs(secret) if len(run) >= _MIN_RUN)
        needles.update(_recoded_forms(secret))
        for needle in needles:
            if needle not in self._needles:
                self._needles.append(needle)
        # Longest first, so a long form is masked before one of its own
        # prefixes turns it into something the long form no longer matches.
        self._needles.sort(key=len, reverse=True)

    def scrub(self, text: object) -> str:
        text = text if isinstance(text, str) else str(text)
        for needle in self._needles:
            if needle in text:
                text = text.replace(needle, MASK)
        return text

    def uncovered(self) -> List[str]:
        """Registered secrets with no invariant run -- the honest weak spots.

        Returns the SECRETS themselves, because the only caller is a pin
        running in-process. Never log this.
        """
        return [
            secret
            for secret in self._secrets
            if not [run for run in invariant_runs(secret) if len(run) >= _MIN_RUN]
        ]

    @property
    def needle_count(self) -> int:
        return len(self._needles)


def url_secrets(url: str) -> List[str]:
    """The userinfo an operator put in a URL, so a redactor can know it too."""
    try:
        parts = urllib.parse.urlsplit(url)
        return [p for p in (parts.username, parts.password) if p]
    except ValueError:
        return []


def _selftest() -> int:  # pragma: no cover - exercised through the two callers
    print(__doc__ or "")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_selftest())
