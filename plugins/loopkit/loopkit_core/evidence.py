#!/usr/bin/env python3
"""evidence.py — the evidence gate's pure half. docs/adr/0008-evidence-gate.md.

A finding may move to `spec-draft`, `spec-ready` or `pr-open` only when its
assessment says, in two sections a parser can read, what it rests on:

  ## Sources       always at least two `read:` rows on DIFFERENT systems of
                   record, one of them LIVE (identity, data, runtime) unless
                   the section declares `Live state: none — <why>`.
  ## Does not fit  `- none — <what was checked>`, or one item per observation
                   that does not fit the conclusion, each with `explained:`.

Why a declared value and not a guess. The gate never tries to spot "a claim
about live state" in prose: that is a blocklist of phrasings and it leaks the
first time a claim is phrased a new way. Every assessment states its position
instead. A real `Live state: none — <why>` is written down where a reviewer
can disagree with it, and waives only the live requirement; a fabricated one —
a missing section, an empty table, a `none` with no reason — is refused.

Why the CLASS decides the system and not the cell. Each sources row names a
class; the class maps to a system of record in `<project>/.loopkit/sources.json`
(or the defaults below). Independence is counted over systems, so two rows of
one class, or two classes mapped to one system, are ONE source. A value the
application derived from a system — a response field, a URL parameter, a
cached flag — is class `derived`, which `counts: false`: it can be recorded,
and is worth zero. That was the 2026-10-15 incident's entire evidence.

What this cannot do: prove a `read:` happened, or that the right systems were
read. It makes an agent name two systems and say what it read; believing it is
the reviewer's job.

Stdlib only, no I/O beyond reading the files it is pointed at: this module is
in `loopkit_core`, and the package rule is that its decisions are reproducible
from its inputs alone.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

MIN_INDEPENDENT = 2
GATED_STATUSES = frozenset({"spec-draft", "spec-ready", "pr-open"})
SOURCES_CONFIG = (".loopkit", "sources.json")

# The default classes. `templates/loopkit/sources.json` is a copy of exactly
# this mapping (tests/pins/evidence-gate.py asserts equality), so a project
# with no config and a project that ran init behave identically.
#   system   the system of record; independence is counted over these, after
#            lower-casing and collapsing whitespace.
#   counts   false: recorded, worth zero (a value the app derived itself).
#   live     true: a system where live state is KEPT (identity, data, runtime).
#            Unless an assessment says `Live state: none — <why>`, one of its
#            two systems must be live. Code, memory, tracker and derived are not.
#   nouns    what source-snapshot.py matches in a finding, as a SUGGESTION.
#            The gate never reads nouns.
#   probes   argv lists source-snapshot.py runs to see whether a tool answers.
#            A class with none is reported `unknown`.
#   note     shown beside the class in the snapshot.
DEFAULT_CLASSES: dict[str, dict] = {
    "identity": {
        "system": "identity provider",
        "live": True,
        "note": "users, roles, tenants, sign-in; read the provider, not the app's copy",
        "nouns": ["user", "users", "role", "roles", "tenant", "tenants", "login", "sign-in",
                  "signin", "invite", "invites", "invitation", "member", "membership",
                  "permission", "permissions", "account", "session"],
        "probes": [],
    },
    "data": {
        "system": "database",
        "live": True,
        "note": "rows, flags, counts; read-only, never write while assessing",
        "nouns": ["row", "rows", "flag", "flags", "count", "counts", "record", "records",
                  "table", "column", "profile", "balance", "total", "totals"],
        "probes": [["psql", "--version"], ["sqlite3", "--version"]],
    },
    "runtime": {
        "system": "logs and analytics",
        "live": True,
        "note": "behaviour, errors, timing; read the logs at the time it happened",
        "nouns": ["error", "errors", "timeout", "timeouts", "latency", "timing", "crash",
                  "exception", "log", "logs", "slow", "retry", "redirect", "loop", "500"],
        "probes": [],
    },
    "code": {
        "system": "code graph and git",
        "note": "code paths; read them at the DEPLOYED commit, not the branch tip",
        "nouns": ["function", "handler", "route", "endpoint", "code", "branch", "commit",
                  "deploy", "deployed", "guard", "migration"],
        "probes": [["git", "--version"]],
    },
    "memory": {
        "system": "memory",
        "note": "rulings and traps already on record",
        "nouns": ["ruling", "rulings", "decision", "decided", "trap", "invariant",
                  "precedent", "rule"],
        "probes": [],
    },
    "tracker": {
        "system": "tracker",
        "note": "ticket and pull-request history",
        "nouns": ["ticket", "tickets", "issue", "issues", "pr", "regression", "history",
                  "reopened"],
        "probes": [["gh", "--version"]],
    },
    "derived": {
        "system": "the application's own output",
        "counts": False,
        "note": "a value the app derived from another system; recorded, worth zero",
        "nouns": ["response field", "url parameter", "query string", "query parameter",
                  "cached flag", "cache", "header", "payload"],
        "probes": [],
    },
}

_ROW_SOURCE = re.compile(r"^\s*Row source:\s*(.+?)\s*$", re.I)
_HEADING2 = re.compile(r"^##(?!#)\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_PLACEHOLDER = re.compile(r"<[^<>]*>")
_SUBSTANCE = re.compile(r"[^\W_]{2,}")
_SEP = r"(?:—|–|-{1,2}|:)"
_LIVE_NONE = re.compile(rf"^\s*(?:[-*+]\s+)?Live state:\s*none\b\s*(?:{_SEP}\s*(.*))?$", re.I)
_EVIDENCE = re.compile(r"^(read|n/a)\s*:\s*(.*)$", re.I)
_BULLET = re.compile(r"^ ?(?:[-*+]|\d+[.)])\s+(.*)$")
_NONE_ITEM = re.compile(rf"^none\b\s*(?:{_SEP}\s*(.*))?$", re.I)
_EXPLAINED = re.compile(r"\bexplained\s*:(.*)$", re.I)


def norm(value: str) -> str:
    return " ".join(str(value).strip().strip("`").split())


def system_key(value: str) -> str:
    return norm(value).lower()


def has_substance(text: str | None) -> bool:
    """Two consecutive letters or digits once `<…>` placeholders are gone.
    `read: <what, env>`, `none —`, `explained:` and ` - ` all fail it."""
    if text is None:
        return False
    return bool(_SUBSTANCE.search(_PLACEHOLDER.sub("", text)))


def strip_noise(text: str) -> str:
    """Drop HTML comments and fenced blocks: a table SHOWN in a fence or left
    in a template comment is documentation, not evidence."""
    text = _COMMENT.sub("", text)
    out, inside = [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


def load_classes(root: Path) -> dict[str, dict]:
    """The project's classes, or the defaults. An unreadable or malformed config
    RAISES: a gate whose configuration cannot be read has not passed."""
    path = Path(root).joinpath(*SOURCES_CONFIG)
    if not path.exists():
        return copy.deepcopy(DEFAULT_CLASSES)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} could not be read as JSON ({exc}); the evidence gate "
                         f"fails closed rather than guess the systems of record") from exc
    classes = data.get("classes") if isinstance(data, dict) else None
    if not isinstance(classes, dict) or not classes:
        raise ValueError(f"{path} has no `classes` object; see templates/loopkit/sources.json")
    out: dict[str, dict] = {}
    for name, spec in classes.items():
        if not isinstance(spec, dict) or not norm(spec.get("system", "")):
            raise ValueError(f"{path}: class '{name}' has no `system`")
        out[norm(name).lower()] = spec
    return out


def sections(text: str, title: str) -> list[str]:
    """Bodies of every level-2 section whose title is `title` (case-insensitive),
    each running to the next level-2 heading."""
    want = title.strip().lower()
    found: list[list[str]] = []
    current: list[str] | None = None
    for line in strip_noise(text).splitlines():
        m = _HEADING2.match(line)
        if m:
            current = [] if m.group(1).strip().lower() == want else None
            if current is not None:
                found.append(current)
            continue
        if current is not None:
            current.append(line)
    return ["\n".join(body) for body in found]


def _cells(line: str) -> list[str]:
    inner = line.strip().strip("|")
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


def _is_separator(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= set("-:| ")


def source_rows(body: str) -> list[tuple[str, str]]:
    """(class, evidence) for every data row of every table in the section
    whose header carries both a `class` and an `evidence` column. Other tables
    — the snapshot's tool list, say — are not evidence and are skipped."""
    rows: list[tuple[str, str]] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("|") or _is_separator(line):
            i += 1
            continue
        header = [c.lower() for c in _cells(line)]
        i += 1
        if "class" not in header or "evidence" not in header:
            while i < len(lines) and lines[i].strip().startswith("|"):
                i += 1
            continue
        ci, ei = header.index("class"), header.index("evidence")
        while i < len(lines) and lines[i].strip().startswith("|"):
            row = lines[i].strip()
            i += 1
            if _is_separator(row):
                continue
            cells = _cells(row)
            if not any(cells):
                continue
            cls = cells[ci] if ci < len(cells) else ""
            ev = cells[ei] if ei < len(cells) else ""
            rows.append((norm(cls).lower(), ev.strip()))
    return rows


def check_sources(body: str | None, classes: dict[str, dict]) -> tuple[list[str], list[str]]:
    """(problems, independent systems counted). No problems means this half passes."""
    if body is None:
        return ([f"no `## Sources` section. Write one: at least {MIN_INDEPENDENT} `read:` rows "
                 f"on different systems of record, one of them live unless you declare `Live state: none — <why>` (run "
                 f"scripts/source-snapshot.py for the skeleton)"], [])
    problems: list[str] = []
    live_none: str | None = None
    for line in body.splitlines():
        m = _LIVE_NONE.match(line)
        if m:
            if has_substance(m.group(1)):
                live_none = m.group(1).strip()
            else:
                problems.append("`Live state: none` needs a reason after the dash: "
                                "`Live state: none — <why no system holds state about this>`")
    rows = source_rows(body)
    systems: list[str] = []
    live: list[str] = []
    for cls, ev in rows:
        label = f"sources row '{cls or '<blank>'}'"
        if cls not in classes:
            problems.append(f"{label}: class not configured (known: "
                            f"{', '.join(sorted(classes))}). Add it to .loopkit/sources.json "
                            f"with its system of record, or use a known class")
            continue
        m = _EVIDENCE.match(ev)
        if not m or not has_substance(m.group(2)):
            problems.append(f"{label}: evidence must be `read: <what you read, which env>` "
                            f"or `n/a: <why>`, got '{ev[:60]}'")
            continue
        spec = classes[cls]
        if m.group(1).lower() != "read":
            continue
        counts = spec.get("counts", True) is not False
        key = system_key(spec["system"])
        if counts and key not in systems:
            systems.append(key)
        if counts and spec.get("live") is True and key not in live:
            live.append(key)
    if live_none is None and not rows and not problems:
        problems.append("`## Sources` is empty: no sources table rows and no "
                        "`Live state: none — <why>`. An empty section is a zero nobody stated")
    # Rule 1, always — `Live state: none` does NOT waive it (owner, 2026-10-16:
    # "both above options are needed"). A finding that claims nothing live
    # still claims something, and that claim needs two systems too.
    if len(systems) < MIN_INDEPENDENT:
        problems.append(
            f"reads {len(systems)} independent system(s) of record"
            f"{' (' + ', '.join(systems) + ')' if systems else ''}; at least "
            f"{MIN_INDEPENDENT} are needed, with or without `Live state: none`. Two reads "
            f"of one system, or a value the app derived (class `derived`), count once or "
            f"not at all. A code-only finding reads code plus one more system — memory "
            f"or the tracker")
    # Rule 2, unless the assessment states there is no live-state claim: one of
    # those systems must be LIVE (identity, data, runtime). Code, memory,
    # tracker and derived values describe or copy the state; they are not it.
    if live_none is None and not live:
        live_names = sorted(n for n, c in classes.items()
                            if c.get("live") is True and c.get("counts", True) is not False)
        problems.append(
            f"no read from a LIVE system ({', '.join(live_names) or 'none configured'}). "
            f"A finding about a real user, row, flag or runtime behaviour must read where "
            f"that state is kept; code, memory, tracker and derived values never satisfy "
            f"this. If the finding makes no claim about live state, say so: "
            f"`Live state: none — <why>`")
    return problems, systems


def _items(body: str) -> list[str]:
    items: list[list[str]] = []
    for line in body.splitlines():
        m = _BULLET.match(line)
        if m:
            items.append([m.group(1)])
        elif items and line.strip():
            items[-1].append(line.strip())
    return ["\n".join(i) for i in items]


def check_does_not_fit(body: str | None) -> list[str]:
    if body is None:
        return ["no `## Does not fit` section. Write `- none — <what was checked>`, or one "
                "item per observation that does not fit the conclusion, each with an "
                "`explained:` line"]
    items = _items(body)
    if not items:
        return ["`## Does not fit` has no list items. Write `- none — <what was checked>` "
                "or one `- <observation>` item per contradiction"]
    nones = [i for i in items if _NONE_ITEM.match(i.splitlines()[0].strip())]
    if nones:
        if len(items) > 1:
            return ["`## Does not fit` says `none` AND lists observations; it is one or "
                    "the other"]
        m = _NONE_ITEM.match(nones[0].splitlines()[0].strip())
        if not has_substance(m.group(1)):
            return ["`- none` must say what was checked: `- none — <what was checked>`"]
        return []
    problems = []
    for item in items:
        reasons = [m.group(1) for m in (_EXPLAINED.search(l) for l in item.splitlines()) if m]
        if not any(has_substance(r) for r in reasons):
            first = item.splitlines()[0].strip()
            problems.append(f"does-not-fit item not explained: '{first[:90]}'. Add "
                            f"`explained: <why it does not change the conclusion>`, or "
                            f"change the conclusion")
    return problems


def declared_rows(text: str) -> list[str]:
    return [norm(m.group(1)) for m in (_ROW_SOURCE.match(l) for l in strip_noise(text).splitlines()) if m]


def find_assessments(root: Path, source: str) -> list[Path]:
    """Markdown files directly under state/ that declare `Row source: <source>`.
    Top level only: that is where loop-assess writes them."""
    want = norm(source)
    hits = []
    state = Path(root) / "state"
    if not state.is_dir():
        return hits
    for path in sorted(state.glob("*.md")):
        if path.name == "triage.md":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if want in declared_rows(text):
            hits.append(path)
    return hits


def evaluate_text(text: str, classes: dict[str, dict]) -> list[str]:
    problems: list[str] = []
    src = sections(text, "Sources")
    dnf = sections(text, "Does not fit")
    if len(src) > 1:
        problems.append("two `## Sources` sections; keep one")
    if len(dnf) > 1:
        problems.append("two `## Does not fit` sections; keep one")
    problems += check_sources(src[0] if src else None, classes)[0]
    problems += check_does_not_fit(dnf[0] if dnf else None)
    return problems


def evaluate(root: Path, source: str, explicit: str | None = None,
             cwd: Path | None = None) -> tuple[Path | None, list[str]]:
    """(the assessment used, problems). Empty problems means the gate passes.
    Raises ValueError only for a broken sources.json (fail closed)."""
    root = Path(root)
    classes = load_classes(root)
    want = norm(source)
    if explicit:
        p = Path(explicit)
        candidates = [p] if p.is_absolute() else [(cwd or Path.cwd()) / p, root / p]
        path = next((c for c in candidates if c.is_file()), None)
        if path is None:
            return None, [f"--assessment {explicit}: no such file"]
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return path, [f"{path}: cannot be read ({exc})"]
        if want not in declared_rows(text):
            return path, [f"{path} does not declare `Row source: {want}`; it is some other "
                          f"row's assessment"]
        return path, evaluate_text(text, classes)
    hits = find_assessments(root, want)
    if not hits:
        return None, [f"no assessment under {root / 'state'} declares `Row source: {want}`. "
                      f"loop-assess writes state/<date>-<slug>-assessment.md; put that line "
                      f"in it"]
    if len(hits) > 1:
        return None, [f"{len(hits)} assessments declare `Row source: {want}`: "
                      f"{', '.join(str(h) for h in hits)}. Pick one with --assessment <path>"]
    try:
        text = hits[0].read_text(encoding="utf-8")
    except OSError as exc:
        return hits[0], [f"{hits[0]}: cannot be read ({exc})"]
    return hits[0], evaluate_text(text, classes)


# ---------------------------------------------------------------------------
# THE APPROVER OF AN OVERRIDE (owner ruling, 2026-10-16, on urgent fixes:
# "i should always review, nothing skips a human in loop at least once").
#
# An override moves a finding on without the evidence, so it must name the
# HUMAN who decided that. There is no label-based skip of any kind: no flag,
# no priority, no word in the finding opens the door. The approver is checked
# three ways, strongest first:
#
#   1. ALLOWLIST. If <project>/.loopkit/approvers.txt exists (one name per
#      line, `#` comments), the approver must be on it, case-insensitively.
#      This is the strong form: the project names its humans explicitly.
#   2. NOT THE ACTOR. The approver may not equal LOOPKIT_AGENT, the identity
#      of the agent running the command. An agent approving itself is the
#      self-approval the constitution already forbids.
#   3. NOT AGENT-SHAPED. Without an allowlist, a name is refused when any of
#      its words is a role or tool word below, when it carries `[bot]` or an
#      `@` address, when it holds a run of 4+ digits (runner-4821, job ids),
#      or when it has no word of two or more letters.
#
# Rule 3 is a tripwire, not proof of humanity: it stops an agent writing its
# own role, its model or its CI identity into the field, which is the failure
# that actually happens. A determined agent can type a human-looking name;
# that is why rule 1 exists and why the name is committed where a human reads
# it. Projects that want the guarantee add approvers.txt.
# ---------------------------------------------------------------------------

APPROVERS_FILE = (".loopkit", "approvers.txt")
AGENT_WORDS = frozenset({
    "agent", "agents", "subagent", "bot", "bots", "ai", "llm", "model", "claude", "anthropic",
    "opus", "sonnet", "haiku", "fable", "gpt", "openai", "codex", "copilot", "gemini",
    "loop", "loopkit", "tick", "planner", "implementer", "reviewer", "assessor",
    "generator", "evaluator", "judge", "ci", "cd", "cron", "automation", "automated",
    "auto", "system", "script", "pipeline", "workflow", "runner", "actions", "action",
    "github", "gitlab", "noreply", "dependabot", "renovate", "service", "daemon",
    "hook", "hooks", "scheduler", "scheduled", "job",
})
_WORDS = re.compile(r"[^\W\d_]+")
_LONG_DIGITS = re.compile(r"\d{4,}")


def approvers_allowlist(root: Path) -> list[str] | None:
    path = Path(root).joinpath(*APPROVERS_FILE)
    if not path.exists():
        return None
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(norm(line).lower())
    return names


def approver_problems(name: str | None, acting_agent: str | None, root: Path) -> list[str]:
    """Empty when `name` is acceptable as the human approving an override."""
    who = norm(name or "")
    if not has_substance(who) or not _WORDS.search(who):
        return ["--override-sources must name the HUMAN who approved it: add "
                "--approved-by \"<human name>\". Nothing skips a human (owner, 2026-10-16)"]
    allow = approvers_allowlist(root)
    if allow is not None:
        if who.lower() not in allow:
            return [f"approver '{who}' is not listed in {Path(root).joinpath(*APPROVERS_FILE)}"]
        return []
    if acting_agent and who.lower() == norm(acting_agent).lower():
        return [f"approver '{who}' is the agent running this command (LOOPKIT_AGENT); "
                f"an agent cannot approve its own override"]
    words = {w.lower() for w in _WORDS.findall(who)}
    hits = sorted(words & AGENT_WORDS)
    if hits or "[bot]" in who.lower() or "@" in who or _LONG_DIGITS.search(who):
        why = f"its words {hits}" if hits else "its shape (a bot tag, an address or an id number)"
        return [f"approver '{who}' looks like an agent or process identity, not a human "
                f"({why}). Name the person who decided; or list your humans in "
                f".loopkit/approvers.txt"]
    return []


def match_nouns(finding: str, classes: dict[str, dict]) -> dict[str, list[str]]:
    """Which classes a finding's words touch. A SUGGESTION for the snapshot;
    the gate never calls this."""
    text = " " + re.sub(r"[^\w\s/-]", " ", finding.lower()) + " "
    text = " ".join(text.split())
    out: dict[str, list[str]] = {}
    for name, spec in classes.items():
        hits = [n for n in spec.get("nouns", [])
                if re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", text)]
        if hits:
            out[name] = hits
    return out
