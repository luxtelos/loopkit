#!/usr/bin/env python3
"""loop_doctrine.py — make every /loop tick inherit the pipeline discipline.

WHY THIS EXISTS

`/loop` is a generic timer built into Claude Code. It has one channel into a
project: the prompt text, re-injected verbatim each tick. So the tick
discipline — "look up the stage, advance it, record it, stop" — used to have to
be typed out in full, every time, by a human. That is not agentic engineering;
that is a human being the orchestrator.

Three of the four layers already self-apply: hooks fire on tool matchers
regardless of the prompt, CLAUDE.md auto-loads every session, and skills
auto-trigger on their description. But a CONSTRAINT is not a capability — no
skill description matches "do less than you otherwise would" — so the one part
that prevents drift was the one part left to human memory.

This hook closes it. On any prompt that looks like a loop tick, it prints the
doctrine; a UserPromptSubmit hook's stdout is added to the model's context. The
user types `/loop work the backlog` and gets the full discipline anyway.

It also carries the oversight paper's one-liner (Mitchell, Ghosh, Passi 2026,
arXiv 2608.23642): once a session has passed LOOPKIT_APPROVAL_FATIGUE_N
permission prompts (default 30), a tick gets one extra line saying so, because
past that point an "allow" is a reflex and the escalation door is the honest
route. The count comes from count_approvals.py; nothing here decides anything.

WHAT IT DELIBERATELY DOES NOT DO

It never blocks a prompt — always exit 0. A doctrine injector that can stop the
user working is a far worse failure than one that occasionally stays quiet.

It stays silent on prompts that merely mention loops in passing ("fix the retry
loop"), because a hook that fires on everything is noise, and noise is how a
guard gets switched off.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
FATIGUE_N = int(os.environ.get("LOOPKIT_APPROVAL_FATIGUE_N", "30") or 30)

# Fires on: an explicit /loop invocation, a named loop-tick run, or the
# backlog-work phrasing that the loop-tick skill's own description advertises.
TICK_SIGNALS = (
    r"^\s*/loop\b",
    r"\bloop-tick\b",
    r"\bloopkit\s+backlog\b",
    r"\bwork\s+the\s+backlog\b",
    r"\badvance\s+the\s+pipeline\b",
    r"\bprocess\s+(?:the\s+)?triage\b",
    r"\brun\s+the\s+loop\b",
)

# Kept narrow on purpose: these read as loop ticks but are not. Checked BEFORE
# the signals, so they win. Note the anchors: `\b` does not match before `/`,
# because `/` is not a word character — a stop rule written `\b/loop` silently
# never fires.
NOT_A_TICK = (
    r"\b(?:for|while|event|render|retry|game|infinite)\s+loop\b",
    r"\bloop\s+(?:variable|counter|index|body|unrolling)\b",
    r"(?:^|\s)/loop\s+(?:stop|cancel|end)\b",
)


def doctrine() -> str:
    s = str(SCRIPTS)
    return f"""\
LOOP-KIT TICK DISCIPLINE (injected by the loopkit plugin's loop_doctrine.py hook)

>>> FIRST ACTION, BEFORE ANY OTHER TOOL CALL: invoke the skill.
>>>     Skill(skill="loopkit:loop-tick")      # to advance a stage
>>>     Skill(skill="loopkit:loop-scan")      # if this is a status question
>>>     Skill(skill="loopkit:loop-assess")    # when classifying a finding
>>>
>>> Actually CALL the Skill tool. Paraphrasing this block is not the same
>>> thing, and the difference is invisible in the transcript.

Three rules, and they are the whole of it:

1. Do NOT choose the stage yourself — it is a lookup:
       bash {s}/loop-next.sh
2. A POLL line means run {s}/loop-watch.sh FIRST; QUIET means ignore it and
   do the work stage. A PR in flight must never starve the backlog.
3. Record every transition, keyed on --source (NOT --finding):
       python3 {s}/triage_state.py update --state state/triage.md \\
         --source "<row source>" --status <new-status>
4. Never sleep unless something OUTSIDE the loop must move first. Read the
   NEXT: line loop-next.sh prints: CONTINUE means run the next tick now, no
   wakeup; WAIT (a PR review, CI, a human ruling) is the only case for a
   ScheduleWakeup; IDLE means run morning-triage now. Ask "what am I waiting
   on?" — if the answer is "nothing", a timer is a delay, not a discipline.

The skill you invoked carries everything else, and is the source of truth."""


def looks_like_tick(prompt: str) -> bool:
    if any(re.search(p, prompt, re.I) for p in NOT_A_TICK):
        return False
    return any(re.search(p, prompt, re.I) for p in TICK_SIGNALS)


def session_key(payload: object) -> str:
    candidates = []
    if isinstance(payload, dict):
        candidates += [payload.get("session_id"), payload.get("transcript_path")]
    candidates += [os.environ.get("CLAUDE_SESSION_ID"), str(os.getppid())]
    for c in candidates:
        if c:
            return hashlib.sha256(str(c).encode()).hexdigest()[:16]
    return "unknown"


def approvals(payload: object) -> int:
    """How many permission prompts count_approvals.py has seen this session."""
    try:
        p = Path(tempfile.gettempdir()) / f"loopkit-approvals-{session_key(payload)}" / "count"
        return int(p.read_text().strip() or 0) if p.exists() else 0
    except Exception:
        return 0


def fatigue_line(n: int) -> str:
    return (
        f"\nAPPROVAL FATIGUE: {n} permission prompts this session (threshold {FATIGUE_N}). "
        "Past this point an 'allow' is a reflex, not oversight (Mitchell, Ghosh & Passi 2026). "
        "Anything that needs a human now goes through inbox/needs-human.md with both options costed — not another click."
    )


def lane_of(prompt: str) -> str | None:
    """The lane a tick is scoped to: --scope <lane> in the prompt, LOOPKIT_LANE,
    or a lane name from .loopkit/scopes.json appearing as a word."""
    m = re.search(r"--scope[= ]+([\w-]+)", prompt)
    if m:
        return m.group(1).lower()
    env = os.environ.get("LOOPKIT_LANE")
    if env:
        return env.lower()
    try:
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
        lanes = json.loads((root / ".loopkit" / "scopes.json").read_text(encoding="utf-8"))
        for lane in lanes:
            if lane.startswith("_"):
                continue
            if re.search(rf"\b{re.escape(lane)}\b", prompt, re.I):
                return lane.lower()
    except Exception:
        pass
    return None


def traps_for(lane: str | None, limit: int = 5) -> str:
    """Traps recorded for this lane, from the knowledge bundle — never into
    CLAUDE.md, only into the tick that touches the lane."""
    if not lane:
        return ""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from loopkit_memory import load
        kn = load(os.environ.get("CLAUDE_PROJECT_DIR")).get("knowledge")
        if not hasattr(kn, "search"):
            return ""
        hits = kn.search("", limit=limit, types=("Trap",), lane=lane)
    except Exception:
        return ""
    if not hits:
        return ""
    lines = [f"\nTRAPS recorded for lane '{lane}' (read before acting; `memory.py knowledge get <path>` for the full concept):"]
    lines += [f"- {h['title']} ({h['path']})" for h in hits]
    return "\n".join(lines)


def read_payload() -> tuple[str, object]:
    raw = sys.stdin.read()
    if not raw.strip():
        return "", None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        # Never guess structure. If the envelope changes shape, scanning the raw
        # text still finds `/loop`, and a false positive here costs a few lines
        # of context while a false negative costs the whole discipline.
        return raw, None
    if isinstance(payload, dict):
        return str(payload.get("prompt") or payload.get("user_prompt") or raw), payload
    return raw, payload


def main() -> int:
    try:
        prompt, payload = read_payload()
        if prompt and looks_like_tick(prompt):
            out = doctrine()
            n = approvals(payload)
            if n >= FATIGUE_N:
                out += fatigue_line(n)
            out += traps_for(lane_of(prompt))
            print(out)
    except Exception:
        pass  # never block a prompt
    return 0


if __name__ == "__main__":
    sys.exit(main())
