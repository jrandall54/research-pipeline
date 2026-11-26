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
| Warning | yellow `!` | `warn(text)`, `warn(text, err=True)` for stderr |
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
4. `header` reads the terminal width with `shutil.get_terminal_size((80, 24))` and draws the rule `min(80, width)` columns wide, so a narrow window never wraps it. A title that is itself wider than the terminal is never cut: it prints in full, with no rule after it, and the terminal wraps it. Commands therefore keep headers short and put long text (counts, instructions such as `Ctrl-C stops; grades given are kept.`) in a `hint` line under the header.

Worked example: `rpipe profile show | cat` is not a terminal, so it prints `── Topics ───…` with no escape codes at all; the same command on your screen prints the header in bold cyan. The nightly `rpipe run` under systemd is not a terminal either, so its end-of-run summary is plain text for the journal:

```
✓ Digest written to <vault>/digests/2026-11-21.md
  inserted       339
  updated        11
  triaged        15
  skipped        324
```

(`✓` and the two-space indent are ordinary characters; there is no `\x1b` anywhere. A test pins this: `test_run_output_plain_when_piped`.)

**Messages with several lines.** `ok`, `warn` and `error` indent every line after the first by two spaces, so a second line sits under the text and not under the mark:

```
! Not ready yet: model not installed
  Fix it, then run rpipe model setup
```

**Warnings on stderr.** `warn(text, err=True)` sends the line to stderr. The run uses it for "unknown categories" and for the "another run holds the lock" message, so a script reading stdout never sees them.

## What stays plain

Some output is deliberately not styled, because it is data that people copy, pipe or read line by line, or because it is a one-line answer where a mark adds nothing:

- **Data listings:** `rpipe runs` rows, `rpipe feedback` rows (the votes, saved papers and counts under their headers), `rpipe show` and the review screen's paper text (title, id, summary, link), `rpipe gold list` / `suggest` / `edit-fact` fact lists, the `rpipe eval grade` paper and summary text, `rpipe eval report`, `rpipe model catalog show` rows and the catalog diff lines, `rpipe shelf list` rows, `rpipe sources` rows (indented 2 like other listings), `rpipe profile show` numbered lists, the numbered pick lists (`_pick_index`, the model pick menus), `rpipe share following` lines.
- **Text meant to be copied:** `$ command` lines and the indented command lines of `rpipe model tune`, the manual instructions and drop-in contents, `rpipe schedule show` unit text (`# name` plus the systemd file), the `Serving ... at http://...` line of `rpipe serve`.
- **Short answers:** `Not saved.`, `Nothing was created or changed.`, `Nothing was deleted or changed.`, `Nothing to review.`, `Nothing left to grade.`, `The catalog is already up to date`, `... is already in your profile`, `No feeds found.`, `You follow nobody yet.`, `Following: nobody yet`, and the lines of the form `Summarizing N paper(s); ...`, `Found N of M proposed papers`. They are answers, not events. Cancellations (`Cancelled. Nothing was changed.`, `Nothing was changed.`) are dim `hint` lines everywhere, a Ctrl-C stop is a `!` warning everywhere (`Saved what you entered so far`, `Stopped. ...`), and a closing pointer to the next step is `Next:` as a hint line (or a `Next steps` header).
- **Stderr progress lines** (`_progress`, `  line`): the live progress display owns those.
- **`NO_MODEL`:** the same sentence is also an error message, so it prints as text.

The share commands follow the same rules as the rest: `rpipe share status` prints `Publishing: on as '...'` in bold with `fields` rows under it and bulleted followed feeds; `rpipe share browse` prints each person as a bold name line plus two dimmed lines, and every untrusted field (display name, handle, topics, feed errors) goes through `clean_text` before it is styled.

## Why it's built this way

- **Colour only on a terminal.** Escape codes in a pipe, a log file or the systemd journal are unreadable noise (`\x1b[32m...`). Checking `isatty` is the standard fix, and `NO_COLOR` (see no-color.org) is the standard opt-out.
- **Plain marks, always.** A `✓` is readable even without colour, so colour is only decoration and nothing depends on it. Tests can pin the marks.
- **`typer.style`, not `rich`.** rpipe already uses rich for live progress bars (`progress_render.py`, untouched). For static lines, `typer.style` is a few lines and keeps the strings plain when colour is off. We rejected routing everything through rich because its markup would also be applied to text we print from outside (paper titles, feed names), which is a risk.
- **Rejected: boxed panels** (explanations in rounded boxes). More visual, but it takes more vertical space, and the boxes break when the text wraps. **Rejected: minimal** (bold headers only). Little change, but most of the clunkiness stays.
- **Untrusted text is still cleaned** (`clean_text`) before it reaches these helpers; styling does not bypass it.

## How to run / check it

- `rpipe init` in a new vault, or `rpipe profile show`: see headers, ticks and aligned fields.
- `NO_COLOR=1 rpipe profile show`: same text, no colour, marks kept.
- `rpipe profile show | cat`: no escape codes (the pipe is not a terminal).
- Shrink the window to under 80 columns: the header rules shrink with it, and a long header title still shows in full.
- Tests: `uv run pytest -q tests/test_term.py`.

## Where the code is

- `src/research_pipeline/term.py`: the helpers. It is in the composition group (see [architecture.md](architecture.md)); it imports only `typer` and the standard library.
- `src/research_pipeline/cli.py`: `_fail`, the init wizard, the `profile` and `model` commands call it. The run, collect, triage, summarize, digest, requests, shelf, schedule, app and sources commands, the whole `model` group (scan, recommend, setup, tune, fit, bench, catalog), vote/save/feedback/review, and the share, gold and eval groups use it too.
- `tests/test_term.py`: no escape codes off a terminal, colour on one, `NO_COLOR`, header width (including a title longer than the window), alignment, errors to stderr.
