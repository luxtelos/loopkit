#!/usr/bin/env python3
"""require_recall.py — block authoring a storage invariant until memory was consulted.

The case this is built on: a UNIQUE index that enforced "one row per user" was
wrong about what a row means. The fact was recorded twice — a memory drawer
describing the same defect one layer up, and a code graph showing the column
written in 175 places — and neither was read. The migration revoked live
assignments. A loop with strong GENERATION discipline and no RECALL discipline
writes findings faithfully and reads them never.

Injection did not fix it (the protocol was already written down and skipped);
a refusal does. Same lesson as require_contracts.py.

Scope, deliberately narrow: only writes that create a DB-level invariant —
migration files by path, UNIQUE / PRIMARY KEY / CONSTRAINT … CHECK / EXCLUDE
by content on code-like files, and Bash that redirects, copies or moves into a
migrations directory. Rules come from <project>/.loopkit/recall-triggers.txt
(defaults below). Prose files are exempt from the content rule: documentation
about invariants is not an invariant, and gating it trains bypasses. For the
same reason, comments inside a code file are dropped before the content rule
runs (see strip_comments below).

What counts as "memory consulted": whatever the registry's adapters say —
an MCP tool name or a Bash command matching an adapter's recall pattern this
session. The hook never names a server. Mark mode (PostToolUse) records it.

Outage escape, audited: when the content rule fires and the memory tools are
genuinely down, write the reason (non-empty) into
$TMPDIR/loopkit-recall-<session>/recall-unavailable. Never honoured for a real
migration write — those wait for memory or for a human.

Passive until .loopkit/memory.json exists. Exit 2 blocks; everything else 0.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

DEFAULT_RULES = {
    "path": [r"(^|/)(db/)?migrations/.*\.sql$"],
    "content": [r"\b(UNIQUE\s+INDEX|UNIQUE\s*\(|PRIMARY\s+KEY|"
                r'CONSTRAINT\s+("[^"\n]+"|\w+)[^\n]{0,80}?CHECK\s*\(|EXCLUDE\s+USING|'
                r"ADD\s+CONSTRAINT)\b"],
    "bash": [r"(>>?\s*[^\n;|&]*migrations/[^\n;|&]*\.sql"
             r"|(?:^|[;|&]\s*|\$\(\s*)(?:tee\s+(?:-a\s+)?|(?:cp|mv|install)\s+)"
             r"[^\n;|&]*migrations/[^\n;|&]*\.sql)"],
}
PROSE_PATH = re.compile(r"\.(md|mdx|txt|rst|adoc)$", re.I)


# Prose inside a CODE file is the same mistake one level down: a comment that
# names an invariant ("rows are keyed by the primary key of users") blocked a
# .py/.ts edit while changing no schema. A comment cannot create a storage
# invariant; a string can (real DDL lives in strings), so strings are kept.
#
# HOW. One scan over the whole body that knows whether it is in code, in a
# string, or in a block comment, using the COMMENT SYNTAX OF THE FILE'S
# LANGUAGE, picked from its extension. Comment markers count only in code, and
# only where they start a word; text inside any string is always kept.
# Ported from a downstream fix that passed two rounds of independent review.
#
# What the reviews taught, so the next change does not repeat it:
#   * Stripping line by line dropped `/* note */ CREATE … INDEX` whole, and
#     `#` / `//` / `*` lines inside multi-line strings went too.
#   * One scanner for every language is wrong: in shell, YAML, Dockerfile and
#     Makefile `/*` is a GLOB (`for f in db/seeds/*.sql` hid the rest of the
#     file), `#` is a TS private field, `//` is Python floor division.
# So: a file type not listed below is NOT stripped at all (the safe default:
# everything is checked); `/*` opens a block only where it starts a word; and
# a block comment that never closes gives its text back.
#
# Python docstrings are strings and are NOT dropped: telling a docstring from
# a SQL string needs a parse, and a missed real invariant costs more than a
# blocked docstring. Migration paths never reach this (always in scope).
# Known limit (review: acceptable): an Edit fragment that STARTS inside a
# multi-line string is read as code from its first character.


class _Style:
    def __init__(self, line=(), block=False, strings=("'", '"'), multiline=False,
                 can_open=None, alt=None):
        self.line = line  # line-comment markers, e.g. ("#",)
        self.block = block  # /* … */ comments
        self.strings = strings  # string delimiters, longest first
        self.multiline = multiline  # may ' and " strings cross a newline?
        self.can_open = can_open  # (body, i, quote) -> may this quote open a string?
        self.alt = alt  # a second reading of the same file; the gate checks both


# Where a quote can start a YAML scalar or a shell word. A quote anywhere else
# is an apostrophe in text ("it's"), not a delimiter.
_WORD_QUOTE_AFTER = " \t\r\n[{,:=($|&;<>"


def _quote_at_word_start(body: str, i: int, quote: str) -> bool:
    return i == 0 or body[i - 1] in _WORD_QUOTE_AFTER


# JS keywords after which an expression (so a template literal) may start.
_JS_EXPR_KEYWORDS = frozenset((
    "return", "yield", "await", "case", "typeof", "void", "in", "of", "throw",
    "new", "else", "do", "delete", "instanceof", "extends",
))
# After one of these characters, the next token is an expression.
_JS_EXPR_AFTER = "=(,:[{?!&|+-*%<;^~"


def _ident(c: str) -> bool:
    return c.isalnum() or c in "_$"


def _backtick_in_expression(body: str, i: int, quote: str) -> bool:
    """In JSX a backtick in TEXT (`<p>run `npm</p>`) is a character, not a
    template literal. Open a template only where an expression can start."""
    if quote != "`" or i == 0:
        return True
    if _ident(body[i - 1]):
        return True  # a tagged template: sql`…`, styled.div`…`
    j = i - 1
    while j >= 0 and body[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    p = body[j]
    if p == ">":
        return j > 0 and body[j - 1] == "="  # `=> `…`` yes; `<p> `…`` is JSX text
    if p in _JS_EXPR_AFTER:
        return True
    if _ident(p):
        k = j
        while k >= 0 and _ident(body[k]):
            k -= 1
        return body[k + 1 : j + 1] in _JS_EXPR_KEYWORDS
    return False


_HASH = _Style(line=("#",), strings=("'", '"', "`"), multiline=True)  # sh, toml, …
_PY = _Style(line=("#",), strings=('"""', "'''", "'", '"'))
_C = _Style(line=("//",), block=True, strings=("'", '"', "`"))  # js/ts/go/java/…
_CSS = _Style(block=True)
_SQL = _Style(line=("--",), block=True)
_HCL = _Style(line=("#", "//"), block=True, strings=('"',))
# YAML and JSX each get TWO readings, and the gate blocks if EITHER shows an
# invariant outside a comment (review follow-ups, 2026-10-03):
#   * YAML: in a plain scalar an apostrophe is text, so `name: it's …` opened a
#     `'` string that ran to the end of the file and turned every later quote
#     inside out -- `run: psql -c 'SELECT 1 #x' -c '<DDL>'` then lost the DDL
#     after ` #`. The second reading opens a quote only where a scalar or a
#     shell word can start, and ends it at the newline.
#   * JSX: an odd backtick in JSX text (`<p>run `npm</p>`) opened a template
#     literal that swallowed the next real one, so `x // y <DDL>` inside it read
#     as a comment. The second reading opens a backtick only where an
#     expression can start.
# Each reading alone has shapes it gets wrong (a multi-line shell string in a
# `run: |` block; `css `…`` with a space). Checking both can only ADD blocks,
# never lose one the plain reading had: a missed invariant costs more than a
# blocked comment. The price: after a stray apostrophe or backtick, a later
# comment can still block, exactly as it did before.
_YAML = _Style(line=("#",), strings=("'", '"'), can_open=_quote_at_word_start, alt=_HASH)
_JSX = _Style(line=("//",), block=True, strings=("'", '"', "`"),
              can_open=_backtick_in_expression, alt=_C)

_STYLE_BY_EXT = {
    **dict.fromkeys(
        ("sh", "bash", "zsh", "ksh", "toml", "rb", "r", "pl", "mk", "env",
         "cfg", "conf", "properties", "gitignore", "dockerignore", "dockerfile"),
        _HASH,
    ),
    **dict.fromkeys(("yml", "yaml"), _YAML),
    # FizzBee is Python-like: # comments, Python strings. The loop writes
    # state/models/*.fizz, and a # comment naming an invariant used to block.
    **dict.fromkeys(("py", "fizz"), _PY),
    **dict.fromkeys(("jsx", "tsx"), _JSX),
    **dict.fromkeys(
        ("js", "mjs", "cjs", "ts", "mts", "cts", "java", "go", "c",
         "h", "cc", "cpp", "hpp", "cs", "kt", "kts", "swift", "rs", "scala",
         "dart", "scss", "less"),
        _C,
    ),
    "css": _CSS,
    "sql": _SQL,
    **dict.fromkeys(("tf", "hcl"), _HCL),
}
_STYLE_BY_NAME = {"dockerfile": _HASH, "makefile": _HASH, "gnumakefile": _HASH}
# `/*` opens a block comment only after one of these (or at the start).
_BLOCK_OPENS_AFTER = " \t\n;(,{}"


def comment_style(path: str):
    """The comment syntax for this file, or None: then nothing is stripped."""
    name = os.path.basename(path).lower()
    if name in _STYLE_BY_NAME:
        return _STYLE_BY_NAME[name]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    # `dockerfile.prod` is a Dockerfile; `dockerfile.ts` and
    # `dockerfile-render.ts` are TypeScript. A prefix match read the last two
    # with # comments, so a TS private field `#ddl = "<DDL>"` went through.
    # (`app.dockerfile` is caught by the extension table.)
    if name.startswith("dockerfile.") and ext not in _STYLE_BY_EXT:
        return _HASH
    return _STYLE_BY_EXT.get(ext)


def readings(body: str, style) -> list:
    """Every reading of the body the gate checks: one per style it carries."""
    out = [strip_comments(body, style)]
    if style is not None and style.alt is not None:
        out.append(strip_comments(body, style.alt))
    return out


def strip_comments(body: str, style) -> str:
    """The body with comment text removed, for the content heuristic only."""
    if style is None:
        return body
    out = []
    i, n = 0, len(body)
    string = None  # the delimiter that closes the string we are in, or None
    block_from = None  # body index where an open /* began, or None
    while i < n:
        c = body[i]
        if block_from is not None:
            if body.startswith("*/", i):
                block_from = None
                out.append(" ")
                i += 2
            else:
                if c == "\n":
                    out.append(c)
                i += 1
            continue
        if string:
            if c == "\\":
                out.append(body[i : i + 2])
                i += 2
                continue
            if body.startswith(string, i):
                out.append(string)
                i += len(string)
                string = None
                continue
            if c == "\n" and len(string) == 1 and string != "`" and not style.multiline:
                string = None  # a one-line string cannot cross a newline here
            out.append(c)
            i += 1
            continue
        # --- code ---
        if c == "\\":  # an escaped character is never a quote or a marker
            out.append(body[i : i + 2])
            i += 2
            continue
        word_start = i == 0 or body[i - 1] in " \t\n"
        if style.block and body.startswith("/*", i) and (
            i == 0 or body[i - 1] in _BLOCK_OPENS_AFTER
        ):
            block_from = i
            i += 2
            continue
        if word_start and any(body.startswith(m, i) for m in style.line):
            j = body.find("\n", i)
            i = n if j < 0 else j  # keep the newline itself
            continue
        opener = next((q for q in style.strings if body.startswith(q, i)), None)
        if opener and (style.can_open is None or style.can_open(body, i, opener)):
            string = opener
            out.append(opener)
            i += len(opener)
            continue
        out.append(c)
        i += 1
    if block_from is not None:
        out.append(body[block_from:])  # never closed: give the text back
    return "".join(out)



def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def load_rules(root: Path) -> dict[str, list[re.Pattern[str]]]:
    raw: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_RULES.items()}
    f = root / ".loopkit" / "recall-triggers.txt"
    if f.exists():
        parsed: dict[str, list[str]] = {"path": [], "content": [], "bash": []}
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            kind, rx = line.split(":", 1)
            kind = kind.strip().lower()
            if kind in parsed and rx.strip():
                parsed[kind].append(rx.strip())
        if any(parsed.values()):
            raw = parsed
    out: dict[str, list[re.Pattern[str]]] = {}
    for kind, rxs in raw.items():
        compiled = []
        for rx in rxs:
            try:
                compiled.append(re.compile(rx, re.I | re.M))
            except re.error:
                continue
        out[kind] = compiled
    return out


def session_key(payload: dict) -> str:
    for c in (payload.get("session_id"), os.environ.get("CLAUDE_SESSION_ID"), payload.get("transcript_path"), str(os.getppid())):
        if c:
            return hashlib.sha256(str(c).encode()).hexdigest()[:16]
    return "unknown"


def marker_dir(key: str) -> Path:
    d = Path(tempfile.gettempdir()) / f"loopkit-recall-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    mode = sys.argv[1] if len(sys.argv) > 1 else "gate"
    root = project_root()
    try:
        from loopkit_memory import load as load_registry
        reg = load_registry(root)
    except Exception:
        return 0  # the registry failing to import must never lock the session
    if not reg.configured:
        return 0  # passive until the project opts in

    key = session_key(payload)
    flag = marker_dir(key) / "queried"
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}

    if mode == "mark":
        if reg.is_recall(tool, ti):
            flag.touch()
        return 0

    rules = load_rules(root)
    migration_scope = False
    touches = False
    target = ""
    if tool == "Bash":
        cmd = str(ti.get("command") or "")
        touches = migration_scope = any(rx.search(cmd) for rx in rules["bash"])
        target = "(Bash write into a migrations directory)" if touches else ""
    elif tool in ("Write", "Edit", "MultiEdit"):
        target = str(ti.get("file_path") or "")
        pieces = [str(ti.get(k) or "") for k in ("content", "new_string")]
        for e in ti.get("edits") or []:
            if isinstance(e, dict):
                pieces.append(str(e.get("new_string") or ""))
        migration_scope = any(rx.search(target) for rx in rules["path"])
        if not migration_scope and not PROSE_PATH.search(target):
            # Each piece is its own fragment: strip it alone, so an open quote
            # in one edit cannot carry into the next.
            style = comment_style(target)
            texts = [r for x in pieces if x for r in readings(x, style)]
            touches = any(rx.search(t) for t in texts for rx in rules["content"])
        else:
            touches = migration_scope
    else:
        return 0
    if not touches or flag.exists():
        return 0

    outage = marker_dir(key) / "recall-unavailable"
    if not migration_scope:
        try:
            if outage.exists() and outage.read_text().strip():
                return 0
        except OSError:
            pass

    memcli = PLUGIN_ROOT / "scripts" / "memory.py"
    print(
        "BLOCKED — you are authoring a STORAGE INVARIANT without having consulted memory this session.\n\n"
        f"  target: {target or '(inline SQL)'}\n"
        f"  {reg.status_line()}\n\n"
        "Consult BOTH before writing it:\n"
        f"  1. python3 {memcli} recall \"<table or column>\"   — has this invariant bitten before?\n"
        f"  2. python3 {memcli} graph callers <symbol>        — which functions write these columns?\n"
        "     A UNIQUE constraint is a claim about what ONE ROW MEANS. Enumerate every row kind the\n"
        "     predicate captures, especially via the columns you are NOT keying on.\n\n"
        "Then state, in the migration itself, what one row means and which row kinds the predicate captures.\n"
        + ("" if migration_scope else
           f"\nIf memory is genuinely unreachable this session, write the reason into {outage} and retry —\n"
           "content-rule writes only; the note is audited. Real migration writes are never exempted.\n"),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
