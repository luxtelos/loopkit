# VENDORED verbatim from the origin project at commit 48d75b05 (2026-08-02 build),
# scripts/okf_bundle.py. Do not edit here: fix upstream, re-vendor, keep this header.
#!/usr/bin/env python3
"""okf_bundle.py — the Open Knowledge Format v0.2 model for `knowledge/`.

Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md

This module owns the FORMAT. `knowledge_actor.py` owns the PROTOCOL. Nothing
here knows about mailboxes, messages or locks.

Two deliberate constraints:

1.  **Stdlib only.** PyYAML is not installed (Python 3.14, no venv) and every
    existing hook in this repo is stdlib-only. So this ships its own frontmatter
    parser.

2.  **The parser refuses rather than guesses.** It supports exactly the YAML
    subset the bundle uses — scalars, quoted strings, block scalars, flat lists,
    lists of maps, and nested maps — and raises `FrontmatterError` on anything
    else. A parser that guesses at an unsupported construct silently corrupts a
    human-ratified concept on the next write, which is the one failure this
    whole system exists to prevent. Refusing turns that into a dead-letter.

Round-trip idempotence is a tested invariant:

    emit(parse(x)) == emit(parse(emit(parse(x))))
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

OKF_VERSION = "0.2"

#: Reserved filenames. Generated, never authored, and exempt from the `type` rule.
RESERVED_NAMES = ("index.md", "log.md")

#: Frontmatter key order on emit. Keys not listed sort after these,
#: alphabetically, so output is stable no matter what order a message supplied.
CANONICAL_KEY_ORDER = (
    "type",
    "title",
    "description",
    "resource",
    "tags",
    "status",
    "stale_after",
    "okf_version",
    "sources",
    "usage_window",
    "generated",
    "verified",
    "superseded_by",
)

#: Key order INSIDE known sub-structures. CANONICAL_KEY_ORDER is a top-level
#: concept ordering; applying it to a `sources` entry would sort `title` above
#: `id`, which reads as noise. Anything not listed here sorts alphabetically.
SUBKEY_ORDER: dict[str, tuple[str, ...]] = {
    "sources": ("id", "resource", "title", "author", "usage_count", "last_modified"),
    "generated": ("by", "at"),
    "verified": ("by", "at"),
    "usage_window": ("from", "to"),
    "x_source_digests": (
        "id",
        "resource",
        "kind",
        "digest",
        "captured_at",
        "captured_commit",
        "granularity",
    ),
    "x_drift": ("kind", "detected_at", "findings"),
    "findings": (
        "source_id",
        "resource",
        "recorded_digest",
        "observed_digest",
        "recorded_at",
        "observed_commit",
    ),
}

#: Strings longer than this are emitted as folded block scalars rather than one
#: long escaped line. OKF exists to be read without tooling; a 200-character
#: quoted string with `\"` escapes defeats that.
FOLD_THRESHOLD = 72
FOLD_WIDTH = 76

VALID_STATUS = ("draft", "stable", "deprecated")

#: OKF's actor convention: `human:<id>`, `process:<id>`, or `<producer>/<version>`.
ACTOR_RE = re.compile(r"^(human:[\w.@+-]+|process:[\w./+-]+|[\w.-]+/[\w.-]+)$")

_FENCE = "---"


class FrontmatterError(ValueError):
    """Raised when frontmatter is absent, malformed, or uses unsupported YAML.

    Always carries a human-readable reason — it is surfaced verbatim in
    dead-letter files, so it has to explain itself to somebody at 2am.
    """


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _strip_comment(value: str) -> str:
    """Drop a trailing ` #comment`, respecting quotes.

    Only fires on a `#` preceded by whitespace, so `sha256:ab#cd` and
    `git-blob:1234#` survive intact.
    """
    out: list[str] = []
    quote: str | None = None
    prev_space = True  # a `#` in column 0 is a comment
    for ch in value:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            prev_space = False
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            prev_space = False
            continue
        if ch == "#" and prev_space:
            break
        out.append(ch)
        prev_space = ch in " \t"
    return "".join(out).rstrip()


def _parse_scalar(raw: str, *, where: str) -> Any:
    """Parse a YAML scalar. Deliberately narrow.

    Note `true`/`false`/`null` and bare integers are recognised, but a date-like
    bare token (`2026-08-02`) stays a string — OKF dates are compared as text,
    and turning them into anything else would break byte round-tripping.
    """
    raw = raw.strip()
    if raw == "":
        return ""
    if raw[0] in "\"'":
        quote = raw[0]
        if len(raw) < 2 or raw[-1] != quote:
            raise FrontmatterError(f"{where}: unterminated {quote} string: {raw!r}")
        inner = raw[1:-1]
        if quote == '"':
            # Only the escapes we actually emit. Anything else is a refusal.
            if re.search(r'\\(?![\\"n])', inner):
                raise FrontmatterError(
                    f"{where}: unsupported backslash escape in double-quoted string: {raw!r}"
                )
            inner = inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        elif "''" in inner:
            inner = inner.replace("''", "'")
        return inner
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    if raw in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    for bad in ("&", "*", "!!"):
        if raw.startswith(bad):
            raise FrontmatterError(
                f"{where}: YAML anchors/aliases/tags are not supported: {raw!r}"
            )
    # Empty flow collections are accepted because other producers emit them.
    # This emitter never does — it drops empty collections entirely — but a
    # parser that refuses `[]` would reject perfectly ordinary OKF written by
    # anything else, and the spec requires tolerating what other tools produce.
    if raw == "[]":
        return []
    if raw == "{}":
        return {}
    if raw.startswith("[") or raw.startswith("{"):
        raise FrontmatterError(
            f"{where}: non-empty inline flow collections are not supported; "
            f"use block style: {raw!r}"
        )
    return raw


@dataclass
class _Line:
    indent: int
    text: str
    number: int


def _tokenize(block: str) -> list[_Line]:
    lines: list[_Line] = []
    for i, raw in enumerate(block.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise FrontmatterError(
                f"line {i}: tab used for indentation; YAML forbids it — use spaces"
            )
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(_Line(len(raw) - len(raw.lstrip(" ")), raw.strip(), i))
    return lines


def _parse_block_scalar(
    lines: list[_Line], idx: int, parent_indent: int, style: str
) -> tuple[str, int]:
    """Consume a `>` (folded) or `|` (literal) block scalar body."""
    body: list[str] = []
    i = idx
    while i < len(lines) and lines[i].indent > parent_indent:
        body.append(lines[i].text)
        i += 1
    if style == "|":
        return "\n".join(body), i
    return " ".join(body), i


def _parse_mapping(lines: list[_Line], idx: int, indent: int) -> tuple[dict, int]:
    out: dict[str, Any] = {}
    i = idx
    while i < len(lines):
        line = lines[i]
        if line.indent < indent:
            break
        if line.indent > indent:
            raise FrontmatterError(
                f"line {line.number}: unexpected indent (got {line.indent}, expected {indent})"
            )
        if line.text.startswith("- "):
            break
        if ":" not in line.text:
            raise FrontmatterError(
                f"line {line.number}: expected `key: value`, got {line.text!r}"
            )
        key, _, rest = line.text.partition(":")
        key = key.strip()
        if not key:
            raise FrontmatterError(f"line {line.number}: empty key")
        rest = _strip_comment(rest.strip())
        where = f"line {line.number}"
        i += 1

        if rest in (">", "|", ">-", "|-"):
            value, i = _parse_block_scalar(lines, i, line.indent, rest[0])
            out[key] = value
            continue
        if rest:
            out[key] = _parse_scalar(rest, where=where)
            continue

        # Empty value: a nested block, or an explicit empty.
        if i >= len(lines) or lines[i].indent <= line.indent:
            out[key] = None
            continue
        if lines[i].text.startswith("- "):
            out[key], i = _parse_sequence(lines, i, lines[i].indent)
        else:
            out[key], i = _parse_mapping(lines, i, lines[i].indent)
    return out, i


def _parse_sequence(lines: list[_Line], idx: int, indent: int) -> tuple[list, int]:
    out: list[Any] = []
    i = idx
    while i < len(lines):
        line = lines[i]
        if line.indent < indent or not line.text.startswith("- "):
            break
        item = _strip_comment(line.text[2:].strip())
        where = f"line {line.number}"
        child_indent = line.indent + 2
        i += 1

        if ":" in item and not item[0] in "\"'":
            # `- key: value` starts a mapping whose remaining keys are indented.
            head, _, rest = item.partition(":")
            rest = _strip_comment(rest.strip())
            entry: dict[str, Any] = {}
            if rest in (">", "|", ">-", "|-"):
                entry[head.strip()], i = _parse_block_scalar(
                    lines, i, line.indent, rest[0]
                )
            elif rest:
                entry[head.strip()] = _parse_scalar(rest, where=where)
            else:
                entry[head.strip()] = None
            if i < len(lines) and lines[i].indent >= child_indent:
                tail, i = _parse_mapping(lines, i, lines[i].indent)
                entry.update(tail)
            out.append(entry)
            continue

        if item:
            out.append(_parse_scalar(item, where=where))
            continue

        if i < len(lines) and lines[i].indent >= child_indent:
            nested, i = _parse_mapping(lines, i, lines[i].indent)
            out.append(nested)
        else:
            out.append(None)
    return out, i


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split `text` into (frontmatter mapping, body).

    Raises FrontmatterError if the fence is missing or the YAML subset is
    exceeded. Never returns a partially-understood mapping.
    """
    if not text.startswith(_FENCE):
        raise FrontmatterError("file does not open with a `---` frontmatter fence")
    rest = text[len(_FENCE) :]
    if rest[:1] not in ("\n", "\r"):
        raise FrontmatterError("`---` fence must be alone on the first line")
    rest = rest.lstrip("\r").lstrip("\n")
    match = re.search(r"^---[ \t]*$", rest, flags=re.MULTILINE)
    if match is None:
        raise FrontmatterError("frontmatter is never closed by a `---` line")
    block, body = rest[: match.start()], rest[match.end() :]
    body = body.lstrip("\r").lstrip("\n")

    lines = _tokenize(block)
    if not lines:
        return {}, body
    mapping, consumed = _parse_mapping(lines, 0, lines[0].indent)
    if consumed != len(lines):
        leftover = lines[consumed]
        raise FrontmatterError(
            f"line {leftover.number}: could not parse {leftover.text!r} "
            "(unsupported YAML construct)"
        )
    return mapping, body


# --------------------------------------------------------------------------
# Emitting
# --------------------------------------------------------------------------

_PLAIN_SAFE = re.compile(r"^[A-Za-z_./][\w .,/@+()'-]*$")


def _emit_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if "\n" in text:
        raise FrontmatterError(
            "multi-line scalars must be emitted as block scalars, not inline"
        )
    # Quote anything that could be misread: reserved words, date-likes, digits,
    # leading/trailing space, or characters outside the plain-safe set.
    if (
        text in ("true", "false", "null", "True", "False", "~")
        or re.fullmatch(r"-?\d+", text)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text)
        or not _PLAIN_SAFE.fullmatch(text)
    ):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _fold(text: str, indent: int) -> list[str]:
    """Word-wrap `text` into folded-block-scalar lines at a fixed width.

    Deterministic by construction, which is what keeps folding idempotent: the
    parser rejoins the lines with single spaces, and re-folding that string
    reproduces exactly these lines. Never emits extra leading whitespace — in a
    folded scalar a more-indented line is preserved literally, which would
    change the value.
    """
    pad = " " * (indent + 2)
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if current and len(candidate) > FOLD_WIDTH:
            lines.append(pad + current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(pad + current)
    return lines


def _should_fold(value: Any) -> bool:
    if not isinstance(value, str) or "\n" in value:
        return False
    if len(value) <= FOLD_THRESHOLD:
        return False
    # Folding round-trips only if the text is plain prose: rejoining on single
    # spaces must reproduce it. Runs of whitespace and leading/trailing space
    # would not survive, so those stay quoted.
    return " ".join(value.split()) == value.strip() == value


def _emit_node(key: str, value: Any, indent: int, out: list[str]) -> None:
    pad = " " * indent
    # Empty collections are DROPPED, not emitted as `[]` / `{}`. For OKF,
    # absent and empty mean the same thing, and emitting flow syntax the
    # block-style parser then has to special-case is how an emit/parse
    # asymmetry gets into the format.
    if isinstance(value, (dict, list)) and not value:
        return
    if isinstance(value, dict):
        out.append(f"{pad}{key}:")
        for k, v in _ordered(value, key):
            _emit_node(k, v, indent + 2, out)
        return
    if isinstance(value, list):
        out.append(f"{pad}{key}:")
        for item in value:
            if isinstance(item, dict):
                if not item:
                    out.append(f"{pad}  - {{}}")
                    continue
                pairs = _ordered(item, key)
                first_k, first_v = pairs[0]
                if isinstance(first_v, (dict, list)):
                    raise FrontmatterError(
                        f"{key}: a list item may not open with a nested collection ({first_k})"
                    )
                out.append(f"{pad}  - {first_k}: {_emit_scalar(first_v)}")
                for k, v in pairs[1:]:
                    _emit_node(k, v, indent + 4, out)
            else:
                out.append(f"{pad}  - {_emit_scalar(item)}")
        return
    if isinstance(value, str) and "\n" in value:
        out.append(f"{pad}{key}: |")
        for line in value.split("\n"):
            out.append(f"{pad}  {line}" if line else "")
        return
    if _should_fold(value):
        out.append(f"{pad}{key}: >")
        out.extend(_fold(value, indent))
        return
    out.append(f"{pad}{key}: {_emit_scalar(value)}")


def _ordered(mapping: dict, parent: str | None = None) -> list[tuple[str, Any]]:
    """Deterministic key order: known keys first, then the rest alphabetically.

    At the top level that means CANONICAL_KEY_ORDER; inside a known
    sub-structure it means that structure's own order (SUBKEY_ORDER). Extension
    keys (`x_source_digests`, `x_drift`) land in the alphabetical tail, which is
    where they belong — OKF requires consumers to tolerate unknown keys, and the
    `x_` prefix guarantees no collision with a future official one.
    """
    order = CANONICAL_KEY_ORDER if parent is None else SUBKEY_ORDER.get(parent, ())
    known = [(k, mapping[k]) for k in order if k in mapping]
    rest = sorted((k, v) for k, v in mapping.items() if k not in order)
    return known + rest


def emit_frontmatter(mapping: dict) -> str:
    """Render `mapping` as a canonical `---` frontmatter block (trailing newline)."""
    out: list[str] = [_FENCE]
    for key, value in _ordered(mapping):
        _emit_node(key, value, 0, out)
    out.append(_FENCE)
    return "\n".join(out) + "\n"


def render(mapping: dict, body: str) -> str:
    """Render a whole concept file: frontmatter + a blank line + normalised body."""
    return emit_frontmatter(mapping) + "\n" + normalize_body(body)


def normalize_body(body: str) -> str:
    """One trailing newline, no trailing whitespace, no CRLF.

    Byte-determinism starts here: if the body is not normalised on write, a
    regenerated copy will differ from disk on invisible characters and
    `reindex --check` will fail for reasons nobody can see in a diff.
    """
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip("\n") + "\n"


# --------------------------------------------------------------------------
# Concepts
# --------------------------------------------------------------------------


@dataclass
class Concept:
    """One OKF concept file: its bundle path, frontmatter and body."""

    path: str  # bundle-relative, always starts with "/" e.g. "/loop/foo.md"
    frontmatter: dict = field(default_factory=dict)
    body: str = ""

    def render(self) -> str:
        return render(self.frontmatter, self.body)

    @property
    def trust_tier(self) -> str:
        return trust_tier(self.frontmatter)

    @property
    def is_pointer(self) -> bool:
        """A concept with `resource:` points at canonical text held elsewhere."""
        return bool(self.frontmatter.get("resource"))


def trust_tier(frontmatter: dict) -> str:
    """Derive OKF's trust tier from `verified`.

    unverified -> machine-confirmed -> human-reviewed. This is the only trust
    signal the format has, which is why the actor must never fabricate a
    `human:` entry.
    """
    verified = frontmatter.get("verified")
    if not verified:
        return "unverified"
    entries = [verified] if isinstance(verified, dict) else verified
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get("by", "")).startswith("human:"):
            return "human-reviewed"
    return "machine-confirmed"


def is_reserved(path: Path | str) -> bool:
    return Path(str(path)).name in RESERVED_NAMES


def resolve_target(bundle: Path, target: str) -> Path:
    """Map a bundle-relative target like `/loop/foo.md` onto a real path.

    Guards, in order: leading slash required, `.md` required, no parent-dir
    escape after resolution, and no symlink in any component. The symlink check
    matters because a resolved path can sit inside the bundle while a symlinked
    component redirects the actual write outside it.
    """
    if not target.startswith("/"):
        raise ValueError(f"target must be bundle-absolute (start with '/'): {target!r}")
    if not target.endswith(".md"):
        raise ValueError(f"target must be a .md file: {target!r}")
    if "\x00" in target:
        raise ValueError("target contains a NUL byte")

    bundle_root = bundle.resolve()

    # Symlink walk BEFORE the escape check. A symlinked component makes
    # `.resolve()` land outside the bundle, so the escape check would catch it
    # too — but with the wrong diagnosis. This message ends up in a dead-letter
    # file that somebody has to act on, so it must name the actual cause.
    walked = bundle_root
    for part in target.strip("/").split("/"):
        walked = walked / part
        if walked.is_symlink():
            raise ValueError(f"target traverses a symlink at {part!r}: {target!r}")

    candidate = (bundle_root / target.lstrip("/")).resolve()
    if candidate != bundle_root and bundle_root not in candidate.parents:
        raise ValueError(f"target escapes the bundle: {target!r}")
    return bundle_root / target.lstrip("/")


def bundle_path_of(bundle: Path, file_path: Path) -> str:
    """Inverse of resolve_target: real path -> `/loop/foo.md`."""
    rel = file_path.resolve().relative_to(bundle.resolve())
    return "/" + rel.as_posix()


def load_concept(bundle: Path, target: str) -> Concept | None:
    path = resolve_target(bundle, target)
    if not path.is_file():
        return None
    frontmatter, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return Concept(path=target, frontmatter=frontmatter, body=body)


def write_concept(bundle: Path, concept: Concept) -> bool:
    """Write atomically. Returns True if bytes changed, False if already identical.

    The byte comparison is load-bearing: it is the second, independent
    idempotency layer. Even with the ledger wiped, replaying every message
    re-converges on the same bundle and produces no spurious log entries.
    """
    path = resolve_target(bundle, concept.path)
    rendered = concept.render()
    if path.is_file() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, rendered)
    return True


def write_text_atomic(path: Path, text: str) -> None:
    """tmp + os.replace, so a crash mid-write never leaves a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".okf-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def iter_concepts(bundle: Path) -> Iterator[Path]:
    """Every non-reserved .md in the bundle, in stable sorted order."""
    if not bundle.is_dir():
        return
    for path in sorted(bundle.rglob("*.md")):
        if not is_reserved(path):
            yield path


def iter_indexes(bundle: Path) -> Iterator[Path]:
    if not bundle.is_dir():
        return
    yield from sorted(bundle.rglob("index.md"))


# --------------------------------------------------------------------------
# Conformance
# --------------------------------------------------------------------------


def conformance_errors(
    bundle: Path, *, require_sources: bool = False, root: Path | None = None
) -> list[str]:
    """OKF v0.2 conformance plus this bundle's own house rules.

    Spec conformance: every non-reserved .md parses and carries a non-empty
    `type`; reserved files follow their structure.

    House rules, because the spec deliberately leaves them open and we need them
    machine-checked: known `status` values, well-formed actor strings, and
    bundle-absolute source paths. With --require-sources, a `stable` concept
    must name its evidence — partial cover for the one failure drift detection
    cannot see, namely a claim resting on a file nobody listed.
    """
    errors: list[str] = []
    if not bundle.is_dir():
        return [f"bundle directory does not exist: {bundle}"]

    for path in sorted(bundle.rglob("*.md")):
        rel = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            errors.append(f"{rel}: is a symlink; the bundle must be plain files")
            continue
        try:
            frontmatter, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except FrontmatterError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        except UnicodeDecodeError as exc:
            errors.append(f"{rel}: not valid UTF-8 ({exc})")
            continue

        node_type = frontmatter.get("type")
        if not isinstance(node_type, str) or not node_type.strip():
            errors.append(f"{rel}: frontmatter must carry a non-empty `type`")

        status = frontmatter.get("status")
        if status is not None and status not in VALID_STATUS:
            errors.append(
                f"{rel}: status {status!r} is not one of {', '.join(VALID_STATUS)}"
            )

        errors.extend(_actor_errors(rel, frontmatter))
        errors.extend(_source_errors(rel, frontmatter, root))

        if is_reserved(path):
            continue

        if require_sources and status == "stable" and not frontmatter.get("sources"):
            errors.append(
                f"{rel}: status is `stable` but no `sources` — ratified knowledge "
                "must name its evidence, or drift detection is blind to it"
            )

    root_index = bundle / "index.md"
    if root_index.is_file():
        try:
            frontmatter, _ = parse_frontmatter(root_index.read_text(encoding="utf-8"))
            declared = frontmatter.get("okf_version")
            if declared is not None and str(declared) != OKF_VERSION:
                errors.append(
                    f"index.md: okf_version is {declared!r}, this actor implements {OKF_VERSION!r}"
                )
        except FrontmatterError:
            pass  # already reported above

    return errors


def _actor_errors(rel: str, frontmatter: dict) -> list[str]:
    errors: list[str] = []
    generated = frontmatter.get("generated")
    if isinstance(generated, dict) and "by" in generated:
        if not ACTOR_RE.fullmatch(str(generated["by"])):
            errors.append(f"{rel}: generated.by {generated['by']!r} is not a valid actor")
    verified = frontmatter.get("verified")
    entries = [verified] if isinstance(verified, dict) else (verified or [])
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append(f"{rel}: verified entries must be mappings, got {entry!r}")
            continue
        if "by" not in entry or "at" not in entry:
            errors.append(f"{rel}: verified entry needs both `by` and `at`: {entry!r}")
            continue
        if not ACTOR_RE.fullmatch(str(entry["by"])):
            errors.append(f"{rel}: verified.by {entry['by']!r} is not a valid actor")
    return errors


def _source_errors(rel: str, frontmatter: dict, root: Path | None = None) -> list[str]:
    errors: list[str] = []
    sources = frontmatter.get("sources") or []
    if isinstance(sources, dict):
        sources = [sources]
    for entry in sources:
        if not isinstance(entry, dict):
            errors.append(f"{rel}: sources entries must be mappings, got {entry!r}")
            continue
        resource = entry.get("resource")
        if not resource:
            errors.append(f"{rel}: every source needs a `resource`: {entry!r}")
            continue
        resource = str(resource)
        if resource.startswith("/Users/") or resource.startswith("/home/"):
            # The .claude/agents/mempalace.md lesson: a knowledge artifact that
            # stores a machine-local absolute path is broken on every other box.
            errors.append(
                f"{rel}: source resource {resource!r} is a machine-local absolute "
                "path; use a repo-relative path"
            )
            continue
        # A DIRECTORY has no blob digest, so drift detection reports it
        # "missing" forever — an un-clearable false positive that trains people
        # to ignore drift. Caught here at author time instead.
        # A *missing file* is deliberately NOT an error: that is real drift, and
        # scan-drift reporting it is the system working.
        if root is not None and "://" not in resource:
            if (root / resource).is_dir():
                errors.append(
                    f"{rel}: source resource {resource!r} is a directory; cite a "
                    "specific file — a directory has no digest and drifts forever"
                )
    return errors


# --------------------------------------------------------------------------
# Digests
# --------------------------------------------------------------------------


def blob_digest(path: Path) -> str | None:
    """`git hash-object` of a file, or None if it is missing.

    Same primitive scripts/provenance.sh already uses — no new dependency, and a
    human can cross-check any digest by hand.
    """
    if not path.is_file():
        return None
    try:
        result = subprocess.run(
            ["git", "hash-object", "--", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return "git-blob:" + result.stdout.strip()


def repo_root(start: Path | None = None) -> Path:
    """The working tree root.

    `--show-toplevel`, not `--git-common-dir`: this repo is a git submodule, so
    the common dir is `.../.git/modules/ledgermind`, which is not a working tree
    at all. The mailbox is tracked and per-branch by design — messages travel
    with the PR and git is the concurrency control.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start or Path.cwd()),
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return Path(start or Path.cwd()).resolve()
