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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

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


def usage_is_reported(result: Mapping[str, Any]) -> bool:
    """True when the upstream actually reported token counts."""
    return isinstance(result.get("usage"), Mapping)


def usage_or_zero(result: Mapping[str, Any]) -> Dict[str, int]:
    """Criterion-23 shape for a caller that wants zeros. Opt in deliberately.

    Calling this asserts "for my purpose an unreported count is zero". Do not
    call it from anything that aggregates tokens per task.
    """
    usage = result.get("usage")
    if isinstance(usage, Mapping):
        return {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        }
    return {"input_tokens": 0, "output_tokens": 0}


def _coerce_usage(raw: Any, input_key: str, output_key: str) -> Optional[Dict[str, int]]:
    """Map an upstream usage object onto the contract, or None if it reported none.

    A usage object that is present but carries neither count is treated as
    "not reported" -- the same rule, one level down: an empty envelope is not
    a measurement.
    """
    if not isinstance(raw, Mapping):
        return None
    got_in = raw.get(input_key)
    got_out = raw.get(output_key)
    if got_in is None and got_out is None:
        return None
    out: Dict[str, int] = {}
    for name, value in (("input_tokens", got_in), ("output_tokens", got_out)):
        if value is None:
            continue
        try:
            out[name] = int(value)
        except (TypeError, ValueError):
            return None
    return out or None


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
        clean: Dict[str, int] = {}
        for name in ("input_tokens", "output_tokens"):
            value = usage.get(name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise ProviderResponseError("usage '%s' is not an integer" % name)
            clean[name] = value
        usage = clean or None
    return {"text": text, "tool_calls": calls, "usage": usage}


def _safe_url(url: str) -> str:
    """Scheme + host + path. Drops userinfo, query and fragment."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<url>"
    host = parts.hostname or ""
    if parts.port:
        host = "%s:%d" % (host, parts.port)
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


class _Redactor:
    """Scrubs known secret substrings out of anything on its way to a human."""

    def __init__(self, secrets: Iterable[Optional[str]] = ()) -> None:
        self._secrets = sorted(
            {s for s in secrets if isinstance(s, str) and len(s) >= 4},
            key=len,
            reverse=True,
        )

    def add(self, secret: Optional[str]) -> None:
        if isinstance(secret, str) and len(secret) >= 4 and secret not in self._secrets:
            self._secrets.append(secret)
            self._secrets.sort(key=len, reverse=True)

    def scrub(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, "***")
        return text


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
        self._redact = _Redactor([api_key])
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = _no_proxy_opener()

    def _post_json(self, url: str, headers: Mapping[str, str], body: Mapping[str, Any]) -> Any:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        for header, value in headers.items():
            request.add_header(header, value)
        request.add_header("Content-Type", "application/json")
        where = _safe_url(url)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:  # non-2xx
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # pragma: no cover - body already consumed
                detail = ""
            raise ProviderTransportError(
                self._redact.scrub("%s returned HTTP %s: %s" % (where, exc.code, detail))
            ) from None
        except urllib.error.URLError as exc:
            raise ProviderTransportError(
                self._redact.scrub("%s unreachable: %s" % (where, exc.reason))
            ) from None
        except Exception as exc:  # timeouts, socket errors
            raise ProviderTransportError(
                self._redact.scrub("%s failed: %s" % (where, exc))
            ) from None
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProviderResponseError(
                self._redact.scrub("%s returned non-JSON: %s" % (where, exc))
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
        payload = self._post_json(self.base_url + "/chat/completions", headers, body)
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
        payload = self._post_json(self.base_url + "/v1/messages", headers, body)
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
    """Run `call`, returning (result, error_text, stdout+stderr)."""
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    result = None
    error_text = ""
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            result = call()
        except BaseException as exc:  # noqa: BLE001 - the pin is about the text
            error_text = "%s: %s" % (type(exc).__name__, exc)
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


def _selftest() -> int:
    server, base = _mock_server()
    try:
        _pin_stub_deterministic()
        _pin_stub_refuses_unscripted()
        _pin_usage_absent_not_zero(base)
        _pin_http_round_trip(base)
        _pin_malformed_is_an_error(base)
        _pin_no_secret_leak(base)
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
