#!/usr/bin/env python3
"""criterion5-purity-attacks.py — the condition evaluator is closed, and the
check that says so is attacked rather than admired.

`specs/blocked-waits-on-what.md` criterion 5: the condition check SHALL be a
closed function of its arguments — no clock, no network, no filesystem, no
module-level state.

Criterion 5's check has now been defeated twice, and both defeats had the same
shape: a rule about NAMES, applied to a namespace that was not the one the name
is resolved in.

  round 1  a DENY-list of forbidden module names.   Defeated six ways.
  round 2  an ALLOW-list over the union of `co_names`. Defeated three ways by
           the post-merge review of PR #29 — `count = time.time` at module
           level, then `count()` in the body: `co_names == {'any','count'}`,
           and both names were on the list because `count` is a `str`/`list`
           attribute name. Attribute names and free globals were ONE list.

This pin holds the round-3 answer and, more importantly, holds the attacks. A
third name-list would be defeated a fourth time; what changed is the structure:

  A. ENFORCEMENT. The evaluator is CALLED against a sealed globals mapping that
     contains only the allow-listed builtins. A free global that is not on the
     list does not resolve at all. This is not a check that can be fooled by a
     name — the module namespace is simply not on the lookup path.
  B. DETECTION. A static check for what sealing cannot reach: attribute names,
     which travel through the ARGUMENT and so survive any globals policy.
     Its part (d) partitions the instruction stream with `dis` instead of
     unioning `co_names`, so a global and an attribute are judged by different
     lists, and it FAILS CLOSED on any name-carrying opcode it does not
     classify.

Both are needed. Sealing cannot see `rows.__class__.__base__.__subclasses__()`;
the static check cannot see a module that rebinds a name after the check ran.

Neither can see gap 7, and nor can a sample taken inside one process. Set
iteration order is hash-seeded PER PROCESS; `str` of a default repr embeds an
address. An evaluator built on either uses no name the pure control does not,
so there is nothing for a list or a seal to refuse, and `flips()` runs under
one seed on one heap, so the value it samples never moves. The only sampler
that sees this class runs the SEALED evaluator in a fresh interpreter per
call with a distinct PYTHONHASHSEED each — `verdicts_across_processes()` —
and its bound is the seeds it ran. A tick is a process; that is why it matters.

usage: python3 tests/pins/criterion5-purity-attacks.py
       python3 tests/pins/criterion5-purity-attacks.py --child
         (internal: evaluator source on stdin, one sealed verdict on stdout)
"""
from __future__ import annotations

import builtins
import dis
import os
import signal
import subprocess
import sys
import time
import types

fails = 0
gaps: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def fail(msg: str) -> None:
    global fails
    print(f"  FAIL {msg}")
    fails += 1


def gap(msg: str) -> None:
    """A miss this check does NOT close, printed so it cannot be forgotten.
    An allow-list with an unnamed gap is a deny-list wearing better clothes."""
    gaps.append(msg)
    print(f"  gap  {msg}")


# ===================================================================== ROUND 2
# The check exactly as specs/blocked-waits-on-what.md stated it before this
# change. Kept so the defeats below are demonstrated, not recited.
R2_ALLOWED = {
    "any", "all", "len", "str", "bool", "isinstance", "sorted", "next", "iter",
    "get", "items", "keys", "values", "count", "index", "append", "strip",
    "split", "join", "startswith", "endswith", "lower", "upper", "replace",
}


def _codes(code: types.CodeType):
    yield code
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            yield from _codes(c)


def round2_check(fn) -> list[str]:
    bad: list[str] = []
    if not isinstance(fn, types.FunctionType):
        return ["not a plain function"]
    if fn.__defaults__ or fn.__kwdefaults__:
        bad.append("defaulted parameter")
    if fn.__closure__ is not None:
        bad.append("closure over non-argument state")
    names: set[str] = set()
    for c in _codes(fn.__code__):
        names |= set(c.co_names)
    bad += [f"unlisted name: {n}" for n in sorted(names - R2_ALLOWED)]
    g = fn.__globals__
    for n in sorted(names):
        if hasattr(builtins, n) and n in g and g[n] is not getattr(builtins, n):
            bad.append(f"shadowed builtin: {n}")
    return bad


# ===================================================================== ROUND 3
# Two lists, because a free global and an attribute are two namespaces.
#
# ALLOWED_GLOBALS is short because the evaluator's job is small, and it is
# curated rather than derived: `getattr`, `id`, `hash`, `open`, `vars`,
# `globals` and `__import__` are all pristine builtins and all forbidden. See
# the N2 and N3b cases below for why each of those exclusions is load-bearing.
# `str` IS on it, and `str(<anything with a default repr>)` is address-derived
# exactly as `id` is — gap 3 below measures that. The list refuses `id` because
# nobody wrote it down and admits `str` because somebody did; it is not a
# control against that class and never was.
ALLOWED_GLOBALS = {"any", "all", "len", "str", "bool", "isinstance"}

# Attribute names reachable on an argument. All non-mutating members of `str`,
# `dict` and `list`. `append` is deliberately absent: it mutates a caller's row.
ALLOWED_ATTRS = {"get", "items", "keys", "values", "strip", "split",
                 "startswith", "endswith", "lower", "join"}

_GLOBAL_READ = {"LOAD_GLOBAL", "LOAD_NAME"}
_ATTR_READ = {"LOAD_ATTR", "LOAD_METHOD"}          # LOAD_METHOD: CPython <= 3.11
_MUTATE = {"STORE_GLOBAL", "DELETE_GLOBAL", "STORE_NAME", "DELETE_NAME",
           "STORE_ATTR", "DELETE_ATTR"}
_IMPORT = {"IMPORT_NAME", "IMPORT_FROM"}


def _resolve_as_python_would(fn, name):
    """Resolve a free global the way CPython will at CALL time: `__globals__`,
    then the function's OWN `__builtins__` fallback. Comparing against the
    pristine `builtins` module instead is what let a poisoned `__builtins__`
    through in round 2 — that check asked the wrong dictionary."""
    g = fn.__globals__
    if name in g:
        return g[name], "globals"
    bi = g.get("__builtins__", builtins)
    if isinstance(bi, types.ModuleType):
        bi = vars(bi)
    if name in bi:
        return bi[name], "__builtins__"
    raise KeyError(name)


def static_check(fn) -> list[str]:
    """Round 3, half B. Returns [] if accepted, else the refusal reasons."""
    bad: list[str] = []
    if not isinstance(fn, types.FunctionType):                       # (a)
        return ["not a plain function"]
    if fn.__defaults__ or fn.__kwdefaults__:                         # (b)
        bad.append("defaulted parameter")
    if fn.__closure__ is not None:                                   # (c)
        bad.append("closure over non-argument state")

    globals_used: set[str] = set()
    attrs_used: set[str] = set()
    for code in _codes(fn.__code__):                                 # (d)
        seen: set[str] = set()
        for ins in dis.get_instructions(code):
            if ins.opcode not in dis.hasname:
                continue
            n, op = ins.argval, ins.opname
            seen.add(n)
            if op in _GLOBAL_READ:
                globals_used.add(n)
            elif op in _ATTR_READ:
                attrs_used.add(n)
            elif op in _MUTATE:
                bad.append(f"mutating opcode {op} on {n}")
            elif op in _IMPORT:
                bad.append(f"import inside the evaluator: {n}")
            else:
                # FAIL CLOSED. `dis.hasname` grows between CPython releases —
                # LOAD_SUPER_ATTR and LOAD_FROM_DICT_OR_GLOBALS are in it today
                # and in none of the four sets above. An opcode this partition
                # cannot classify is refused, never waved through.
                bad.append(f"unclassified name-carrying opcode {op} ({n})")
        for n in set(code.co_names) - seen:
            bad.append(f"name in co_names reached by no classified opcode: {n}")

    for n in sorted(attrs_used):                                     # (d2)
        if n.startswith("_"):
            bad.append(f"underscore attribute: {n}")
        elif n not in ALLOWED_ATTRS:
            bad.append(f"unlisted attribute: {n}")

    for n in sorted(globals_used):                                   # (d1)
        if n not in ALLOWED_GLOBALS:
            bad.append(f"unlisted global: {n}")
            continue
        try:
            obj, where = _resolve_as_python_would(fn, n)
        except KeyError:
            bad.append(f"global resolves to nothing: {n}")
            continue
        if obj is not getattr(builtins, n, object()):
            bad.append(f"global {n} (via {where}) is not the pristine builtin")
    return bad


def seal(fn):
    """Round 3, half A. Rebind the evaluator to a globals mapping holding only
    the allow-listed builtins. `count = time.time` in the defining module is
    then not a name the check has to recognise — it is a name that does not
    exist."""
    sealed = {n: getattr(builtins, n) for n in ALLOWED_GLOBALS}
    return types.FunctionType(fn.__code__, {"__builtins__": sealed,
                                            "__name__": "sealed"},
                              fn.__name__, None, None)


# ======================================================================= CORPUS
ROWS = [{"source": "row-A", "status": "done"}, {"source": "row-B", "status": "new"}]
ARG = "row-A"
HIT = ('hit = any(r.get("source") == arg and r.get("status") == "done" '
       'for r in rows)')


def build(src: str):
    g = {"__builtins__": builtins}
    exec(compile(src, "<evaluator>", "exec"), g)
    return g["evaluate"], g


def flips(fn, rows=ROWS, n=200, gapsec=0.005) -> bool:
    """Does it return different answers for IDENTICAL inputs, sampled within
    ONE process? Evidence, not a gate: `count()` only flips because the clock
    moves during the sample. A per-process constant — set order under this
    process's hash seed, an address on this heap — never moves during the
    sample, so this sampler is BLIND to gap 7 by construction. The pin asserts
    that blindness below; `verdicts_across_processes` is the sampler that sees
    it."""
    seen = set()
    for _ in range(n):
        try:
            seen.add(bool(fn(rows, ARG)))
        except Exception as e:                                    # noqa: BLE001
            seen.add(type(e).__name__)
        time.sleep(gapsec)
    return len(seen) > 1


if sys.argv[1:] == ["--child"]:
    # One SEALED call in THIS fresh interpreter: source on stdin, verdict on
    # stdout, nothing else printed. The parent varies PYTHONHASHSEED per child
    # and nothing else, so a verdict that differs between children differs
    # because of the seed (or the heap), never because of the harness.
    _child_fn, _child_g = build(sys.stdin.read())
    try:
        print(repr(seal(_child_fn)(ROWS, ARG)))
    except Exception as _e:                                       # noqa: BLE001
        print(type(_e).__name__)
    raise SystemExit(0)


def verdicts_across_processes(src: str, seeds, stop_when_split: bool = False
                              ) -> dict[str, int]:
    """Run the SEALED evaluator once per FRESH interpreter, one PYTHONHASHSEED
    per process. The only sampler that can see a per-process constant. Returns
    {verdict: number of processes that returned it}. `stop_when_split` returns
    as soon as two verdicts disagree — the claim being tested is "it flips",
    and one disagreement proves it; the pure control is run without it, so
    every seed asked for is a seed it had to survive."""
    seen: dict[str, int] = {}
    for seed in seeds:
        env = dict(os.environ, PYTHONHASHSEED=str(seed))
        out = subprocess.run([sys.executable, os.path.abspath(__file__), "--child"],
                             input=src, env=env, capture_output=True, text=True,
                             timeout=60)
        verdict = out.stdout.strip() or f"rc={out.returncode}"
        seen[verdict] = seen.get(verdict, 0) + 1
        if stop_when_split and len(seen) > 1:
            break
    return seen


CASES: list[tuple[str, str, str, str]] = [
    ("CONTROL pure", "allowed", f"""
def evaluate(rows, arg):
    {HIT}
    return hit
""", "the known-good evaluator; if this is refused the check is useless"),

    ("CONTROL known-bad time.time()", "forbidden", f"""
import time
def evaluate(rows, arg):
    {HIT}
    return hit and (time.time() % 2 < 1)
""", "the harness must be able to fail"),

    # --- the three from the post-merge review of PR #29 --------------------
    ("R1 count = time.time", "forbidden", f"""
import time
count = time.time
def evaluate(rows, arg):
    {HIT}
    return hit and (count() % 2 < 1)
""", "an allow-listed ATTRIBUTE name used as a free GLOBAL"),

    ("R3 index = os.getpid", "forbidden", f"""
import os
index = os.getpid
def evaluate(rows, arg):
    {HIT}
    return hit and (index() % 2 == 0)
""", "same shape, different name"),

    # --- written to attack ROUND 3, not round 2 ---------------------------
    ("N2 get = getattr, attributes as string CONSTS", "forbidden", f"""
get = getattr
def evaluate(rows, arg):
    {HIT}
    subs = get(get(get(rows, "__class__"), "__base__"), "__subclasses__")()
    return hit and (len(subs) % 2 == 0)
""", "no attribute NAME is in co_names at all — they are constants"),

    ("N5 all = a clock wrapper", "forbidden", f"""
import time
def _tick(xs):
    return bool(time.time() % 2 < 1)
all = _tick
def evaluate(rows, arg):
    {HIT}
    return hit and all([True])
""", "an allow-listed BUILTIN name bound to something dangerous"),

    ("N8 append() mutates the caller's rows", "forbidden", f"""
def evaluate(rows, arg):
    {HIT}
    rows.append({{"source": "ghost", "status": "done"}})
    return hit
""", "round 2 allowed it: `append` was on the one flat list"),
]

print("== criterion 5: the evaluator is closed, and the check is attacked")
print()
print(f"  {'evaluator':<46} {'intent':<10} {'round2':<9} {'round3':<9} flips?")
print(f"  {'-'*46} {'-'*10} {'-'*9} {'-'*9} -----")
accepted_controls = 0
for name, intent, src, why in CASES:
    fn, _g = build(src)
    r2 = round2_check(fn)
    r3 = static_check(fn)
    v2 = "allowed" if not r2 else "REFUSED"
    v3 = "allowed" if not r3 else "REFUSED"
    mark = lambda v: (v + "!") if (intent == "forbidden" and v == "allowed") else v
    print(f"  {name:<46} {intent:<10} {mark(v2):<9} {mark(v3):<9} "
          f"{'YES' if flips(fn) else 'no'}")
    if intent == "allowed":
        accepted_controls += 1
        if r3:
            fail(f"{name}: the KNOWN-GOOD evaluator was refused — {r3}")
    else:
        if not r3:
            fail(f"{name}: forbidden evaluator ACCEPTED by round 3")
        else:
            print(f"       refused because: {'; '.join(r3)[:96]}")
print("  (flips? is sampled within ONE process; the cross-process sample is under "
      "the gaps below)")
print()

# --- the two attacks that need a doctored namespace, not doctored source ---
print("== __builtins__ is on the lookup path, so it is on the check's path")
for label, poison in (
    ("R2 __builtins__ is a poisoned MODULE", "module"),
    ("N1 __builtins__ is a poisoned DICT", "dict"),
):
    fn, g = build(f"""
def evaluate(rows, arg):
    {HIT}
    return hit and any([True])
""")
    tainted = lambda xs: bool(time.time() % 2 < 1)
    if poison == "module":
        m = types.ModuleType("fakebuiltins")
        for k in dir(builtins):
            setattr(m, k, getattr(builtins, k))
        m.any = tainted
        g["__builtins__"] = m
    else:
        d = dict(vars(builtins))
        d["any"] = tainted
        g["__builtins__"] = d
    r2, r3 = round2_check(fn), static_check(fn)
    if r2:
        fail(f"{label}: round 2 was expected to be fooled and was not — "
             "the harness is not reproducing the defect it exists to pin")
    elif not r3:
        fail(f"{label}: round 3 ACCEPTED a poisoned builtins fallback")
    else:
        ok(f"{label} — round 2 allowed it, round 3 refuses: {r3[0]}")
        accepted_controls += 0

print()
print("== sealing: a name that is not on the list does not resolve at all")
for label, src in (
    ("count = time.time", CASES[2][2]),
    ("index = os.getpid", CASES[3][2]),
    ("the pure control", CASES[0][2]),
):
    fn, _g = build(src)
    try:
        r = repr(seal(fn)(ROWS, ARG))
    except Exception as e:                                        # noqa: BLE001
        r = f"{type(e).__name__}: {e}"
    print(f"  {label:<24} sealed call -> {r}")
fn, _g = build(CASES[0][2])
if seal(fn)(ROWS, ARG) is not True:
    fail("sealing broke the pure control — enforcement must not change a legal verdict")
else:
    ok("sealing leaves the pure control's verdict unchanged")
fn, _g = build(CASES[2][2])
try:
    seal(fn)(ROWS, ARG)
    fail("sealing let `count = time.time` resolve")
except NameError:
    ok("sealing turns `count = time.time` into a NameError, not a verdict")

print()
print("== what this does NOT close, named rather than left for the next reviewer")

# TOCTOU: the static check is a photograph; the module can move afterwards.
fn, g = build(f"""
def evaluate(rows, arg):
    {HIT}
    return hit and any([True])
""")
before = static_check(fn) or "accepted"
g["any"] = lambda xs: bool(time.time() % 2 < 1)
if before == "accepted" and flips(fn):
    gap("TOCTOU: the static check accepted, then the module rebound a name and "
        "the UNSEALED evaluator flipped. Only sealing survives this, which is "
        "why the runtime SHALL call the sealed function and not the original.")
    if flips(seal(fn)):
        fail("sealing did NOT survive a post-check rebinding")
    else:
        ok("the SEALED evaluator is unmoved by the same rebinding")
else:
    fail("TOCTOU demonstration did not reproduce — the gap claim is unmeasured")

# A pristine builtin can still be impure: address-derived. `id` is off the
# list and is shown by putting it on; `str` is ON the list and needs no help.
fn, g = build(f"""
def evaluate(rows, arg):
    {HIT}
    return hit and (id(rows) % 2 == 0)
""")
ALLOWED_GLOBALS.add("id")
try:
    id_accepted = not static_check(fn)
finally:
    ALLOWED_GLOBALS.discard("id")
str_fn, _g = build(f"""
def evaluate(rows, arg):
    {HIT}
    return str((r for r in rows)) if hit else ""
""")
str_accepted = not static_check(str_fn)
str_out = seal(str_fn)(ROWS, ARG)
if id_accepted and str_accepted and " at 0x" in str_out:
    gap("address-derived builtins: `id` resolves to the pristine `builtins.id` "
        "and is address-derived, and so is `str` of anything with a default "
        f"repr — `str(<genexp>)` passes both halves and returns {str_out!r}. "
        "`str` is ON ALLOWED_GLOBALS today, so the short list is NOT a control "
        "against this class; identity to a pristine builtin is NECESSARY, not "
        "SUFFICIENT. Removing `str` would close this one spelling and nothing "
        "of gap 7, which has no name to remove.")
else:
    fail(f"the address-derived demonstration did not reproduce (id accepted="
         f"{id_accepted}, str accepted={str_accepted}, str -> {str_out!r})")

# Non-termination: no name at all, so no name-based check can see it.
fn, _g = build("""
def evaluate(rows, arg):
    while True:
        pass
""")
if not static_check(fn) and fn.__code__.co_names == ():
    def _boom(*_a):
        raise TimeoutError
    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, 0.3)
    hung = False
    try:
        seal(fn)(ROWS, ARG)
    except TimeoutError:
        hung = True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    if hung:
        gap("non-termination: `while True: pass` has an EMPTY co_names, so both "
            "checks accept it and the tick hangs. Purity is not termination; "
            "criterion 5 does not bound runtime and does not claim to.")
    else:
        fail("the non-termination demonstration did not reproduce")
else:
    fail("the non-termination case was refused — the demonstration is invalid")

# Argument-mediated impurity: the evaluator text IS the pure control.
class ClockRow(dict):
    def get(self, k, d=None):
        if k == "status":
            return "done" if time.time() % 2 < 1 else "new"
        return dict.get(self, k, d)


fn, _g = build(CASES[0][2])
if not static_check(fn) and flips(seal(fn), [ClockRow(source="row-A")]):
    gap("argument-mediated: the PURE control flips when a row's `.get` reads the "
        "clock. No static check on the evaluator can see this; criterion 6 "
        "(fixed argument list, already-parsed rows, no path) is the only thing "
        "that closes it. The two criteria hold only together.")
else:
    fail("the argument-mediated demonstration did not reproduce")

# Language-level nondeterminism: NO name beyond the pure control's, so no list,
# no seal and no in-process sample can see it. Only a fresh interpreter per
# sample can — and a tick is a fresh interpreter, so two ticks disagree.
print()
print("== across processes: a fresh interpreter and a distinct PYTHONHASHSEED each,"
      " sealed call")
SET_ORDER_SRC = f"""
def evaluate(rows, arg):
    {HIT}
    for x in {{"row-A", "row-B", "row-C"}}:
        return hit and x == "row-A"
"""
STR_ADDR_SRC = f"""
def evaluate(rows, arg):
    {HIT}
    return hit and (str((r for r in rows))[-5] in "02468")
"""
set_fn, _g = build(SET_ORDER_SRC)
set_static = static_check(set_fn)
set_inproc = flips(set_fn, n=40, gapsec=0)
set_xproc = verdicts_across_processes(SET_ORDER_SRC, range(64), stop_when_split=True)
str_static = static_check(build(STR_ADDR_SRC)[0])
str_xproc = verdicts_across_processes(STR_ADDR_SRC, range(8))
ctl_xproc = verdicts_across_processes(CASES[0][2], range(8))
_v = lambda bad: "allowed" if not bad else "REFUSED"
print(f"  {'set-order (zero names)':<28} static={_v(set_static):<8} in-process "
      f"flips={'YES' if set_inproc else 'no':<4} "
      f"across {sum(set_xproc.values())} processes -> {set_xproc}")
print(f"  {'str(genexp) address digit':<28} static={_v(str_static):<8} "
      f"{'':<21} across {sum(str_xproc.values())} processes -> {str_xproc}  "
      "(measured, not asserted: whether an address moves is the allocator's)")
print(f"  {'CONTROL pure':<28} {'':<36} "
      f"across {sum(ctl_xproc.values())} processes -> {ctl_xproc}")
if set_static:
    fail(f"the set-order evaluator was refused — the demonstration is invalid: {set_static}")
elif set_inproc:
    fail("the in-process sampler saw the set-order flip; it is supposed to be "
         "blind to a per-process constant, so the blindness claim is unmeasured")
elif len(set_xproc) < 2:
    fail(f"set-order did not flip across {sum(set_xproc.values())} hash seeds — "
         "the gap-7 claim is unmeasured")
elif str_static:
    fail(f"the str(genexp) evaluator was refused — the demonstration is invalid: {str_static}")
elif ctl_xproc != {"True": sum(ctl_xproc.values())}:
    fail(f"the pure control is not stable across processes: {ctl_xproc}")
else:
    gap("language-level nondeterminism: set iteration order is hash-seeded per "
        "process and `str` of a default repr is address-derived. The set-order "
        "evaluator has NO name beyond the pure control's, so no list, no seal and "
        "no in-process sample can see it; sealed, across fresh interpreters, it "
        f"returned {set_xproc}. A tick is a process. Only the cross-process "
        "sampler sees this, and it is bounded by the seeds it ran — evidence, "
        "never the gate.")

gap("hand-assembled bytecode: a code object whose linear disassembly disagrees "
    "with execution would defeat part (d). NOT demonstrated here — claimed as "
    "unproven rather than measured, and unclosed either way.")
gap("non-CPython: `dis`, `co_names` and opcode names are CPython details. On a "
    "runtime without them part (d) cannot run; sealing still can.")

print()
# The harness must not be able to report success by refusing everything.
if accepted_controls == 0:
    print("HARNESS BROKEN: no control that must be ACCEPTED, so a check that "
          "refuses every input would satisfy every assertion above")
    raise SystemExit(1)
if not gaps:
    print("HARNESS BROKEN: zero gaps reported. This check has known misses; a "
          "clean sheet means the demonstrations stopped running, not that the "
          "misses were closed")
    raise SystemExit(1)

print(f"{len(gaps)} known gap(s), named above.")
print("CRITERION-5 PURITY PINS PASS" if fails == 0 else f"{fails} FAILURE(S)")
raise SystemExit(1 if fails else 0)
