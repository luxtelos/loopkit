#!/usr/bin/env python3
"""judge.py — the pairwise, position-swapped judge as a script.

    judge.py --criterion "<EARS line>" [--criterion ...] --a FILE --b FILE
             [--judge NAME] [--generator NAME] [--model NAME] [--root DIR]
    judge.py --print-rules

For every criterion the script calls `claude -p --output-format json` twice:
once with A in position 1 and B in position 2, once swapped. The prompt is
built at run time from the Rules section of
skills/acceptance-review/references/judge.md — the six rules live there and
only there; this file never carries them. The verdict rule is deterministic:

    the two runs disagree  → FINAL: TIE confidence 0.5   (position bias showed)
    the two runs agree     → FINAL: <that verdict> confidence <c>, where c is
                             the smaller of the two reported confidences
                             clamped to [0.5, 1.0]

One block per criterion is printed in judge.md's output shape. Every run's
raw responses land in state/judge/<ts>-<pid>.json; a completed run appends one
`judge` event (final, confidence) to state/ticks.jsonl through ticks.py.

Exit codes: 0 verdicts printed · 2 bad input, `claude` missing, `claude`
non-zero, or a response without a verdict (no FINAL line is printed) ·
3 `--judge` names the same thing as `--generator` (the judge is never the
generator; no FINAL line). No retries — a failed call is a failed run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
RULES_FILE = PLUGIN_ROOT / "skills" / "acceptance-review" / "references" / "judge.md"

sys.path.insert(0, str(HERE))
import ticks  # noqa: E402  (the ledger writer loop-next.sh and stop_gate.sh use)

STATES = ("held", "not held", "unverifiable")
VERDICT_RE = re.compile(r"^\s*VERDICT\s*:\s*(FIRST|SECOND|TIE)\b", re.I | re.M)
CONF_RE = re.compile(r"^\s*CONFIDENCE\s*:\s*([0-9]*\.?[0-9]+)", re.I | re.M)
LINE_RE = {
    "FIRST": re.compile(r"^\s*FIRST\s*:\s*(.+?)\s*$", re.I | re.M),
    "SECOND": re.compile(r"^\s*SECOND\s*:\s*(.+?)\s*$", re.I | re.M),
    "JUSTIFICATION": re.compile(r"^\s*JUSTIFICATION\s*:\s*(.+?)\s*$", re.I | re.M),
}


class JudgeError(Exception):
    """Anything that ends the run with exit 2."""


# ---------------------------------------------------------------- rules --

def read_rules(path: Path = RULES_FILE) -> str:
    """The `## Rules` section of judge.md, heading excluded, one rule per line.

    judge.md hard-wraps its list items at ~78 columns; Markdown reads those
    soft breaks as one paragraph, so each numbered rule is unwrapped onto a
    single line. The words are the file's words — nothing is added, dropped
    or reordered — which is what lets a one-line grep prove the rule reached
    the model.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise JudgeError(f"cannot read judge rules at {path}: {e}")
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.strip().lower() == "## rules"
            continue
        if not inside:
            continue
        if line[:1].isspace() and line.strip() and out and out[-1]:
            out[-1] = out[-1].rstrip() + " " + line.strip()  # continuation of the item above
        else:
            out.append(line.rstrip())
    rules = "\n".join(out).strip("\n")
    if not rules:
        raise JudgeError(f"no '## Rules' section in {path}")
    return rules


# --------------------------------------------------------------- prompt --

def build_prompt(rules: str, criterion: str, first: str, second: str) -> str:
    return (
        "You are the judge. Grade two candidates against ONE criterion.\n"
        "Follow these rules exactly as written:\n\n"
        f"{rules}\n\n"
        f"CRITERION: {criterion}\n\n"
        "CANDIDATE FIRST (position 1):\n"
        f"{first.rstrip()}\n\n"
        "CANDIDATE SECOND (position 2):\n"
        f"{second.rstrip()}\n\n"
        "Answer with exactly these five lines and nothing else:\n"
        "FIRST: <held|not held|unverifiable> — <evidence: command + output line>\n"
        "SECOND: <held|not held|unverifiable> — <evidence>\n"
        "JUSTIFICATION: <why one is better, or why they tie>\n"
        "VERDICT: <FIRST|SECOND|TIE>\n"
        "CONFIDENCE: <a number from 0.0 to 1.0>\n"
    )


# ---------------------------------------------------------------- claude --

def call_claude(prompt: str, model: str | None) -> dict:
    """One `claude -p --output-format json` call. Returns the raw record."""
    exe = shutil.which("claude")
    if not exe:
        raise JudgeError("claude not on PATH")
    args = [exe, "-p", "--output-format", "json"]
    if model:
        args += ["--model", model]
    try:
        p = subprocess.run(args, input=prompt, capture_output=True, text=True)
    except OSError as e:
        raise JudgeError(f"claude could not be started: {e}")
    return {"args": args[1:], "rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def result_text(raw: dict) -> str:
    """The judge's answer text out of the JSON envelope claude -p emits."""
    if raw["rc"] != 0:
        raise JudgeError(f"claude exited {raw['rc']}: {raw['stderr'].strip()[:200]}")
    try:
        doc = json.loads(raw["stdout"])
    except ValueError:
        raise JudgeError("claude output is not JSON")
    if isinstance(doc, list):  # a stream of records: the last one is the result
        doc = doc[-1] if doc else {}
    if isinstance(doc, dict):
        res = doc.get("result")
        if isinstance(res, str):
            return res
        if isinstance(res, (dict, list)):
            return json.dumps(res)
        raise JudgeError("claude JSON carries no result")
    if isinstance(doc, str):
        return doc
    raise JudgeError("claude JSON has an unexpected shape")


def parse_answer(text: str) -> dict:
    m = VERDICT_RE.search(text)
    if not m:
        raise JudgeError("no VERDICT line in the judge's answer")
    ans = {"verdict": m.group(1).upper()}
    c = CONF_RE.search(text)
    ans["confidence"] = float(c.group(1)) if c else 0.5
    for key, rx in LINE_RE.items():
        mm = rx.search(text)
        ans[key.lower()] = mm.group(1) if mm else "unverifiable — no line in the judge's answer"
    return ans


# --------------------------------------------------------------- verdict --

def clamp(c: float) -> float:
    return max(0.5, min(1.0, c))


def to_ab(position_verdict: str, first_is: str) -> str:
    """Map FIRST/SECOND/TIE onto A/B/TIE given which candidate sat first."""
    if position_verdict == "TIE":
        return "TIE"
    second_is = "B" if first_is == "A" else "A"
    return first_is if position_verdict == "FIRST" else second_is


def decide(v_ab: str, v_ba: str, c_ab: float, c_ba: float) -> tuple[str, float]:
    """Pure: two position-swapped verdicts → FINAL and its confidence."""
    if v_ab != v_ba:
        return "TIE", 0.5
    return v_ab, clamp(min(c_ab, c_ba))


def fmt_conf(c: float) -> str:
    return str(round(c, 2))


def render_block(criterion: str, run_ab: dict, run_ba: dict, v_ab: str, v_ba: str, final: str, conf: float) -> str:
    just = run_ab["justification"]
    if v_ab != v_ba:
        just = f"{just} | swapped run disagreed ({v_ba}), so position bias decides: {run_ba['justification']}"
    return (
        f"CRITERION: {criterion}\n"
        f"A: {run_ab['first']}\n"
        f"B: {run_ab['second']}\n"
        f"JUSTIFICATION: {just}\n"
        f"VERDICT (A-vs-B): {v_ab}   VERDICT (B-vs-A): {v_ba}   FINAL: {final} confidence {fmt_conf(conf)}"
    )


# ------------------------------------------------------------------- io --

def read_candidate(label: str, path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise JudgeError(f"--{label} {path}: not a file")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise JudgeError(f"--{label} {path}: {e}")
    if not text.strip():
        raise JudgeError(f"--{label} {path}: empty candidate")
    return text


def save_raw(root: Path, record: dict) -> Path:
    """state/judge/<ts>-<pid>.json, created exclusively so two runs never share a file."""
    d = root / "state" / "judge"
    d.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    base = f"{ts}-{os.getpid()}"
    for n in range(100):
        p = d / (f"{base}.json" if n == 0 else f"{base}-{n}.json")
        try:
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=1)
        return p
    return d / f"{base}.json"


# ----------------------------------------------------------------- main --

def main() -> int:
    common = argparse.ArgumentParser(add_help=False)  # --root anywhere, as memory.py does
    common.add_argument("--root", default=None, help="project root (state/ lives here)")
    ap = argparse.ArgumentParser(parents=[common], description=__doc__.splitlines()[0])
    ap.add_argument("--criterion", action="append", default=[], help="one EARS line; repeatable")
    ap.add_argument("--a", help="candidate A file")
    ap.add_argument("--b", help="candidate B file")
    ap.add_argument("--judge", default=None, help="name of the judging model/agent")
    ap.add_argument("--generator", default=None, help="name of whatever produced the candidates")
    ap.add_argument("--model", default=None, help="passed to claude --model")
    ap.add_argument("--print-rules", action="store_true", help="print the rules read from judge.md and exit")
    args = ap.parse_args()

    if args.print_rules:
        try:
            print(read_rules())
        except JudgeError as e:
            print(f"judge: {e}", file=sys.stderr)
            return 2
        return 0

    if args.judge and args.generator and args.judge.strip().casefold() == args.generator.strip().casefold():
        print(f"judge: refused — judge and generator are both {args.judge!r}; the judge is never the generator", file=sys.stderr)
        return 3
    if not args.criterion or not args.a or not args.b:
        print("usage: judge.py --criterion '<EARS line>' [--criterion ...] --a FILE --b FILE", file=sys.stderr)
        return 2

    root = ticks.project_root(args.root)
    record: dict = {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "judge": args.judge, "generator": args.generator, "model": args.model,
        "a": args.a, "b": args.b, "calls": [], "blocks": [], "error": None,
    }
    blocks: list[str] = []
    finals: list[tuple[str, float]] = []
    try:
        rules = read_rules()
        cand_a = read_candidate("a", args.a)
        cand_b = read_candidate("b", args.b)
        for criterion in args.criterion:
            runs = {}
            for order, (first_is, first, second) in (("A-vs-B", ("A", cand_a, cand_b)), ("B-vs-A", ("B", cand_b, cand_a))):
                prompt = build_prompt(rules, criterion, first, second)
                raw = call_claude(prompt, args.model)
                call = {"criterion": criterion, "order": order, "first_is": first_is, "prompt": prompt}
                call.update(raw)
                record["calls"].append(call)
                ans = parse_answer(result_text(raw))
                ans["verdict_ab"] = to_ab(ans["verdict"], first_is)
                call["parsed"] = ans
                runs[order] = ans
            ab, ba = runs["A-vs-B"], runs["B-vs-A"]
            # In the swapped run FIRST is B, so its FIRST/SECOND lines describe B then A.
            ba_view = dict(ba, first=ba["second"], second=ba["first"])
            final, conf = decide(ab["verdict_ab"], ba["verdict_ab"], ab["confidence"], ba["confidence"])
            finals.append((final, conf))
            block = render_block(criterion, ab, ba_view, ab["verdict_ab"], ba["verdict_ab"], final, conf)
            blocks.append(block)
            record["blocks"].append({"criterion": criterion, "final": final, "confidence": conf,
                                     "verdict_ab": ab["verdict_ab"], "verdict_ba": ba["verdict_ab"]})
    except JudgeError as e:
        record["error"] = str(e)
        p = save_raw(root, record)
        print(f"judge: {e} — raw responses in {p}", file=sys.stderr)
        return 2

    p = save_raw(root, record)
    print("\n\n".join(blocks))
    ticks.append(
        root, "judge",
        final=",".join(f for f, _ in finals),
        confidence=fmt_conf(min(c for _, c in finals)),
        criteria=len(args.criterion), calls=len(record["calls"]),
        judge=args.judge, generator=args.generator, raw=str(p.relative_to(root)) if str(p).startswith(str(root)) else str(p),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
