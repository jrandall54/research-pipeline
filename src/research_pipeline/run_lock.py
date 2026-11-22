"""A non-blocking lock so two `rpipe run`s never work on one vault at the same time."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = ".run.lock"


class RunLocked(Exception):
    """Another run already holds the lock for this vault."""


@contextmanager
def run_lock(vault: Path) -> Iterator[None]:
    """Hold the vault's run lock, or raise RunLocked at once if someone else has it.

    Uses `flock`, which the operating system drops when the process dies for any reason
    (crash, `kill -9`, SIGTERM), so a stale lock file can never block a later run.
    See docs/guide/running.md.
    """
    with open(vault / LOCK_NAME, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunLocked(f"another `rpipe run` is already using {vault}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
