"""push_guard — force push and remote delete, read the way the shell reads it.

Ported from the Balancia project copy of block_dangerous.py (luxtelos/
adaptive-unified-accountingos @ 160862d77, PR #2965, independent review PASS
in round 3, 2026-10-03). Keep the two copies in step: a fix to one is a fix
owed to the other.

The one loopkit-specific change: every finding carries a KIND — "force",
"delete" or "either" — so `.loopkit/block-disabled.txt` can switch off
`git-force-push` and `git-push-delete` separately, as it could when they were
two regexes. A finding the guard cannot attribute (unreadable quoting, a `$F`
before the remote, a heredoc or quoted text that names a push) is "either":
it blocks while EITHER rule is on. A disabled kind is skipped, not returned,
so a later finding of the other kind is still seen.
"""

from __future__ import annotations

import os
import re
import shlex
from itertools import count

FORCE = "force"
DELETE = "delete"
EITHER = "either"
ALL_KINDS = frozenset({FORCE, DELETE})

# ---------------------------------------------------------------------------
# Force push and remote delete: read the command the way the shell does.
#
# History. Dev had `git\s+push\b[^\n]*(-f\b|--force)`: it ran past `&&` into a
# `gh pr create` whose title said "-F ... -f" and refused a plain push. PR
# #2965 stopped the regex at `;`, `&`, `|` and wanted a space before the flag.
# Review FAIL 2026-10-03: that let through nine real force pushes / deletes —
# a quoted flag (`"-f"`), a separator INSIDE quotes (`"HEAD:refs/heads/a;b" -f`),
# a redirect (`2>&1 -f`, `&>/dev/null --force`) and `$(a; b) -f`. A regex
# cannot tell a real `;` from a quoted one. So:
#   1. _Scan splits the text at real separators only (quotes, `$( )`,
#      backticks, comments, heredocs and redirects honoured);
#   2. shlex (punctuation_chars=True) turns each simple command into argv;
#   3. every `git … push` argv is checked token by token (case-sensitive:
#      git push has no -F or -D, see #2955 for the same rule on add).
# Text the hook cannot parse (an unclosed quote) that mentions git and push is
# BLOCKED, with the reason — fail closed, never guess.
# ---------------------------------------------------------------------------
# Long options git push reads as force or delete. Git takes any unique prefix
# of a long option (`--forc`, `--del`), so a `--name` that is a prefix of one
# of these is treated as that option. --mirror and --prune delete remote refs.
_DANGEROUS_LONG = (
    "force",
    "force-with-lease",
    "force-if-includes",
    "delete",
    "mirror",
    "prune",
)
# Long options whose value is the NEXT token (so the value is data, not a flag).
_LONG_WITH_VALUE = ("push-option", "repo", "receive-pack", "exec")
# git global options that take a separate value before the subcommand.
_GIT_GLOBAL_WITH_VALUE = (
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--config-env",
    "--super-prefix",
)
# Programs whose quoted arguments (or heredoc) are themselves run as shell.
_SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "fish", "eval", "ssh", "su", "watch")
# Two separate searches, NOT one `\bgit\b[\s\S]*\bpush\b`: that spanning
# regex is quadratic (11.7 s on 48 KB of "git" words with no "push"; review
# round 2, 2026-10-03). Each of these is one linear pass.
_GIT_WORD = re.compile(r"\bgit\b")
_PUSH_WORD = re.compile(r"\bpush\b")


def _mentions_push(text):
    return bool(_GIT_WORD.search(text) and _PUSH_WORD.search(text))


# Quoted text and heredocs given to a NON-shell program are data the hook
# cannot read as commands; they get dev's old rule, so nothing dev blocked
# there slips through (e.g. `python3 -c "os.system('git push -f')"`).
# Same meaning as dev's `git\s+push\b[^\n]*(-f\b|--force)` and
# `git\s+push\b[^\n]*(--delete|\s:)`, but linear: see _data_net().
_DATA_PUSH = re.compile(r"git\s+push\b", re.IGNORECASE)
_DATA_FLAG = re.compile(r"-f\b|--force|--delete|\s:", re.IGNORECASE)
_PH_RE = re.compile(r"__loopkit_subst_\d+__")
_MAX_DEPTH = 6
# `$( … )` / backtick nesting deeper than this is refused (fail closed) long
# before Python's recursion limit: a RecursionError used to exit 1, and exit 1
# lets the command run (review round 2, 2026-10-03).
_MAX_NEST = 100


class _Unparsable(ValueError):
    """The hook cannot read the shell quoting of this text."""


class _Scan:
    """Split shell text into simple commands at REAL separators only.

    `pieces` holds (command_text, holder); holder["heredocs"] collects the
    bodies of heredocs that command opened. Each `$( … )` / backtick body is
    scanned as its own command(s) AND replaced in the outer word by a
    placeholder; `subs` maps the placeholder to the inner command texts.
    """

    def __init__(self, text, ctx):
        self.s = text
        self.n = len(text)
        self.ctx = ctx
        self.pieces = []

    def run(self):
        self._scan(0, None)
        return self

    def _placeholder(self, inner):
        ph = f"__loopkit_subst_{next(self.ctx['counter'])}__"
        self.ctx["subs"][ph] = inner
        return ph

    def _backtick(self, i):
        """i is just past the opening backtick; return (placeholder, end)."""
        s, n, j = self.s, self.n, i
        while j < n and s[j] != "`":
            j += 2 if s[j] == "\\" else 1
        if j >= n:
            raise _Unparsable("an unclosed backtick")
        child = _Scan(s[i:j], self.ctx).run()
        self.pieces.extend(child.pieces)
        return self._placeholder([p for p, _ in child.pieces]), j + 1

    def _dquote(self, i, cur):
        """i is just past the opening double quote; return the end index."""
        s, n = self.s, self.n
        buf = ['"']
        while True:
            if i >= n:
                raise _Unparsable("an unclosed double quote")
            d = s[i]
            if d == "\\":
                buf.append(s[i : i + 2])
                i += 2
            elif d == '"':
                buf.append('"')
                i += 1
                break
            elif d == "$" and i + 1 < n and s[i + 1] == "(":
                i, inner = self._scan(i + 2, ")")
                buf.append(self._placeholder(inner))
            elif d == "`":
                ph, i = self._backtick(i + 1)
                buf.append(ph)
            else:
                buf.append(d)
                i += 1
        cur.append("".join(buf))
        return i

    def _heredoc_bodies(self, i, pending):
        s, n = self.s, self.n
        for delim, strip_tabs, holder in pending:
            lines = []
            while i < n:
                j = s.find("\n", i)
                line = s[i:] if j < 0 else s[i:j]
                i = n if j < 0 else j + 1
                if (line.lstrip("\t") if strip_tabs else line) == delim:
                    break
                lines.append(line)
            holder["heredocs"].append("\n".join(lines))
        return i

    def _heredoc_delim(self, j):
        s, n = self.s, self.n
        while j < n and s[j] in " \t":
            j += 1
        out = []
        while j < n and s[j] not in " \t\n;&|<>()":
            ch = s[j]
            if ch in "'\"":
                k = s.find(ch, j + 1)
                if k < 0:
                    raise _Unparsable("an unclosed quote in a heredoc delimiter")
                out.append(s[j + 1 : k])
                j = k + 1
            elif ch == "\\":
                out.append(s[j + 1 : j + 2])
                j += 2
            else:
                out.append(ch)
                j += 1
        return "".join(out), j

    def _scan(self, i, closer):
        """Count nesting around _scan_body; refuse absurd depth."""
        nest = self.ctx.get("nest", 0) + 1
        if nest > _MAX_NEST:
            raise _Unparsable(f"$( ) or backticks nested more than {_MAX_NEST} deep")
        self.ctx["nest"] = nest
        try:
            return self._scan_body(i, closer)
        finally:
            self.ctx["nest"] = nest - 1

    def _scan_body(self, i, closer):
        """Scan from i. With closer=")" stop at the matching `)` of a `$(`.

        Returns (end_index, command texts produced in this scope).
        """
        s, n = self.s, self.n
        local = []
        pending = []
        state = {"cur": [], "holder": {"heredocs": []}}
        depth = 0
        word_start = True

        def flush():
            text = "".join(state["cur"])
            if text.strip() or state["holder"]["heredocs"]:
                self.pieces.append((text, state["holder"]))
                local.append(text)
            state["cur"] = []
            state["holder"] = {"heredocs": []}

        while i < n:
            cur = state["cur"]
            c = s[i]
            if c == "\\":
                if i + 1 < n and s[i + 1] == "\n":  # line continuation
                    i += 2
                    continue
                cur.append(s[i : i + 2])
                i += 2
                word_start = False
                continue
            if c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise _Unparsable("an unclosed single quote")
                cur.append(s[i : j + 1])
                i = j + 1
                word_start = False
                continue
            if c == '"':
                i = self._dquote(i + 1, cur)
                word_start = False
                continue
            if c == "$" and i + 1 < n and s[i + 1] in "({'\"":
                nx = s[i + 1]
                if nx == "(":
                    i, inner = self._scan(i + 2, ")")
                    cur.append(self._placeholder(inner))
                elif nx == "{":
                    j = s.find("}", i + 2)
                    if j < 0:
                        raise _Unparsable("an unclosed ${")
                    cur.append(s[i : j + 1])
                    i = j + 1
                elif nx == "'":  # ANSI-C quoting: $'-f' is -f
                    j = i + 2
                    while j < n and s[j] != "'":
                        j += 2 if s[j] == "\\" else 1
                    if j >= n:
                        raise _Unparsable("an unclosed $'")
                    raw = s[i + 2 : j]
                    try:
                        text = raw.encode("latin-1", "backslashreplace").decode(
                            "unicode_escape"
                        )
                    except (UnicodeError, ValueError):
                        text = raw
                    cur.append(shlex.quote(text))
                    i = j + 1
                else:  # $"…" is a plain double-quoted string
                    i += 1
                word_start = False
                continue
            if c == "`":
                ph, i = self._backtick(i + 1)
                cur.append(ph)
                word_start = False
                continue
            if c == "#" and word_start:  # comment to end of line
                j = s.find("\n", i)
                i = n if j < 0 else j
                continue
            if c == "\n":
                flush()
                i = self._heredoc_bodies(i + 1, pending)
                pending = []
                word_start = True
                continue
            if c == "<" and s.startswith("<<<", i):  # here-string
                cur.append("<<<")
                i += 3
                word_start = True
                continue
            if c == "<" and s.startswith("<<", i):  # heredoc
                j = i + 2
                strip_tabs = j < n and s[j] == "-"
                if strip_tabs:
                    j += 1
                delim, j = self._heredoc_delim(j)
                if delim:
                    pending.append((delim, strip_tabs, state["holder"]))
                cur.append(" ")
                i = j
                word_start = True
                continue
            if c == "&":
                if i > 0 and s[i - 1] in "<>":  # >&  <&
                    cur.append(c)
                    i += 1
                    continue
                if i + 1 < n and s[i + 1] == ">":  # &>  &>>
                    cur.append("&>")
                    i += 2
                    word_start = False
                    continue
                flush()
                i += 1
                word_start = True
                continue
            if c == "|":
                if i > 0 and s[i - 1] == ">":  # >| clobber redirect
                    cur.append(c)
                    i += 1
                    continue
                flush()
                i += 1
                word_start = True
                continue
            if c == ";":
                flush()
                i += 1
                word_start = True
                continue
            if c == "(":
                flush()
                depth += 1
                i += 1
                word_start = True
                continue
            if c == ")":
                flush()
                i += 1
                word_start = True
                if depth > 0:
                    depth -= 1
                elif closer == ")":
                    return i, local
                continue
            cur.append(c)
            word_start = c in " \t"
            i += 1
        flush()
        if closer is not None:
            raise _Unparsable("an unclosed $(")
        return n, local


def _words(piece):
    """argv of one simple command, via shlex (punctuation_chars=True).

    Redirect operators (`2>&1`, `&>`) come back as their own tokens and are
    KEPT: shlex cannot say whether a lone `;` was quoted, and dropping it let
    `-o ";" -f` read `-f` as the -o value. A kept `>&` is harmless to the
    flag check; a dropped token can hide a flag.
    """
    lex = shlex.shlex(piece, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError as exc:  # e.g. "No closing quotation"
        raise _Unparsable(str(exc))


def _brace_variants(word):
    """`{-f,}` expands to `-f`: check each alternative of one brace group."""
    m = re.match(r"^(.*?)\{([^{}]*)\}(.*)$", word)
    if not m or ("," not in m.group(2)):
        return [word]
    return [word] + [m.group(1) + alt + m.group(3) for alt in m.group(2).split(",")]


# Which kind each dangerous long option is. --mirror both forces and deletes.
_LONG_KIND = {
    "force": FORCE,
    "force-with-lease": FORCE,
    "force-if-includes": FORCE,
    "delete": DELETE,
    "mirror": EITHER,
    "prune": DELETE,
}


def _push_token(tok):
    """Return ((kind, reason) or None, next token is this option's value)."""
    if tok.startswith("--"):
        name = tok[2:].split("=", 1)[0]
        if not name:
            return None, False
        for full in _DANGEROUS_LONG:
            if full.startswith(name):
                return (_LONG_KIND[full], f"`{tok}` (git reads it as --{full})"), False
        return None, (name in _LONG_WITH_VALUE and "=" not in tok)
    if tok.startswith("-") and len(tok) > 1:
        for k, ch in enumerate(tok[1:]):
            if ch == "f":
                return (FORCE, f"`{tok}` (-f forces the push)"), False
            if ch == "d":
                return (DELETE, f"`{tok}` (-d deletes the remote ref)"), False
            if ch == "o":  # -o takes a value: the rest, or the next token
                return None, k == len(tok) - 2
        return None, False
    if tok.startswith("+"):
        return (FORCE, f"refspec `{tok}` (a leading + forces the push)"), False
    if tok.startswith(":") and len(tok) > 1:
        return (DELETE, f"refspec `{tok}` (an empty source deletes the remote ref)"), False
    return None, False


def _on(reason, ctx):
    """A (kind, text) finding counts unless its kind is switched off."""
    return bool(reason) and (reason[0] == EITHER or reason[0] in ctx["enabled"])


def _opaque(tok):
    """A word whose value only exists at run time: $F, ${F}, $( ), backticks.

    Known gap (the Balancia copy has it too): a `$F` AFTER the remote is
    allowed, because there it is almost always a branch name
    (`origin "$BRANCH"`), and blocking it would refuse everyday pushes. If
    that `$F` expands to `-f` at run time, git still reads it as a flag and
    the push is forced. The backstop is server-side: in the luxtelos repos,
    GitHub branch rules refuse a force push to dev, main and pre_prod. A
    project using this plugin should protect its own long-lived branches the
    same way; this hook is the floor, not the only guard.
    """
    if tok.startswith("$") or _PH_RE.match(tok):
        return True
    # `-$X` / `--$X` could also expand to a flag.
    return tok.startswith("-") and ("$" in tok or bool(_PH_RE.search(tok)))


def _push_args_reason(words, start, ctx, lead_only=False):
    """Check words[start:] as `git push` arguments.

    lead_only: an earlier `git push` in the same argv already checked every
    token from here on (token checks depend only on the token before), so
    only the before-the-remote rule needs re-running; that keeps the check
    linear on inputs with thousands of `git push` words.
    """
    skip = False
    positional_seen = False
    for idx in range(start, len(words)):
        arg = words[idx]
        if skip:
            skip = False
            continue
        if arg == "--":
            positional_seen = True
            continue
        if _opaque(arg):
            if not positional_seen:
                # Fail closed (review round 2, 2026-10-03): before the remote
                # an option can stand, and `$F` may expand to -f. After the
                # remote it is a refspec name (`origin "$BRANCH"`), allowed.
                return (
                    EITHER,
                    f"`{arg}` is expanded only at run time, where a push option "
                    "can stand; it may expand to -f or --delete",
                )
        elif not arg.startswith("-"):
            if lead_only and positional_seen:
                return None
            positional_seen = True
        if lead_only:
            _, skip = _push_token(arg)
            continue
        nxt = False
        for cand in _brace_variants(arg):
            reason, val = _push_token(cand)
            if _on(reason, ctx):
                return reason
            nxt = nxt or (cand == arg and val)
        skip = nxt
        # `$(echo -f)` as an argument: read what the substitution prints.
        for ph in _PH_RE.findall(arg):
            for inner in ctx["subs"].get(ph, []):
                for word in _words(inner):
                    reason, _ = _push_token(word)
                    if _on(reason, ctx):
                        return (reason[0], reason[1] + " inside a $( ) argument")
    return None


def _git_subcommand(words, j):
    while j < len(words):
        w = words[j]
        if w in _GIT_GLOBAL_WITH_VALUE:
            j += 2
        elif w.startswith("-"):
            j += 1
        else:
            return j
    return None


def _is_shellish(words):
    return any(os.path.basename(w) in _SHELLS for w in words)


def _data_net(text):
    """True where dev's regex would match: a flag after `git push`, same line.

    Linear: each line is searched once, from its FIRST `git push` (a flag
    after any later `git push` on that line is also after the first one).
    """
    scanned_to = -1
    for m in _DATA_PUSH.finditer(text):
        if m.end() <= scanned_to:
            continue
        eol = text.find("\n", m.end())
        eol = len(text) if eol < 0 else eol
        if _DATA_FLAG.search(text, m.end(), eol):
            return (EITHER, "quoted text or a heredoc that names a force push or delete")
        scanned_to = eol
    return None


def _argv_reason(words, ctx, depth):
    covered = False
    for idx, w in enumerate(words):
        if os.path.basename(w) == "git":
            sub = _git_subcommand(words, idx + 1)
            if sub is not None and words[sub] == "push":
                reason = _push_args_reason(words, sub + 1, ctx, lead_only=covered)
                covered = True
                if reason:
                    return reason
    shellish = _is_shellish(words)
    for k, w in enumerate(words):
        if not any(ch in w for ch in " \t\n"):
            continue
        if shellish or (k > 0 and words[k - 1] == "-c"):
            reason = _text_reason(w, ctx, depth + 1)
        else:
            reason = _data_net(w)
        if reason:
            return reason
    return None


def _text_reason(text, ctx, depth=0):
    if depth > _MAX_DEPTH:
        return (EITHER, "commands nested too deep to read") if _mentions_push(text) else None
    try:
        scan = _Scan(text, ctx).run()
        for piece, holder in scan.pieces:
            words = _words(piece)
            reason = _argv_reason(words, ctx, depth)
            if reason:
                return reason
            for body in holder["heredocs"]:
                if _is_shellish(words):
                    reason = _text_reason(body, ctx, depth + 1)
                else:
                    reason = _data_net(body)
                if reason:
                    return reason
    except _Unparsable as exc:
        if _mentions_push(text):
            return (
                EITHER,
                f"cannot read the shell quoting ({exc}) of a command that mentions "
                "git push; refusing rather than guessing",
            )
    return None


def push_danger(command: str, enabled=ALL_KINDS):
    """(kind, reason) if this command force-pushes or deletes a remote ref.

    `enabled` is the set of kinds still switched on ("force", "delete"). With
    both off the check is skipped; an EITHER finding blocks while any is on.
    """
    enabled = frozenset(enabled) & ALL_KINDS
    if not enabled or not _mentions_push(command):
        return None
    ctx = {"subs": {}, "counter": count(), "enabled": enabled}
    return _text_reason(command, ctx)


def push_danger_reason(command: str):
    """Why this command force-pushes or deletes a remote ref, or None.

    Same name and meaning as the Balancia copy, so its tests run unchanged.
    """
    found = push_danger(command)
    return found[1] if found else None
