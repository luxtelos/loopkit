# Judge discipline — when a verdict compares two things

Read this when a review must choose between two candidate fixes, two specs,
or two outputs, or when an LLM is asked to score anything.

Run it, do not hand-roll it: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" --criterion "<EARS line>" --a FILE --b FILE --judge <name> --generator <name>`
reads the rules below, asks `claude -p` twice with positions swapped, applies
rule 2 deterministically and prints the output shape (exit 3 if judge = generator).

## Rules

1. **Justification before score.** Write why, then the verdict. Scoring first
   and rationalising after is how a judge agrees with itself.
2. **Pairwise, run twice, positions swapped.** Ask A-vs-B, then B-vs-A. If the
   two answers disagree, the verdict is TIE with confidence 0.5 — position bias
   just showed itself. If they agree, report the agreement.
3. **Judge ≠ generator.** The agent (or model) that produced a candidate never
   grades it. Same reason the loop's implementer never approves: self-
   enhancement bias is not a character flaw, it is a measured effect.
4. **Say what not to prefer.** The judge prompt states: do not prefer the
   longer answer; do not prefer the first position; do not prefer confident
   wording. Unstated, each of these wins.
5. **One criterion, one measurable aspect.** A 1–10 scale with no level
   descriptions is noise; use the EARS line itself as the criterion and grade
   held / not held / unverifiable, with the evidence attached.
6. **Grade the end state, not the path.** Two fixes that reach the same
   verified state are equal even if one took a route the judge would not have.

## Output shape

```
CRITERION: <EARS line>
A: <held|not held|unverifiable> — <evidence: command + output line>
B: <held|not held|unverifiable> — <evidence>
JUSTIFICATION: <why one is better, or why they tie>
VERDICT (A-vs-B): <A|B|TIE>   VERDICT (B-vs-A): <A|B|TIE>   FINAL: <A|B|TIE> confidence <0.5–1.0>
```
