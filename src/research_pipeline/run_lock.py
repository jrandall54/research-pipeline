"""A non-blocking lock so two pipeline commands never work on one vault at the same time."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

LOCK_NAME = ".run.lock"


class RunLocked(Exception):
    """Another pipeline command already holds the lock for this vault."""

    def __init__(self, vault: Path, holder: str) -> None:
        super().__init__(f"{holder} is already working on {vault}")
        self.vault = vault
        self.holder = holder  # the command written in the lock file, "unknown" if it was empty


class VaultLock:
    """The vault's run lock as `holder` (e.g. "rpipe run"); can be let go of and taken again.

    Uses `flock`, which the operating system drops when the process dies for any reason
    (crash, `kill -9`, SIGTERM), so a stale lock file can never block a later run. While
    held, the file contains `holder`, so a refused command can say who is working; it is
    emptied on release. See docs/guide/running.md.
    """

    def __init__(self, vault: Path, holder: str) -> None:
        self.vault = vault
        self.holder = holder
        self._handle: TextIO | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        """Take the lock, or raise RunLocked at once (never waits) if someone else has it."""
        handle = open(self.vault / LOCK_NAME, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            holder = handle.read().strip() or "unknown"
            handle.close()
            raise RunLocked(self.vault, holder) from exc
        handle.truncate(0)
        handle.write(self.holder)  # "a+" appends, and the file is empty now, so this is the whole text
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        """Empty the file and let go; does nothing if not held."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.truncate(0)
            handle.flush()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


@contextmanager
def run_lock(vault: Path, holder: str) -> Iterator[None]:
    """Hold the vault's run lock as `holder` for the block, or raise RunLocked at once."""
    lock = VaultLock(vault, holder)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
