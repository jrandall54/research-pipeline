"""Host allowlist shared by every server that has no login (Datasette `rpipe serve`, the app).

Only localhost, loopback and Tailscale addresses are accepted; anything else is refused.
"""

import ipaddress
import subprocess
from collections.abc import Callable

Runner = Callable[[list[str]], subprocess.CompletedProcess]

_TAILSCALE_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
_ALLOWED = "localhost, a loopback address (127.0.0.0/8, ::1), or a Tailscale address (100.64.0.0/10, fd7a:115c:a1e0::/48)"


class HostError(Exception):
    pass


def system_runner(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)


def check_host(host: str) -> str:
    """Allowlist: localhost, loopback IPs and Tailscale IPs only. Anything else is refused."""
    h = host.strip().lower()
    if "%" in h:  # IPv6 scope ids (fe80::1%eth0) are not supported
        raise HostError(f"Refusing to listen on {host!r}: scope ids ('%') are not supported. Allowed: {_ALLOWED}.")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h == "localhost":
        return h
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        ip = None
    if ip is not None:
        if getattr(ip, "ipv4_mapped", None) is None and (
            ip.is_loopback or any(ip in net for net in _TAILSCALE_NETS if net.version == ip.version)
        ):
            return h
        reason = "is not a loopback or Tailscale address"
    else:
        reason = "is not an IP address (or localhost)"
    raise HostError(
        f"Refusing to listen on {host!r}: it {reason}, and this server has no login. Allowed: {_ALLOWED}."
    )


def tailscale_ip(runner: Runner) -> str:
    msg = "Tailscale not running; use --host 127.0.0.1 to serve locally"
    try:
        result = runner(["tailscale", "ip", "-4"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostError(msg) from exc
    lines = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or not lines:
        raise HostError(msg)
    return check_host(lines[0])


def resolve_host(host: str | None, runner: Runner) -> str:
    return tailscale_ip(runner) if host is None else check_host(host)
