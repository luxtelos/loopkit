#!/usr/bin/env bash
# model-invariants-live.sh — prove every assertion in the runtime model can FAIL.
#
# WHY THIS EXISTS. On 2026-09-07 a reviewer found that `EffectAtMostOnce`
# passed even with its convergence guard deleted: no reachable state applied
# the effect twice, so the assertion was true by construction rather than by
# proof — and criterion 12 cited it as evidence. The same review found
# criterion 27's claim about the lease guard was simply false. A model checker
# reporting PASSED tells you nothing about whether it would ever report FAILED.
#
# So: for each assertion, apply the mutation recorded beside it in the .fizz
# file and require the driver to exit 1. A mutation that still passes means the
# assertion is vacuous, and this script fails the build rather than the
# reviewer having to re-derive it by hand.
#
# usage: bash tests/pins/model-invariants-live.sh
# exit 0 = every assertion is live; exit 1 = at least one is vacuous, or the
# unmutated model does not pass, or a mutation no longer applies.

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODEL="$REPO/specs/loopkit-runtime.model.fizz"
DRIVER="$REPO/plugins/loopkit/skills/run-state-model/driver.mjs"
fails=0
ok()   { echo "  ok   $1"; }
fail() { echo "  FAIL $1"; fails=$((fails+1)); }

if ! command -v node >/dev/null 2>&1; then echo "  skip node not on PATH"; exit 0; fi

# The ENGINES are a separate question from node. `driver.mjs doctor` probes each
# engine's output, not just its path. Without them the driver cannot run and
# every verdict below would be a guess — so skip, loudly, and say what was NOT
# verified. Skipping quietly is the failure this repo names as "a gated suite is
# not a passing suite": a suite that reports success by running nothing.
# Install with plugins/loopkit/skills/run-state-model/install.sh; a CI runner
# without them leaves the model invariants unproven THERE, which is why the
# macOS run is the one that verifies them today.
if ! ( cd "$REPO" && node "$DRIVER" doctor ) >/dev/null 2>&1; then
  echo "  skip model checkers not installed — the runtime model's invariants are UNVERIFIED on this machine"
  echo "       (install: bash plugins/loopkit/skills/run-state-model/install.sh)"
  exit 0
fi
[ -f "$MODEL" ]  || { echo "  FAIL no model at $MODEL"; exit 1; }
[ -f "$DRIVER" ] || { echo "  FAIL no driver at $DRIVER"; exit 1; }

WORK="$(mktemp -d)"
cleanup() { [ -n "${WORK:-}" ] && [ -d "$WORK" ] && find "$WORK" -delete >/dev/null 2>&1; }
trap cleanup EXIT

# run_driver <file> -> silent; returns the driver's exit code. The driver is
# the arbiter, not the engine: fizz itself exits 0 on FAILED, the driver does
# not. Never read the engine's own code here.
run_driver() { ( cd "$WORK" && node "$DRIVER" check "$1" ) >/dev/null 2>&1; }

# The control case for the control case: unmutated, the model must PASS. If it
# does not, every "the mutation fails" result below is meaningless.
cp "$MODEL" "$WORK/clean.fizz"
if run_driver "$WORK/clean.fizz"; then
  ok "unmutated model PASSES (driver rc=0)"
else
  fail "unmutated model does not pass — every result below is meaningless"
fi

# mutate <label> <assertion> <FROM<<>>TO ...>
# Replacements are applied literally, first occurrence. A FROM that is not
# found is itself a failure: a mutation that silently does nothing is exactly
# how a vacuous assertion survives its own pin.
mutate() {
  local label="$1" assertion="$2"; shift 2
  local out="$WORK/$label.fizz"
  if ! python3 - "$MODEL" "$out" "$@" <<'PY'
import sys
src, dst, *pairs = sys.argv[1:]
s = open(src, encoding="utf-8").read()
for pair in pairs:
    frm, to = pair.split("<<>>", 1)
    if frm not in s:
        sys.stderr.write("mutation target not found: %r\n" % frm)
        sys.exit(2)
    s = s.replace(frm, to, 1)
open(dst, "w", encoding="utf-8").write(s)
PY
  then fail "$label: mutation could not be applied (the model moved under the pin)"; return; fi
  if run_driver "$out"; then
    fail "$label: model still PASSES with the mutation — assertion $assertion is VACUOUS"
  else
    ok "$label: mutation FAILS the checker — assertion $assertion is live"
  fi
}

echo "== model assertions are live (each mutation must FAIL the checker)"

# EffectAtMostOnce: delete the convergence guard from the single apply site.
mutate MUT-EFFECT EffectAtMostOnce \
  '    if effects == 0:
        effects = effects + 1<<>>    effects = effects + 1'

# NoEffectWithoutJournaledIntent: drop journal-before-act from BOTH guards.
# Dropping it from CallProvider alone changes nothing — Claim already implies
# it — which is the near miss recorded in the model's own comments.
mutate MUT-INTENT NoEffectWithoutJournaledIntent \
  '    if intent == 1 and result == 0 and owner == "":<<>>    if result == 0 and owner == "":' \
  '    if owner == w and intent == 1 and result == 0 and called[w] == 0:<<>>    if owner == w and result == 0 and called[w] == 0:'

# NoProviderCallAfterJournaledResult: let a replay re-call the Provider.
mutate MUT-REPLAY NoProviderCallAfterJournaledResult \
  '        replays = replays + 1<<>>        replays = replays + 1
        provider_calls = provider_calls + 1'

# LeaseIsExclusive: delete the put_if_absent lease guard from Claim, so a
# second writer steals the lease while the first is mid-flight.
mutate MUT-LEASE LeaseIsExclusive \
  '    if intent == 1 and result == 0 and owner == "":<<>>    if intent == 1 and result == 0:'

echo
[ "$fails" = 0 ] && echo "MODEL PINS PASS" || echo "$fails MODEL PIN FAILURE(S)"
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
