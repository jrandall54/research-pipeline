"""A non-blocking lock so two pipeline commands never work on one vault at the same time."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = ".run.lock"


class RunLocked(Exception):
    """Another pipeline command already holds the lock for this vault."""

    def __init__(self, vault: Path, holder: str) -> None:
        super().__init__(f"{holder} is already working on {vault}")
        self.vault = vault
        self.holder = holder  # the command written in the lock file, "unknown" if it was empty


@contextmanager
def run_lock(vault: Path, holder: str) -> Iterator[None]:
    """Hold the vault's run lock as `holder` (e.g. "rpipe run"), or raise RunLocked at once.

    Uses `flock`, which the operating system drops when the process dies for any reason
    (crash, `kill -9`, SIGTERM), so a stale lock file can never block a later run. While
    held, the file contains `holder`, so a refused command can say who is working; it is
    emptied on release. See docs/guide/running.md.
    """
    with open(vault / LOCK_NAME, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            raise RunLocked(vault, handle.read().strip() or "unknown") from exc
        try:
            handle.truncate(0)
            handle.write(holder)  # "a+" appends, and the file is empty now, so this is the whole text
            handle.flush()
            yield
        finally:
            handle.truncate(0)
            handle.flush()
            fcntl.flock(handle, fcntl.LOCK_UN)
