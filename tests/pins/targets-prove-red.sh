#!/usr/bin/env bash
# targets-prove-red.sh — a derivation that cannot disagree with a fixture is not
# a derivation. Perturb each fixture's `targets` and require the pin to object.
#
# This exists because the pin once carried a `targets_of()` that recomputed the
# key from a hand-written reading of a noun the spec had never defined, and
# agreed with every fixture by construction. Agreement is only evidence when
# disagreement was possible.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
PIN="tests/pins/fixture-derivable.py"
red=0; notred=0

for f in spec/fixtures/0*.json; do
  case "$f" in *README*) continue ;; esac
  n="$(python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
print(len(d.get('expected',{}).get('targets') or []))" "$f")"
  if [ "$n" = 0 ]; then echo "  skip $(basename "$f") — no targets to perturb"; continue; fi
  cp "$f" "$f.bak"
  python3 - "$f" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["expected"]["targets"][0]["source"] += "-WRONG"
json.dump(d, open(p, "w"), indent=2)
PY
  out="$(python3 "$PIN" 2>&1)"; rc=$?
  if [ "$rc" != 0 ] && printf '%s' "$out" | grep -q 'targets: spec'; then
    red=$((red+1)); echo "  RED  $(basename "$f") — the pin objects to a wrong target"
  else
    notred=$((notred+1)); echo "  NOT RED  $(basename "$f") (rc=$rc) — the derivation is not biting"
  fi
  mv "$f.bak" "$f"
done

echo "TARGETS MUTATIONS red=$red not-red=$notred"
[ "$red" -gt 0 ] && [ "$notred" = 0 ]
