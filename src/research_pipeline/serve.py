"""Browse the vault from a phone: Datasette over the private Tailscale network.

Datasette is an optional extra (`uv sync --extra serve`); nothing here imports it. It is
started as a separate process, so this module only builds its metadata and command line.
"""

import importlib.util
import ipaddress
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

Runner = Callable[[list[str]], subprocess.CompletedProcess]

# Same format as db._now(): 2026-11-21T09:30:00+00:00
_TS = "strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')"
_EXISTS = "EXISTS (SELECT 1 FROM papers WHERE id = :paper_id)"
_TAILSCALE_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
_ALLOWED = "localhost, a loopback address (127.0.0.0/8, ::1), or a Tailscale address (100.64.0.0/10, fd7a:115c:a1e0::/48)"


class ServeError(Exception):
    pass


def _vote(value: int, title: str) -> dict:
    # Foreign keys are not enforced in SQLite by default, so check the paper exists here.
    return {
        "title": title,
        "write": True,
        "sql": f"INSERT INTO votes (paper_id, vote, created_at) "
        f"SELECT :paper_id, {value}, {_TS} WHERE {_EXISTS}",
        "on_success_message": title,
    }


def build_metadata() -> dict:
    """Datasette metadata: one database `vault` with canned read and write queries."""
    queries = {
        "todays_digest": {
            "title": "Latest digest",
            "sql": "SELECT id, title, triage_score, url FROM papers "
            "WHERE digest_date = (SELECT MAX(digest_date) FROM papers) "
            "ORDER BY triage_score DESC",
        },
        "saved": {
            "title": "Saved papers",
            "sql": "SELECT p.id, p.title, p.url, s.created_at AS saved_at "
            "FROM saves s JOIN papers p ON p.id = s.paper_id "
            "ORDER BY s.created_at DESC, s.rowid DESC",
        },
        "vote_up": _vote(1, "Vote up"),
        "vote_down": _vote(-1, "Vote down"),
        "vote_clear": _vote(0, "Clear vote"),
        "save": {
            "title": "Save paper",
            "write": True,
            "sql": f"INSERT OR IGNORE INTO saves (paper_id, created_at) "
            f"SELECT :paper_id, {_TS} WHERE {_EXISTS}",
            "on_success_message": "Saved",
        },
        "unsave": {
            "title": "Unsave paper",
            "write": True,
            "sql": "DELETE FROM saves WHERE paper_id = :paper_id",
            "on_success_message": "Unsaved",
        },
    }
    return {"title": "Research vault", "databases": {"vault": {"queries": queries}}}


def system_runner(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)


def check_host(host: str) -> str:
    """Allowlist: localhost, loopback IPs and Tailscale IPs only. Anything else is refused."""
    h = host.strip().lower()
    if "%" in h:  # IPv6 scope ids (fe80::1%eth0) are not supported
        raise ServeError(f"Refusing to listen on {host!r}: scope ids ('%') are not supported. Allowed: {_ALLOWED}.")
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
    raise ServeError(
        f"Refusing to listen on {host!r}: it {reason}, and this server has no login. Allowed: {_ALLOWED}."
    )


def tailscale_ip(runner: Runner) -> str:
    msg = "Tailscale not running; use --host 127.0.0.1 to serve locally"
    try:
        result = runner(["tailscale", "ip", "-4"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServeError(msg) from exc
    lines = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or not lines:
        raise ServeError(msg)
    return check_host(lines[0])


def resolve_host(host: str | None, runner: Runner) -> str:
    return tailscale_ip(runner) if host is None else check_host(host)


def command(vault_db: Path, host: str, port: int, metadata_path: Path) -> list[str]:
    # No --immutable: the vote/save queries must be able to write.
    return [
        sys.executable, "-m", "datasette", "serve", str(vault_db),
        "--host", host, "--port", str(port), "--metadata", str(metadata_path),
    ]


def datasette_available() -> bool:
    return importlib.util.find_spec("datasette") is not None


def run_datasette(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, check=False).returncode
    except KeyboardInterrupt:
        return 0
