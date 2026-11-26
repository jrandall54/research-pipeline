"""Browse the vault from a phone: Datasette over the private Tailscale network.

Datasette is an optional extra (`uv sync --extra embed --extra serve` in the repo; `uv tool install --editable '.[embed,serve]' --force` for plain `rpipe`); nothing here imports it. It is
started as a separate process, so this module only builds its metadata and command line.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

from research_pipeline import netguard
from research_pipeline.netguard import Runner, check_host, resolve_host, system_runner, tailscale_ip

ServeError = netguard.HostError
__all__ = ["Runner", "ServeError", "check_host", "resolve_host", "system_runner", "tailscale_ip"]

# Same format as db._now(): 2026-11-21T09:30:00+00:00
_TS = "strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')"
_EXISTS = "EXISTS (SELECT 1 FROM papers WHERE id = :paper_id)"


def _vote(value: int, title: str) -> dict:
    # Foreign keys are not enforced in SQLite by default, so check the paper exists here.
    return {
        "title": title,
        "write": True,
        "params": ["paper_id"],  # explicit: Datasette's regex would also see ':00' in the timestamp literal
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
            "params": ["paper_id"],
            "sql": f"INSERT OR IGNORE INTO saves (paper_id, created_at) "
            f"SELECT :paper_id, {_TS} WHERE {_EXISTS}",
            "on_success_message": "Saved",
        },
        "unsave": {
            "title": "Unsave paper",
            "write": True,
            "params": ["paper_id"],
            "sql": "DELETE FROM saves WHERE paper_id = :paper_id",
            "on_success_message": "Unsaved",
        },
    }
    return {"title": "Research vault", "databases": {"vault": {"queries": queries}}}


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
