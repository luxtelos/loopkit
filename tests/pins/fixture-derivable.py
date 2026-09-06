#!/usr/bin/env python3
"""fixture-derivable.py — every conformance fixture's expected output is
derivable from its input using only the rules `specs/loopkit-runtime.md` states.

WHY THIS EXISTS. On 2026-09-07 a reviewer hand-computed two fixtures from the
spec and got different answers, and found a third whose `events` key admitted
three incompatible readings. Fixtures that cannot be computed from the spec pin
nothing across SDKs: they record what one implementation printed. Two SDKs
built against them would agree only by copying each other.

WHAT THIS IS. A reference implementation of the spec's stated rules — stage
precedence (criterion 4), the Next three-way split (criterion 7), Counts, the
journal delta (criteria 1, 2, 10, 11, 12), lane scoping (criterion 14), the
draft gate (criterion 15) and trust tiers (criterion 16) — written from the
spec, then run against every fixture. It is deliberately NOT `loopkit_core`:
it computes only what the spec determines, it has no Provider and no Store, and
it is ~150 lines so a reader can check it against the prose.

Where the spec defers to shipped code it IMPORTS that code rather than
reimplementing it — `okf_bundle.trust_tier` and
`knowledge_actor.validate_message` — so a fixture that disagrees with the
runtime fails here rather than in a reviewer's head.

WHAT IT DOES NOT CHECK. ONE key is not derivable from `input`: `messages`
content. `targets` was the second until the spec defined it in §Targets on
2026-09-07; this pin now derives it from that rule.

`messages` content is not derivable: what an agent proposes depends on the
Provider, whose script the fixture does not carry. For `messages` this pin
asserts only what the spec DOES determine — that every expected Message is one
`validate_message` accepts. That limit was stated from the start, because
fixture 05 shipped asserting a Message the runtime was required to dead-letter.

`targets` IS derivable, from spec §Targets. Until 2026-09-07 it was not: the
noun was undefined and this pin carried a `targets_of()` that recomputed it
from a hand-written reading, reporting a guess as a derivation. The rule now
lives in the spec and `derive_targets` implements that rule and nothing else.

usage: python3 tests/pins/fixture-derivable.py
exit 0 = every fixture derives; exit 1 = at least one disagreement.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "spec" / "fixtures"
sys.path.insert(0, str(REPO / "plugins" / "loopkit" / "loopkit_memory" / "vendor"))

import okf_bundle as okf  # noqa: E402
import knowledge_actor as actor  # noqa: E402

# --- the spec's rules, one function each ------------------------------------

# criterion 4: the four work statuses, highest precedence first.
WORK = ["fixing", "spec-ready", "spec-draft", "new"]
# The Counts noun: these six keys, always all six, 0 when no row holds one.
STATUSES = ["pr-open", "fixing", "spec-ready", "spec-draft", "new", "blocked"]


def dedupe(queue: list[dict]) -> list[dict]:
    """Criterion 9: identity is `source`. Two rows with one source are one row.
    File order is the order of first appearance."""
    seen: dict[str, dict] = {}
    order: list[str] = []
    for row in queue:
        src = str(row.get("source", ""))
        if src not in seen:
            order.append(src)
        seen[src] = row
    return [seen[s] for s in order]


def count(rows: list[dict]) -> dict:
    c = {s: 0 for s in STATUSES}
    for row in rows:
        st = str(row.get("status", "")).strip()
        if st in c:
            c[st] += 1
    return c


def decide(counts: dict) -> tuple[str, str]:
    """Criteria 4, 5, 6, 7. Pure: counts in, (Stage, Next) out."""
    for stage in WORK:
        if counts[stage] > 0:
            return stage, "CONTINUE"
    if counts["pr-open"] > 0 or counts["blocked"] > 0:
        return "discover", "WAIT"
    return "discover", "IDLE"


PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
MAX_FANOUT = 8


def derive_targets(rows: list[dict], stage: str, lane_terms: list[str] | None) -> list[dict]:
    """`targets` from the spec's §Targets rule, not from a reading of the code.

    The rule the spec settles: the SERVED stage only (loop-next.sh filters the
    picker's fuller stream, and that filtered form is what every fixture
    asserts); Queue order unscoped, priority order when scoped; capped at
    MAX_FANOUT; identity is `source`.

    This function existed once before as `targets_of()`, encoding one reading of
    a noun the spec had never defined — which made the pin read as covering
    derivability while it covered a guess. It is back only because §Targets now
    states the rule it implements.
    """
    if stage == "discover":
        return []
    seen: set[str] = set()
    picked: list[dict] = []
    for r in rows:
        src = str(r.get("source", "")).strip()
        if src in seen:
            continue
        seen.add(src)
        if (r.get("status") or "").strip() != stage:
            continue
        if lane_terms and not any(t in str(r.get("finding", "")) + src for t in lane_terms):
            continue
        picked.append(r)
    if lane_terms:
        picked = sorted(picked, key=lambda r: PRIORITY_RANK.get(
            (r.get("priority") or "").strip().lower(), 9))
    return [{"stage": stage,
             "finding": str(r.get("finding", "")).strip(),
             "source": str(r.get("source", "")).strip()} for r in picked[:MAX_FANOUT]]


def targets_problems(rows: list[dict], stage: str, targets: list[dict],
                     lane_terms: list[str] | None = None) -> list[str]:
    """Compare the fixture's `targets` against the derivation above."""
    want = derive_targets(rows, stage, lane_terms)
    got = [{"stage": t.get("stage"), "finding": t.get("finding"), "source": t.get("source")}
           for t in targets]
    if want == got:
        return []
    return [f"targets: spec §Targets derives {want!r} but the fixture asserts {got!r}"]


def steps_of(journal: list[dict]) -> tuple[dict, set]:
    """Journaled steps: first `intent` per step, and the set with a `result`."""
    intents: dict[str, dict] = {}
    results: set[str] = set()
    for e in journal:
        kind = e.get("event")
        if kind == "intent":
            intents.setdefault(str(e.get("step")), e)
        elif kind == "result":
            results.add(str(e.get("step")))
    return intents, results


def events_delta(run: dict, journal: list[dict], stage: str) -> list[dict]:
    """The Events this run APPENDS, in order (README rule 2)."""
    out: list[dict] = []
    # Criterion 1 / 2: run_start first, and only on a fresh Run.
    if not any(e.get("event") == "run_start" for e in journal):
        out.append({
            "event": "run_start",
            "run_id": run["run_id"],
            "store_uri": run["store_uri"],
            "provider": run["provider"],
            "policy_path": run["policy_path"],
        })
    out.append({"event": "stage", "stage": stage, "scope": run.get("lane", "")})
    # Criteria 11, 12: a step whose result is journaled is answered from the
    # journal; a step with an intent and no result re-executes and appends its
    # result. It does NOT re-journal the intent — the one on disk is what
    # criterion 10 requires, and re-journalling it would record an act that
    # never went unrecorded.
    intents, results = steps_of(journal)
    for step, e in intents.items():
        if step not in results:
            out.append({
                "event": "result",
                "run_id": e.get("run_id"),
                "step": step,
                "idempotency_key": e.get("idempotency_key"),
                "status": "ok",
            })
    return out


def provider_calls_of(journal: list[dict]) -> dict:
    """Criterion 11: 0 for a step already carrying a result, 1 for a step
    re-executed from an orphaned intent."""
    intents, results = steps_of(journal)
    return {step: (0 if step in results else 1) for step in intents}


def lane_matches(tags, lane: str) -> bool:
    """§Policy scoping. Byte-for-byte the rule in
    plugins/loopkit/loopkit_memory/okf.py's `search`: a bare tag matches no lane."""
    ln = lane.lower()
    low = [str(t).lower() for t in (tags or [])]
    return any(
        t in (f"lane/{ln}", f"scope/{ln}", f"domain/{ln}") or t.endswith(f"/{ln}")
        for t in low
    )


def enforceable_of(concepts: list[dict], lane: str) -> list[str]:
    """Criteria 14 + 15: in-lane AND `status: stable`. Draft is gated by the
    trust rule; deprecated is not enforceable either."""
    out = []
    for c in concepts:
        fm = c.get("frontmatter") or {}
        if str(fm.get("status", "")) != "stable":
            continue
        if lane and not lane_matches(fm.get("tags"), lane):
            continue
        out.append(c["path"])
    return out


# --- the harness -------------------------------------------------------------

fails: list[str] = []


def check(name: str, key: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}: {key} derives")
    else:
        fails.append(name)
        print(f"  FAIL {name}: {key}\n         spec derives: {json.dumps(got, sort_keys=True)}"
              f"\n         fixture says: {json.dumps(want, sort_keys=True)}")


def main() -> int:
    files = sorted(FIXTURES.glob("*.json"))
    if not files:
        print("  FAIL no fixtures found")
        return 1
    print("== every fixture's expected output derives from the spec's rules")
    for path in files:
        fx = json.loads(path.read_text(encoding="utf-8"))
        name = fx["name"]
        if name != path.stem:
            fails.append(name)
            print(f"  FAIL {path.name}: name {name!r} does not match the filename")
        inp, exp = fx["input"], fx["expected"]
        run = inp["run"]
        rows = dedupe(inp["queue"])
        counts = count(rows)
        stage, nxt = decide(counts)

        check(name, "stage", stage, exp["stage"])
        check(name, "next", nxt, exp["next"])
        if "counts" in exp:
            check(name, "counts", counts, exp["counts"])
        if "targets" in exp:
            probs = targets_problems(rows, stage, exp["targets"])
            if probs:
                fails.append(name)
                for p in probs:
                    print(f"  FAIL {name}: targets — {p}")
            else:
                print(f"  ok   {name}: targets satisfies criteria 5/6/9 "
                      f"(membership, ordering and cap are NOT derivable — README rule 6)")
        if "events" in exp:
            check(name, "events", events_delta(run, inp["journal"], stage), exp["events"])
        if "provider_calls" in exp:
            check(name, "provider_calls", provider_calls_of(inp["journal"]), exp["provider_calls"])

        concepts = (inp.get("policy") or {}).get("concepts") or []
        if "enforceable" in exp:
            check(name, "enforceable", enforceable_of(concepts, run.get("lane", "")), exp["enforceable"])
        if "trust_tiers" in exp:
            tiers = {c["path"]: okf.trust_tier(c.get("frontmatter") or {}) for c in concepts}
            check(name, "trust_tiers", tiers, exp["trust_tiers"])

        # messages: not derivable (README rule 6). Assert only that the runtime
        # would ACCEPT each one — a Message that fails validate_message is
        # dead-lettered by criterion 18, so a fixture asserting it as delivered
        # asserts the opposite of the spec.
        for i, msg in enumerate(exp.get("messages") or []):
            fm = dict(msg)
            fm.setdefault("msg_id", "00000000-0000-4000-8000-000000000000")  # README rule 4
            try:
                actor.validate_message(actor.Message(path=Path(f"{name}-{i}.md"), frontmatter=fm, body=""))
                print(f"  ok   {name}: messages[{i}] passes validate_message")
            except Exception as exc:  # PermanentError and anything else
                fails.append(name)
                print(f"  FAIL {name}: messages[{i}] is REFUSED by validate_message "
                      f"and would be dead-lettered (criterion 18): {exc}")

    print()
    if fails:
        print(f"{len(set(fails))} FIXTURE(S) DO NOT DERIVE: {', '.join(sorted(set(fails)))}")
        return 1
    print("FIXTURE PINS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
