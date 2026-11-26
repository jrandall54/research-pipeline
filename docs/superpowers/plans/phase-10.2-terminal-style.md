# Phase 10.2 — A cleaner terminal look: Implementation Plan

> **Phase 10.2** · Status: merged · Design: [phase-10.2-terminal-style-design.md](../specs/phase-10.2-terminal-style-design.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

**Goal:** Every `rpipe` command uses one consistent look, from one style module (the design's "The look" table): bold cyan rule headers, dim explanations, `›` before prompts, `✓` / `!` / `✗` result marks, aligned key-value lines.

**Architecture:** A new module, `src/research_pipeline/term.py`, in the COMPOSITION group with `cli` and `progress_render` (add it to `COMPOSITION` in `tests/test_architecture.py`). It holds every helper. `cli.py` swaps plain `typer.echo` calls for these helpers, command group by command group. No wording changes.

**Tech stack:** `typer.style` / `click.echo`. Colour comes from click and is stripped when output isn't a terminal. No new dependencies.

**Spec:** the design file above.

## Global constraints

- **Wording is frozen.** Change no user-facing sentence, except to:
  - add the marks;
  - drop a leading `Warning: ` or `Error: ` that a mark now replaces;
  - indent.
  Tests that assert exact old text get updated only for these three changes.
- **Colour only on a terminal.** All styling goes through `term.py`. Colour is on only when the target stream is a tty and `NO_COLOR` is unset; CliRunner output must contain no `\x1b[`. The marks `✓ ! ✗ › • ─` are plain characters and always print.
- **Width:** header rules are `min(80, terminal width)` columns wide, read with `shutil.get_terminal_size((80, 24))`.
- **Streams unchanged.** Errors still go to stderr (`_fail`), and exit codes are unchanged.
- **Nightly output stays plain.** `rpipe run` under the systemd timer isn't a tty, so the journal stays plain text. Add a test for this.
- **Progress bars:** `progress_render.py`'s live bars are untouched.
- **Web app:** no changes.
- **Docs:** each task updates the sample output in the guide pages it touches, in the same commit. Task 1 adds `docs/guide/terminal.md`, in the CLAUDE.md page shape (what, how, why with the rejected boxed/minimal options, how to check including `NO_COLOR`, where the code is), and links it from `docs/guide/README.md` or the guide index if one exists.
- **Checks:** tests offline with `uv run pytest -q`; `uv run ruff check` clean.

## `term.py` interface

```python
def use_colour(err: bool = False) -> bool          # tty and no NO_COLOR
def header(title: str) -> None                     # "\n── Title ───…" bold cyan
def step(n: int, total: int, name: str) -> None    # header(f"Step {n} of {total} · {name}")
def hint(text: str) -> None                        # dim, every line indented 2
def ok(text: str) -> None                          # "✓ text", ✓ green
def warn(text: str) -> None                        # "! text", ! yellow
def error(text: str) -> None                       # "✗ text" to stderr, ✗ red
def bullet(text: str, indent: int = 2) -> None     # "  • text"
def fields(rows: list[tuple[str, str]], indent: int = 2) -> None  # keys dim, padded to the longest key + 2
def strong(text: str) -> str                       # bold, returns a string
def cmd(text: str) -> str                          # cyan, returns a string
def prompt(text: str, **kw) -> Any                 # typer.prompt with "› " prefix (bold when colour is on)
def confirm(text: str, **kw) -> bool               # typer.confirm, same prefix
```

Implement styling as `typer.style(...)` only when `use_colour()` is true, so the strings stay plain otherwise. Don't rely on click stripping ANSI codes: prompts don't always pass through `echo`.

## Review focus

1. Piped or non-tty output (`rpipe profile show | cat`, the systemd journal, CliRunner) must contain no escape codes.
2. `NO_COLOR=1` on a real terminal gives plain text, with the marks still printed.
3. A narrow terminal (40 columns) must not wrap the header rule.
4. Error paths (`_fail`) still print to stderr with exit code 1.
5. Untrusted text such as followed feed names and paper titles still goes through `clean_text`; adding styling must not bypass it.

## Tasks

### Task 1: `term.py`, the init wizard, profile and model commands

**Files:** create `src/research_pipeline/term.py` and `tests/test_term.py`; modify `cli.py` (`_fail`, `_step`, `_wizard*`, the `profile` group, the `model` group, and the interactive helpers they share, such as `_ask` and the seed look-up/confirm flow) and `tests/test_architecture.py`; add `docs/guide/terminal.md`; update the samples in `docs/guide/vault-and-config.md` and `docs/guide/profile.md`.

- [x] Tests first, in `tests/test_term.py`:
  - `test_plain_when_not_tty`: each helper's output has no `\x1b`.
  - `test_colour_when_tty`: monkeypatch `use_colour` to return True, then `ok` output contains `\x1b[`.
  - `test_no_color_env`: `NO_COLOR=1` with a fake tty gives `use_colour() is False`.
  - `test_header_width`: with a 40-column terminal the rule line is exactly 40 characters; with 200 columns it is 80.
  - `test_fields_align`: the values start in the same column.
  - `test_error_to_stderr`.
- [x] The wizard uses `step(n, 5, name)` and `hint(...)` for each explainer.
- [x] The categories menu uses the `✓` style rows: a ticked row shows a green `✓`, an unticked row shows a blank mark and is dimmed, and the `Also selected:` line is kept.
- [x] "Added topic: …" and similar lines use `ok`. Prompts go through `term.prompt` / `term.confirm`. `profile show` uses `header` and `bullet`/numbered lists plus `fields`.
- [x] Ending: "All set. Next:" becomes `header("All set")`, then the commands with `cmd()`, aligned.
- [x] Update `test_init_wizard.py` and `test_categories.py` assertions where the marks change (`[x]` becomes `✓`).
- [x] Commit: `feat: term.py styles; init, profile and model commands use it (Phase 10.2)`.

### Task 2: run, status, schedule, app, sources, requests, shelf

**Files:** modify `cli.py` (those groups, the end-of-run summary and health lines, the "Next steps" outside a terminal); update the tests asserting their text; update the samples in `docs/guide/running.md`, `docs/guide/app.md` and the other guide pages that quote this output.

- [x] `status` and the run summary use `header` and `fields`.
- [x] Problems use `warn`.
- [x] Install/uninstall results use `ok`.
- [x] Add `test_run_output_plain_when_piped`.
- [x] Commit: `feat: run, status, schedule, app, sources, requests and shelf use term.py (Phase 10.2)`.

### Task 3: share, gold, eval, catalog and anything left

**Files:** modify `cli.py` (the remaining groups); update `_browse_entry`, keeping `clean_text` on every untrusted field before styling; update the tests and the samples in `docs/guide/sharing.md`, `docs/guide/` for gold and eval, and `README.md` if it shows output.

- [x] Sweep with `grep -n "typer.echo" src/research_pipeline/cli.py`. Every remaining plain echo is either converted or intentionally plain (data output such as `rpipe model catalog show` tables, ids, paths meant for scripts).
- [x] Record those intentional ones in `docs/guide/terminal.md` under "What stays plain".
- [x] Update the ROADMAP row (Phase 10.2) and this plan's status.
- [x] Commit: `feat: share, gold, eval and catalog use term.py (Phase 10.2)`.
