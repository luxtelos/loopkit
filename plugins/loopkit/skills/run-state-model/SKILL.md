---
name: run-state-model
description: Model-check a state machine before it becomes a spec — FizzBee, Quint/Apalache and TLA+ behind one driver with one honest exit-code contract (PASS / VIOLATION / ERROR / UNPROVEN). Use when a design has two writers, a retry, a guard-then-write, or a non-atomic step.
---

# run-state-model

Three model checkers, one driver. The skill directory is
`${CLAUDE_PLUGIN_ROOT}/skills/run-state-model`; call it `$RSM` below.

**The agent path is `driver.mjs`.** Do not shell out to `fizz`, `quint` or
`java tlc2.TLC` directly — gotcha 1 is why that gate can never fail.

```bash
node "$RSM/driver.mjs" doctor
```

## Why this exists

Unit tests check the transitions you thought of. A model checker enumerates the
ones you didn't, and hands back the shortest trace that breaks your invariant.
If a project says its client state is a deterministic state machine and every
transition must be idempotent, this is how that claim gets checked.

The worked example in `examples/` is not a toy. It is a real shipped bug
reduced to its state machine: a route that wrote the identity provider, THEN
ran `assertNotLastAdmin`, returned 422 *"Cannot remove last admin"* — and
removed the last admin anyway, at the victim's next login, because the identity
provider is what login believes. The suite that shipped that bug could not
catch it: it mocked both guards to no-ops.

## Install

```bash
bash "$RSM/install.sh"
```

Idempotent — re-running prints `already at ...` and exits 0. Installs under
`LOOPKIT_TOOLS_DIR` (default `~/.loopkit/tools`; FizzBee alone unpacks to
~67 MB, so point it at a disk with room). Needs `node`, `npm`, `java`, `curl`.
Verified end to end on darwin/arm64; it selects a Linux asset by `uname` but
that path has not been run, and the Linux FizzBee assets are ~300 MB against
33 MB for macOS.

Override with `FIZZ_BIN`, `QUINT_BIN`, `TLA2TOOLS_JAR`, `JAVA_BIN`,
`LOOPKIT_TOOLS_DIR`, `DRIVER_TIMEOUT_MS`.

## Run (agent path)

```bash
node "$RSM/driver.mjs" check "$RSM/examples/role-change-saga-buggy.fizz"
```

Prints the counterexample, exits **1**. The fixed one exits **0**:

```bash
node "$RSM/driver.mjs" check "$RSM/examples/role-change-saga.fizz"
```

**`verify-all` is the gate** — it is the sweep that proves rather than
samples, and the only one that returns 0 across all six fixtures:

```bash
node "$RSM/driver.mjs" verify-all "$RSM/examples" 6
```

`check-all` is the fast loop. It returns **3 (UNPROVEN)** on these fixtures
by design, because `quint run` samples:

```bash
node "$RSM/driver.mjs" check-all "$RSM/examples" 6
```

The trailing `6` is the expected fixture count, and it is not decoration —
it caught Apalache writing `violation.tla` into the fixture directory, which
a bare glob would have silently adopted as two more specs.

### Driver contract

| Command | What it does |
|---|---|
| `doctor` | Probes each engine's **output**, not just its path. Exits 2 if any is missing or broken |
| `check <spec> [args]` | `.fizz`→fizz, `.qnt`→`quint run`, `.tla`→TLC |
| `verify <spec> [args]` | Same, but `.qnt` goes to `quint verify` (Apalache) |
| `check-all <dir> [n]` / `verify-all <dir> [n]` | Recursive sweep, then a summary. `n` asserts the fixture count |
| `selftest` | Drives the driver against stub engines built to lie to it |

Exit codes, identical for all three engines — this is the driver's whole job:

- **0 PASS** — checked exhaustively, no violation
- **1 VIOLATION** — counterexample printed. Trustworthy even from a sampler:
  a counterexample is a proof of the bug
- **2 ERROR** — parse or tool failure, *or a spec that asserts nothing*. The
  model never ran, or ran against no property
- **3 UNPROVEN** — it ran and found nothing, but it **sampled**. Never
  reported as PASS

Sweep precedence: any ERROR → 2, else any wrong verdict → 1, else any
UNPROVEN → 3, else 0. A missing toolchain can therefore never be mistaken
for a broken design.

A spec may carry engine flags on a comment line in its first 10 lines. This
is how Quint gets its invariant name — it is a CLI flag, not part of the
module:

```
// CHECK-ARGS: --invariant=safety --max-steps=8 --max-samples=2000
```

Add `VERIFY-ARGS:` to override it under `verify`. Those tokens become
process argv, so they are whitelisted: an engine flag or a plain value,
anything with whitespace, quotes or shell metacharacters is dropped by name
with a warning.

**A spec that asserts nothing is refused, not passed.** A `.qnt` with no
`--invariant`, a `.tla` whose `.cfg` declares no `INVARIANT`/`PROPERTY`, a
`.fizz` with no `assertion` — all exit 2.

## Which engine

Start in **FizzBee** for anything shaped like a service. Python-shaped
syntax, and it models non-atomic steps natively — "the DB write landed, the
response never came back" is a first-class state, which is the shape of most
bugs. The buggy saga: 19 nodes, 2.8 ms.

**Quint** when you want the same model checked symbolically by Apalache, or
when the reviewer reads TypeScript more fluently. 27 ms sampled, ~12 s under
Apalache.

**TLA+** when a reviewer already knows it, or the property needs TLAPS. TLC
is exhaustive and unsurprising. It is no harder to *run* than the others —
only harder to write.

Do not pick by criticality. Pick FizzBee, get a spec today, port it if the
model outgrows the engine.

FizzBee ships Claude skills of its own (`fizz install-skills`) for WRITING
a spec. Use theirs to write one, use this driver to run one: their examples
type bare `fizz`, and `fizz` alone cannot be gated on (gotcha 1).

## Gotchas

1. **`fizz` exits 0 when the model FAILS.** The vendor's own wrapper says so:
   *"Binary doesn't exit non-zero on FAILED — detect from output."*
   `fizz spec.fizz && echo ok` prints `ok` on a broken model, forever. So the
   FizzBee reader **fails closed**: PASS needs a positive success token, and a
   buffer the driver does not recognise is ERROR. Six drift shapes that used
   to produce a silent PASS — ANSI colour, indent, lowercase, reworded text,
   `Deadlock detected`, and a violation on stderr glued to an unterminated
   stdout line — are pinned in `selftest`.
2. **TLC exits 12 on a violation**, not 1. The driver reads that code
   directly: it is free and format-independent, so the TLC path is immune to
   the whole drift class above.
3. **`quint run` samples.** A non-finding is UNPROVEN (3), never PASS. Only
   `verify` (Apalache) earns a 0. A sampled run without `--max-samples` gets
   a floor of 10000 so "found nothing" means something.
4. **Sampling is detected from the real argv, not the mode flag.** A `.tla`
   whose comment carries `-simulate` turns TLC into one random trace; that
   run reports UNPROVEN, not `PASS [tlc]`.
5. **FizzBee reserves `role` as a keyword.** A local variable named `role`
   fails to parse with a wall of ANTLR noise pointing at the wrong line.
6. **`VAR = any COLLECTION` is deprecated** for `oneof`. It still runs and
   prints a DeprecationWarning per use, which buries the verdict.
7. **`require` is a guard, not an assertion.** `require balance >= amount`
   disables the action instead of failing it — that is how a FizzBee spec
   passes vacuously. Assert with `always assertion`.
8. **A TLA+ module name must equal its filename** and cannot contain a dash,
   so the TLA fixtures are `RoleChangeSagaBuggy.tla` while the others are
   kebab-case. TLC picks up `<Module>.cfg` from the same directory.
9. **Every engine litters next to the spec** — and Apalache writes its
   counterexamples **as `.tla` modules**, so a naive sweep model checks its
   own output on the second run. The driver skips `violation*`,
   `counterexample*`, `MC.*` and `*_TTrace_*`; `examples/.gitignore` covers
   those plus `out/`, `_apalache-out/`, `states/` and `*.json`.
10. **Quint's Rust evaluator downloads to `~/.quint`** on first run, not to
    the tools dir. First `quint run` is slow (~2 s) and needs network.
11. **A stale `fizz` on PATH shadows the pinned one.** An older package
    manager build does not warn — it dies with a raw traceback on a spec the
    pinned version checks in milliseconds. The driver never uses PATH for
    `fizz`; `doctor` WARNs if one is found there anyway.
12. **`check-all` needs a directory argument.** It does not default to `.`,
    because a sweep that finds nothing used to exit 0.
13. **Vendor installers write to `$HOME/.claude`.** If your Claude config
    lives elsewhere (`CLAUDE_CONFIG_DIR`), pass `HOME=<that parent>` to
    `fizz install-skills` or the skills land where nothing reads them.

## What the fixtures do and do not prove

Stated because a model's scope is exactly as load-bearing as its invariant.

- The buggy trio reproduce the shipped bug and cannot violate for an
  unrelated reason: remove the 422-returning transitions and no violation
  remains, so every path to the bad state runs through the unguarded
  rejection write.
- `RejectionWritesNothing` is the sharper witness and fires at depth 1 —
  one step, no login needed. `FirmKeepsAnAdmin` needs two.
- FizzBee and TLC find that trace **exhaustively**. Quint's `check` path
  finds it by sampling, so "all three find it" is true of `verify-all`, not
  of every run.
- **Not modelled:** any other writer of the identity provider. Login-time
  adoption carries no last-admin guard, so an out-of-band console demotion of
  the last admin would still zero the firm, and the fixed model's PASS says
  nothing about it. Adding such an action would make the fixed model violate
  — that is a real finding about the system, and a separate ticket.

## Where a spec goes

Beside the design it justifies: `specs/<feature>.model.fizz`, with the trace
cited in the EARS criteria. `specs/` is written through the spec-writer
skill — the model is evidence you hand it, not a replacement for it.
`examples/` is fixtures for this driver only. Keep a `-buggy` twin next to
every fixed model: a checker that has never seen a violation has never been
tested.

## Files

```
skills/run-state-model/
  SKILL.md
  driver.mjs        <- the harness; three engines, one exit-code contract
  install.sh        <- idempotent, pinned, https-only installer
  examples/
    role-change-saga-buggy.fizz  role-change-saga.fizz
    role-change-saga-buggy.qnt   role-change-saga.qnt
    RoleChangeSagaBuggy.tla|cfg  RoleChangeSaga.tla|cfg
```
