"""Terminal styling for the `rpipe` commands: one small vocabulary, plain text off a terminal.

Colour is used only when the target stream is a terminal and NO_COLOR is unset, so pipes, the
systemd journal and tests get plain text. The marks (✓ ! ✗ › • ─) are ordinary characters and
always print. See docs/guide/terminal.md.
"""

import os
import shutil
import sys
from typing import Any

import typer

MAX_WIDTH = 80


def use_colour(err: bool = False) -> bool:
    """True when the stream (stdout, or stderr with `err`) is a terminal and NO_COLOR is unset."""
    if os.environ.get("NO_COLOR"):
        return False
    stream = sys.stderr if err else sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _style(text: str, err: bool = False, **kw: Any) -> str:
    return typer.style(text, **kw) if use_colour(err) else text


def strong(text: str) -> str:
    return _style(text, bold=True)


def cmd(text: str) -> str:
    return _style(text, fg="cyan")


def dim(text: str) -> str:
    return _style(text, dim=True)


def header(title: str) -> None:
    """A blank line, then `── Title ───…` as wide as the terminal (at most 80 columns)."""
    width = min(MAX_WIDTH, shutil.get_terminal_size((MAX_WIDTH, 24)).columns)
    line = f"── {title} "
    line += "─" * max(0, width - len(line))
    typer.echo("\n" + _style(line[:width], bold=True, fg="cyan"))


def step(n: int, total: int, name: str) -> None:
    header(f"Step {n} of {total} · {name}")


def hint(text: str) -> None:
    """Explanatory text: dim, every line indented 2."""
    for line in text.splitlines() or [""]:
        typer.echo(dim("  " + line) if line else "")


def _align(text: str) -> str:
    """Indent the continuation lines of a multi-line message so they sit under the text, not the mark."""
    first, *rest = text.split("\n")
    return "\n".join([first, *(("  " + line) if line else "" for line in rest)])


def ok(text: str) -> None:
    typer.echo(f"{tick()} {_align(text)}")


def warn(text: str, err: bool = False) -> None:
    """`! text`; `err=True` sends it to stderr (for warnings a script should not read as output)."""
    typer.echo(f"{_style('!', err=err, fg='yellow')} {_align(text)}", err=err)


def error(text: str) -> None:
    typer.echo(f"{_style('✗', err=True, fg='red')} {_align(text)}", err=True)


def bullet(text: str, indent: int = 2) -> None:
    typer.echo(f"{' ' * indent}• {text}")


def fields(rows: list[tuple[str, str]], indent: int = 2) -> None:
    """`key  value` lines: keys dim, padded to the longest key plus 2, so the values line up."""
    if not rows:
        return
    pad = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        typer.echo(f"{' ' * indent}{dim(key.ljust(pad))}{value}")


def _mark() -> str:
    return _style("›", bold=True) + " "


def prompt(text: str, **kw: Any) -> Any:
    return typer.prompt(_mark() + text, **kw)


def confirm(text: str, **kw: Any) -> bool:
    return typer.confirm(_mark() + text, **kw)


def tick() -> str:
    """A green ✓ as a string, for rows built by hand (the ticked menu rows)."""
    return _style("✓", fg="green")
