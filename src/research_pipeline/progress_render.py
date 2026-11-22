"""Turn progress events into something a person can read.

On a terminal (`LiveRenderer`) a spinner, elapsed time and, when the total is
known, a bar are redrawn in place. Anywhere else (systemd journal, a pipe,
a file) `LineRenderer` writes plain lines only (no colour codes, no carriage
returns) and a "still waiting" heartbeat every 30 s of silence so a long model
call does not look frozen. Everything goes to the stream you pass in (stderr
for the CLI); stages themselves never print.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from typing import TextIO

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn
from rich.text import Text

from research_pipeline.progress import ProgressEvent

HEARTBEAT_SECONDS = 30.0


def format_seconds(seconds: float) -> str:
    """`41s` under a minute, `1m30s` from a minute on."""
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    return f"{total // 60}m{total % 60:02d}s"


def format_line(event: ProgressEvent) -> str | None:
    """The plain-text form of an event (None = print nothing)."""
    if event.kind == "start":
        return f"{event.stage}: {event.label}…" if event.label else f"{event.stage}…"
    if event.kind == "note":
        return f"  {event.label}" if event.label else None
    if event.kind == "step":
        if event.stage == "summarize" and event.outcome in ("done", "stopped", "failed"):
            # Exactly today's per-paper line (cli._paper_progress).
            if event.outcome == "done":
                tail = f"done (summary + critique, {event.elapsed:.0f}s)"
            elif event.outcome == "stopped":
                tail = "stopped: usage limit reached"
            else:
                tail = f"failed: {event.detail}"
            return f"[{event.current}/{event.total}] {event.label} {tail}"
        prefix = f"[{event.current}/{event.total}] " if event.total else ""
        line = f"  {prefix}{event.label}"
        if event.outcome:
            line += f" {event.outcome}"
        if event.detail:
            line += f": {event.detail}"
        return line
    if event.kind == "done":
        if event.outcome == "failed":
            return f"{event.stage} failed: {event.detail}"
        return f"{event.stage} done ({format_seconds(event.elapsed)})"
    return None


class LineRenderer:
    """Plain lines plus a heartbeat; safe for journals and pipes."""

    def __init__(
        self,
        out: TextIO,
        heartbeat: float = HEARTBEAT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        start_thread: bool = True,
    ) -> None:
        self._out = out
        self._heartbeat = heartbeat
        self._clock = clock
        self._lock = threading.Lock()
        self._waiting_label: str | None = None
        self._waiting_since = 0.0
        self._last_output = clock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start_thread:
            self._thread = threading.Thread(target=self._run, name="progress-heartbeat", daemon=True)
            self._thread.start()

    def _write(self, line: str) -> None:
        self._out.write(line + "\n")
        self._out.flush()
        self._last_output = self._clock()

    def emit(self, event: ProgressEvent) -> None:
        with self._lock:
            line = format_line(event)
            if line is not None:
                self._write(line)
            now = self._clock()
            if event.kind == "done":
                self._waiting_label = None
            else:
                if event.kind == "note":
                    self._waiting_label = event.label
                elif event.kind == "start":
                    self._waiting_label = event.label or event.stage
                else:
                    self._waiting_label = event.stage
                self._waiting_since = now
            self._last_output = now

    def tick(self) -> None:
        """One heartbeat check: print `still waiting` if nothing was written for `heartbeat` seconds."""
        with self._lock:
            now = self._clock()
            if self._waiting_label and now - self._last_output >= self._heartbeat:
                waited = format_seconds(now - self._waiting_since)
                self._write(f"still waiting: {self._waiting_label} ({waited})")

    def _run(self) -> None:
        while not self._stop.wait(self._heartbeat):
            self.tick()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)


class _BarIfKnown(BarColumn):
    """A bar only for tasks whose total is known (and above zero)."""

    def render(self, task):  # type: ignore[no-untyped-def]
        if not task.total:
            return Text("")
        return super().render(task)


class LiveRenderer:
    """In-place spinner, elapsed time and (when the total is known) a bar, on a terminal."""

    def __init__(self, out: TextIO) -> None:
        self._console = Console(file=out, highlight=False)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            _BarIfKnown(),
            TextColumn("{task.fields[count]}"),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
        )
        self._live = self._progress.live
        self._tasks: dict[str, TaskID] = {}
        self._closed = False
        self._progress.start()

    def emit(self, event: ProgressEvent) -> None:
        if self._closed:
            return
        if event.kind == "start":
            total = event.total if event.total and event.total > 0 else None
            description = f"{event.stage}: {event.label}" if event.label else event.stage
            self._tasks[event.stage] = self._progress.add_task(description, total=total, count="")
        elif event.kind == "done":
            task = self._tasks.pop(event.stage, None)
            if task is not None:
                self._progress.remove_task(task)
            line = format_line(event)
            if line:
                self._print(line)
        else:
            task = self._tasks.get(event.stage)
            if event.kind == "note" and task is not None:
                self._progress.update(task, description=f"{event.stage}: {event.label}")
            elif event.kind == "step":
                if task is not None and event.current is not None:
                    count = f"{event.current}/{event.total}" if event.total else str(event.current)
                    self._progress.update(task, completed=event.current, count=count)
                if event.outcome:
                    line = format_line(event)
                    if line:
                        self._print(line)

    def _print(self, line: str) -> None:
        self._console.print(Text(line))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._progress.stop()


def make_reporter(out: TextIO | None = None) -> LineRenderer | LiveRenderer:
    """Live display on a terminal, plain lines and heartbeat otherwise."""
    stream = out if out is not None else sys.stderr
    if stream.isatty():
        return LiveRenderer(stream)
    return LineRenderer(stream)
