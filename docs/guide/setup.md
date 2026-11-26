# Setup walkthrough

## What it does

`rpipe model setup` walks you through choosing which backend writes summaries: it looks at your computer, recommends a backend, lets you accept or override it, does the one-time preparation (pulling a model, checking a login), saves the choice and tests it. `rpipe init` runs the same walkthrough as its first step, because the next step ("describe your research") already calls the model. You never edit `config.yaml` by hand.

## What the walkthrough looks at

`detect_machine` gathers one `MachineInfo` record:

- **Operating system and memory.** Linux, macOS (Darwin) or Windows, and total RAM where it is easy to read.
- **GPU.** NVIDIA via `nvidia-smi` (when several GPUs exist, the one with the most memory wins), AMD via `rocm-smi` (each card is read on its own, so one unreadable card is skipped and the others still count), Apple Silicon via the total memory from `sysctl`. Apple chips share one memory pool with the CPU, and macOS lets the GPU use about two thirds of it, so only 66% (`APPLE_GPU_FRACTION`) is counted as GPU memory. A probe that is missing, exits non-zero (for example an NVIDIA driver mismatch) or prints something unreadable is skipped, never a crash; the next probe is tried, then "no GPU".
- **Ollama.** Whether the program is installed, whether its server answers within 5 seconds, and which models are already pulled.
- **Free memory, CPU and free disk.** Memory available right now, CPU threads and vector instructions, and free disk where Ollama keeps models; the full scan (every GPU, the Ollama server's settings) is `rpipe model scan`, see [hardware.md](hardware.md).
- **Claude Code.** Where the `claude` program is and whether you are logged in (see [backends.md](backends.md)).

## How it recommends

`recommend` ranks the model **catalog** for this machine with the same code as `rpipe model recommend` (see "Recommendations" in [hardware.md](hardware.md): each model is placed on the GPU, partly in RAM, or on the CPU, or left out), then applies these rules in order:

1. A catalog model fits on the GPU (fully, or a mixture-of-experts model partly in RAM): `ollama`. The reason names the GPU memory. It says to install Ollama if it is missing, and if no text-writing model is pulled yet (embedding-only models, with "embed" in the name, do not count) it proposes the top-ranked model.
2. Otherwise, if you are logged in to Claude Code, it recommends `claude-code`.
3. Otherwise, if a small model fits in memory (RAM) on the CPU: `ollama`, with the reason "no GPU found; a small model runs on the CPU overnight (slow)" (or "no catalog model fits in the GPU memory free now; ..."), proposing the top-ranked CPU model. Each model call normally may take at most 600 seconds, and on a slow CPU a long paper can need more. Run `rpipe model bench` (setup offers it): once the speed is measured, the limit grows to fit it, so long papers finish instead of timing out and falling back to their abstracts (see "The time limit for one call" in [llm.md](llm.md)). CPU choices come up on machines with no GPU or a GPU under 8 GB, and also on a bigger card when no catalog model fits in the GPU memory free right now (for example an 8 GB card with most of its memory taken by a game or another loaded model); see the CPU step in [hardware.md](hardware.md). If the card is only busy, closing what holds its memory and running `rpipe model setup` again may give a GPU choice instead.
4. Otherwise `none` (abstracts only), saying that no catalog model fits this computer's GPU memory or RAM and what would unlock more: a bigger GPU plus Ollama, more memory for a model on the CPU, or a Claude Code login.

The other two backends always stay on the menu. Which models can be proposed, and their download sizes, come from the catalog (`defaults/model_catalog.yaml`; `rpipe model catalog show` lists it). With the shipped catalog (2026-11-24), a 16 GB card is offered the 12 GB Qwen3.6 27B dense model; a computer with no GPU, or with a GPU under 8 GB, and 16 GB of memory is offered `qwen3:8b` (5.2 GB) on the CPU; with 8 GB of memory or less nothing fits and the answer is Claude Code or `none`.

Install hints shown when Ollama is missing (`OLLAMA_INSTALL`): Linux `curl -fsSL https://ollama.com/install.sh | sh`; macOS `brew install ollama` or the download page; Windows the download page.

## The walkthrough, step by step

1. **What was found.** One line each: operating system and memory (shown as "GB" but counted in GiB, 1024 x 1024 x 1024 bytes, so it can read slightly lower than the box says), the GPU (name and memory, or "no GPU found"), Ollama (installed, running, models pulled), Claude Code (found, logged in).
2. **The recommendation and its reasons**, then a numbered menu that always lists all three built-ins: the recommended one first, marked `[recommended]` (the default, just press Enter), then the other two in the order Ollama, Claude Code, abstracts only. You can pick one the machine is not ready for yet; the "not installed" and "not logged in" re-check loops below then run, so you can fix it and carry on.
3. **If you pick `ollama`.** Ollama missing: the install command for your OS is printed and you are asked "Installed it? Check again"; yes re-detects, no moves on (the final check will then say what is missing). If the recommendation proposed a model: the download size and the free disk where Ollama keeps models are shown (`download X GB; free disk where Ollama keeps models: Y GB`); if X + 2 GB is more than Y the download is refused (`not enough free disk`), otherwise "Download NAME now (X GB)? [y/N]" (default no; the same check and question as `rpipe model recommend`), with progress on stderr while it downloads (a live bar that follows the download percent on a terminal; in a log, `model pull…`, a line about every 10% such as `  qwen3:14b 43%`, then `model pull done (...)`). The final check prints `model check…` / `model check done (...)` on stderr before the `Ready:` line. Then the backend is saved.
4. **If you pick `claude-code`.** Not logged in: "Run `claude` once in a terminal and log in, then come back", with the same re-check loop. Then it explains the trade: paper text goes to Anthropic, about 30 calls a night come out of your plan's usage quota, your shelf (`my_work/`) never leaves the machine, and `haiku` uses less quota than `sonnet`. You pick `sonnet` (default), `haiku` or `opus`. The backend is saved with the path of the `claude` program, so the nightly timer finds it even though it has no `~/.local/bin` on its PATH.
5. **If you pick `none`.** Saved; digests show abstracts only.
6. **The final check.** The saved backend is built and tested exactly as a run would. `✓ Ready: <what was checked>` means done. `! Not ready yet: <reason>` plus "Fix it, then run rpipe model setup" means your choice is saved but something is missing; the command exits 1. `none` is always ready.

Worked transcripts (abridged):

```
$ rpipe model setup                              # 16 GB NVIDIA card, Ollama running, no models
What I found on this computer:
  system: Linux, 32 GB of memory
  graphics card: RTX 5070 Ti, 16.0 GB of GPU memory
  Ollama: installed, running; no models pulled yet
  Claude Code: not found
── Recommended: ollama ─────────────────────────────────────────────────────────
  • RTX 5070 Ti has 16.0 GB of GPU memory, enough to run a model locally
  1. a model on this computer (Ollama)  [recommended]
  2. Claude Code (your login)
  3. no summaries (abstracts only)
› Choice [1]:
download 12 GB; free disk where Ollama keeps models: 120 GB
› Download hf.co/unsloth/Qwen3.6-27B-GGUF:UD-IQ3_XXS now (12 GB)? [y/N]: y
  pulling manifest ... downloading 42% ... success
✓ Ready: Ollama is running at http://localhost:11434 and model ... is installed.
```

```
$ rpipe model setup                              # no GPU, logged in to Claude Code
── Recommended: claude-code ────────────────────────────────────────────────────
  • no GPU was found, but you are logged in to Claude Code
  1. Claude Code (your login)  [recommended]
  2. a model on this computer (Ollama)
  3. no summaries (abstracts only)
› Choice [1]:
With Claude Code, the text of each paper is sent to Anthropic ... about 30 calls ...
› Model (sonnet, haiku or opus) [sonnet]: haiku
✓ Ready: Claude Code is logged in at /home/you/.local/bin/claude and model haiku answered.
```

```
$ rpipe model setup                              # no GPU, no login, 16 GB of memory
── Recommended: ollama ─────────────────────────────────────────────────────────
  • no GPU found; a small model runs on the CPU overnight (slow)
  1. a model on this computer (Ollama)  [recommended]
  ...
download 5.2 GB; free disk where Ollama keeps models: 120 GB
› Download qwen3:8b now (5.2 GB)? [y/N]: y
Model qwen3:8b and a context of 32,768 tokens are both pinned: the automatic choice (auto) only accepts models that sit fully in GPU memory, and this one does not.
```

```
$ rpipe model setup                              # no GPU, no login, too little memory
── Recommended: none ───────────────────────────────────────────────────────────
  1. no summaries (abstracts only)  [recommended]
  2. a model on this computer (Ollama)
  3. Claude Code (your login)
› Choice [1]:
✓ Ready: abstracts-only mode needs nothing else.
```

Without a terminal the command refuses ("rpipe model setup is interactive; use `rpipe model set-backend NAME` in scripts"). The scriptable equivalents are `rpipe model set-backend`, `set` and `check` ([llm.md](llm.md)).

## Why it's built this way

- **Every probe is injected.** Commands run through a `CmdRunner` and the server check through a function, so tests use fakes and never touch a real GPU tool, Ollama or the network.
- **Failures degrade, never crash.** A broken `nvidia-smi` is common (driver updates); treating it as "no NVIDIA GPU" keeps setup usable.
- **rpipe never installs Ollama.** It prints the command; you run it. Pulling a model needs your yes/no.
- **One ranking for setup and `rpipe model recommend`.** The walkthrough proposes the top model of the same ranking, so the two never disagree. Rejected: the old fixed three-model table keyed on GPU size, which could not see free memory, server settings or newer models.
- **A CPU model before giving up.** A night has hours to spare, so a small model on the CPU is better than no summaries (with the per-call limit grown to the measured speed, as described above). A Claude Code login still comes first because it is far faster. Machines too small even for that get `none`.
- **Backend first in `rpipe init`.** The describe step asks the model to propose topics, so the model has to exist before it. Rejected: asking last, which would make the first model call fail on every fresh install.
- **Save, then check.** The choice is written before the final check, so a missing model or login leaves you with the right setting and one thing to fix, not a half-finished setup. Rejected: saving only on success, which loses the choice on every hiccup.
- **A yes before any download, after seeing size against free disk.** A pull is gigabytes; it needs your yes (the default is no) and is refused when the disk would be left with less than 2 GB.
- **Catalog in a data file** so new models can be added without code changes (`rpipe model catalog update`).

## How to run / check it

`rpipe model setup` (any time; it is safe to re-run), or let `rpipe init` run it. When a config already exists the first question is "Keep current backend (X)? [Y/n]": Enter keeps the backend and any model you pinned with `rpipe model set`, skips the menu and only re-runs the final check; `n` walks the menu (choosing a different backend resets the model to `auto`, because names differ per backend; choosing the same one again keeps it). Tests: `uv run pytest -q tests/test_machine.py tests/test_setup_wizard.py` (offline: machine detection, the model pull and the `claude` program are all faked; they also cover the probe timeout (exit code 124), `ollama_tags`, and Ctrl-C at a question, which prints "Stopped. Nothing further was changed" and exits 130).

After the wizard pulls a model for the GPU, the configured model stays `auto` on purpose: the list of installed models is part of the fit cache key, so the next `rpipe model fit` or `rpipe run` notices the new model, re-probes and can pick it (see `llm.md`); pin one with `rpipe model set NAME` if you want a specific model. A model pulled to run on the CPU or partly in RAM is different: its name and a 32,768-token context are pinned, because `auto` only accepts models that fit fully in GPU memory. Left on `auto`, such a machine would have no model `rpipe` accepts, and every night would fall back to abstracts.

The same applies when nothing is downloaded on a machine whose best option is the CPU or a spill (no GPU, a small GPU): step by step,

1. If a model was just downloaded, it is pinned as above.
2. Otherwise, if a catalog model that runs on this machine is already installed, the best-ranked installed one is pinned (model plus 32,768 tokens), with the same message. Example: no GPU, 16 GB of memory, `qwen3:4b` already pulled: the walkthrough asks no download question and pins `qwen3:4b`.
3. Otherwise (you declined the download, or the disk was too full) it says `No model that runs on this computer is installed yet: run `rpipe model recommend` to pick and pin a model that runs on this computer.` The final check then says what is missing.

On a machine where the best option fits fully on the GPU, nothing is pinned: `auto` finds it. To choose among more models (installed or not) later, use `rpipe model recommend`.

## Where the code is

`src/research_pipeline/machine.py` (`recommend`), `src/research_pipeline/recommender.py` (the ranking), `src/research_pipeline/defaults/model_catalog.yaml`; `_wizard_backend` and the `model setup` command in `src/research_pipeline/cli.py`; `pull_model` in `src/research_pipeline/llm.py`. Tests: `tests/test_machine.py`, `tests/test_setup_wizard.py`.
