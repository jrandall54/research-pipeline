"""Terminal styling for the `rpipe` commands: one small vocabulary, plain text off a terminal.

Colour is used only when the target stream is a terminal and NO_COLOR is unset, so pipes, the
systemd journal and tests get plain text. The marks (✓ ! ✗ › • ─) are ordinary characters and
always print. See docs/guide/terminal.md.
"""

import os
import shutil
import sys
import textwrap
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


def width() -> int:
    """The width to lay text out in: the terminal's, at most 80 columns."""
    return min(MAX_WIDTH, shutil.get_terminal_size((MAX_WIDTH, 24)).columns)


def _fill(line: str, room: int, indent: str) -> list[str]:
    return textwrap.wrap(line, max(room, 20), break_long_words=False, break_on_hyphens=False,
                         initial_indent=indent, subsequent_indent=indent) or [indent]


def reflow(text: str, indent: int = 0) -> list[str]:
    """Lines of `text`, each prefixed with `indent` spaces, fitted to the terminal.

    On a terminal 80 columns or wider, or when every line already fits, the text is printed as
    written (the copy is laid out for 80 columns). On a narrower terminal (a phone) the text is rewrapped instead, so the terminal
    doesn't wrap each long line again into a ragged long-short pattern: consecutive lines join into
    one paragraph; blank lines and lines starting with a space (lists, examples) keep their break.
    """
    pad = " " * indent
    lines = text.split("\n")
    room = width() - indent
    if width() >= MAX_WIDTH or all(len(line) <= room for line in lines):
        return [pad + line if line else "" for line in lines]
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            out.extend(_fill(" ".join(para), room, pad))
            para.clear()

    for line in lines:
        if not line.strip():
            flush()
            out.append("")
        elif line.startswith(" "):
            flush()
            lead = len(line) - len(line.lstrip(" "))
            out.extend(_fill(line.strip(), room - lead, pad + " " * lead))
        else:
            para.append(line.strip())
    flush()
    return out


def header(title: str) -> None:
    """A blank line, then `── Title ───…` as wide as the terminal (at most 80 columns).

    A title too wide for the terminal is printed in full with no trailing rule: never cut text.
    """
    cols = width()
    line = f"── {title} "
    if len(line) > cols:
        line = f"── {title}"
    else:
        line += "─" * (cols - len(line))
    typer.echo("\n" + _style(line, bold=True, fg="cyan"))


def step(n: int, total: int, name: str) -> None:
    header(f"Step {n} of {total} · {name}")


def hint(text: str) -> None:
    """Explanatory text: dim, every line indented 2, rewrapped on a narrow terminal."""
    for line in reflow(text, 2) or [""]:
        typer.echo(dim(line) if line else "")


def _align(text: str) -> str:
    """Indent the continuation lines of a multi-line message so they sit under the text, not the mark."""
    lines = reflow(text, 2)
    return "\n".join([lines[0][2:], *lines[1:]])


def _marked(mark: str, text: str, err: bool = False) -> None:
    """Print `mark text`; newlines at the start of `text` come out as blank lines before the mark."""
    body = text.lstrip("\n")
    if lead := len(text) - len(body):
        typer.echo("\n" * (lead - 1), err=err)
    typer.echo(f"{mark} {_align(body)}", err=err)


def ok(text: str) -> None:
    _marked(tick(), text)


def warn(text: str, err: bool = False) -> None:
    """`! text`; `err=True` sends it to stderr (for warnings a script should not read as output)."""
    _marked(_style("!", err=err, fg="yellow"), text, err)


def error(text: str) -> None:
    _marked(_style("✗", err=True, fg="red"), text, True)


def bullet(text: str, indent: int = 2) -> None:
    typer.echo(f"{' ' * indent}• {text}")


def fields(rows: list[tuple[str, str]], indent: int = 2) -> None:
    """`key  value` lines: keys dim, padded to the longest key plus 2, so the values line up."""
    if not rows:
        return
    pad = max(len(k) for k, _ in rows) + 2
    room = width() - indent - pad
    for key, value in rows:
        narrow = width() < MAX_WIDTH and len(value) > room
        first, *rest = _fill(value, room, "") if narrow else [value]
        typer.echo(f"{' ' * indent}{dim(key.ljust(pad))}{first}")
        for more in rest:
            typer.echo(" " * (indent + pad) + more)


def _mark() -> str:
    return _style("›", bold=True) + " "


def prompt(text: str, **kw: Any) -> Any:
    return typer.prompt(_mark() + text, **kw)


def confirm(text: str, **kw: Any) -> bool:
    return typer.confirm(_mark() + text, **kw)


def tick() -> str:
    """A green ✓ as a string, for rows built by hand (the ticked menu rows)."""
    return _style("✓", fg="green")
