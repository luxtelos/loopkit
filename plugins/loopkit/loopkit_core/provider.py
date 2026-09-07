#!/usr/bin/env python3
"""provider.py — the Provider contract and its three reference implementations.

Runtime plan §M2 (`docs/research/runtime-plan.md`); spec criteria 22-25 of
`specs/loopkit-runtime.md`.

THE CONTRACT

    complete(messages, tools, response_schema) -> {"text", "tool_calls", "usage"}

`text` is a string, possibly empty. `tool_calls` is a list, possibly empty, of
`{"name": str, "arguments": object}`. `usage` is either a mapping carrying
`input_tokens` and `output_tokens` as integers, or `None`.

WHY `usage` MAY BE `None` -- AND WHERE THAT DISAGREES WITH THE SPEC

A provider must never report usage it did not receive. If an upstream response
carries no usage at all, this module returns `usage=None`, not
`{"input_tokens": 0, "output_tokens": 0}`. A fabricated zero is
indistinguishable from a genuinely free call, so it silently corrupts the
tokens-per-task metric the runtime plan exists to measure -- and it corrupts it
downward, which is the direction nobody audits.

Criterion 23 of the spec currently says the opposite: usage "SHALL use 0 --
never null, never a missing key -- when the upstream API reports no count."
Both rules cannot hold. This module implements absent-not-zero and does NOT
touch the spec; the disagreement is for a human to settle. Until then:

  * the `usage` KEY is always present in the returned mapping, so the
    "carries all three keys" half of criterion 23 holds either way;
  * `usage_or_zero(result)` below is the one-line adapter for a caller that
    wants criterion 23's shape, so neither reading is blocked by the other;
  * `usage_is_reported(result)` is how a metric decides to skip a call rather
    than add zero to its denominator.

The disagreement is routed to the owner as Q5 of `inbox/needs-human.md`, with
the reasoning and both costs. This module does NOT edit `specs/`.

PARTIAL USAGE -- ALL OR NOTHING, AND WHY THE TWO HALVES DIFFER

`{"input_tokens": 11}` with no output count is not a smaller measurement; it
is not a measurement. There is no true value for the missing half, and
filling it with 0 is the fabricated zero this whole rule exists to prevent.
So a partial usage is never carried:

  * `_coerce_usage` -- the VENDOR-facing half. A partial (or empty, or
    non-integer, or negative) upstream usage object becomes `None`: absent.
    We cannot fix a vendor's wire format, so recording "not reported" is the
    honest answer and the run continues.
  * `normalise_result` -- the CONTRACT-facing half. A Provider handing this
    module a partial usage is violating the contract, and unlike a vendor the
    offender is reachable, so it RAISES `ProviderResponseError`. Criterion 24
    then journals a `provider_error` and no `result`, which is what makes the
    defect visible instead of silently downgrading it to "absent".

Consequence a caller can rely on: `result["usage"]` is either `None` or a
mapping with BOTH counts as non-negative ints. `usage_is_reported()` is
therefore true only of a real measurement, and `usage_or_zero()` is all-or-
nothing -- it never returns one real count beside a fabricated one.

HOW A FIXTURE CARRIES A STUB SCRIPT

`StubProvider` is the conformance control case: no clock, no network, no
randomness, same inputs -> same bytes. It answers only from a script it was
given. A conformance fixture supplies that script as a top-level
`provider_script` object:

    "provider_script": {
      "by_digest": {
        "3f6c1a...": {"text": "...", "tool_calls": [], "usage": {...}}
      },
      "sequence": [ {"text": "first"}, {"text": "second"} ],
      "default":  {"text": "", "tool_calls": [], "usage": null}
    }

`by_digest` is looked up first and is keyed by `request_digest(...)` below --
a sha256 over the canonical JSON of `(messages, tools, response_schema)`,
truncated to 16 hex characters. It is order-independent, so a fixture that
reorders its steps still resolves. `sequence` is consumed in call order for
scripts that do not care about the request. `default` answers anything left.
A request matching none of the three raises `ProviderScriptMiss` rather than
inventing a reply -- a stub that improvises is not a control case.

Load `StubProvider.from_fixture(fixture_dict)` to build one; it returns `None`
when the fixture has no `provider_script`. AS OF THIS COMMIT NO FIXTURE IN
`spec/fixtures/` CARRIES ONE -- a reviewer flagged that, it is still true, and
this module cannot fix it without touching `spec/fixtures/`, which is out of
scope for this change. Writing those scripts is the next piece of work.

SECRETS

No provider prints anything, ever. API keys and any URL userinfo are scrubbed
from every exception this module raises, and endpoint URLs are reduced to
scheme+host+path before they appear in a message. Pinned by
`--selftest` case P3, which drives every error path against a local mock
endpoint with a sentinel key and asserts the sentinel appears in no exception
text and no stream.

VERIFICATION STATUS (be suspicious of anything not listed here)

  * StubProvider           -- exercised by --selftest, deterministic.
  * OpenAICompatProvider   -- exercised by --selftest against a LOCAL mock
                              `/chat/completions` endpoint over real HTTP.
                              NOT exercised against a vendor endpoint.
  * AnthropicProvider      -- exercised by --selftest against a LOCAL mock
                              `/v1/messages` endpoint over real HTTP.
                              NOT exercised against a vendor endpoint.

Run the pins:  python3 -m loopkit_core.provider --selftest
(from `plugins/loopkit/`, or with that directory on PYTHONPATH)

Python 3.9+, standard library only.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence

# The redactor is shared with store.py rather than copied. Two copies of one
# redactor drift, and the copy that drifts is the one nobody re-reads: that is
# exactly how `S3Store._scrub` ended up two reviews behind this file's defence
# against the same `%r` escaping.
#
# This file is run BOTH ways: `python3 -m loopkit_core.provider --selftest`
# and `python3 plugins/loopkit/loopkit_core/provider.py --selftest`. Under the
# second, sys.path[0] is the package DIRECTORY, so `loopkit_core` is not
# importable until its parent is on the path.
if __package__ in (None, ""):  # pragma: no cover - exercised by the path gate
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from loopkit_core.redact import Redactor, url_secrets  # noqa: E402

__all__ = [
    "Provider",
    "ProviderError",
    "ProviderResponseError",
    "ProviderTransportError",
    "ProviderScriptMiss",
    "StubProvider",
    "OpenAICompatProvider",
    "AnthropicProvider",
    "request_digest",
    "usage_is_reported",
    "usage_or_zero",
    "normalise_result",
]

DEFAULT_TIMEOUT = 60.0


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------
class ProviderError(Exception):
    """Base for every error this module raises. Never carries a secret."""


class ProviderTransportError(ProviderError):
    """The endpoint could not be reached, or answered with a non-2xx status."""


class ProviderResponseError(ProviderError):
    """The endpoint answered, but not in a shape the contract accepts."""


class ProviderScriptMiss(ProviderError):
    """StubProvider was asked something its script does not answer."""


# --------------------------------------------------------------------------
# the protocol
# --------------------------------------------------------------------------
try:  # pragma: no cover - typing.Protocol is 3.8+, runtime_checkable 3.8+
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class Provider(Protocol):
        """complete(messages, tools, response_schema) -> {text, tool_calls, usage}."""

        def complete(
            self,
            messages: Sequence[Mapping[str, Any]],
            tools: Optional[Sequence[Mapping[str, Any]]] = None,
            response_schema: Optional[Mapping[str, Any]] = None,
        ) -> Dict[str, Any]:
            ...

except ImportError:  # pragma: no cover
    class Provider:  # type: ignore[no-redef]
        """Fallback stand-in when typing.Protocol is unavailable."""


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------
def canonical_json(obj: Any) -> str:
    """Stable JSON: sorted keys, no incidental whitespace. Same in, same bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def request_digest(
    messages: Sequence[Mapping[str, Any]],
    tools: Optional[Sequence[Mapping[str, Any]]] = None,
    response_schema: Optional[Mapping[str, Any]] = None,
    length: int = 16,
) -> str:
    """The key a fixture's `by_digest` script uses. Pure; no clock, no salt."""
    payload = canonical_json(
        {
            "messages": list(messages or []),
            "tools": list(tools or []),
            "response_schema": response_schema,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def _is_count(value: Any) -> bool:
    """A token count is a non-negative int. `True` is not a count."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def usage_is_reported(result: Mapping[str, Any]) -> bool:
    """True only when BOTH token counts were really reported.

    A partial usage is not a measurement (see PARTIAL USAGE in the header), so
    it can never reach here: the shape is either complete or `None`. This
    checks both counts anyway, because a caller may hand in a mapping this
    module did not build.
    """
    usage = result.get("usage")
    return (
        isinstance(usage, Mapping)
        and _is_count(usage.get("input_tokens"))
        and _is_count(usage.get("output_tokens"))
    )


def usage_or_zero(result: Mapping[str, Any]) -> Dict[str, int]:
    """Criterion-23 shape for a caller that wants zeros. Opt in deliberately.

    Calling this asserts "for my purpose an unreported count is zero". Do not
    call it from anything that aggregates tokens per task.

    It is all-or-nothing: a mapping that is not a complete measurement yields
    two zeros rather than one real count beside a fabricated one. Mixing the
    two produced a number that looked measured and was not -- the exact
    failure this module exists to prevent.
    """
    if usage_is_reported(result):
        usage = result["usage"]
        return {
            "input_tokens": int(usage["input_tokens"]),
            "output_tokens": int(usage["output_tokens"]),
        }
    return {"input_tokens": 0, "output_tokens": 0}


def _coerce_usage(raw: Any, input_key: str, output_key: str) -> Optional[Dict[str, int]]:
    """Map an UPSTREAM usage object onto the contract, or None if it reported none.

    All or nothing. A usage object carrying only one of the two counts is
    treated as "not reported", exactly like an empty envelope and like no
    envelope at all. Half a measurement cannot be completed honestly -- there
    is no true value for the missing half, and filling it with 0 is the
    fabricated zero this module exists to prevent -- so the only honest
    answer is absent.

    This is the vendor-facing half of the rule; `normalise_result` is the
    contract-facing half and RAISES instead. See PARTIAL USAGE in the header
    for why the two differ.
    """
    if not isinstance(raw, Mapping):
        return None
    got_in = raw.get(input_key)
    got_out = raw.get(output_key)
    if got_in is None or got_out is None:
        return None
    out: Dict[str, int] = {}
    for name, value in (("input_tokens", got_in), ("output_tokens", got_out)):
        if isinstance(value, bool):
            return None
        try:
            count = int(value)
        except (TypeError, ValueError):
            return None
        if count < 0:
            return None
        out[name] = count
    return out


def normalise_result(raw: Any) -> Dict[str, Any]:
    """Validate and normalise one provider answer, or raise ProviderResponseError."""
    if not isinstance(raw, Mapping):
        raise ProviderResponseError("provider result is not a mapping")
    for key in ("text", "tool_calls", "usage"):
        if key not in raw:
            raise ProviderResponseError("provider result is missing key: %s" % key)
    text = raw.get("text")
    if text is None:
        text = ""
    if not isinstance(text, str):
        raise ProviderResponseError("provider result 'text' is not a string")
    calls_raw = raw.get("tool_calls") or []
    if not isinstance(calls_raw, (list, tuple)):
        raise ProviderResponseError("provider result 'tool_calls' is not a list")
    calls: List[Dict[str, Any]] = []
    for entry in calls_raw:
        if not isinstance(entry, Mapping) or "name" not in entry:
            raise ProviderResponseError("tool_call entry must be {name, arguments}")
        calls.append({"name": str(entry["name"]), "arguments": entry.get("arguments")})
    usage = raw.get("usage")
    if usage is not None:
        if not isinstance(usage, Mapping):
            raise ProviderResponseError("provider result 'usage' is not a mapping")
        present = [n for n in ("input_tokens", "output_tokens") if usage.get(n) is not None]
        if not present:
            # An empty envelope is not a measurement. Absent, NEVER {0, 0}:
            # zero-filling here is mutation MUT-R1, and it is pinned red.
            usage = None
        elif len(present) == 1:
            # HALF a measurement. Unlike an upstream wire payload -- which
            # this module cannot fix, so `_coerce_usage` records it as absent
            # -- a provider handing back a partial usage is violating THIS
            # module's contract, and the offender is reachable. Raise, so
            # criterion 24 journals a `provider_error` rather than letting a
            # half-real number into the tokens-per-task metric.
            raise ProviderResponseError(
                "provider result 'usage' reports only '%s': a partial usage is "
                "not a measurement -- report both counts or omit usage entirely"
                % present[0]
            )
        else:
            clean: Dict[str, int] = {}
            for name in ("input_tokens", "output_tokens"):
                value = usage.get(name)
                if not _is_count(value):
                    raise ProviderResponseError(
                        "usage '%s' is not a non-negative integer" % name
                    )
                clean[name] = int(value)
            usage = clean
    return {"text": text, "tool_calls": calls, "usage": usage}


def _url_secrets(url: str) -> List[str]:
    """The userinfo an operator put in a URL, so the redactor can know it too.

    Structural scrubbing (`_safe_url`) is the primary defence; registering
    these with the `Redactor` is the backstop for text this module did not
    compose itself -- an exception raised inside urllib, say.
    """
    return url_secrets(url)


def _safe_url(url: str) -> str:
    """Scheme + host + port. NOTHING else, and this function never raises.

    The PATH is dropped as well as userinfo, query and fragment. That is a
    deliberate widening after the 2026-09-07 hostile review: a secret in a
    URL path (`https://gateway/v1/<key>/chat`) is a real deployment shape,
    it carries no structural marker that would let a redactor find it, and
    the previous version echoed it back verbatim into every transport error.
    A path can only be omitted, never scrubbed. Callers name the endpoint
    with a STATIC label instead -- see `_post_json(..., label=)` -- so the
    diagnostic value is kept without the caller's bytes.

    It also never raises. `urlsplit` is lazy: `parts.port` parses on access
    and throws `ValueError` on a malformed port, which previously escaped
    this helper as a bare non-`ProviderError` from ABOVE the guarded region.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme or ""
        host = parts.hostname or ""
        try:
            port = parts.port
        except ValueError:
            port = None
        if port:
            host = "%s:%d" % (host, port)
    except ValueError:
        return "<url>"
    if not scheme and not host:
        return "<url>"
    return "%s://%s" % (scheme or "?", host or "?")


# `_Redactor` used to live here as a literal-substring scrubber:
#
#     if secret in text: text = text.replace(secret, "***")
#
# Any `%r` defeats that -- a newline in a credential becomes backslash-n, so
# the secret in the text is no longer the secret in memory. It has been
# replaced by the shared, escape-robust `Redactor` in loopkit_core/redact.py,
# which matches the parts of a secret that survive ANY character-escaping
# instead of enumerating escapers. Pinned by P9.
_Redactor = Redactor


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    """Never route a keyed request through an ambient proxy from the environment."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# --------------------------------------------------------------------------
# StubProvider
# --------------------------------------------------------------------------
class StubProvider:
    """Deterministic, scripted, offline. The conformance control case.

    No clock, no network, no randomness: two identical calls return identical
    bytes, and a request the script does not answer raises rather than guesses.
    """

    def __init__(
        self,
        by_digest: Optional[Mapping[str, Mapping[str, Any]]] = None,
        sequence: Optional[Sequence[Mapping[str, Any]]] = None,
        default: Optional[Mapping[str, Any]] = None,
        name: str = "stub",
    ) -> None:
        self.name = name
        self._by_digest = {str(k): dict(v) for k, v in dict(by_digest or {}).items()}
        self._sequence = [dict(item) for item in (sequence or [])]
        self._default = dict(default) if default is not None else None
        self._cursor = 0
        self.calls: List[Dict[str, Any]] = []

    @classmethod
    def from_fixture(cls, fixture: Mapping[str, Any], name: str = "stub") -> Optional["StubProvider"]:
        """Build from a fixture's `provider_script`, or None when it has none."""
        script = fixture.get("provider_script") if isinstance(fixture, Mapping) else None
        if not isinstance(script, Mapping):
            return None
        return cls(
            by_digest=script.get("by_digest") or {},
            sequence=script.get("sequence") or [],
            default=script.get("default"),
            name=name,
        )

    @classmethod
    def from_script(cls, script: Mapping[str, Any], name: str = "stub") -> "StubProvider":
        return cls(
            by_digest=script.get("by_digest") or {},
            sequence=script.get("sequence") or [],
            default=script.get("default"),
            name=name,
        )

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        response_schema: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        digest = request_digest(messages, tools, response_schema)
        self.calls.append({"digest": digest})
        if digest in self._by_digest:
            return normalise_result(self._by_digest[digest])
        if self._cursor < len(self._sequence):
            answer = self._sequence[self._cursor]
            self._cursor += 1
            return normalise_result(answer)
        if self._default is not None:
            return normalise_result(self._default)
        raise ProviderScriptMiss(
            "stub has no scripted answer for request digest %s "
            "(add it to provider_script.by_digest)" % digest
        )

    def reset(self) -> None:
        """Rewind the sequence cursor so a replay starts from the same state."""
        self._cursor = 0
        self.calls = []


# --------------------------------------------------------------------------
# HTTP providers
# --------------------------------------------------------------------------
class _HttpProvider:
    """Shared urllib plumbing: no logging, no ambient proxy, scrubbed errors."""

    def __init__(self, api_key: Optional[str], base_url: str, timeout: float) -> None:
        # The redactor knows the key AND any userinfo the operator put in the
        # base URL. Registering the URL's credentials is what makes the
        # scrubber cover text this module did not compose -- an exception
        # raised inside urllib, for instance.
        self._redact = Redactor([api_key] + _url_secrets(base_url))
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # REFUSE every scheme but http(s), decided rather than left open
        # (2026-09-07 review). `build_opener` keeps urllib's default handler
        # set, `FileHandler` and `FTPHandler` included, so a `file://` base URL
        # makes `complete()` READ A LOCAL FILE and return its contents as model
        # output -- no network call, no error, and `_safe_url` renders it as
        # `file://?`, which hides WHICH file. An LLM endpoint is http(s) by
        # definition, so nothing legitimate is lost. Checked here rather than
        # in each subclass: both of them end __init__ with this super() call,
        # so this is the one place every base URL passes through.
        scheme = ""
        try:
            scheme = urllib.parse.urlsplit(self.base_url).scheme
        except ValueError:  # pragma: no cover - urlsplit is lazy, but be safe
            scheme = ""
        if scheme not in ("http", "https"):
            raise ProviderError(
                self._redact.scrub(
                    "base URL scheme %r is not supported: providers speak http "
                    "and https only. urllib would otherwise serve file:// and "
                    "ftp:// from its default handlers and return local bytes as "
                    "model output." % scheme
                )
            )
        self._opener = _no_proxy_opener()

    def _post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        label: str = "endpoint",
    ) -> Any:
        """POST JSON. NOTHING derived from `url`, `headers` or `body` escapes.

        `label` is a STATIC string supplied by this module (never caller
        data); `where` is scheme+host+port only. Everything -- including the
        `Request` construction and the JSON encoding -- happens inside the
        guarded region, because both can raise carrying their input:
        `Request("//user:pw@host/x")` raises `ValueError: unknown url type:
        '//user:pw@host/x'`, which before 2026-09-07 was raised from ABOVE
        this `try` and reached a human unscrubbed and not even wrapped as a
        `ProviderError`.
        """
        for secret in _url_secrets(url):
            self._redact.add(secret)
        where = _safe_url(url)
        try:
            data = json.dumps(body).encode("utf-8")
            request = urllib.request.Request(url, data=data, method="POST")
            for header, value in headers.items():
                request.add_header(header, value)
            request.add_header("Content-Type", "application/json")
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:  # non-2xx
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # pragma: no cover - body already consumed
                detail = ""
            raise ProviderTransportError(
                self._redact.scrub(
                    "%s %s returned HTTP %s: %s" % (where, label, exc.code, detail)
                )
            ) from None
        except urllib.error.URLError as exc:
            raise ProviderTransportError(
                self._redact.scrub("%s %s unreachable: %s" % (where, label, exc.reason))
            ) from None
        except TypeError as exc:
            # An unserialisable request body. Its repr can quote the object.
            raise ProviderResponseError(
                self._redact.scrub("%s %s: request body is not JSON: %s" % (where, label, exc))
            ) from None
        except ValueError as exc:
            # `Request(url)` on a URL with no usable scheme raises this and
            # quotes the WHOLE url back, userinfo included. Never re-raise it.
            raise ProviderTransportError(
                self._redact.scrub("%s %s: unusable endpoint URL (%s)" % (where, label, type(exc).__name__))
            ) from None
        except Exception as exc:  # timeouts, socket errors
            # NAME THE TYPE, NEVER THE TEXT. This is the catch-all: by
            # definition it holds the exceptions nobody enumerated, so it is
            # the one branch that must not interpolate an unknown message. The
            # `ValueError` branch above has worked this way since the first
            # review; `store.py`'s equivalent did not, and leaked a whole
            # signed Authorization header for two more reviews.
            raise ProviderTransportError(
                self._redact.scrub(
                    "%s %s failed: %s (message withheld: catch-all)"
                    % (where, label, type(exc).__name__)
                )
            ) from None
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProviderResponseError(
                self._redact.scrub("%s %s returned non-JSON: %s" % (where, label, exc))
            ) from None


def _parse_arguments(raw: Any) -> Any:
    """Tool arguments arrive as a JSON string on some APIs, an object on others."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


class OpenAICompatProvider(_HttpProvider):
    """Any OpenAI-compatible `/chat/completions` endpoint, over urllib.

    Base URL and key come from parameters or the environment
    (`LOOPKIT_OPENAI_BASE_URL` / `OPENAI_BASE_URL`, `LOOPKIT_OPENAI_API_KEY` /
    `OPENAI_API_KEY`, `LOOPKIT_OPENAI_MODEL` / `OPENAI_MODEL`). Never
    hardcoded, never printed, never written to a file.
    """

    name = "openai-compat"

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        env: Optional[Mapping[str, str]] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> None:
        env = os.environ if env is None else env
        resolved_base = (
            base_url
            or env.get("LOOPKIT_OPENAI_BASE_URL")
            or env.get("OPENAI_BASE_URL")
            or ""
        ).strip()
        if not resolved_base:
            raise ProviderError(
                "no OpenAI-compatible base URL: pass base_url= or set "
                "LOOPKIT_OPENAI_BASE_URL"
            )
        resolved_key = api_key or env.get("LOOPKIT_OPENAI_API_KEY") or env.get("OPENAI_API_KEY")
        self.model = (
            model or env.get("LOOPKIT_OPENAI_MODEL") or env.get("OPENAI_MODEL") or ""
        ).strip()
        if not self.model:
            raise ProviderError("no model: pass model= or set LOOPKIT_OPENAI_MODEL")
        self.extra_body = dict(extra_body or {})
        super().__init__(resolved_key, resolved_base, timeout)

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        response_schema: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = dict(self.extra_body)
        body["model"] = self.model
        body["messages"] = [dict(m) for m in messages]
        if tools:
            body["tools"] = [
                {"type": "function", "function": dict(tool)} for tool in tools
            ]
        if response_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": str(response_schema.get("title") or "response"),
                    "schema": dict(response_schema),
                    "strict": True,
                },
            }
        headers = {}
        if self._api_key:
            headers["Authorization"] = "Bearer %s" % self._api_key
        payload = self._post_json(
            self.base_url + "/chat/completions", headers, body, label="POST /chat/completions"
        )
        return self._normalise(payload)

    def _normalise(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ProviderResponseError("chat/completions payload is not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderResponseError("chat/completions payload carries no choices")
        message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
        if not isinstance(message, Mapping):
            raise ProviderResponseError("chat/completions choice carries no message")
        text = message.get("content")
        text = "" if text is None else str(text)
        calls: List[Dict[str, Any]] = []
        for entry in message.get("tool_calls") or []:
            if not isinstance(entry, Mapping):
                continue
            function = entry.get("function") if isinstance(entry.get("function"), Mapping) else entry
            calls.append(
                {
                    "name": str(function.get("name", "")),
                    "arguments": _parse_arguments(function.get("arguments")),
                }
            )
        return {
            "text": text,
            "tool_calls": calls,
            # Absent, never a fabricated zero. See the module header.
            "usage": _coerce_usage(payload.get("usage"), "prompt_tokens", "completion_tokens"),
        }


class AnthropicProvider(_HttpProvider):
    """The Anthropic Messages API, same contract, same urllib plumbing.

    Base URL and key come from parameters or the environment
    (`LOOPKIT_ANTHROPIC_BASE_URL` / `ANTHROPIC_BASE_URL`,
    `LOOPKIT_ANTHROPIC_API_KEY` / `ANTHROPIC_API_KEY`,
    `LOOPKIT_ANTHROPIC_MODEL` / `ANTHROPIC_MODEL`).
    """

    name = "anthropic"
    API_VERSION = "2023-06-01"
    DEFAULT_BASE_URL = "https://api.anthropic.com"

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        timeout: float = DEFAULT_TIMEOUT,
        env: Optional[Mapping[str, str]] = None,
        api_version: Optional[str] = None,
    ) -> None:
        env = os.environ if env is None else env
        resolved_base = (
            base_url
            or env.get("LOOPKIT_ANTHROPIC_BASE_URL")
            or env.get("ANTHROPIC_BASE_URL")
            or self.DEFAULT_BASE_URL
        ).strip()
        resolved_key = (
            api_key or env.get("LOOPKIT_ANTHROPIC_API_KEY") or env.get("ANTHROPIC_API_KEY")
        )
        self.model = (
            model or env.get("LOOPKIT_ANTHROPIC_MODEL") or env.get("ANTHROPIC_MODEL") or ""
        ).strip()
        if not self.model:
            raise ProviderError("no model: pass model= or set LOOPKIT_ANTHROPIC_MODEL")
        self.max_tokens = int(max_tokens)
        self.api_version = api_version or self.API_VERSION
        super().__init__(resolved_key, resolved_base, timeout)

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        response_schema: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        system_parts: List[str] = []
        turns: List[Dict[str, Any]] = []
        for raw in messages:
            message = dict(raw)
            if message.get("role") == "system":
                system_parts.append(str(message.get("content") or ""))
                continue
            turns.append(message)
        body: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": turns,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        declared: List[Dict[str, Any]] = []
        for tool in tools or []:
            tool = dict(tool)
            declared.append(
                {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("input_schema") or tool.get("parameters") or {},
                }
            )
        if response_schema:
            # The Messages API has no response_format; a schema is expressed as a
            # single tool the model is forced to call. Keeps the caller's
            # contract identical across providers.
            declared.append(
                {
                    "name": "emit_response",
                    "description": "Return the answer as structured JSON.",
                    "input_schema": dict(response_schema),
                }
            )
            body["tool_choice"] = {"type": "tool", "name": "emit_response"}
        if declared:
            body["tools"] = declared
        headers = {"anthropic-version": self.api_version}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        payload = self._post_json(
            self.base_url + "/v1/messages", headers, body, label="POST /v1/messages"
        )
        return self._normalise(payload)

    def _normalise(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ProviderResponseError("messages payload is not an object")
        content = payload.get("content")
        if not isinstance(content, list):
            raise ProviderResponseError("messages payload carries no content list")
        chunks: List[str] = []
        calls: List[Dict[str, Any]] = []
        for block in content:
            if not isinstance(block, Mapping):
                continue
            kind = block.get("type")
            if kind == "text":
                chunks.append(str(block.get("text") or ""))
            elif kind == "tool_use":
                calls.append(
                    {
                        "name": str(block.get("name", "")),
                        "arguments": _parse_arguments(block.get("input")),
                    }
                )
        return {
            "text": "".join(chunks),
            "tool_calls": calls,
            # Absent, never a fabricated zero. See the module header.
            "usage": _coerce_usage(payload.get("usage"), "input_tokens", "output_tokens"),
        }


# --------------------------------------------------------------------------
# pins  --  python3 -m loopkit_core.provider --selftest
#
# Every pin below has been shown RED by breaking the mechanism it guards and
# GREEN again after restoring it. A pin that cannot fail is decoration.
# --------------------------------------------------------------------------
_RESULTS: List[str] = []


def _ok(label: str) -> None:
    _RESULTS.append("PASS %s" % label)
    print("PASS %s" % label)


def _bad(label: str, why: str) -> None:
    _RESULTS.append("FAIL %s -- %s" % (label, why))
    print("FAIL %s -- %s" % (label, why))


def _check(label: str, condition: bool, why: str = "") -> None:
    if condition:
        _ok(label)
    else:
        _bad(label, why or "condition false")


_MOCK_KEY_SENTINEL = "sk-PINSENTINEL-do-not-log-0123456789"


def _mock_server():
    """A local HTTP endpoint speaking both wire formats. Not a vendor API."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # keep the pin output clean
            return

        def do_POST(self):  # noqa: N802 - stdlib naming
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            parts = self.path.strip("/").split("/")
            mode = parts[1] if len(parts) > 1 and parts[0] == "m" else "ok"
            auth = self.headers.get("Authorization", "") + self.headers.get("x-api-key", "")
            anthropic = self.path.endswith("/v1/messages")
            if mode == "echo401":
                # The nastiest real-world leak: an upstream that echoes the
                # Authorization header back inside its error body.
                body = json.dumps({"error": {"message": "bad credential: " + auth}})
                self._send(401, body)
                return
            if mode == "badjson":
                self._send(200, "<html>not json</html>")
                return
            if anthropic:
                payload = {
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "tool_use", "name": "grep", "input": {"q": "x"}},
                    ]
                }
                if mode == "emptyusage":
                    payload["usage"] = {}
                elif mode == "partialusage":
                    payload["usage"] = {"input_tokens": 11}
                elif mode == "negusage":
                    payload["usage"] = {"input_tokens": 11, "output_tokens": -7}
                elif mode != "nousage":
                    payload["usage"] = {"input_tokens": 11, "output_tokens": 7}
            else:
                payload = {
                    "choices": [
                        {
                            "message": {
                                "content": "hello",
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "grep",
                                            "arguments": '{"q": "x"}',
                                        }
                                    }
                                ],
                            }
                        }
                    ]
                }
                if mode == "emptyusage":
                    payload["usage"] = {}
                elif mode == "partialusage":
                    payload["usage"] = {"prompt_tokens": 11}
                elif mode == "negusage":
                    payload["usage"] = {"prompt_tokens": 11, "completion_tokens": -7}
                elif mode != "nousage":
                    payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
            self._send(200, json.dumps(payload))

        def _send(self, status: int, body: str) -> None:
            raw = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def _capture(call):
    """Run `call`, returning (result, error_text, stdout+stderr).

    `error_text` carries the FULL formatted traceback, not just `str(exc)`.
    A leak that only appears in a chained `__context__` or in a frame's
    exception line is still a leak reaching a human's terminal, and pinning
    only `str(exc)` is how the 2026-09-07 review found two the pins missed.
    """
    import contextlib
    import traceback

    out = io.StringIO()
    err = io.StringIO()
    result = None
    error_text = ""
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            result = call()
        except BaseException as exc:  # noqa: BLE001 - the pin is about the text
            error_text = "%s: %s\n%s" % (
                type(exc).__name__,
                exc,
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            )
    return result, error_text, out.getvalue() + err.getvalue()


def _pin_stub_deterministic() -> None:
    """PIN P1: same inputs, same bytes. Break `request_digest` to see it red."""
    messages = [{"role": "user", "content": "who broke the build"}]
    script = {
        "by_digest": {
            request_digest(messages): {
                "text": "nobody",
                "tool_calls": [{"name": "grep", "arguments": {"q": "x"}}],
                "usage": {"input_tokens": 3, "output_tokens": 4},
            }
        }
    }
    first = canonical_json(StubProvider.from_script(script).complete(messages))
    second = canonical_json(StubProvider.from_script(script).complete(messages))
    third = canonical_json(StubProvider.from_script(script).complete(messages))
    _check(
        "P1 StubProvider is deterministic (three calls, identical bytes)",
        first == second == third,
        "got %r / %r / %r" % (first, second, third),
    )
    reused = StubProvider.from_script(script)
    _check(
        "P1b one StubProvider answers the same request identically twice",
        canonical_json(reused.complete(messages)) == canonical_json(reused.complete(messages)),
    )


def _pin_stub_refuses_unscripted() -> None:
    """PIN P5: an unscripted request raises. A stub that improvises is not a control."""
    stub = StubProvider(by_digest={"deadbeef": {"text": "", "tool_calls": [], "usage": None}})
    _, error, _ = _capture(lambda: stub.complete([{"role": "user", "content": "unknown"}]))
    _check(
        "P5 StubProvider refuses an unscripted request",
        error.startswith("ProviderScriptMiss"),
        "expected ProviderScriptMiss, got %r" % error,
    )


def _pin_usage_absent_not_zero(base: str) -> None:
    """PIN P2: no provider reports usage it did not receive."""
    stub = StubProvider(default={"text": "hi", "tool_calls": [], "usage": None})
    result = stub.complete([{"role": "user", "content": "x"}])
    _check(
        "P2a stub: unreported usage is absent, not zero",
        result["usage"] is None and "usage" in result,
        "usage was %r" % (result["usage"],),
    )

    openai = OpenAICompatProvider(
        base_url=base + "/m/nousage", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
    )
    result = openai.complete([{"role": "user", "content": "x"}])
    _check(
        "P2b openai-compat: a response with no usage yields absent, not zero",
        result["usage"] is None,
        "usage was %r" % (result["usage"],),
    )

    anthropic = AnthropicProvider(
        base_url=base + "/m/nousage", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
    )
    result = anthropic.complete([{"role": "user", "content": "x"}])
    _check(
        "P2c anthropic: a response with no usage yields absent, not zero",
        result["usage"] is None,
        "usage was %r" % (result["usage"],),
    )
    _check(
        "P2d usage_is_reported() tells a metric to skip rather than add zero",
        usage_is_reported(result) is False and usage_or_zero(result)["input_tokens"] == 0,
    )

    # An upstream that returns `usage: {}` reported nothing either. An empty
    # envelope is not a measurement, and this is the case a mutation of
    # `_coerce_usage` slipped through until it was pinned.
    for label, provider in (
        (
            "P2e openai-compat",
            OpenAICompatProvider(
                base_url=base + "/m/emptyusage", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
            ),
        ),
        (
            "P2f anthropic",
            AnthropicProvider(
                base_url=base + "/m/emptyusage", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
            ),
        ),
    ):
        empty = provider.complete([{"role": "user", "content": "x"}])
        _check(
            "%s: an EMPTY usage envelope is absent, not zero" % label,
            empty["usage"] is None,
            "usage was %r" % (empty["usage"],),
        )


def _pin_usage_all_or_nothing(base: str) -> None:
    """PIN P7: half a measurement is not a measurement.

    This is where mutation MUT-R1 -- zero-filling inside `normalise_result`
    rather than only in the two HTTP adapters -- goes RED. P2a-P2f drive the
    adapters and the `usage: None` stub path, and every one of them stays
    green under MUT-R1 because `normalise_result`'s usage branch is never
    entered with a mapping. These cases enter it.
    """
    # -- the CONTRACT-facing half: normalise_result --------------------------
    empty = normalise_result({"text": "hi", "tool_calls": [], "usage": {}})
    _check(
        "P7a normalise_result: an EMPTY usage envelope is None, never {0, 0}",
        empty["usage"] is None,
        "usage was %r (this is mutation MUT-R1)" % (empty["usage"],),
    )
    for missing, partial in (
        ("output_tokens", {"input_tokens": 11}),
        ("input_tokens", {"output_tokens": 7}),
    ):
        _, error, _ = _capture(
            lambda u=partial: normalise_result({"text": "hi", "tool_calls": [], "usage": u})
        )
        _check(
            "P7b normalise_result REFUSES a usage missing '%s'" % missing,
            error.startswith("ProviderResponseError"),
            "expected ProviderResponseError, got %r" % error,
        )
    for label, bad in (
        ("a negative count", {"input_tokens": 11, "output_tokens": -7}),
        ("a float count", {"input_tokens": 11, "output_tokens": 7.5}),
        ("a boolean count", {"input_tokens": 11, "output_tokens": True}),
        ("a string count", {"input_tokens": "11", "output_tokens": "7"}),
    ):
        _, error, _ = _capture(
            lambda u=bad: normalise_result({"text": "hi", "tool_calls": [], "usage": u})
        )
        _check(
            "P7c normalise_result REFUSES %s" % label,
            error.startswith("ProviderResponseError"),
            "expected ProviderResponseError, got %r" % error,
        )
    kept = normalise_result(
        {"text": "hi", "tool_calls": [], "usage": {"input_tokens": 11, "output_tokens": 0}}
    )
    _check(
        "P7d normalise_result KEEPS a complete usage, including a real 0",
        kept["usage"] == {"input_tokens": 11, "output_tokens": 0},
        "usage was %r" % (kept["usage"],),
    )

    # A stub whose script carries a partial usage is refused at the boundary,
    # not silently downgraded -- the offender is reachable, so it is told.
    stub = StubProvider(default={"text": "x", "tool_calls": [], "usage": {"input_tokens": 5}})
    _, error, _ = _capture(lambda: stub.complete([{"role": "user", "content": "x"}]))
    _check(
        "P7e a stub script with a PARTIAL usage raises rather than inventing 0",
        error.startswith("ProviderResponseError"),
        "got %r" % error,
    )

    # -- the VENDOR-facing half: _coerce_usage over real HTTP ----------------
    for label, provider in (
        (
            "P7f openai-compat",
            OpenAICompatProvider(
                base_url=base + "/m/partialusage", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
            ),
        ),
        (
            "P7g anthropic",
            AnthropicProvider(
                base_url=base + "/m/partialusage", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
            ),
        ),
    ):
        got = provider.complete([{"role": "user", "content": "x"}])
        _check(
            "%s: an upstream PARTIAL usage is absent, not half-invented" % label,
            got["usage"] is None,
            "usage was %r" % (got["usage"],),
        )
    for label, provider in (
        (
            "P7h openai-compat",
            OpenAICompatProvider(
                base_url=base + "/m/negusage", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
            ),
        ),
        (
            "P7i anthropic",
            AnthropicProvider(
                base_url=base + "/m/negusage", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
            ),
        ),
    ):
        got = provider.complete([{"role": "user", "content": "x"}])
        _check(
            "%s: an upstream NEGATIVE count is absent, not carried" % label,
            got["usage"] is None,
            "usage was %r" % (got["usage"],),
        )

    # -- the consequence a caller relies on ---------------------------------
    partial_result = {"text": "", "tool_calls": [], "usage": {"input_tokens": 11}}
    _check(
        "P7j usage_is_reported() is FALSE for a partial usage",
        usage_is_reported(partial_result) is False,
    )
    _check(
        "P7k usage_or_zero() never returns one real count beside a fabricated one",
        usage_or_zero(partial_result) == {"input_tokens": 0, "output_tokens": 0},
        "got %r" % (usage_or_zero(partial_result),),
    )


def _pin_http_round_trip(base: str) -> None:
    """Both HTTP providers parse a real (local) round trip into one shape."""
    openai = OpenAICompatProvider(
        base_url=base + "/m/ok", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
    )
    got = openai.complete(
        [{"role": "user", "content": "x"}],
        tools=[{"name": "grep", "parameters": {"type": "object"}}],
        response_schema={"type": "object", "title": "answer"},
    )
    _check(
        "P6a openai-compat normalises text, tool_calls and usage",
        got["text"] == "hello"
        and got["tool_calls"] == [{"name": "grep", "arguments": {"q": "x"}}]
        and got["usage"] == {"input_tokens": 11, "output_tokens": 7},
        "got %r" % (got,),
    )
    anthropic = AnthropicProvider(
        base_url=base + "/m/ok", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
    )
    got = anthropic.complete(
        [{"role": "system", "content": "be terse"}, {"role": "user", "content": "x"}],
        tools=[{"name": "grep", "input_schema": {"type": "object"}}],
    )
    _check(
        "P6b anthropic normalises text, tool_calls and usage",
        got["text"] == "hello"
        and got["tool_calls"] == [{"name": "grep", "arguments": {"q": "x"}}]
        and got["usage"] == {"input_tokens": 11, "output_tokens": 7},
        "got %r" % (got,),
    )


def _pin_malformed_is_an_error(base: str) -> None:
    """A malformed answer is a distinct error, never a silent success (criterion 24)."""
    broken = StubProvider(default={})
    _, error, _ = _capture(lambda: broken.complete([{"role": "user", "content": "x"}]))
    _check(
        "P4a a provider result missing keys raises ProviderResponseError",
        error.startswith("ProviderResponseError"),
        "got %r" % error,
    )
    openai = OpenAICompatProvider(
        base_url=base + "/m/badjson", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
    )
    _, error, _ = _capture(lambda: openai.complete([{"role": "user", "content": "x"}]))
    _check(
        "P4b a non-JSON body raises ProviderResponseError",
        error.startswith("ProviderResponseError"),
        "got %r" % error,
    )


def _pin_no_secret_leak(base: str) -> None:
    """PIN P3: no key, and no URL carrying credentials, reaches a human or a stream.

    Drives every error path -- including an upstream that echoes the
    Authorization header straight back inside its 401 body.
    """
    dead = "http://127.0.0.1:9/m/ok"  # discard port: always refuses
    cases = [
        (
            "401 whose body echoes the credential",
            OpenAICompatProvider(
                base_url=base + "/m/echo401", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
            ),
        ),
        (
            "anthropic 401 whose body echoes the credential",
            AnthropicProvider(
                base_url=base + "/m/echo401", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
            ),
        ),
        (
            "unreachable endpoint",
            OpenAICompatProvider(base_url=dead, api_key=_MOCK_KEY_SENTINEL, model="pin-model"),
        ),
        (
            "non-JSON body",
            OpenAICompatProvider(
                base_url=base + "/m/badjson", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
            ),
        ),
        (
            "happy path",
            OpenAICompatProvider(
                base_url=base + "/m/ok", api_key=_MOCK_KEY_SENTINEL, model="pin-model"
            ),
        ),
    ]
    leaked: List[str] = []
    for label, provider in cases:
        _, error, streams = _capture(
            lambda p=provider: p.complete([{"role": "user", "content": "x"}])
        )
        for where, text in (("exception", error), ("stdout/stderr", streams)):
            if _MOCK_KEY_SENTINEL in text:
                leaked.append("%s -> %s: %s" % (label, where, text[:160]))
    _check(
        "P3a no API key appears in any exception text or stream (5 paths)",
        not leaked,
        "; ".join(leaked),
    )

    # A base URL carrying userinfo must never be repeated back whole.
    creds_url = "http://pinuser:pinpassword@127.0.0.1:9/m/ok"
    provider = OpenAICompatProvider(base_url=creds_url, model="pin-model")
    _, error, streams = _capture(lambda: provider.complete([{"role": "user", "content": "x"}]))
    _check(
        "P3b a URL's userinfo never reaches an exception or a stream",
        "pinpassword" not in error and "pinpassword" not in streams and "pinuser" not in error,
        "got %r" % error,
    )

    # And nothing is written to disk by a provider at all.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="loopkit-provider-pin-") as tmp:
        before = sorted(os.listdir(tmp))
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            _capture(lambda: cases[0][1].complete([{"role": "user", "content": "x"}]))
        finally:
            os.chdir(cwd)
        _check(
            "P3c a provider writes no file at all",
            sorted(os.listdir(tmp)) == before,
            "new files: %r" % (sorted(os.listdir(tmp)),),
        )


def _pin_leak_sweep() -> None:
    """PIN P8: a SENTINEL planted in every input position reaches nothing.

    Written after the 2026-09-07 hostile review, whose whole finding was that
    `P3a`/`P3b` covered the two shapes their author thought of. A pin that
    enumerates known-bad cases permits every case nobody imagined -- this
    repository's named recurring defect -- so this one is built the other way
    round: it plants ONE sentinel string in every position an operator can put
    a credential (key, URL username, URL password, path segment, query value),
    drives every provider entry point over a grid of malformed and hostile
    endpoints, and greps EVERYTHING that can reach a human -- the full
    traceback, stdout, stderr, and every header actually put on the wire.

    Three leaks it catches that a two-case pin did not:
      * a scheme-less or empty-scheme base URL -- `Request()` raises
        `ValueError` quoting the whole URL, from ABOVE the guarded region;
      * a malformed port -- `parts.port` raises inside `_safe_url`, escaping
        as a bare non-`ProviderError`;
      * a credential in the URL PATH -- `_safe_url` used to keep `parts.path`
        and echo it back verbatim into every transport error.
    """
    sentinel = "ZZ-LEAK-SENTINEL-9911-ZZ"
    seen_headers: List[Dict[str, str]] = []

    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            seen_headers.append(
                dict({k.lower(): v for k, v in self.headers.items()}, _path=self.path)
            )
            # Echo the credential straight back, the nastiest real upstream.
            body = json.dumps(
                {
                    "error": {
                        "message": "rejected: %s %s"
                        % (self.headers.get("Authorization", ""), self.headers.get("x-api-key", ""))
                    },
                    "seen": raw[:200].decode("utf-8", "replace"),
                }
            ).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server_port = server.server_address[1]
    live = "http://127.0.0.1:%d" % server_port

    # Every position a credential can occupy, x every provider.
    endpoints = [
        ("scheme-less base URL", "//u:%s@127.0.0.1:9" % sentinel),
        ("empty-scheme base URL", ":%s@127.0.0.1:9" % sentinel),
        ("unknown scheme", "gopher://u:%s@127.0.0.1:9" % sentinel),
        ("userinfo, dead host", "http://u:%s@127.0.0.1:9" % sentinel),
        ("userinfo as username", "http://%s:pw@127.0.0.1:9" % sentinel),
        ("port out of range", "http://u:%s@127.0.0.1:99999999" % sentinel),
        ("non-numeric port", "http://u:%s@127.0.0.1:notaport" % sentinel),
        ("credential in the PATH", "http://127.0.0.1:9/%s" % sentinel),
        ("credential in the QUERY", "http://127.0.0.1:9/x?token=%s" % sentinel),
        ("credential in the PATH, live host", "%s/%s" % (live, sentinel)),
        ("trailing-dot host", "http://u:%s@127.0.0.1.:9" % sentinel),
        ("uppercase scheme", "HTTP://u:%s@127.0.0.1:9" % sentinel),
    ]
    keys = [("no key", None), ("key is the sentinel", sentinel), ("live key", "sk-live-decoy")]

    leaked: List[str] = []
    calls = 0
    try:
        for factory_name, factory in (
            ("openai-compat", OpenAICompatProvider),
            ("anthropic", AnthropicProvider),
        ):
            for where_label, base_url in endpoints:
                for key_label, key in keys:
                    label = "%s / %s / %s" % (factory_name, where_label, key_label)

                    def drive(f=factory, b=base_url, k=key):
                        provider = f(base_url=b, api_key=k, model="pin-model")
                        return provider.complete(
                            [{"role": "user", "content": "x"}],
                            tools=[{"name": "grep", "parameters": {"type": "object"}}],
                            response_schema={"type": "object", "title": "answer"},
                        )

                    calls += 1
                    result, error, streams = _capture(drive)
                    for kind, text in (("traceback", error), ("stdout/stderr", streams)):
                        if sentinel in text:
                            leaked.append("%s -> %s: %s" % (label, kind, text.strip()[:120]))
                    if result is not None and sentinel in canonical_json(result):
                        leaked.append("%s -> returned value" % label)
                    # Every raise must still be a ProviderError, not a bare
                    # ValueError escaping the guarded region.
                    if error and not error.split(":")[0].startswith("Provider"):
                        leaked.append("%s -> raised %s, not a ProviderError" % (label, error.split(":")[0]))
    finally:
        pass

    _check(
        "P8a a sentinel in ANY url position reaches no traceback, stream or return "
        "value, and every raise is a ProviderError (%d paths)" % calls,
        not leaked,
        " | ".join(leaked[:4]),
    )

    # The wire assertion has to be stated precisely, or it is wrong in the
    # permissive direction. An API KEY belongs in `Authorization` -- that is
    # what a key IS -- and a path the operator wrote is the path we must
    # request. The property worth pinning is narrower and is exactly the one
    # `S3Store` broke: userinfo an operator put in the URL is NEVER PROMOTED
    # onto the wire, not into a header, not into the request line.
    #
    # A CONTROL first, or the assertion below is vacuous: an identical request
    # WITHOUT userinfo must actually arrive, proving the observation channel
    # works. Without it "no leak reached the server" and "the pin never fired"
    # are the same output -- verification that cannot fail is not verification.
    seen_headers.clear()
    control = OpenAICompatProvider(
        base_url="http://127.0.0.1:%d" % server_port, api_key="sk-live-decoy", model="pin-model"
    )
    _capture(lambda: control.complete([{"role": "user", "content": "x"}]))
    _check(
        "P8b-control the observation channel works: a plain request IS seen",
        len(seen_headers) == 1 and "authorization" in seen_headers[0],
        "seen=%r" % (seen_headers,),
    )

    seen_headers.clear()
    userinfo_only = "http://%s:%s@127.0.0.1:%d" % (sentinel, sentinel, server_port)
    errors: List[str] = []
    for factory in (OpenAICompatProvider, AnthropicProvider):
        _, error, streams = _capture(
            lambda f=factory: f(
                base_url=userinfo_only, api_key="sk-live-decoy", model="pin-model"
            ).complete([{"role": "user", "content": "x"}])
        )
        if sentinel in error or sentinel in streams:
            errors.append(error.strip()[:120])
        if not error.startswith("Provider"):
            errors.append("raised %s, not a ProviderError" % error.split(":")[0])
    on_wire = [
        "%s=%s" % (name, value[:40])
        for headers in seen_headers
        for name, value in headers.items()
        if name != "_path" and sentinel in value
    ] + ["path=%s" % h["_path"] for h in seen_headers if sentinel in h["_path"]]
    _check(
        "P8b URL userinfo is never promoted onto the wire, and the failure it "
        "causes is a scrubbed ProviderError (%d requests reached the server)"
        % len(seen_headers),
        not on_wire and not errors,
        "wire=%r errors=%r" % (on_wire[:2], errors[:2]),
    )

    # The sentinel as the API KEY, against a live upstream that echoes it back
    # inside its own 500 body -- the one path where the redactor, not the
    # structure, is what saves us.
    echoing = OpenAICompatProvider(base_url=live, api_key=sentinel, model="pin-model")
    _, error, streams = _capture(lambda: echoing.complete([{"role": "user", "content": "x"}]))
    _check(
        "P8c an upstream ECHOING the key back in its error body is redacted",
        sentinel not in error and sentinel not in streams,
        "%r" % error[:200],
    )

    _check(
        "P8d _safe_url keeps scheme+host+port and DROPS userinfo, path and query",
        _safe_url("http://u:%s@example.test:8443/v1/%s?t=%s" % (sentinel, sentinel, sentinel))
        == "http://example.test:8443",
        "got %r" % _safe_url("http://u:%s@example.test:8443/v1/%s" % (sentinel, sentinel)),
    )
    _check(
        "P8e _safe_url never raises, even on a malformed port",
        _safe_url("http://u:%s@127.0.0.1:99999999/x" % sentinel).find(sentinel) == -1,
    )

    server.shutdown()
    server.server_close()


def _pin_leak_sweep_nasty() -> None:
    """PIN P9: the same hunt as P8, with a sentinel P8's could not have caught.

    P8's sentinel is plain ASCII (`ZZ-LEAK-SENTINEL-9911-ZZ`). Every redactor
    in this package matched it by literal substring, so P8 could pass while the
    redactor was defeated by ANY escaping -- which is exactly what happened in
    `store.py`, where an access key carrying a trailing newline made
    `putheader` raise `ValueError("Invalid header value %r" % value)` and `%r`
    turned the newline into backslash-n, so the literal `in` test was False and
    the whole signed `Authorization` header reached the operator.

    So P9's sentinel carries a newline, a tab, both quote characters and two
    non-ASCII characters, and detection asks the question `%r` cannot make
    false: is any part of the secret that SURVIVES escaping present?
    """
    import base64 as _b64
    import shlex
    import xml.sax.saxutils as _saxutils

    sentinel = "ZZ-PROV-LEAK-9911-c0ffee\n\tq\"'éΩ-ZZ"
    # The escape-invariant core. Written as a literal, not computed from
    # redact.py, so the pin does not grade the implementation with the
    # implementation's own ruler.
    core = "ZZ-PROV-LEAK-9911-c0ffee"
    raw = sentinel.encode("utf-8")
    forms = sorted(
        {
            f
            for f in {
                sentinel,
                core,
                repr(sentinel)[1:-1],
                repr(raw)[2:-1],
                json.dumps(sentinel)[1:-1],
                json.dumps(sentinel, ensure_ascii=False)[1:-1],
                urllib.parse.quote(sentinel, safe=""),
                sentinel.encode("unicode_escape").decode("ascii"),
                sentinel.encode("ascii", "backslashreplace").decode("ascii"),
                shlex.quote(sentinel),
                _saxutils.escape(sentinel),
                _b64.b64encode(raw).decode("ascii"),
                raw.hex(),
            }
            if len(f) >= 8
        },
        key=len,
        reverse=True,
    )

    def hits(text: str) -> List[str]:
        return [f[:24] for f in forms if f in text]

    seen_headers: List[Dict[str, str]] = []

    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            seen_headers.append(
                dict({k.lower(): v for k, v in self.headers.items()}, _path=self.path)
            )
            # Echo the credential straight back: the one path where the
            # REDACTOR, not the shape of the message, is all that stands
            # between the key and a human.
            body = json.dumps(
                {
                    "error": {
                        "message": "rejected: %s %s"
                        % (self.headers.get("Authorization", ""), self.headers.get("x-api-key", ""))
                    }
                }
            ).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    live = "http://127.0.0.1:%d" % server.server_address[1]

    endpoints = [
        ("dead host", "http://127.0.0.1:9"),
        ("live upstream echoing the key back", live),
        ("userinfo, dead host", "http://u:%s@127.0.0.1:9" % sentinel),
        ("credential in the PATH", "http://127.0.0.1:9/%s" % sentinel),
        ("credential in the QUERY", "http://127.0.0.1:9/x?token=%s" % sentinel),
        ("malformed port", "http://u:%s@127.0.0.1:99999999" % sentinel),
        ("non-numeric port", "http://u:%s@127.0.0.1:notaport" % sentinel),
        ("scheme-less", "//u:%s@127.0.0.1:9" % sentinel),
        ("file:// base URL", "file:///etc/hosts"),
        ("unknown scheme", "gopher://u:%s@127.0.0.1:9" % sentinel),
    ]
    keys = [
        ("api key + trailing newline", core + "\n"),
        ("api key IS the sentinel", sentinel),
        ("api key + embedded tab", core + "\t"),
        ("api key + embedded quote", core + '"'),
        ("no key", None),
    ]

    leaked: List[str] = []
    calls = 0
    try:
        for factory_name, factory in (
            ("openai-compat", OpenAICompatProvider),
            ("anthropic", AnthropicProvider),
        ):
            for where, base_url in endpoints:
                for key_label, key in keys:
                    label = "%s / %s / %s" % (factory_name, where, key_label)

                    def drive(f=factory, b=base_url, k=key):
                        return f(base_url=b, api_key=k, model="pin-model").complete(
                            [{"role": "user", "content": "x"}],
                            tools=[{"name": "grep", "parameters": {"type": "object"}}],
                            response_schema={"type": "object", "title": "answer"},
                        )

                    calls += 1
                    result, error, streams = _capture(drive)
                    for kind, text in (("traceback", error), ("stdout/stderr", streams)):
                        hit = hits(text)
                        if hit:
                            leaked.append("%s -> %s %r: %s" % (label, kind, hit, text.strip()[:100]))
                    if result is not None and hits(canonical_json(result)):
                        leaked.append("%s -> returned value" % label)
                    if error and not error.split(":")[0].startswith("Provider"):
                        leaked.append(
                            "%s -> raised %s, not a ProviderError" % (label, error.split(":")[0])
                        )
        _check(
            "P9a a sentinel carrying a newline, a tab, both quotes and non-ASCII "
            "reaches no traceback, stream or return value, and every raise is a "
            "ProviderError (%d paths, %d escaped renderings checked)" % (calls, len(forms)),
            not leaked,
            " | ".join(leaked[:3]),
        )

        # The wire. An API KEY belongs in `Authorization`/`x-api-key` -- that is
        # what a key IS -- so the property pinned is the narrower true one: a
        # DAMAGED key never reaches the wire in a header it does not belong in,
        # and URL userinfo is never promoted onto the wire at all.
        seen_headers.clear()
        control = OpenAICompatProvider(base_url=live, api_key="sk-live-decoy", model="pin-model")
        _capture(lambda: control.complete([{"role": "user", "content": "x"}]))
        _check(
            "P9b-control the observation channel works: a plain request IS seen",
            len(seen_headers) == 1 and "authorization" in seen_headers[0],
            "seen=%r" % (seen_headers,),
        )

        seen_headers.clear()
        _capture(
            lambda: OpenAICompatProvider(
                base_url="http://%s:%s@127.0.0.1:%d" % (sentinel, sentinel, server.server_address[1]),
                api_key="sk-live-decoy",
                model="pin-model",
            ).complete([{"role": "user", "content": "x"}])
        )
        on_wire = [
            "%s=%s" % (name, value[:40])
            for headers in seen_headers
            for name, value in headers.items()
            if name != "_path" and hits(value)
        ] + ["path=%s" % h["_path"] for h in seen_headers if hits(h["_path"])]
        # HONEST NOTE, or this assertion reads stronger than it is: urllib
        # never RESOLVES a userinfo URL -- it treats `user:pw@host` as one
        # hostname and DNS fails -- so `len(seen_headers)` here is 0 and "no
        # leak reached the server" and "the pin never fired" print the same.
        # P9b-control is what keeps it from being vacuous, and P9c2 below adds
        # a wire assertion that DOES fire.
        _check(
            "P9c URL userinfo is never promoted onto the wire, even carrying a "
            "newline (%d requests reached the server; urllib refuses to resolve "
            "a userinfo URL at all, so see P9c2)" % len(seen_headers),
            not on_wire,
            "%r" % on_wire[:3],
        )

        # A wire assertion that really fires. A TAB in an API key is accepted
        # by `putheader` (only CR and LF are rejected), so this request does
        # reach the server -- and the property worth pinning is that the key
        # lands ONLY where a key belongs. If a future edit ever composed the
        # key into the Host header, the request line or a second header, this
        # goes red with a real request on the wire to point at.
        seen_headers.clear()
        damaged = core + "\t"
        _capture(
            lambda: OpenAICompatProvider(
                base_url=live, api_key=damaged, model="pin-model"
            ).complete([{"role": "user", "content": "x"}])
        )
        misplaced = [
            "%s=%s" % (name, value[:40])
            for headers in seen_headers
            for name, value in headers.items()
            if core in value and name not in ("authorization",)
        ] + ["path=%s" % h["_path"] for h in seen_headers if core in h["_path"]]
        _check(
            "P9c2 a key carrying a tab DOES reach the wire, and lands ONLY in "
            "Authorization -- not in Host, not in the request line, not in any "
            "other header (%d requests reached the server)" % len(seen_headers),
            len(seen_headers) == 1 and not misplaced,
            "seen=%d misplaced=%r" % (len(seen_headers), misplaced[:3]),
        )

        # The redactor itself, driven over escapers redact.py does not
        # enumerate. This is the pin `store.py` did not have, which is why a
        # leak survived two reviews there.
        redactor = _Redactor([sentinel])
        survived = []
        for label, text in (
            ("raw", sentinel),
            ("repr(str)", repr(sentinel)),
            ("repr(bytes)", repr(raw)),
            ("putheader ValueError", "Invalid header value %r" % ("Bearer " + sentinel).encode()),
            ("json", json.dumps(sentinel)),
            ("percent-encoded", urllib.parse.quote(sentinel, safe="")),
            ("shell-quoted", shlex.quote(sentinel)),
            ("XML entities", _saxutils.escape(sentinel)),
            ("base64", _b64.b64encode(raw).decode("ascii")),
            ("hex", raw.hex()),
        ):
            if hits(redactor.scrub("upstream said: " + text)):
                survived.append(label)
        _check(
            "P9d the redactor survives repr-, JSON-, percent-, shell-, entity-, "
            "base64- and hex-encoding of the same secret (10 renderings)",
            not survived,
            "%r" % survived,
        )
        _check(
            "P9e-control the redactor is not simply blanking everything",
            _Redactor([sentinel]).scrub("http://127.0.0.1:9 endpoint unreachable")
            == "http://127.0.0.1:9 endpoint unreachable",
        )

        # The redactor is a BACKSTOP. `_post_json`'s catch-all must compose no
        # untrusted exception text at all, pinned with a marker that is not a
        # secret so it stays red even if the redactor is perfect.
        marker = "MARKER-CATCHALL-77123-DO-NOT-ECHO"

        class _Exploding:
            def open(self, *_args, **_kwargs):
                raise RuntimeError(marker)

        provider = OpenAICompatProvider(base_url=live, api_key="sk-live-decoy", model="pin-model")
        provider._opener = _Exploding()
        _result, error, streams = _capture(
            lambda: provider.complete([{"role": "user", "content": "x"}])
        )
        _check(
            "P9f _post_json's catch-all names the exception TYPE and never its "
            "text (the catch-all is where store.py's header leak lived)",
            marker not in (error + streams) and "RuntimeError" in error,
            "%r" % (error + streams)[:200],
        )

        # file:// and friends are refused, not served by urllib's default
        # handlers. Left open, `complete()` would read a local file and return
        # its contents as model output, with no network call and no error.
        refused, accepted = 0, []
        for base in (
            "file://" + os.path.abspath(__file__),
            "file:///etc/hosts",
            "ftp://127.0.0.1:9",
            "gopher://127.0.0.1:9",
            "//127.0.0.1:9",
        ):
            try:
                OpenAICompatProvider(base_url=base, api_key="sk-x", model="pin-model")
            except ProviderError:
                refused += 1
            else:
                accepted.append(base)
        _check(
            "P9g every non-http(s) base URL is refused at construction "
            "(file://, ftp://, gopher://, scheme-less)",
            refused == 5,
            "accepted: %r" % accepted,
        )
        _check(
            "P9h-control http(s) base URLs still build",
            OpenAICompatProvider(base_url=live, api_key="sk-x", model="pin-model").name
            == "openai-compat"
            and AnthropicProvider(api_key="sk-x", model="pin-model").name == "anthropic",
        )
    finally:
        server.shutdown()
        server.server_close()


def _selftest() -> int:
    server, base = _mock_server()
    try:
        _pin_stub_deterministic()
        _pin_stub_refuses_unscripted()
        _pin_usage_absent_not_zero(base)
        _pin_usage_all_or_nothing(base)
        _pin_http_round_trip(base)
        _pin_malformed_is_an_error(base)
        _pin_no_secret_leak(base)
        _pin_leak_sweep()
        _pin_leak_sweep_nasty()
    finally:
        server.shutdown()
        server.server_close()
    failures = [line for line in _RESULTS if line.startswith("FAIL")]
    print("")
    if failures:
        print("provider.py: %d FAIL of %d" % (len(failures), len(_RESULTS)))
        return 1
    print("provider.py: ALL PASS (%d pins)" % len(_RESULTS))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--selftest":
        return _selftest()
    print(__doc__ or "")
    print("usage: python3 -m loopkit_core.provider --selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
