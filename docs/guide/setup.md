# Setup walkthrough

## What it does

`rpipe model setup` walks you through choosing which backend writes summaries: it looks at your computer, recommends a backend, lets you accept or override it, does the one-time preparation (pulling a model, checking a login), saves the choice and tests it. `rpipe init` runs the same walkthrough as its first step, because the next step ("describe your research") already calls the model. You never edit `config.yaml` by hand.

## What the walkthrough looks at

`detect_machine` gathers one `MachineInfo` record:

- **Operating system and memory.** Linux, macOS (Darwin) or Windows, and total RAM where it is easy to read.
- **GPU.** NVIDIA via `nvidia-smi` (when several GPUs exist, the one with the most memory wins), AMD via `rocm-smi` (each card is read on its own, so one unreadable card is skipped and the others still count), Apple Silicon via the total memory from `sysctl`. Apple chips share one memory pool with the CPU, and macOS lets the GPU use about two thirds of it, so only 66% (`APPLE_GPU_FRACTION`) is counted as GPU memory. A probe that is missing, exits non-zero (for example an NVIDIA driver mismatch) or prints something unreadable is skipped, never a crash; the next probe is tried, then "no GPU".
- **Ollama.** Whether the program is installed, whether its server answers within 5 seconds, and which models are already pulled.
- **Claude Code.** Where the `claude` program is and whether you are logged in (see [backends.md](backends.md)).

## How it recommends

`recommend` applies these rules in order:

1. A GPU with at least 6 GB (`MIN_GPU_VRAM_GB`) means `ollama`. It says to install Ollama if it is missing, and if no text-writing model is pulled yet (embedding-only models, with "embed" in the name, do not count) it proposes the largest table model that fits the GPU. The other two backends stay on the menu.
2. Otherwise, if you are logged in to Claude Code, it recommends `claude-code`, with the other two on the menu.
3. Otherwise it recommends `none` (abstracts only) and says what would unlock more: a GPU with 6 GB or more plus Ollama, or a Claude Code login. A CPU-friendly pipeline is planned.

The model table (`defaults/ollama_models.yaml`, sizes from ollama.com, checked 2026-11-21):

| GPU memory at least | Model tag | Download |
|---|---|---|
| 6 GB | `qwen3:8b` | 5.2 GB |
| 12 GB | `qwen3:14b` | 9.3 GB |
| 24 GB | `qwen3:32b` | 20 GB |

Install hints shown when Ollama is missing (`OLLAMA_INSTALL`): Linux `curl -fsSL https://ollama.com/install.sh | sh`; macOS `brew install ollama` or the download page; Windows the download page.

## The walkthrough, step by step

1. **What was found.** One line each: operating system and memory (shown as "GB" but counted in GiB, 1024 x 1024 x 1024 bytes, so it can read slightly lower than the box says), the GPU (name and memory, or "no GPU found"), Ollama (installed, running, models pulled), Claude Code (found, logged in).
2. **The recommendation and its reasons**, then a numbered menu that always lists all three built-ins: the recommended one first, marked `[recommended]` (the default, just press Enter), then the other two in the order Ollama, Claude Code, abstracts only. You can pick one the machine is not ready for yet; the "not installed" and "not logged in" re-check loops below then run, so you can fix it and carry on.
3. **If you pick `ollama`.** Ollama missing: the install command for your OS is printed and you are asked "Installed it? Check again"; yes re-detects, no moves on (the final check will then say what is missing). If the recommendation proposed a model: "Pull qwen3:14b now (9.3 GB download)?" (default yes), with progress lines while it downloads. Then the backend is saved.
4. **If you pick `claude-code`.** Not logged in: "Run `claude` once in a terminal and log in, then come back", with the same re-check loop. Then it explains the trade: paper text goes to Anthropic, about 30 calls a night come out of your plan's usage quota, your shelf (`my_work/`) never leaves the machine, and `haiku` uses less quota than `sonnet`. You pick `sonnet` (default), `haiku` or `opus`. The backend is saved with the path of the `claude` program, so the nightly timer finds it even though it has no `~/.local/bin` on its PATH.
5. **If you pick `none`.** Saved; digests show abstracts only.
6. **The final check.** The saved backend is built and tested exactly as a run would. `Ready: <what was checked>` means done. `Not ready yet: <reason>` plus "Fix it, then run rpipe model setup" means your choice is saved but something is missing; the command exits 1. `none` is always ready.

Worked transcripts (abridged):

```
$ rpipe model setup                              # 16 GB NVIDIA card, Ollama running, no models
What I found on this computer:
  system: Linux, 32 GB of memory
  graphics card: RTX 5070 Ti, 16.0 GB of GPU memory
  Ollama: installed, running; no models pulled yet
  Claude Code: not found
Recommended: ollama
  - RTX 5070 Ti has 16.0 GB of GPU memory, enough to run a model locally
  1. a model on this computer (Ollama)  [recommended]
  2. Claude Code (your login)
  3. no summaries (abstracts only)
Choice [1]:
Pull qwen3:14b now (9.3 GB download)? [Y/n]:
  pulling manifest ... downloading 42% ... success
Ready: Ollama is running at http://localhost:11434 and model qwen3:14b is installed.
```

```
$ rpipe model setup                              # no GPU, logged in to Claude Code
Recommended: claude-code
  - no GPU with at least 6 GB was found, but you are logged in to Claude Code
  1. Claude Code (your login)  [recommended]
  2. a model on this computer (Ollama)
  3. no summaries (abstracts only)
Choice [1]:
With Claude Code, the text of each paper is sent to Anthropic ... about 30 calls ...
Model (sonnet, haiku or opus) [sonnet]: haiku
Ready: Claude Code is logged in at /home/you/.local/bin/claude and model haiku answered.
```

```
$ rpipe model setup                              # no GPU, no login
Recommended: none
  1. no summaries (abstracts only)  [recommended]
  2. a model on this computer (Ollama)
  3. Claude Code (your login)
Choice [1]:
Ready: abstracts-only mode needs nothing else.
```

Without a terminal the command refuses ("rpipe model setup is interactive; use `rpipe model set-backend NAME` in scripts"). The scriptable equivalents are `rpipe model set-backend`, `set` and `check` ([llm.md](llm.md)).

## Why it's built this way

- **Every probe is injected.** Commands run through a `CmdRunner` and the server check through a function, so tests use fakes and never touch a real GPU tool, Ollama or the network.
- **Failures degrade, never crash.** A broken `nvidia-smi` is common (driver updates); treating it as "no NVIDIA GPU" keeps setup usable.
- **rpipe never installs Ollama.** It prints the command; you run it. Pulling a model needs your yes/no.
- **6 GB floor.** Below that, models that summarize well do not fit fully in GPU memory and run painfully slowly. Rejected: recommending a CPU model, which is why `none` is the fallback for now.
- **Backend first in `rpipe init`.** The describe step asks the model to propose topics, so the model has to exist before it. Rejected: asking last, which would make the first model call fail on every fresh install.
- **Save, then check.** The choice is written before the final check, so a missing model or login leaves you with the right setting and one thing to fix, not a half-finished setup. Rejected: saving only on success, which loses the choice on every hiccup.
- **A yes/no before any download.** A pull is gigabytes; it needs your yes.
- **Table in a data file** so new models can be added without code changes.

## How to run / check it

`rpipe model setup` (any time; it is safe to re-run), or let `rpipe init` run it. When a config already exists the first question is "Keep current backend (X)? [Y/n]": Enter keeps the backend and any model you pinned with `rpipe model set`, skips the menu and only re-runs the final check; `n` walks the menu (choosing a different backend resets the model to `auto`, because names differ per backend; choosing the same one again keeps it). Tests: `uv run pytest -q tests/test_machine.py tests/test_setup_wizard.py` (offline: machine detection, the model pull and the `claude` program are all faked; they also cover the probe timeout (exit code 124), `ollama_tags`, and Ctrl-C at a question, which prints "Stopped. Nothing further was changed" and exits 130).

## Where the code is

`src/research_pipeline/machine.py`, `src/research_pipeline/defaults/ollama_models.yaml`; `_wizard_backend` and the `model setup` command in `src/research_pipeline/cli.py`; `pull_model` in `src/research_pipeline/llm.py`. Tests: `tests/test_machine.py`, `tests/test_setup_wizard.py`.
