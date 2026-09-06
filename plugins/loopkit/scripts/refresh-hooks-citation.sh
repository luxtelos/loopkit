#!/usr/bin/env bash
# refresh-hooks-citation.sh — re-fetch the hooks reference and rewrite
# vendor/hooks-doc-excerpt.md from it. Never edit that file by hand: the point
# of it is that a human (or CI) can re-derive it and see the diff.
#
# Exit 0 rewrote or confirmed, 1 fetch failed, 2 the quoted lines moved (the
# doc changed shape — read it before touching the excerpt).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(dirname "$HERE")"
OUT="$PLUGIN/vendor/hooks-doc-excerpt.md"
URL="https://code.claude.com/docs/en/hooks.md"
TMP="$(mktemp "${TMPDIR:-/tmp}/hooks-doc.XXXXXX")"
curl -sS -L -o "$TMP" "$URL" || { echo "FAIL: could not fetch $URL" >&2; exit 1; }
python3 - "$TMP" "$OUT" "$URL" <<'PY'
import hashlib, re, sys
from pathlib import Path
src, out, url = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
text = src.read_text()
bullet = next((l.lstrip("* ").strip() for l in text.splitlines()
               if l.lstrip().startswith("* `PreToolUse`: `updatedInput`")), None)
cell = None
for l in text.splitlines():
    if l.strip().startswith("| `updatedInput`") and "Replaces the entire input object" in l:
        cell = l.strip().strip("|").split("|", 1)[1].strip(); break
if not bullet or not cell:
    print("FAIL: the quoted lines are no longer in the document; read it before editing the excerpt", file=sys.stderr)
    sys.exit(2)
digest = hashlib.sha256(src.read_bytes()).hexdigest()
from datetime import date
out.write_text(f"""# Vendored excerpt — Claude Code hooks reference

Source: {url}
Fetched: {date.today().isoformat()}
SHA-256 of the whole document at fetch time: {digest}
Refresh and re-verify: bash plugins/loopkit/scripts/refresh-hooks-citation.sh

This file exists so a quotation in this repo can be checked without a network
call. Two earlier headers in hooks/offload_rewrite.py quoted sentences that are
not in the document at all — a paraphrase from a summarising fetch, pasted as if
verbatim, twice. The selftest now diffs the header's quoted block against the
block below, so an invented sentence fails the suite rather than shipping.
Nothing here is edited by hand; the refresh script rewrites the whole file.

## QUOTE-BEGIN
{bullet}
{cell}
## QUOTE-END
""")
print(f"refreshed {out} (doc sha256 {digest[:16]}…)")
PY
rc=$?; rm -f "$TMP"; exit $rc
