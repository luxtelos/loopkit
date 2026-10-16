#!/usr/bin/env python3
"""stop_review.py — the Stop review, decided by code, not by a model's judgement.

Replaces the agent-type Stop hook. That hook asked a model to run git, find the
changed code and run acceptance-review on it. On 2026-10-15 it blocked a
session that was correctly waiting on background agents more than 30 times in
a row, until Claude Code overrode it ("A hook blocked the turn from ending 9
consecutive times ... check stop_hook_active in the input and return success
while it's true"). It also graded untracked files that predated the turn, and
it could never pass when its own Bash tool was denied.

Why a command hook and not a better prompt. The Claude Code hooks reference
(code.claude.com/docs/en/hooks) says three things that decide it:
  * Stop input carries `stop_hook_active`, `last_assistant_message` and
    `background_tasks` — the last one exists so a hook can tell "session is
    done" from "session is paused waiting for background work".
  * "All matching hooks run in parallel." A command hook placed in front of an
    agent hook cannot stop the agent hook from blocking; any block wins.
  * Agent hooks get the input through `$ARGUMENTS`, so a model CAN see
    `stop_hook_active` — but obeying it is then a judgement, and agent hooks
    are marked experimental ("For production workflows, prefer command hooks").
So the decision moved here, where it is a pure function of the payload plus
facts read from git. The MODEL JUDGE is still on by default (owner decision,
2026-10-16) — but it is run BY this hook, headless, so this hook alone decides
whether its verdict can block. That is the only way "blocks at most once" can
be true while hooks run in parallel. Spec draft: state/2026-10-15-spec-draft-stop-review-loop.md.
Pinned by tests/pins/stop-review-loop.sh, proven red by
tests/pins/stop-review-prove-red.sh.

THE LADDER (first match wins)
  1. `stop_hook_active` is true → ALLOW, always. This stop follows a block
     already given in this chain. If code changed, the judge may run again,
     but its verdict is only RECORDED (ticks ledger, and the output), never
     enforced.
  2. Waiting → ALLOW. Both must hold: `background_tasks` lists a task whose
     status is not terminal, AND `last_assistant_message` contains the literal
     marker LOOPKIT:WAITING. The harness's task list is the fact; the marker is
     the session saying it is waiting on it (a perpetual log monitor alone must
     not be a free pass). The session is re-invoked when the work finishes, and
     judged again then. A Claude Code that does not send `background_tasks`
     gets no waiting pass.
  3. No code file changed this turn → ALLOW, silently.
  4. No turn record (scope fell back to the whole working tree) and a valid
     `.loopkit/turn-evidence.json` → ALLOW. The evidence is the main session's
     own record that this turn changed no code, with the file list it ran.
  5. Otherwise → the JUDGE runs. Default: `claude -p` with Read, Grep and Glob
     only (`--disallowedTools Bash`), hooks off for that run
     (`--settings '{"disableAllHooks": true}'`) and LOOPKIT_STOP_REVIEW_NESTED=1
     so neither Stop hook runs inside it. It is handed the hook's own file list
     and the evidence file — it never needs git. Its last line is
     `VERDICT: PASS` (allow) or `VERDICT: REJECT <reason>` (block once).
     LOOP_STOP_REVIEW_CMD swaps the judge (exit 0 = PASS);
     LOOP_STOP_REVIEW_JUDGE=off turns it off; LOOPKIT_JUDGE_CLAUDE names the
     CLI. No verdict (CLI missing, timeout, no VERDICT line, judge off) blocks
     once with the turn's files and a request to dispatch loopkit:reviewer.
     Rule 1 guarantees the next stop is let through.

THE TURN'S SCOPE
  `stop_review.py turn-start` runs on UserPromptSubmit and records, in
  $TMPDIR/loopkit-turn-<key>.json (key = session id + repo root), the HEAD and
  a content hash of every file already dirty. At Stop, "changed this turn" is:
  files committed since that HEAD, plus dirty files that were clean then or
  whose content changed since. Untracked nested repositories and worktrees are
  never counted, and `.claude/` and `.loopkit/` never count as code. With no
  record (plugin installed mid-session, or the record could not be written) the
  scope is the whole working tree, and the output says so — that is the case
  the evidence file exists for. A turn re-invoked by a background-task
  notification keeps the record from the last user prompt, which is the
  conservative direction: the scope can only be wider, never narrower.

THE EVIDENCE FILE  (.loopkit/turn-evidence.json, gitignored by init)
  {"version": 1, "kind": "no-code-change", "head": "<git rev-parse HEAD>",
   "written_at": <unix seconds>, "command": "<what was run>",
   "changed_files": ["state/x.md", ...]}
  Accepted only with a matching HEAD, a `written_at` no older than
  LOOPKIT_EVIDENCE_MAX_AGE seconds (default 900) and not in the future, a
  non-empty command, and no code file in `changed_files`. Write it with
  `python3 <plugin>/hooks/stop_review.py write-evidence [--changed-file F ...]`.
  It is consulted only when there is no turn record: when git can scope the
  turn, git's measurement wins over any claim.

Output: block → {"decision": "block", "reason": ...}; a non-silent allow →
{"systemMessage": ...}; exit 0 either way. Fail-open: unreadable input or an
internal error allows the stop and says why.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WAIT_MARKER = "LOOPKIT:WAITING"
EVIDENCE_REL = ".loopkit/turn-evidence.json"
EVIDENCE_KIND = "no-code-change"
DEFAULT_MAX_AGE = 900
SNAPSHOT_CAP = 5000
# Same default as hooks/stop_gate.sh, so the two Stop hooks agree on what code is.
DEFAULT_GLOBS = "*.js *.jsx *.ts *.tsx *.mjs *.cjs *.py *.go *.rs *.java *.rb *.kt *.swift"
NEVER_CODE_PREFIXES = (".claude/", ".loopkit/")
# A task in one of these states is not something the session can be waiting on.
TERMINAL = {
    "completed", "complete", "done", "finished", "succeeded", "success",
    "failed", "failure", "error", "errored", "killed", "stopped",
    "cancelled", "canceled", "timeout", "timed_out", "aborted",
}
NESTED = "<nested>"
DELETED = "<deleted>"


# ---------------------------------------------------------------- git facts --
def git(root, *args, stdin=None):
    try:
        p = subprocess.run(["git", "-C", str(root), *args], input=stdin,
                           capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout
    except Exception:
        return 1, ""


def resolve_root(payload):
    for cand in (payload.get("cwd"), os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
        if not cand or not isinstance(cand, str):
            continue
        rc, out = git(cand, "rev-parse", "--show-toplevel")
        if rc == 0 and out.strip():
            return Path(out.strip()).resolve()
    return None


def current_head(root):
    rc, out = git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    return out.strip() if rc == 0 and out.strip() else None


def dirty_entries(root):
    """{path: XY} for every dirty path, or None when git cannot answer.

    -z output: "XY path\\0", and for a rename/copy "XY new\\0old\\0".
    An untracked DIRECTORY entry (trailing slash) is a nested repo or worktree:
    with --untracked-files=all git lists every untracked file, and only stops
    at a directory it will not descend into.
    """
    rc, out = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if rc != 0:
        return None
    entries, toks, i = {}, out.split("\0"), 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if len(t) < 4:
            continue
        xy, path = t[:2], t[3:]
        if xy[0] in "RC":
            i += 1  # skip the origin path of a rename/copy
        entries[path] = xy
    return entries


def hash_dirty(root, entries):
    """{path: content-hash | <nested> | <deleted>} or None on failure."""
    out, to_hash = {}, []
    for path, xy in entries.items():
        if path.endswith("/"):
            out[path] = NESTED
        elif "D" in xy or not (root / path).exists():
            out[path] = DELETED
        elif "\n" in path:
            out[path] = "<unhashable>"
        else:
            to_hash.append(path)
    if to_hash:
        rc, res = git(root, "hash-object", "--stdin-paths", stdin="\n".join(to_hash) + "\n")
        hashes = res.split()
        if rc != 0 or len(hashes) != len(to_hash):
            return None
        out.update(zip(to_hash, hashes))
    return out


def code_globs(root):
    if "LOOP_CODE_GLOBS" in os.environ:  # an explicit empty means "nothing is code"
        return os.environ["LOOP_CODE_GLOBS"].split()
    cfg = root / ".loopkit" / "config.env" if root else None
    try:
        for line in cfg.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if line.startswith("LOOP_CODE_GLOBS="):
                return line.split("=", 1)[1].strip().strip("'\"").split()
    except Exception:
        pass
    return DEFAULT_GLOBS.split()


def is_code(path, globs):
    if not isinstance(path, str) or path.endswith("/") or path.startswith(NEVER_CODE_PREFIXES):
        return False
    # fnmatchcase's `*` crosses `/`, as git's default pathspec does in stop_gate.sh.
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


# --------------------------------------------------------------- turn record --
def session_key(payload, root):
    sid = str(payload.get("session_id") or "")
    if not sid:
        return None
    return hashlib.sha256(f"{sid}\0{root}".encode()).hexdigest()[:16]


def record_path(key):
    return Path(tempfile.gettempdir()) / f"loopkit-turn-{key}.json"


def load_record(path, root):
    try:
        rec = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(rec, dict) or rec.get("version") != 1 or rec.get("root") != str(root):
        return None
    if not isinstance(rec.get("dirty"), dict):
        return None
    return rec


def turn_changes(root, rec):
    """Paths changed since the turn record, or None when git cannot say."""
    changed = set()
    head0, head = rec.get("head"), current_head(root)
    if head and head0 != head:
        base = head0 or "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # the empty tree
        rc, out = git(root, "diff", "--name-only", "--diff-filter=ACMRTUXB", base, "HEAD")
        if rc != 0:
            return None
        changed.update(p for p in out.splitlines() if p)
    entries = dirty_entries(root)
    if entries is None:
        return None
    now = hash_dirty(root, entries)
    if now is None:
        return None
    before = rec["dirty"]
    for path, h in now.items():
        if h in (NESTED, DELETED):
            continue
        if before.get(path) != h:  # MUT-SCOPE: clean at turn start, or content changed since
            changed.add(path)
    return sorted(changed)


def tree_changes(root):
    """The whole working tree's changes — the scope when there is no record."""
    entries = dirty_entries(root)
    if entries is None:
        return None
    return sorted(p for p, xy in entries.items() if not p.endswith("/") and "D" not in xy)


# ----------------------------------------------------------------- evidence --
def check_evidence(root, head, now, globs, max_age):
    """("absent" | "valid" | "invalid", why)."""
    p = root / EVIDENCE_REL
    if not p.is_file():
        return "absent", f"no evidence file at {EVIDENCE_REL}"
    try:
        ev = json.loads(p.read_text())
    except Exception:
        return "invalid", f"{EVIDENCE_REL} is not valid JSON"
    if not isinstance(ev, dict) or ev.get("version") != 1 or ev.get("kind") != EVIDENCE_KIND:
        return "invalid", f"{EVIDENCE_REL} is not version 1 / kind {EVIDENCE_KIND}"
    if not head:
        return "invalid", "HEAD cannot be read, so the evidence cannot be tied to this turn"
    rec_head = ev.get("head")
    if rec_head != head:  # MUT-EV-HEAD
        return "invalid", (f"stale evidence: recorded HEAD {str(rec_head)[:12]} is not the "
                           f"current HEAD {head[:12]}")
    w = ev.get("written_at")
    if isinstance(w, bool) or not isinstance(w, (int, float)):
        return "invalid", "evidence has no numeric written_at"
    if w > now + 5 or now - w > max_age:  # MUT-EV-TIME
        return "invalid", (f"stale evidence: written {int(now - w)}s ago; it must be newer "
                           f"than {max_age}s and not in the future")
    cmd = ev.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return "invalid", "evidence does not say what command produced its file list"
    files = ev.get("changed_files")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        return "invalid", "evidence changed_files is not a list of paths"
    code = [f for f in files if is_code(f, globs)]
    if code:
        return "invalid", "evidence lists code file(s): " + ", ".join(code[:10])
    return "valid", (f"main-session evidence accepted: {len(files)} changed file(s), none of "
                     f"them code, from `{cmd.strip()}` at HEAD {head[:12]}")


# ----------------------------------------------------------------- decision --
def in_flight(payload):
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list):
        return []
    live = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        status = str(t.get("status") or "").strip().lower()
        if status in TERMINAL:
            continue
        live.append(t)
    return live


def _task_label(t):
    bits = [str(t.get("id") or "?")]
    kind = t.get("agent_type") or t.get("type")
    if kind:
        bits.append(str(kind))
    desc = str(t.get("description") or "").strip()
    if desc:
        bits.append(desc[:60])
    return " ".join(bits)


def decide(payload, facts):
    """Pure. -> (verdict, message); verdict is SECOND, ALLOW, ALLOW_QUIET or REVIEW.

    facts: scope ("turn" | "tree"), code_files (list), evidence ((status, why)).
    SECOND means stop_hook_active: the caller may run the judge to RECORD its
    verdict, but must allow the stop whatever it says.
    """
    if payload.get("stop_hook_active") is True:  # MUT-ACTIVE
        return "SECOND", ("LOOPKIT stop review: stop_hook_active is true — this stop follows a "
                          "block already given in this chain, so it does not block again. "
                          "Anything still unfinished is the session's to report.")
    live = in_flight(payload)
    said_waiting = WAIT_MARKER in str(payload.get("last_assistant_message") or "")
    if live and said_waiting:  # MUT-WAIT
        names = "; ".join(_task_label(t) for t in live[:5])
        more = f" (+{len(live) - 5} more)" if len(live) > 5 else ""
        return "ALLOW", (f"LOOPKIT stop review: waiting on {len(live)} background task(s): "
                         f"{names}{more}. Not judged now; the session is judged again when it "
                         "is re-invoked with their results.")
    code = facts["code_files"]
    scope = facts["scope"]
    if not code:
        return "ALLOW_QUIET", f"no code file changed ({scope} scope)"
    ev_status, ev_why = facts["evidence"]
    if scope == "tree" and ev_status == "valid":  # MUT-EV-SCOPE
        return "ALLOW", f"LOOPKIT stop review: {ev_why}."
    return "REVIEW", review_reason(payload, facts, live, said_waiting)


def scope_notes(facts):
    """What the scope was and what the evidence file said — on every block."""
    ev_status, ev_why = facts["evidence"]
    if facts["scope"] == "turn":
        if ev_status == "valid":
            return [f"{EVIDENCE_REL} says no code changed, but git shows the files above "
                    "changed since the turn began; the measurement wins."]
        return []
    return ["No turn record for this session, so the scope is the WHOLE working tree; files "
            "from other work may be listed.",
            f"Evidence: {ev_why}. If none of these files are this turn's, write {EVIDENCE_REL} "
            "(stop_review.py write-evidence --changed-file <each file you changed>) and stop again."]


def review_reason(payload, facts, live, said_waiting):
    code, scope = facts["code_files"], facts["scope"]
    ev_status, ev_why = facts["evidence"]
    shown = ", ".join(code[:20]) + (f" (+{len(code) - 20} more)" if len(code) > 20 else "")
    lines = ["LOOPKIT stop review: this turn changed code and nothing has reviewed it yet.",
             f"Code files ({scope} scope): {shown}"]
    lines += scope_notes(facts)
    lines.append("Next: dispatch the loopkit:reviewer agent (its instructions differ from yours) "
                 "to run loopkit:acceptance-review against specs/ for THESE files only, and act "
                 "on its verdict.")
    if live and not said_waiting:
        lines.append(f"{len(live)} background task(s) are in flight. If you are waiting on them, "
                     f"put {WAIT_MARKER} in your final message and stop; you are judged again "
                     "when their results arrive.")
    elif said_waiting and not live:
        lines.append(f"You said {WAIT_MARKER}, but the payload lists no background task in "
                     "flight, so there is nothing to wait on.")
    lines.append("This hook blocks at most once in a row: on the next stop a verdict is "
                 "recorded, not enforced (stop_hook_active).")
    return "\n".join(lines)


def gather_facts(payload, root, now):
    globs = code_globs(root)
    key = session_key(payload, root)
    rec = load_record(record_path(key), root) if key else None
    files, scope = None, "tree"
    if rec is not None:
        files = turn_changes(root, rec)
        if files is not None:
            scope = "turn"
    if files is None:
        files = tree_changes(root) or []
    code = [f for f in files if is_code(f, globs)]
    try:
        max_age = int(os.environ.get("LOOPKIT_EVIDENCE_MAX_AGE", DEFAULT_MAX_AGE))
    except ValueError:
        max_age = DEFAULT_MAX_AGE
    evidence = check_evidence(root, current_head(root), now, globs, max_age)
    return {"scope": scope, "code_files": code, "evidence": evidence}


def run_review_cmd(cmd, root, payload, code):
    env = dict(os.environ, LOOPKIT_TURN_CODE_FILES="\n".join(code))
    try:
        timeout = int(os.environ.get("LOOP_STOP_REVIEW_TIMEOUT", "170"))
    except ValueError:
        timeout = 170
    try:
        p = subprocess.run(["bash", "-c", cmd], cwd=str(root), input=json.dumps(payload),
                           capture_output=True, text=True, timeout=timeout, env=env)
        tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-20:])
        return p.returncode, tail
    except subprocess.TimeoutExpired:
        return 124, f"LOOP_STOP_REVIEW_CMD timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 1, f"LOOP_STOP_REVIEW_CMD could not run: {e}"


JUDGE_PROMPT = """You are the LoopKit stop-review judge, a separate model from the session
that wrote this code. Your verdict decides whether the session may end its turn.

You have Read, Grep and Glob ONLY. There is no Bash: do not try to run commands,
tests or git. The hook has already measured the changes with git; trust the
list below instead of re-deriving it.

Repository root: {root}
Scope: {scope}
Code files changed this turn ({n}):
{files}

Evidence file {evidence_rel}: {evidence_status}
{evidence_body}

Run the loopkit:acceptance-review discipline against specs/ for THESE files only:
read each file and the spec(s) whose EARS acceptance criteria it serves, and
form a per-criterion verdict from the code and the criteria BEFORE reading the
session's final message below; a judgement formed after the claim is not
oversight. Flag only gaps that affect correctness. Files not in the list are
not this turn's scope: never demand specs or tests for them. If no spec covers
these files, judge them against the session's stated goal and say so.

Session's final message (read LAST):
{last_message}

End your answer with exactly one line, the last line:
VERDICT: PASS
or
VERDICT: REJECT <one-line reason>
"""
VERDICT_LINE = re.compile(r"^\s*VERDICT:\s*(PASS|REJECT)\b[:\s-]*(.*)$", re.M)


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def run_judge(root, payload, facts):
    """-> (PASS | REJECT | ERROR | OFF, text). Never decides whether to block."""
    code = facts["code_files"]
    cmd = os.environ.get("LOOP_STOP_REVIEW_CMD", "").strip()
    if cmd:
        rc, tail = run_review_cmd(cmd, root, payload, code)
        return ("PASS" if rc == 0 else "REJECT"), f"LOOP_STOP_REVIEW_CMD exit {rc}. {tail}".strip()
    if os.environ.get("LOOP_STOP_REVIEW_JUDGE", "on").strip().lower() in ("off", "0", "false", "no"):
        return "OFF", "the model judge is switched off (LOOP_STOP_REVIEW_JUDGE=off)"
    exe = os.environ.get("LOOPKIT_JUDGE_CLAUDE", "claude")
    exe_path = exe if os.sep in exe else shutil.which(exe)
    if not exe_path or not os.access(exe_path, os.X_OK):
        return "ERROR", f"the judge could not run: `{exe}` is not an executable on PATH"
    ev_path = root / EVIDENCE_REL
    try:
        ev_body = ev_path.read_text()[:4000] if ev_path.is_file() else "(no file)"
    except Exception:
        ev_body = "(unreadable)"
    prompt = JUDGE_PROMPT.format(
        root=root, scope=facts["scope"], n=len(code),
        files="\n".join(f"- {f}" for f in code[:200]),
        evidence_rel=EVIDENCE_REL, evidence_status=facts["evidence"][1], evidence_body=ev_body,
        last_message=str(payload.get("last_assistant_message") or "(none)")[:4000])
    argv = [exe_path, "-p",
            "--allowedTools", "Read", "Grep", "Glob",
            "--disallowedTools", "Bash",  # MUT-JUDGE-BASH
            "--settings", '{"disableAllHooks": true}',
            "--no-session-persistence", "--output-format", "text"]
    env = dict(os.environ, LOOPKIT_STOP_REVIEW_NESTED="1",
               LOOPKIT_TURN_CODE_FILES="\n".join(code))
    timeout = _int_env("LOOP_STOP_REVIEW_TIMEOUT", 170)
    try:
        p = subprocess.run(argv, cwd=str(root), input=prompt, capture_output=True,
                           text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return "ERROR", f"the judge timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return "ERROR", f"the judge could not run: {e}"
    found = VERDICT_LINE.findall(p.stdout or "")
    if p.returncode != 0 or not found:
        tail = " ".join(((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-3:])[:400]
        return "ERROR", f"the judge gave no verdict (exit {p.returncode}): {tail}"
    word, why = found[-1]
    return word, (why.strip() or word)


def record(root, firing, verdict, text):
    """The ticks ledger is memory, not a control: a failure here changes nothing."""
    if not (root / "state").is_dir():
        return
    try:
        subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "ticks.py"), "append",
                        "--root", str(root), "--event", "stop_review",
                        "--k", f"firing={firing}", "--k", f"result={verdict}",
                        "--k", f"note={text[:200]}"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def emit(verdict, message):
    if verdict == "BLOCK":
        print(json.dumps({"decision": "block", "reason": message}))
    elif verdict == "ALLOW":
        print(json.dumps({"systemMessage": message}))
    # ALLOW_QUIET prints nothing: a conversational turn needs no banner.


def nested():
    """True inside the judge's own headless session: both Stop hooks stand down there."""
    return os.environ.get("LOOPKIT_STOP_REVIEW_NESTED") == "1"


def stop_main(raw):
    if nested():  # MUT-NESTED
        return 0
    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except Exception:
        emit("ALLOW", "LOOPKIT stop review: the Stop input was not readable JSON; allowed "
                      "(fail-open), nothing was judged.")
        return 0
    root = resolve_root(payload)
    if root is None:
        emit("ALLOW_QUIET", "not a git checkout")
        return 0
    facts = gather_facts(payload, root, time.time())
    verdict, message = decide(payload, facts)
    if verdict == "SECOND":
        # The judge may run again, but only to put its verdict on record. Whatever
        # it says, this firing allows: one block per chain, enforced here, by code.
        live = in_flight(payload)
        waiting = live and WAIT_MARKER in str(payload.get("last_assistant_message") or "")
        evidenced = facts["scope"] == "tree" and facts["evidence"][0] == "valid"
        if facts["code_files"] and not waiting and not evidenced:
            jv, jtext = run_judge(root, payload, facts)
            record(root, "second", jv, jtext)
            message += (f"\nThe judge's verdict on this second firing is recorded, not enforced: "
                        f"{jv} — {jtext}")
        verdict = "ALLOW"  # MUT-SECOND: never BLOCK on a second firing
    elif verdict == "REVIEW":
        jv, jtext = run_judge(root, payload, facts)
        record(root, "first", jv, jtext)
        if jv == "PASS":  # MUT-JUDGE-PASS
            verdict, message = "ALLOW", f"LOOPKIT stop review: the judge PASSED this turn's code. {jtext}".strip()
        elif jv == "REJECT":
            verdict, message = "BLOCK", reject_reason(facts, jtext, payload)
        else:  # ERROR or OFF: no verdict, so ask for the reviewer agent instead — once.
            verdict, message = "BLOCK", f"{message}\nNo judge verdict: {jtext}."
    emit(verdict, message)
    return 0


def reject_reason(facts, jtext, payload):
    code = facts["code_files"]
    shown = ", ".join(code[:20]) + (f" (+{len(code) - 20} more)" if len(code) > 20 else "")
    lines = [f"LOOPKIT stop review: the judge REJECTED this turn's code ({facts['scope']} scope: {shown}).",
             f"Judge: {jtext}",
             "Fix what it names, or say plainly why it is wrong."]
    lines += scope_notes(facts)
    live = in_flight(payload)
    if live and WAIT_MARKER not in str(payload.get("last_assistant_message") or ""):
        lines.append(f"{len(live)} background task(s) are in flight. If you are waiting on them, "
                     f"put {WAIT_MARKER} in your final message and stop.")
    lines.append("This hook blocks at most once in a row: on the next stop the judge's verdict "
                 "is recorded, not enforced (stop_hook_active).")
    return "\n".join(lines)


# --------------------------------------------------------------- subcommands --
def turn_start_main(raw):
    """UserPromptSubmit: record where this turn began. Silent, never blocks."""
    if nested():
        return 0
    try:
        payload = json.loads(raw) if raw.strip() else {}
        root = resolve_root(payload) if isinstance(payload, dict) else None
        key = session_key(payload, root) if root else None
        if not key:
            return 0
        entries = dirty_entries(root)
        if entries is None or len(entries) > SNAPSHOT_CAP:
            # Too big to snapshot: leave no record, so Stop says the scope is the whole tree.
            record_path(key).unlink(missing_ok=True)
            return 0
        dirty = hash_dirty(root, entries)
        if dirty is None:
            record_path(key).unlink(missing_ok=True)
            return 0
        rec = {"version": 1, "root": str(root), "head": current_head(root),
               "started_at": time.time(), "dirty": dirty}
        path = record_path(key)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, path)
    except Exception:
        pass
    return 0


def write_evidence_main(argv):
    ap = argparse.ArgumentParser(prog="stop_review.py write-evidence",
                                 description="Record that this turn changed no code.")
    ap.add_argument("--root", help="repo root (default: git toplevel of the cwd)")
    ap.add_argument("--changed-file", action="append", default=None,
                    help="a file this turn changed; repeat. Omit to use git status.")
    ap.add_argument("--command", default=None, help="the command whose output is the list")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else resolve_root({})
    if root is None:
        print("write-evidence: not inside a git checkout", file=sys.stderr)
        return 2
    head = current_head(root)
    if not head:
        print("write-evidence: HEAD cannot be read; evidence must name a commit", file=sys.stderr)
        return 2
    if a.changed_file is not None:
        files = sorted(set(a.changed_file))
        command = a.command or "declared by the session (--changed-file)"
    else:
        entries = dirty_entries(root)
        if entries is None:
            print("write-evidence: git status failed", file=sys.stderr)
            return 2
        files = sorted(p for p in entries if not p.endswith("/"))
        command = a.command or "git status --porcelain=v1 -z --untracked-files=all"
    globs = code_globs(root)
    code = [f for f in files if is_code(f, globs)]
    if code:
        print("write-evidence: REFUSED — these are code files: " + ", ".join(code[:20]) +
              ". If they are not this turn's, name your own changes with --changed-file.",
              file=sys.stderr)
        return 1
    ev = {"version": 1, "kind": EVIDENCE_KIND, "head": head, "written_at": time.time(),
          "command": command, "changed_files": files}
    path = root / EVIDENCE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(ev, indent=2) + "\n")
    os.replace(tmp, path)
    print(f"write-evidence: wrote {path} ({len(files)} file(s), HEAD {head[:12]})")
    return 0


def read_stdin():
    try:
        return "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    except Exception:
        return ""


def main(argv):
    if argv and argv[0] == "write-evidence":
        return write_evidence_main(argv[1:])
    if argv and argv[0] == "turn-start":
        return turn_start_main(read_stdin())
    raw = read_stdin()
    try:
        return stop_main(raw)
    except Exception as e:  # noqa: BLE001 — fail-open, but say so
        emit("ALLOW", f"LOOPKIT stop review: internal error ({type(e).__name__}: {e}); "
                      "allowed (fail-open), nothing was judged.")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
