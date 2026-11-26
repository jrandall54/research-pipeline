"""Host and same-origin checks for the app server (it has no login).

Two defences, both run for every request by a middleware in ``server.py``:

* **Host check** (against DNS rebinding): the ``Host`` header must be one of the allowed hosts.
* **Same-origin check** (against cross-site request forgery): any request that is not GET/HEAD must
  say, in ``Origin`` (or ``Referer`` when there is no ``Origin``), that it came from a page on an
  allowed host over plain http.
"""

from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

SAFE_METHODS = {"GET", "HEAD"}


def _host_only(value: str) -> str:
    """``Host`` header to bare lower-case host: port and IPv6 brackets removed."""
    h = value.strip().lower()
    if h.startswith("["):  # [::1]:8002
        return h[1:].split("]", 1)[0]
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def _origin_host(value: str) -> str | None:
    """Host of an Origin/Referer URL, or None unless it is a plain http URL."""
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname  # already lower-case, brackets removed
    except ValueError:
        return None
    return host if parts.scheme == "http" and host else None


def check(request: Request, allowed_hosts: set[str]) -> Response | None:
    """Return a refusal response, or None when the request may go on to the app."""
    if _host_only(request.headers.get("host", "")) not in allowed_hosts:
        return PlainTextResponse("Unknown host", status_code=400)
    if request.method not in SAFE_METHODS:
        source = request.headers.get("origin") or request.headers.get("referer")
        if not source or _origin_host(source) not in allowed_hosts:
            return PlainTextResponse("Cross-site request refused", status_code=403)
    return None
