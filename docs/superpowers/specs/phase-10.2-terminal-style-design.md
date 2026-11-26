# Phase 10.2 — A cleaner terminal look (design)

> **Phase 10.2** · Status: merged · Plan: [phase-10.2-terminal-style.md](../plans/phase-10.2-terminal-style.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

Date: 2026-11-26. Owner request after Phase 10.1: "Can you clean up the formatting in the terminal to make it more pretty? It's kind of clunky looking at the moment."

## Intent

The `rpipe` commands print plain, unstyled text. Headings, explanations, prompts and results all look the same, which makes long output (`rpipe init`, `rpipe app status`, `rpipe schedule status`, `rpipe share status`, `rpipe share browse`) hard to scan. The goal is to give every command one consistent look that makes it easy to tell which line is which, without changing what is said.

## Goals and non-goals

- Goal: one look across the whole CLI, built on one small style module, so every command matches.
- Goal: easier to scan. Section headers stand out, explanations step back, and results and problems are marked.
- Non-goal: rewording. The copy stays as it is (Phase 10.1 just settled it). Only marks, styling and spacing change.
- Non-goal: the web app. Its look is frozen (owner ruling, 2026-11-26).
- Anti-goal: output that breaks when piped, logged by the nightly timer, or read by tests. Styling has to switch off cleanly in those cases.

## Approaches (owner picked "Headers + rules")

| Approach | Trade-off |
|---|---|
| **Headers + rules** (picked) | A bold cyan header with a rule for each section, dim explanations, `›` before prompts, a green `✓` for saved results. Still reads as plain text and stays compact. |
| Boxed panels | Explanations sit in rounded boxes. More visual, but it takes more vertical space, and the boxes break when the text wraps. |
| Minimal | Bold headers only. Little change, but most of the clunkiness stays. |

Scope: the whole CLI (owner pick), not just `rpipe init`.

## The look (decided)

| Kind of line | Look | Helper in `term.py` |
|---|---|---|
| Section header | blank line, then `── Title ──────…` in bold cyan, to 80 columns or the terminal width if narrower | `header(title)` |
| Wizard step | `header("Step 4 of 5 · arXiv categories")` | `step(n, total, name)` |
| Explanation / help text | dim, indented 2 | `hint(text)` |
| Success / saved | green `✓ ` + text | `ok(text)` |
| Warning | yellow `! ` + text (replaces a leading `Warning: `) | `warn(text)` |
| Error (stderr) | red `✗ ` + text | `error(text)`, used by `_fail` |
| Prompt | bold `› ` + prompt text | `prompt(...)` / `confirm(...)` wrappers |
| Key: value lines (status, profile show) | key dim, padded to a common width; value plain | `fields(rows)` |
| Lists | `  • item`, or numbered with right-aligned numbers | `bullet(text)` |
| Ticked menu rows | `  ✓  2  cs.LG   Machine learning` ticked (green ✓), otherwise blank mark and dim row | used by the categories menu |
| Names / commands inside text | bold name, cyan `rpipe …` command | `cmd(text)`, `strong(text)` |

Styling uses `typer.style` / `click`. Colour is on only when stdout is a terminal, so pipes, the systemd journal and `CliRunner` tests get plain text. The `NO_COLOR` environment variable turns it off too. The unicode marks (`✓ ! ✗ › • ─`) are always printed. They are part of the text, and the tests pin them.

The progress display (`progress_render.py`, rich) keeps its own renderer. Only its final summary lines use the helpers.

## Left to the builder

The exact per-command layout, within the vocabulary above. If an existing line doesn't fit any kind, it stays plain.
