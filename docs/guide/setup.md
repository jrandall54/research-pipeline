# Setup walkthrough

## What it does

`rpipe model setup` (built in later tasks) helps you choose which backend writes summaries. This page covers its first half: looking at your computer and recommending a backend. Nothing is installed or changed by this part; it only reads facts and gives advice.

## What the walkthrough looks at

`detect_machine` gathers one `MachineInfo` record:

- **Operating system and memory.** Linux, macOS (Darwin) or Windows, and total RAM where it is easy to read.
- **GPU.** NVIDIA via `nvidia-smi` (when several GPUs exist, the one with the most memory wins), AMD via `rocm-smi`, Apple Silicon via the total memory from `sysctl`. Apple chips share one memory pool with the CPU, and macOS lets the GPU use about two thirds of it, so only 66% (`APPLE_GPU_FRACTION`) is counted as GPU memory. A probe that is missing, exits non-zero (for example an NVIDIA driver mismatch) or prints something unreadable is skipped, never a crash; the next probe is tried, then "no GPU".
- **Ollama.** Whether the program is installed, whether its server answers within 5 seconds, and which models are already pulled.
- **Claude Code.** Where the `claude` program is and whether you are logged in (see [backends.md](backends.md)).

## How it recommends

`recommend` applies these rules in order:

1. A GPU with at least 6 GB (`MIN_GPU_VRAM_GB`) means `ollama`. It says to install Ollama if it is missing, and if no model is pulled yet it proposes the largest table model that fits the GPU. Alternatives: `claude-code` (only if you are logged in) and `none`.
2. Otherwise, if you are logged in to Claude Code, it recommends `claude-code`, with `none` (and `ollama` if installed) as alternatives.
3. Otherwise it recommends `none` (abstracts only) and says what would unlock more: a GPU with 6 GB or more plus Ollama, or a Claude Code login. A CPU-friendly pipeline is planned.

The model table (`defaults/ollama_models.yaml`, sizes from ollama.com, checked 2026-11-21):

| GPU memory at least | Model tag | Download |
|---|---|---|
| 6 GB | `qwen3:8b` | 5.2 GB |
| 12 GB | `qwen3:14b` | 9.3 GB |
| 24 GB | `qwen3:32b` | 20 GB |

Install hints shown when Ollama is missing (`OLLAMA_INSTALL`): Linux `curl -fsSL https://ollama.com/install.sh | sh`; macOS `brew install ollama` or the download page; Windows the download page.

## Why it's built this way

- **Every probe is injected.** Commands run through a `CmdRunner` and the server check through a function, so tests use fakes and never touch a real GPU tool, Ollama or the network.
- **Failures degrade, never crash.** A broken `nvidia-smi` is common (driver updates); treating it as "no NVIDIA GPU" keeps setup usable.
- **rpipe never installs Ollama.** It prints the command; you run it. Pulling a model needs your yes/no.
- **6 GB floor.** Below that, models that summarize well do not fit fully in GPU memory and run painfully slowly. Rejected: recommending a CPU model, which is why `none` is the fallback for now.
- **Table in a data file** so new models can be added without code changes.

## How to run / check it

`uv run pytest -q tests/test_machine.py` (offline). Interactive use arrives with `rpipe model setup`.

## Where the code is

`src/research_pipeline/machine.py`, `src/research_pipeline/defaults/ollama_models.yaml`, tests in `tests/test_machine.py`.
