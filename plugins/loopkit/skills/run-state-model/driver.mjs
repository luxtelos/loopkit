#!/usr/bin/env node
// driver.mjs — one front door for three model checkers.
//
// Why this exists: the three engines disagree about how to say "your
// invariant is broken", and one of them lies.
//
//   fizz   -> exits 0 even when it prints "FAILED: Model checker failed"
//             (the vendor's own wrapper says so: "Binary doesn't exit
//             non-zero on FAILED — detect from output.")
//   quint  -> exits 1 on violation
//   TLC    -> exits 12 on an invariant violation, 0 on pass
//
// So a gate that shells out to `fizz` and trusts $? can never fail. This
// driver reads the OUTPUT and exports one contract:
//
//   0 = PASS      exhaustively checked, no violation
//   1 = VIOLATION counterexample printed above (trustworthy even from a
//                 sampler — a counterexample is a proof of the bug)
//   2 = ERROR     parse/tool failure, or a spec that asserts nothing.
//                 The model never ran, or ran against no property.
//   3 = UNPROVEN  it ran and found nothing, but the run SAMPLED rather
//                 than proved. Not the same as PASS and never reported as
//                 one. `quint run` is a sampler; so is any argv carrying
//                 -simulate (TLC) or -x/--simulation (fizz).
//
// The FizzBee reader FAILS CLOSED: PASS needs a positive success token, and
// a buffer this driver does not recognise is ERROR, not success. A guard
// whose default answer is "fine" is not a guard — that is the whole lesson
// of the a528111b fixtures next to this file.
//
// Usage:
//   node driver.mjs doctor
//   node driver.mjs check      <spec-file> [extra engine args...]
//   node driver.mjs verify     <spec-file> [extra engine args...]
//   node driver.mjs check-all  <dir> [expected-fixture-count]
//   node driver.mjs verify-all <dir> [expected-fixture-count]
//   node driver.mjs selftest
//
// check vs verify matters for ONE engine. fizz and TLC are exhaustive
// either way. `quint run` samples; `verify` sends the same .qnt to Apalache
// for a symbolic, bounded-exhaustive check (~12s vs ~0.3s here). So
// verify-all is the gate; check-all is the fast loop and says UNPROVEN.
//
// Engine paths come from env, defaulting to the install.sh layout under
// LOOPKIT_TOOLS_DIR (default ~/.loopkit/tools): FIZZ_BIN, QUINT_BIN,
// TLA2TOOLS_JAR, JAVA_BIN.
// DRIVER_TIMEOUT_MS caps every engine run (default 300000).

import { spawnSync } from 'node:child_process';
import {
  readFileSync, existsSync, readdirSync, realpathSync, statSync, mkdtempSync,
  writeFileSync, chmodSync,
} from 'node:fs';
import { basename, dirname, extname, join, resolve } from 'node:path';
import { homedir, tmpdir } from 'node:os';

const TOOLS_DIR = process.env.LOOPKIT_TOOLS_DIR || join(homedir(), '.loopkit', 'tools');
// Mirrors the asset names install.sh selects, so a default install is found
// on any of the four platforms FizzBee ships for.
function fizzAsset() {
  const os = process.platform === 'darwin' ? 'macos' : 'linux';
  const arch = process.arch === 'arm64' ? 'arm' : 'x86';
  return `fizzbee-v0.5.3-${os}_${arch}`;
}
const FIZZ_BIN = process.env.FIZZ_BIN
  || join(TOOLS_DIR, 'fizzbee', fizzAsset(), 'fizz');
const QUINT_BIN = process.env.QUINT_BIN
  || join(TOOLS_DIR, 'quint', 'node_modules', '.bin', 'quint');
const TLA2TOOLS_JAR = process.env.TLA2TOOLS_JAR
  || join(TOOLS_DIR, 'tlaplus', 'tla2tools.jar');
const JAVA_BIN = process.env.JAVA_BIN || 'java';
const TIMEOUT_MS = Number(process.env.DRIVER_TIMEOUT_MS || 300000);

const PASS = 0, VIOLATION = 1, ERROR = 2, UNPROVEN = 3;
const LABEL = { 0: 'PASS', 1: 'VIOLATION', 2: 'ERROR', 3: 'UNPROVEN' };
const SPEC_EXTS = ['.fizz', '.qnt', '.tla'];

// A sampled run needs a bound, or "found nothing" means nothing at all.
const DEFAULT_QUINT_SAMPLES = '--max-samples=10000';

// ---------------------------------------------------------------- args ----

const VERIFY_UNSUPPORTED = /^--max-samples\b/;
const CHECK_ARGS_RE = /CHECK-ARGS:[ \t]*(.*)$/;
const VERIFY_ARGS_RE = /VERIFY-ARGS:[ \t]*(.*)$/;

// These tokens become process arguments and they come out of a FILE — a
// spec somebody checked in, or pasted out of an issue. Untrusted input on a
// path to spawnSync gets a whitelist, not a sanitiser.
const SAFE_ARG = /^-{0,2}[A-Za-z0-9][A-Za-z0-9_.:,/@+-]*(=[A-Za-z0-9_.:,/@+-]*)?$/;
const MAX_SPEC_ARGS = 16;

// Flags that turn an exhaustive engine into a sampler. Detected, not
// forbidden: a spec is allowed to ask for a quick sample, it is just never
// allowed to have that reported as a proof.
const SAMPLING_FLAG = /^(-simulate|-x|--simulation|--exploration_strategy=random)$/;

function sanitiseArgs(raw, file) {
  const kept = [];
  for (const a of raw) {
    if (kept.length >= MAX_SPEC_ARGS) {
      console.error(`WARN: ${basename(file)} declares more than ${MAX_SPEC_ARGS} args; ignoring the rest`);
      break;
    }
    if (SAFE_ARG.test(a)) kept.push(a);
    else console.error(`WARN: ${basename(file)} declares an unsafe arg, dropped: ${JSON.stringify(a)}`);
  }
  return kept;
}

function specArgs(file, mode) {
  const head = readFileSync(file, 'utf8').split('\n').slice(0, 10);
  const grab = (re) => {
    for (const line of head) {
      const m = re.exec(line);
      if (m) return m[1].trim().split(/\s+/).filter(Boolean);
    }
    return null;
  };
  let raw;
  if (mode === 'verify') {
    raw = grab(VERIFY_ARGS_RE)
      || (grab(CHECK_ARGS_RE) || []).filter((a) => !VERIFY_UNSUPPORTED.test(a));
  } else {
    raw = grab(CHECK_ARGS_RE) || [];
  }
  return sanitiseArgs(raw, file);
}

// --------------------------------------------------------------- spawn ----

const ANSI = /\u001b\[[0-9;]*[A-Za-z]/g;

function run(cmd, args, cwd) {
  const r = spawnSync(cmd, args, {
    cwd, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, timeout: TIMEOUT_MS,
  });
  if (r.error) {
    const timedOut = r.error.code === 'ETIMEDOUT' || r.signal === 'SIGTERM';
    return { code: null, out: `${r.error.message}\n`, spawnFailed: true, timedOut, argv: [cmd, ...args] };
  }
  // Join with a newline, never bare concatenation: stdout whose last line
  // has no trailing newline used to glue onto stderr's first line and hide
  // a violation token inside "Nodes: 19FAILED: ...".
  const out = [r.stdout || '', r.stderr || ''].filter(Boolean).join('\n').replace(ANSI, '');
  return { code: r.status, out, spawnFailed: false, timedOut: r.signal === 'SIGTERM', argv: [cmd, ...args] };
}

// ------------------------------------------------------------- verdicts ----
// Each reads the OUTPUT. Only TLC's exit code carries information, and it
// is used as a first-class signal rather than a tiebreak.

const FIZZ_BROKEN = /compilation failed|traceback \(most recent call last\)|extraneous input|mismatched input|syntax error|no such file/i;
const FIZZ_BAD = /\bFAILED\b|\bDEADLOCK\b|invariant violated|assertion violated/i;
const FIZZ_GOOD = /\bPASSED\b|model checker completed successfully/i;

function verdictFizz({ code, out, spawnFailed }) {
  if (spawnFailed) return ERROR;
  if (FIZZ_BROKEN.test(out)) return ERROR;
  if (FIZZ_BAD.test(out)) return VIOLATION;
  if (FIZZ_GOOD.test(out) && code === 0) return PASS;
  return ERROR; // fail closed: an unrecognised buffer is not a pass
}

const QUINT_BAD = /\[violation\]|invariant violated|found a counterexample/i;
const QUINT_GOOD = /\[ok\]|no violation found|trace length statistics/i;

function verdictQuint({ code, out, spawnFailed }) {
  if (spawnFailed) return ERROR;
  if (QUINT_BAD.test(out)) return VIOLATION;
  if (code === 0 && QUINT_GOOD.test(out)) return PASS;
  return ERROR;
}

const TLC_BAD = /is violated|temporal properties were violated|deadlock reached/i;

function verdictTlc({ code, out, spawnFailed }) {
  if (spawnFailed) return ERROR;
  if (code === 12) return VIOLATION;   // format-independent, free, exact
  if (TLC_BAD.test(out)) return VIOLATION;
  if (code === 0) return PASS;
  return ERROR;
}

// ----------------------------------------------------- property presence ----
// A spec that asserts nothing cannot fail. Refuse it rather than pass it.

function propertyMissing(abs, ext, args) {
  if (ext === '.qnt') {
    return args.some((a) => a.startsWith('--invariant')) ? null
      : 'no --invariant: declare one on a CHECK-ARGS line, or quint asserts nothing';
  }
  if (ext === '.tla') {
    const cfg = abs.replace(/\.tla$/, '.cfg');
    if (!existsSync(cfg)) return `no ${basename(cfg)} beside the module; TLC needs one`;
    return /^\s*(INVARIANT|INVARIANTS|PROPERTY|PROPERTIES)\b/mi.test(readFileSync(cfg, 'utf8'))
      ? null : `${basename(cfg)} declares no INVARIANT or PROPERTY`;
  }
  if (ext === '.fizz') {
    return /\bassertion\b/.test(readFileSync(abs, 'utf8')) ? null
      : 'no assertion in the spec; the model checker would assert nothing';
  }
  return null;
}

// -------------------------------------------------------------- dispatch ----

function checkOne(file, extra = [], mode = 'check') {
  let abs, cwd, name, ext;
  try {
    abs = resolve(file);
    if (!statSync(abs).isFile()) throw new Error('not a regular file');
    cwd = dirname(abs);
    name = basename(abs);
    ext = extname(abs);
  } catch (e) {
    console.error(`ERROR: cannot read spec ${file}: ${e.message}`);
    return ERROR;
  }
  if (!SPEC_EXTS.includes(ext)) {
    console.error(`ERROR: unknown spec type: ${name} (want ${SPEC_EXTS.join(', ')})`);
    return ERROR;
  }

  let args;
  try {
    args = [...specArgs(abs, mode), ...extra];
  } catch (e) {
    console.error(`ERROR: cannot parse args from ${name}: ${e.message}`);
    return ERROR;
  }

  let missing;
  try {
    missing = propertyMissing(abs, ext, args);
  } catch (e) {
    console.error(`ERROR: checking properties of ${name}: ${e.message}`);
    return ERROR;
  }
  if (missing) {
    console.error(`\n=== ERROR [${ext.slice(1)}] ${name} — ${missing} ===`);
    return ERROR;
  }

  let res, verdict, engine;
  switch (ext) {
    case '.fizz':
      engine = 'fizzbee';
      res = run(FIZZ_BIN, [...args, name], cwd);
      verdict = verdictFizz(res);
      break;
    case '.qnt': {
      engine = 'quint';
      const sub = mode === 'verify' ? 'verify' : 'run';
      const bounded = sub === 'run' && !args.some((a) => a.startsWith('--max-samples'))
        ? [DEFAULT_QUINT_SAMPLES] : [];
      res = run(QUINT_BIN, [sub, name, ...args, ...bounded], cwd);
      verdict = verdictQuint(res);
      break;
    }
    case '.tla':
      engine = 'tlc';
      res = run(JAVA_BIN, ['-XX:+UseParallelGC', '-cp', TLA2TOOLS_JAR, 'tlc2.TLC', ...args, name], cwd);
      verdict = verdictTlc(res);
      break;
  }

  // Sampling is decided from the REAL argv that was spawned, never from the
  // mode flag: a spec comment carrying -simulate would otherwise downgrade
  // TLC to one random trace while the driver still printed "PASS [tlc]".
  const sampled = (engine === 'quint' && mode !== 'verify')
    || res.argv.some((a) => SAMPLING_FLAG.test(a));
  if (verdict === PASS && sampled) verdict = UNPROVEN;

  const how = sampled ? ' sampled' : (engine === 'quint' ? ' apalache' : '');
  console.log(res.out.trimEnd());
  if (res.timedOut) console.log(`\n(engine killed after ${TIMEOUT_MS} ms)`);
  console.log(`\n=== ${LABEL[verdict]} [${engine}${how}] ${name}  (engine exit ${res.code}) ===`);
  if (verdict === UNPROVEN) {
    console.log('    Nothing was found, but this run SAMPLED — it is not a proof.');
    console.log(`    Prove it: node driver.mjs verify ${file}`);
  }
  return verdict;
}

// --------------------------------------------------------------- sweeps ----

// The engines write counterexamples into the spec's own directory, and
// Apalache writes them AS .tla modules — so a sweep that globs *.tla will,
// on its second run, try to model check its own output and quietly grow the
// fixture set. Caught by the expected-count assertion below on the first
// run after it was added, which is the argument for having it.
const GENERATED = /^(violation\d*|counterexample\d*|MC)\.|_TTrace_/i;

function collectSpecs(dir) {
  const found = [];
  const walk = (d) => {
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      if (entry.name.startsWith('.')) continue;
      const p = join(d, entry.name);
      if (entry.isDirectory()) { walk(p); continue; }
      if (GENERATED.test(entry.name)) continue;
      if (SPEC_EXTS.includes(extname(entry.name))) found.push(p);
    }
  };
  walk(dir);
  return found.sort();
}

// A fixture is expected to VIOLATE when "buggy" is its own word in the
// stem — not a substring, so `debuggy.fizz` is a must-pass fixture and
// `RoleChangeSagaBuggy.tla` is not (TLA+ module names cannot carry a dash).
function mustViolate(file) {
  const stem = basename(file, extname(file));
  return /(^|[-_.])buggy([-_.]|$)/i.test(stem) || /buggy$/i.test(stem);
}

function checkAll(dir, mode = 'check', expected = null) {
  let files;
  try {
    files = collectSpecs(dir);
  } catch (e) {
    console.error(`ERROR: cannot read fixture directory ${dir}: ${e.message}`);
    return ERROR;
  }
  if (files.length === 0) {
    console.error(`ERROR: no specs under ${dir}. A sweep over nothing is not a pass.`);
    return ERROR;
  }
  if (expected !== null && files.length !== expected) {
    console.error(`ERROR: expected ${expected} specs under ${dir}, found ${files.length}. `
      + 'A renamed or moved fixture must not quietly shrink the gate.');
    return ERROR;
  }

  const results = files.map((f) => [f, checkOne(f, [], mode)]);
  console.log('\n--- summary ---');
  for (const [f, v] of results) console.log(`${LABEL[v]}\t${basename(f)}`);

  const errored = results.filter(([, v]) => v === ERROR);
  const mismatched = results.filter(([f, v]) =>
    mustViolate(f) ? v !== VIOLATION : ![PASS, UNPROVEN].includes(v));
  const unproven = results.filter(([f, v]) => !mustViolate(f) && v === UNPROVEN);

  // Precedence: a broken toolchain is never reported as a broken model.
  if (errored.length) {
    console.error(`\n${errored.length} spec(s) ERRORED — the toolchain or the spec, not the design.`);
    return ERROR;
  }
  if (mismatched.length) {
    console.error(`\n${mismatched.length} spec(s) did not land on their expected verdict.`);
    return VIOLATION;
  }
  if (unproven.length) {
    console.error(`\n${unproven.length} spec(s) only SAMPLED. Run verify-all to prove them.`);
    return UNPROVEN;
  }
  return PASS;
}

// --------------------------------------------------------------- doctor ----

function resolveOnPath(name) {
  for (const dir of (process.env.PATH || '').split(':').filter(Boolean)) {
    const candidate = join(dir, name);
    try { if (statSync(candidate).mode & 0o111) return candidate; } catch { /* next */ }
  }
  return null;
}

function pathFizzCollision() {
  const found = resolveOnPath('fizz');
  if (!found) return null;
  try { if (realpathSync(found) === realpathSync(FIZZ_BIN)) return null; } catch { /* warn */ }
  return found;
}

// doctor inspects what it prints. Existence is not health: three zero-byte
// stand-ins used to report "ok" three times with EACCES underneath.
function doctor() {
  const probes = [
    { name: 'fizzbee', path: FIZZ_BIN, want: /Usage:|fizzbee/i, probe: () => run(FIZZ_BIN, ['--help']) },
    { name: 'quint', path: QUINT_BIN, want: /^\d+\.\d+\.\d+/, probe: () => run(QUINT_BIN, ['--version']) },
    { name: 'tlc', path: TLA2TOOLS_JAR, want: /TLC2 Version/, probe: () => run(JAVA_BIN, ['-cp', TLA2TOOLS_JAR, 'tlc2.TLC']) },
  ];
  let bad = 0;
  for (const { name, path, want, probe } of probes) {
    if (!existsSync(path)) { console.log(`MISSING  ${name}  ${path}`); bad++; continue; }
    const r = probe();
    const first = (r.out || '').split('\n').map((l) => l.trim()).filter(Boolean)[0] || '(no output)';
    if (r.spawnFailed || !want.test(r.out)) {
      console.log(`BROKEN   ${name}  ${path}\n         ${first}`);
      bad++;
    } else {
      console.log(`ok       ${name}  ${path}\n         ${first}`);
    }
  }
  const clash = pathFizzCollision();
  if (clash) {
    console.log(`\nWARN     a DIFFERENT fizz owns the name on PATH: ${clash}`);
    console.log(`         it is not ${FIZZ_BIN}`);
    console.log('         Bare `fizz` — which the vendor /fizz-check skill tells you');
    console.log('         to type — drives that one. Use this driver, or an absolute path.');
  }
  return bad ? ERROR : PASS;
}

// ------------------------------------------------------------- selftest ----
// Drives the driver against engines built to lie to it. Every case below is
// a real drift shape that once produced a silent PASS. If you widen a
// verdict reader, widen this first.

function stub(dir, name, body) {
  const p = join(dir, name);
  writeFileSync(p, `#!/bin/bash\n${body}\n`);
  chmodSync(p, 0o755);
  return p;
}

function selftest() {
  const box = mkdtempSync(join(tmpdir(), 'rsm-selftest-'));
  const spec = join(box, 'S.fizz');
  writeFileSync(spec, 'action Init:\n    x = 0\nalways assertion A:\n    return True\n');
  const qspec = join(box, 'S.qnt');
  writeFileSync(qspec, '// CHECK-ARGS: --invariant=inv\nmodule s { val inv = true }\n');
  const qbare = join(box, 'Bare.qnt');
  writeFileSync(qbare, 'module s { val inv = true }\n');
  const tspec = join(box, 'T.tla');
  writeFileSync(tspec, '---- MODULE T ----\n====\n');
  writeFileSync(join(box, 'T.cfg'), 'INIT Init\nNEXT Next\nINVARIANT Inv\n');

  const cases = [
    ['fizz: ANSI-coloured FAILED', 'fizz', 'printf "Model checking\\n\\033[31mFAILED: Model checker failed\\033[0m\\n"', spec, VIOLATION],
    ['fizz: indented FAILED', 'fizz', 'echo "Model checking"; echo "  FAILED: Model checker failed"', spec, VIOLATION],
    ['fizz: lowercased failed', 'fizz', 'echo "Model checking"; echo "failed: model checker failed"', spec, VIOLATION],
    ['fizz: Deadlock detected', 'fizz', 'echo "Model checking"; echo "Deadlock detected"', spec, VIOLATION],
    ['fizz: reworded violation', 'fizz', 'echo "Model checking"; echo "Invariant violated: A"', spec, VIOLATION],
    ['fizz: FAILED on stderr, no trailing newline on stdout', 'fizz', 'printf "Nodes: 19"; printf "FAILED: x\\n" >&2', spec, VIOLATION],
    ['fizz: python traceback', 'fizz', 'echo "Model checking"; echo "Traceback (most recent call last):"', spec, ERROR],
    ['fizz: ANTLR noise', 'fizz', 'echo "Model checking"; echo "line 7:9 mismatched input \\x27=\\x27"', spec, ERROR],
    ['fizz: unrecognised buffer fails CLOSED', 'fizz', 'echo "Model checking"; echo "hmm"', spec, ERROR],
    ['fizz: real success', 'fizz', 'echo "Model checking"; echo "PASSED: Model checker completed successfully"', spec, PASS],
    ['quint: non-finding is UNPROVEN, not PASS', 'quint', 'echo "Trace length statistics: max=9"', qspec, UNPROVEN],
    ['quint: violation is trustworthy', 'quint', 'echo "[violation] Found an issue"; exit 1', qspec, VIOLATION],
    ['quint: no --invariant is refused', 'quint', 'echo "Trace length statistics: max=9"', qbare, ERROR],
    ['tlc: exit 12 alone means violation', 'java', 'echo "some unrecognised wording"; exit 12', tspec, VIOLATION],
    ['tlc: clean run passes', 'java', 'echo "Model checking completed"; exit 0', tspec, PASS],
  ];

  let failed = 0;
  for (const [title, engine, body, target, want] of cases) {
    const bin = stub(box, `stub-${engine}-${failed}-${title.replace(/\W+/g, '')}`, body);
    const env = { FIZZ_BIN: process.env.FIZZ_BIN, QUINT_BIN: process.env.QUINT_BIN, JAVA_BIN: process.env.JAVA_BIN };
    const r = spawnSync(process.execPath, [new URL(import.meta.url).pathname, 'check', target], {
      encoding: 'utf8',
      env: { ...process.env, ...env, [engine === 'java' ? 'JAVA_BIN' : `${engine.toUpperCase()}_BIN`]: bin },
    });
    const got = r.status;
    const ok = got === want;
    if (!ok) failed++;
    console.log(`${ok ? 'ok  ' : 'FAIL'}  ${title}  (want ${LABEL[want]}, got ${LABEL[got] ?? got})`);
  }

  // Sweep-level guarantees.
  const empty = mkdtempSync(join(tmpdir(), 'rsm-empty-'));
  const sweeps = [
    ['check-all over an empty directory is ERROR, not PASS', [empty], ERROR],
    ['check-all over a missing directory is ERROR', [join(empty, 'nope')], ERROR],
  ];
  for (const [title, argv, want] of sweeps) {
    const r = spawnSync(process.execPath, [new URL(import.meta.url).pathname, 'check-all', ...argv], { encoding: 'utf8' });
    const ok = r.status === want;
    if (!ok) failed++;
    console.log(`${ok ? 'ok  ' : 'FAIL'}  ${title}  (want ${LABEL[want]}, got ${LABEL[r.status] ?? r.status})`);
  }

  console.log(`\n${failed ? `${failed} FAILED` : `all ${cases.length + sweeps.length} selftests passed`}`);
  return failed ? VIOLATION : PASS;
}

// ------------------------------------------------------------------ cli ----

const [cmd, ...rest] = process.argv.slice(2);
const count = (v) => (v === undefined ? null : Number(v));
let exit;
switch (cmd) {
  case 'doctor': exit = doctor(); break;
  case 'check': exit = rest.length ? checkOne(rest[0], rest.slice(1), 'check') : ERROR; break;
  case 'verify': exit = rest.length ? checkOne(rest[0], rest.slice(1), 'verify') : ERROR; break;
  case 'check-all': exit = rest.length ? checkAll(rest[0], 'check', count(rest[1])) : ERROR; break;
  case 'verify-all': exit = rest.length ? checkAll(rest[0], 'verify', count(rest[1])) : ERROR; break;
  case 'selftest': exit = selftest(); break;
  default:
    console.error('usage: node driver.mjs doctor | check|verify <spec> [args]'
      + ' | check-all|verify-all <dir> [expected-count] | selftest');
    console.error('note: check-all and verify-all REQUIRE a directory — they no longer default to ".".');
    exit = ERROR;
}
process.exit(exit);
