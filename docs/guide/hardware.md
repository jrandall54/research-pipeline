# Hardware scan, model recommendations and Ollama tuning

## What it does

`rpipe` runs a model on your own computer through Ollama, and how well that works depends on the machine: how much memory the graphics card has, how many CPU threads there are, how much disk is free, and how the Ollama server was started. The B.4 flow teaches `rpipe` to read all of that and act on it, in four steps:

1. **Scan** (`rpipe model scan`): read the machine and the Ollama server's settings. Changes nothing.
2. **Recommend**: pick models that fit this machine, including ones you have not downloaded yet.
3. **Tune**: turn on Ollama settings that let a model use a longer context in the same memory, after showing you the exact change and how to undo it.
4. **Bench**: time a real summary so the "how long per paper" figure comes from your machine, not a guess.

This page grows with each step. For now it covers the scan, the catalog and the speed benchmark.

## How it works: the scan

`rpipe model scan` shows its progress as a "scan" stage, then prints one plain-words report. Behind it, `detect_machine` fills a `MachineInfo` record; the new facts are:

- **Every GPU**, not just the biggest (`all_gpus`). NVIDIA cards come from `nvidia-smi`, AMD from `rocm-smi`, Apple Silicon from `sysctl`. `MachineInfo.gpu` is still the single best card, so older code is unchanged.
- **Memory free right now** (Linux: `MemAvailable` in `/proc/meminfo`). Total memory says what the machine could do; free memory says what it can do at this moment.
- **CPU**: thread count and which vector instructions it has (`avx2`, `avx512`, `neon`; see the glossary). These decide how fast a model runs when it has to use the CPU.
- **Free disk where Ollama keeps models** (`ollama_models_dir`): `OLLAMA_MODELS` if the server sets it, else `/usr/share/ollama/.ollama/models` for a system service, else `~/.ollama/models`. A big model download must fit there.
- **The Ollama server** (`detect_ollama_server`): its version (`GET /api/version`) and how it runs, tried in this order: system service (`systemctl is-active ollama`), user service (`systemctl --user ...`), Docker container, then, if it answers on the network, "the Ollama app" (macOS, Windows) or "started by hand". For a systemd service it also reads the `OLLAMA_*` settings with `systemctl show ollama --property=Environment` and the drop-in files with `--property=DropInPaths`.

Worked example. On a machine whose service has `Environment=PATH=/usr/bin OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_HOST=127.0.0.1:11436 "OLLAMA_ORIGINS=*"`, the parser splits the line the way a shell would (so the quoted `"OLLAMA_ORIGINS=*"` and values with spaces survive), drops `PATH`, and the report says:

```
  flash attention: on (set in kv-cache.conf)
  context memory type (KV cache): q8_0 (set in kv-cache.conf)
  parallel slots: automatic (default)
  note: the Ollama service listens on port 11436 but rpipe talks to localhost:11434; ...
```

The last line is the **host mismatch** check: if the service's port differs from the port in `llm_url`, the service rpipe could tune is not the server rpipe talks to, so changes would have no effect on your summaries.

## How it works: the catalog

The recommender needs a list of models to choose from. That list is the **catalog**: a versioned YAML file of candidate models, each with the facts needed to judge whether it fits a computer.

Each entry records the exact `ollama pull` name, whether it is dense or a mixture of experts, total and active parameters, the quantization, the download size (also used as the memory its weights need), the context length it was trained for, how much KV cache it needs per 1,000 tokens of context, and which **bands** it suits (`cpu`, `small`, `mid`, `high`, `very_large`: roughly, what size of computer).

Worked example: `qwen3:8b` is dense, Q4_K_M, about 5.2 GB. At about 140 MiB of KV cache per 1,000 tokens, a 32,000-token context adds about 4.4 GB, so it needs roughly 9.6 GB of video memory in total.

Which catalog is used:

1. The copy shipped in the package (`defaults/model_catalog.yaml`) has a `version`.
2. `rpipe model catalog update` downloads the newest copy from the project's GitHub page, checks it, shows what changed (`+` added, `-` removed, `~` changed) and asks `Save this catalog? [y/N]`. Only a yes writes it to `VAULT/cache/model_catalog.yaml`.
3. When loading, the fetched copy wins only if it is valid and its `version` is higher than the shipped one. Anything else (missing, garbled, older) is ignored and the shipped copy is used.

`rpipe model catalog show` prints the catalog grouped by band.

Why it's built this way:

- **Versioned file, not code.** New models appear every month; a data file can be refreshed without a new release. Rejected alternative: hard-coding the list in Python.
- **Version number decides, not file date.** A clock or copy mistake cannot make an old catalog win.
- **A bad download never breaks anything.** It is validated before the prompt and ignored at load time if it later turns out invalid; the shipped copy is always there.
- **Asks before saving, and needs a terminal.** Without one it prints the diff and exits with an error, like every command that changes state.
- **Strict schema.** Unknown fields and duplicate ids are rejected, so a typo cannot silently drop a setting.

```
rpipe model catalog show
rpipe model catalog update
uv run pytest tests/test_catalog.py -q
```

Code: `src/research_pipeline/catalog.py`, the shipped list in `src/research_pipeline/defaults/model_catalog.yaml`, tests in `tests/test_catalog.py`.

## Measuring speed

**What it does.** `rpipe model bench` times your chosen model on your machine and prints how many tokens per second it reads and writes, plus an estimate of the time per paper and for a 15-paper night. Recommendations show this measured time instead of a guess. It needs the `ollama` backend (the cloud backend's speed is the provider's) and takes about 1-3 minutes on a GPU, up to 5 on a CPU.

**How it works.**
1. It resolves the model and context the same way `rpipe model fit` does (so a pinned model is used as is).
2. It sends one request to Ollama with a built-in text of about 3,000 tokens and asks for at most 200 tokens back, at temperature 0. The text starts with a random marker so Ollama cannot reuse reading work from an earlier run. The model is unloaded afterwards.
3. Ollama reports how long it spent reading the prompt and writing the answer (in nanoseconds). Reading speed is prompt tokens divided by reading time; writing speed is written tokens divided by writing time.
4. The estimate for one paper is `paper_tokens / read_speed + 2500 / write_speed`: the paper is read once (the critique reuses the cached paper) and about 2,500 tokens of summary and critique are written. `paper_tokens` is the median size of the full texts already in your vault (characters / 4), or 22,000 (the median of 40 recent papers) when fewer than 5 are stored.

Worked example: reads 1,000 tokens/s, writes 50 tokens/s, 22,000-token paper: 22 s + 50 s = 72 s per paper, so about 18 min for 15 papers.

**Why it's built this way.** One short call measures the machine as it is now, with no download. Rejected alternatives: timing a real paper (needs a download and varies with the paper), and using fixed speed constants per GPU (we have one machine's data, too little to be honest about others). The result is kept per model in `VAULT/cache/bench.json`; the command never changes `config.yaml`.

**How to run / check it.**
```
rpipe model bench
uv run pytest tests/test_bench.py -q
```

**Where the code is.** `src/research_pipeline/bench.py` (`run_bench`, `estimate_seconds`, `median_paper_tokens`, `load_benches`, `save_bench`), the `model bench` command in `src/research_pipeline/cli.py`, tests in `tests/test_bench.py`.

## Why it's built this way

- **Every probe is injected.** Commands go through a runner and `/proc` files through a reader, so tests never run `nvidia-smi` or `systemctl` and the scan is checked offline.
- **A missing tool is "unknown", not an error.** Each probe returns `(127, "")` when the program is absent, which is normal on most machines.
- **Read the service, not just the network.** Asking the server for its version says it is alive but not how it was started; the performance settings live in the service definition, so that is where the scan reads them. Rejected alternative: guess from the process list, which cannot see settings from drop-ins.
- **`scan.py` owns the basic types.** `Gpu`, `CmdRunner` and `system_cmd` moved there and `machine.py` re-exports them, so `machine.py` can use the scan without an import cycle and existing imports keep working.

## How to run / check it

```
rpipe model scan
uv run pytest tests/test_scan.py -q
```

The setup walkthrough (`rpipe model setup`) also shows the new facts (free memory, CPU, free disk).

## Where the code is

- `src/research_pipeline/scan.py`: the probes (`detect_cpu`, `ram_free_gb`, `disk_free_gb`, `all_gpus`, `detect_ollama_server`, `parse_systemd_env`, `ollama_models_dir`, `setting_source`).
- `src/research_pipeline/machine.py`: `MachineInfo` and `detect_machine`, which call the scan.
- `src/research_pipeline/cli.py`: `model scan` and the report text.
- `tests/test_scan.py`: the tests.

Sharing measurements between users (opt-in) is not built; it is a possible later feature.
