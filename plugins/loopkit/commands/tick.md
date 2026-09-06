---
description: Advance the loop by exactly one stage — lookup, poll, act, record, stop.
---

Invoke the skill now, before any other tool call:

    Skill(skill="loopkit:loop-tick")

Do not summarise the skill, do not choose a stage yourself, do not do more than
one stage. When the tick ends with `NEXT: CONTINUE`, run the next tick immediately
— never schedule a wakeup while work is actionable; a wakeup is only for
`NEXT: WAIT`. $ARGUMENTS
