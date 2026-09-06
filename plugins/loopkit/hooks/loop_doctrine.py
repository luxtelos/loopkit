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

WHAT IT DELIBERATELY DOES NOT DO

It never blocks a prompt — always exit 0. A doctrine injector that can stop the
user working is a far worse failure than one that occasionally stays quiet.

It stays silent on prompts that merely mention loops in passing ("fix the retry
loop"), because a hook that fires on everything is noise, and noise is how a
guard gets switched off.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

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

The skill you invoked carries everything else, and is the source of truth."""


def looks_like_tick(prompt: str) -> bool:
    if any(re.search(p, prompt, re.I) for p in NOT_A_TICK):
        return False
    return any(re.search(p, prompt, re.I) for p in TICK_SIGNALS)


def read_prompt() -> str:
    raw = sys.stdin.read()
    if not raw.strip():
        return ""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        # Never guess structure. If the envelope changes shape, scanning the raw
        # text still finds `/loop`, and a false positive here costs a few lines
        # of context while a false negative costs the whole discipline.
        return raw
    if isinstance(payload, dict):
        return str(payload.get("prompt") or payload.get("user_prompt") or raw)
    return raw


def main() -> int:
    try:
        prompt = read_prompt()
        if prompt and looks_like_tick(prompt):
            print(doctrine())
    except Exception:
        pass  # never block a prompt
    return 0


if __name__ == "__main__":
    sys.exit(main())
