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

WHAT IT DOES NOT CHECK. TWO keys are not derivable from `input`, not one.

`messages` content is not derivable: what an agent proposes depends on the
Provider, whose script the fixture does not carry. For `messages` this pin
asserts only what the spec DOES determine — that every expected Message is one
`validate_message` accepts. That limit was stated from the start, because
fixture 05 shipped asserting a Message the runtime was required to dead-letter.

`targets` is not derivable either, and until 2026-09-07 this pin hid that: it
carried a `targets_of()` that recomputed the key from a hand-written reading of
a noun the specification never defines — no shape, no membership rule, no
ordering, no cap. It agreed with the fixtures because both were written from
the same unstated assumption, so the pin read as covering derivability and did
not. That is the precise defect class this file exists to close, so the
reimplementation is gone; see `targets_problems` for what is asserted instead
and for the four sub-rules the spec still owes. Both limits are now named in
spec/fixtures/README.md rule 6 rather than left for a reader to discover.

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


def targets_problems(rows: list[dict], stage: str, targets: list[dict]) -> list[str]:
    """`targets` is NOT derivable from the specification as written — see the
    header and spec/fixtures/README.md rule 6. This function therefore asserts
    only the properties the spec DOES state, and deliberately asserts nothing
    about which rows qualify, in what order, or how many.

    Stated, and checked here:
      - criterion 5: a `pr-open` row is counted, never served — absent from `targets`.
      - criterion 6: a `blocked` row is counted, never served — absent likewise.
      - criterion 9: identity is `source`; one source is one row, so no two
        targets may share one.
      - §Edge cases: Stage `discover` serves no work, so `targets` is empty.
      - a target names a real Queue row, and carries that row's own `finding`.

    NOT stated, and NOT checked — this is the gap, not an oversight:
      - MEMBERSHIP. `loop_next_pick.py` emits a `target:<stage>` line for every
        one of the four work stages; `loop-next.sh` then awk-filters to the
        selected stage alone. Both are defensible readings of one undefined
        noun and they disagree: on fixture 01's queue the picker emits four
        targets and the fixture asserts one. Reproduce with
        `python3 -c "import json;print(json.dumps(json.load(open('spec/fixtures/01-stage-precedence.json'))['input']['queue']))" | python3 plugins/loopkit/scripts/loop_next_pick.py`
      - ORDERING. File order unscoped, but `PRIORITY_RANK` order inside a lane.
      - THE CAP. `loop_next_pick.MAX_FANOUT` is 8 and no fixture reaches it.
      - LANE FILTERING. The picker's row scope is a substring match over terms
        from `<project>/.loopkit/scopes.json`, a file no fixture carries, so a
        scoped target set cannot be computed from `input` at all.

    Until a Nouns row defines those four, a pin that computed `targets` would
    be encoding one reading of an undefined noun and reporting it as
    derivation. That is what this function did until 2026-09-07, and it is the
    exact defect class this pin exists to close. The proposed normative wording
    is in inbox/needs-human.md.
    """
    problems: list[str] = []
    by_source = {str(r.get("source", "")): r for r in rows}
    if stage == "discover" and targets:
        problems.append(f"Stage is discover but {len(targets)} target(s) are served (§Edge cases)")
    seen: set[str] = set()
    for t in targets:
        src = str(t.get("source", ""))
        if src in seen:
            problems.append(f"two targets share source {src!r} — identity is `source` (criterion 9)")
        seen.add(src)
        row = by_source.get(src)
        if row is None:
            problems.append(f"target {src!r} is not a Queue row")
            continue
        st = str(row.get("status", "")).strip()
        if st == "pr-open":
            problems.append(f"target {src!r} is a pr-open row — counted, never served (criterion 5)")
        if st == "blocked":
            problems.append(f"target {src!r} is a blocked row — counted, never served (criterion 6)")
        if t.get("finding", "") != row.get("finding", ""):
            problems.append(
                f"target {src!r} says finding {t.get('finding')!r}, the Queue row says {row.get('finding')!r}"
            )
    return problems


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
