#!/usr/bin/env python3
"""The one HTTP opener every credentialed request in this package uses.

WHY THIS FILE EXISTS
--------------------
`urllib.request.build_opener(ProxyHandler({}))` keeps urllib's DEFAULT handler
set. Two earlier rounds of this review reasoned about `FileHandler` and
`FTPHandler` and closed them with a scheme check in each `__init__`. The same
default set also keeps `HTTPRedirectHandler`, and that one was missed, because
it does not answer the question "what scheme did the operator configure?" --
it answers "what scheme does the SERVER want next?", which nobody was asking.

`HTTPRedirectHandler.redirect_request` copies `req.headers` to the new target
wholesale; it strips only `content-length` and `content-type`. Both modules
attached the credential with `Request.add_header`, which writes to exactly
that dict. So a server answering `301 Location: http://attacker/` -- or a
bucket in the wrong region answering `301 PermanentRedirect`, which is
ORDINARY S3 operation -- received:

    Authorization: Bearer <api key>                       (provider, 301/302/303)
    Authorization: AWS4-HMAC-SHA256 Credential=<key id>   (store, 301/302/303/307/308)

with no exception raised, and the stranger's reply parsed and returned to the
caller as if the configured endpoint had sent it. Proven against a live local
sink, not read: see `_pin_redirect_never_reaches_a_second_host` in both
modules, and mutations MUT-RH1/MUT-RH2/MUT-RH3.

A redirect also REOPENED the `ftp://` door the `__init__` scheme guards had
just closed: `http_error_302` permits `http`, `https` AND `ftp` on a
`Location`, and the guards only ever saw the URL the operator typed. Probed:
`ftp://` on a `Location` reached the FTP handler ("ftp error: [Errno 61]
Connection refused"). Checking the first URL in a chain and then using the
last one is the same shape as every other leak on this branch.

THE POLICY, AND WHY THIS ONE
----------------------------
**Every redirect is refused. None is ever followed.** Not "stripped and
followed", not "followed if the host matches".

  * Stripping the credential and following anyway still returns the
    STRANGER'S BODY to the caller as the endpoint's answer. That is a
    content-injection path even with no key attached, and `list()` returning
    another host's object listing is a silent wrong-source read. Fixing a
    disclosure by leaving a substitution in place is not a fix.

  * Following same-origin only would work, but it costs an origin comparison
    -- default ports, case, trailing dots, IPv6 brackets, IDN -- and this
    branch's entire defect history is guards that compared the wrong two
    things. Refusing outright needs NO comparison to be correct, which is why
    it is the version that can be checked by reading it.

  * Nothing legitimate is lost. An LLM `/chat/completions` endpoint does not
    redirect in its success path. S3's wrong-region 301/307 CANNOT be followed
    correctly here anyway: SigV4 binds the signature to the Host header and to
    the region's scope, so following without re-signing (which this client
    does not do) buys a 403 instead of a clear message. `RedirectRefused` says
    "point the endpoint here instead", which is the operator's actual fix.

  * Because no target is ever used, the scheme guard on the target is not a
    check that could be got wrong -- there is no second URL to check. `ftp://`
    on a `Location` is now unreachable by construction rather than by
    allowlist.

Credentials are additionally attached with `add_unredirected_header` in both
callers. That is deliberate belt-and-braces: this handler is the guard, and
`add_unredirected_header` means that if some future edit reinstates a
redirect-following handler, the key STILL does not travel. Two independent
mechanisms, because one round of this review already shipped a guard that
covered the URL it was handed and not the URL it used.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

__all__ = [
    "REDIRECT_CODES",
    "RedirectRefused",
    "credentialed_opener",
    "safe_origin",
]

# Every status urllib's redirect handler acts on. 308 is listed explicitly
# because `HTTPRedirectHandler.http_error_308` did not exist before Python
# 3.11: on 3.9 a 308 fell through to `http_error_default` and was refused by
# accident. Defining it here makes the refusal a DECISION on every version
# rather than a version-dependent accident.
REDIRECT_CODES = (301, 302, 303, 307, 308)


def safe_origin(url: str) -> str:
    """Scheme + host + port of `url`. NOTHING else, and this never raises.

    Path, query, fragment and userinfo are dropped, not scrubbed -- a secret
    in a URL path carries no structural marker a redactor could find, so it
    can only be omitted. `urlsplit` is lazy: `parts.port` parses on access and
    raises `ValueError` on a malformed port, so that access is guarded too.

    This deliberately duplicates the SHAPE of `provider._safe_url`, which is
    pinned in place by mutation MUT-L1 and cannot move here. Duplication of a
    safety renderer is exactly the drift that put `store._scrub` two reviews
    behind `provider`'s, so the two are held together by a pin that feeds both
    the same hostile URLs and requires identical output
    (`_pin_safe_url_agrees_with_nethttp`). Change one, the pin fails.
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


class RedirectRefused(Exception):
    """A 3xx was refused BEFORE it was followed. Carries no credential.

    Not a subclass of `URLError`/`OSError`, so it cannot be swallowed by the
    `except urllib.error.URLError` branch each caller already has; each caller
    handles it by name, ahead of its catch-all. The message is composed only
    from an integer status and `safe_origin()` output -- never from the
    server's body, and never from a header value.
    """

    def __init__(self, code: int, location: str) -> None:
        self.code = int(code)
        self.target = safe_origin(location or "")
        super().__init__(
            "refused to follow an HTTP %d redirect to %s. A redirect is the "
            "one hop this client will not take: urllib copies request headers "
            "-- the API key or SigV4 Credential included -- to whatever host "
            "the server names, so following one hands the credential to a host "
            "the operator never configured. If %s really is the endpoint, "
            "configure it directly; for S3 a 301/307 here means the bucket "
            "lives in another region, so set that region."
            % (self.code, self.target, self.target)
        )


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every 3xx. Overrides the five status hooks, not `redirect_request`.

    `redirect_request` is the wrong seam: `http_error_302` parses `Location`
    and applies its own http/https/ftp scheme allowlist BEFORE calling it, so
    an override there would inherit urllib's opinion about which schemes are
    acceptable. Overriding the status hooks means urllib never parses, never
    resolves and never opens the target at all.

    Subclassing `HTTPRedirectHandler` is load-bearing: `build_opener` skips a
    default handler when a passed handler is a subclass of it, so this
    REPLACES the redirect handler rather than being added alongside it.
    """

    def _refuse(self, req, fp, code, msg, headers):  # noqa: D401 - urllib's signature
        try:
            fp.close()
        except Exception:  # pragma: no cover - the socket is going away regardless
            pass
        # `headers` is the SERVER's; only the status (an int) and
        # `safe_origin()` of the location reach the message.
        location = ""
        try:
            location = headers.get("location") or headers.get("uri") or ""
        except Exception:  # pragma: no cover - defensive: headers is server data
            location = ""
        raise RedirectRefused(code, location)

    http_error_301 = _refuse
    http_error_302 = _refuse
    http_error_303 = _refuse
    http_error_307 = _refuse
    http_error_308 = _refuse


def credentialed_opener() -> urllib.request.OpenerDirector:
    """The opener for every request that carries a credential.

    * `ProxyHandler({})` -- never route a keyed request through an ambient
      proxy picked up from the environment.
    * `_RefuseRedirect` -- never hand the credential to a host the server
      chose. This replaces the default `HTTPRedirectHandler`.

    `FileHandler` and `FTPHandler` remain in the default set and remain closed
    by the callers' `__init__` scheme guards; with redirects refused there is
    no longer a second URL that could reach them.
    """
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _RefuseRedirect
    )
