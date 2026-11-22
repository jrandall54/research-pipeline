"""Progress events: data objects that stages emit instead of printing.

Stages never write progress text themselves. They emit ProgressEvent values
through a Reporter; the CLI (or a test, or a future app) decides how to show
them. Standard library only: this module is shared and must import nothing
from the rest of the package.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal, Protocol

EventKind = Literal["start", "step", "note", "done"]


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    kind: EventKind
    label: str = ""
    current: int | None = None
    total: int | None = None
    elapsed: float = 0.0
    outcome: str = ""
    detail: str = ""


class Reporter(Protocol):
    def emit(self, event: ProgressEvent) -> None: ...


@dataclass
class ListReporter:
    """Collects every event in order; used by tests."""

    events: list[ProgressEvent] = field(default_factory=list)

    def emit(self, event: ProgressEvent) -> None:
        self.events.append(event)


def emit(reporter: Reporter | None, event: ProgressEvent) -> None:
    if reporter is not None:
        reporter.emit(event)


class StageHandle:
    """Handed to the body of a `stage()` block; emits step and note events."""

    def __init__(
        self,
        reporter: Reporter | None,
        name: str,
        total: int | None,
        clock: Callable[[], float],
        started: float,
    ) -> None:
        self._reporter = reporter
        self._name = name
        self._total = total
        self._clock = clock
        self._started = started

    @property
    def elapsed(self) -> float:
        return self._clock() - self._started

    def step(
        self,
        label: str,
        *,
        current: int | None = None,
        outcome: str = "",
        detail: str = "",
    ) -> None:
        emit(
            self._reporter,
            ProgressEvent(
                stage=self._name,
                kind="step",
                label=label,
                current=current,
                total=self._total,
                elapsed=self.elapsed,
                outcome=outcome,
                detail=detail,
            ),
        )

    def note(self, label: str) -> None:
        emit(
            self._reporter,
            ProgressEvent(stage=self._name, kind="note", label=label, elapsed=self.elapsed),
        )


@contextmanager
def stage(
    reporter: Reporter | None,
    name: str,
    total: int | None = None,
    label: str = "",
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[StageHandle]:
    """Emit `start` on entry and `done` on exit (with `outcome="failed"` and the
    error text if the block raises; the error is re-raised)."""
    started = clock()
    handle = StageHandle(reporter, name, total, clock, started)
    emit(reporter, ProgressEvent(stage=name, kind="start", label=label, total=total))
    try:
        yield handle
    except BaseException as exc:
        emit(
            reporter,
            ProgressEvent(
                stage=name,
                kind="done",
                total=total,
                elapsed=handle.elapsed,
                outcome="failed",
                detail=str(exc) or type(exc).__name__,
            ),
        )
        raise
    emit(
        reporter,
        ProgressEvent(stage=name, kind="done", total=total, elapsed=handle.elapsed),
    )
