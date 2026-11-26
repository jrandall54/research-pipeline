# The terminal look (`term.py`)

## What it does

Every `rpipe` command prints through one small set of styles, so the output looks the same everywhere and is easy to scan:

```
── Step 4 of 5 · arXiv categories ──────────────────────────────────────────────
  (a dimmed explanation, indented two spaces)

  ✓   2  cs.LG    Machine learning
      4  cs.CV    Computer vision (images, video)
› Categories [cs.LG, cs.CL]:
✓ Added topic: prompt injection
! Skipped: There is no option 18
✗ No vault found at ...            (this one goes to stderr)
```

| Kind of line | Look | Helper |
|---|---|---|
| Section header | blank line, then `── Title ───…` in bold cyan | `header(title)`, `step(n, total, name)` |
| Explanation text | dim, indented 2 | `hint(text)` |
| Success | green `✓` | `ok(text)` |
| Warning | yellow `!` | `warn(text)` |
| Error (stderr) | red `✗` | `error(text)` (used by `_fail`) |
| Prompt | bold `›` | `prompt(...)`, `confirm(...)` |
| `key  value` lines | key dim, values lined up | `fields(rows)` |
| List item | `  • item` | `bullet(text)` |
| Command or name inside text | cyan / bold | `cmd(text)`, `strong(text)` |

Phase 10.2 changed only how text looks. The sentences are the same ones as before, apart from the marks, the indent, and a few `Topics:`-style labels that became headers.

## How it works

1. Each helper builds its line as a plain string, then asks `use_colour()` whether to colour it.
2. `use_colour()` is true only when the stream being written to (stdout, or stderr for errors) is a terminal **and** the `NO_COLOR` environment variable is not set. When true, the pieces are wrapped with `typer.style` (ANSI colour codes); when false they stay plain.
3. The marks `✓ ! ✗ › • ─` are ordinary characters, so they print either way. Only the colour is conditional.
4. `header` reads the terminal width with `shutil.get_terminal_size((80, 24))` and draws the rule `min(80, width)` columns wide, so a narrow window never wraps it.

Worked example: `rpipe profile show | cat` and the nightly `rpipe run` under systemd are not terminals, so they print `── Topics ───…` and `✓ ...` with no escape codes at all. The same command on your screen prints the header in bold cyan.

## Why it's built this way

- **Colour only on a terminal.** Escape codes in a pipe, a log file or the systemd journal are unreadable noise (`\x1b[32m...`). Checking `isatty` is the standard fix, and `NO_COLOR` (see no-color.org) is the standard opt-out.
- **Plain marks, always.** A `✓` is readable even without colour, so colour is only decoration and nothing depends on it. Tests can pin the marks.
- **`typer.style`, not `rich`.** rpipe already uses rich for live progress bars (`progress_render.py`, untouched). For static lines, `typer.style` is a few lines and keeps the strings plain when colour is off. We rejected routing everything through rich because its markup would also be applied to text we print from outside (paper titles, feed names), which is a risk.
- **Rejected: boxed output** (rules and corners around sections). It looks busy and wraps badly in narrow windows. **Rejected: minimal** (no marks, colour only). It made success, warning and error hard to tell apart in a long wizard.
- **Untrusted text is still cleaned** (`clean_text`) before it reaches these helpers; styling does not bypass it.

## How to run / check it

- `rpipe init` in a new vault, or `rpipe profile show`: see headers, ticks and aligned fields.
- `NO_COLOR=1 rpipe profile show`: same text, no colour, marks kept.
- `rpipe profile show | cat`: no escape codes (the pipe is not a terminal).
- Shrink the window to under 80 columns: the header rules shrink with it.
- Tests: `uv run pytest -q tests/test_term.py`.

## Where the code is

- `src/research_pipeline/term.py`: the helpers. It is in the composition group (see [architecture.md](architecture.md)); it imports only `typer` and the standard library.
- `src/research_pipeline/cli.py`: `_fail`, the init wizard, the `profile` and `model` commands call it. Other command groups move over in later Phase 10.2 tasks.
- `tests/test_term.py`: no escape codes off a terminal, colour on one, `NO_COLOR`, header width, alignment, errors to stderr.
