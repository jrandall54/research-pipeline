# Hardware scan, model recommendations and Ollama tuning

## What it does

`rpipe` runs a model on your own computer through Ollama, and how well that works depends on the machine: how much memory the graphics card has, how many CPU threads there are, how much disk is free, and how the Ollama server was started. The B.4 flow teaches `rpipe` to read all of that and act on it, in four steps:

1. **Scan** (`rpipe model scan`): read the machine and the Ollama server's settings. Changes nothing.
2. **Recommend**: pick models that fit this machine, including ones you have not downloaded yet.
3. **Tune**: turn on Ollama settings that let a model use a longer context in the same memory, after showing you the exact change and how to undo it.
4. **Bench**: time a real summary so the "how long per paper" figure comes from your machine, not a guess.

This page covers all four: the scan, the catalog, the recommendations, the speed benchmark and the server settings.

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

## Recommendations

**What it does.** `rpipe model recommend` scans the machine (a "scan" progress stage), then prints:

1. A three-line summary: memory and CPU threads, the GPU(s) with total GPU memory and the machine's **band** (size class), Ollama and the free disk where it keeps models.
2. Ollama server settings worth changing (from the same advice as `rpipe model tune`), followed by `Run rpipe model tune to apply them`. Nothing is printed when the settings are already right.
3. Up to five models from the catalog, best first, each with plain reasons: where it would run and with about how much context, `installed` or `download X GB`, the catalog's note, and the measured time per paper (or `speed not measured yet: rpipe model bench`).
4. Installed text models the catalog does not list (`installed, not in the catalog: NAME (rpipe model fit measures any installed model)`), and, when you are logged in to Claude Code, a line saying `rpipe model set-backend claude-code` would use it.
5. On a terminal only: `Use one? number, or Enter to keep your current model`. Without a terminal it stops after printing, so it is safe in scripts.

**How it works.** For each catalog model `place` decides where it would run. The numbers:

- `vram` = all GPU memory added together, minus 1 GB for Ollama's own buffers (`GPU_OVERHEAD_GB`).
- KV cache per token = the catalog's `kv_mib_per_1k` x a factor for the server settings (flash attention on plus `q8_0` halves it, `q4_0` quarters it, otherwise 1).

1. **On the GPU** (`gpu`): if the model file is smaller than `vram`, the leftover memory is filled with context: `leftover / KV per token`, rounded down to a multiple of 1,024, and no more than the model's trained context or 131,072 (`CONTEXT_CEILING`). At least 16,384 tokens counts as a fit.
2. **Partly in RAM** (`spill`, mixture-of-experts models only, on a machine with at least 4 GB of GPU memory): if the model plus a 32,768-token context fits in `vram` + free RAM - 4 GB. Only a few experts work on each token, so the part sitting in RAM slows things down modestly; a dense model in the same spot would crawl, so dense models never spill.
3. **On the CPU** (`cpu`, machines with less than 8 GB of GPU memory, i.e. the `cpu` and `small` bands; checked after the GPU and spill steps, so a small card still gets CPU options when nothing fits on it): if the model has at most 15 billion active parameters (`CPU_MAX_ACTIVE_B`; bigger ones are too slow for one night) and the model plus a 32,768-token context fits in total RAM - 4 GB (`RAM_SAFETY_GB`).
4. Otherwise the model is not shown.

Order: GPU first, then spill, then CPU; inside each, models the catalog lists for this machine's band first, then bigger models by **dense-equivalent size** (quality first), then ones already installed. The top five are shown. Dense-equivalent size is the parameter count for a dense model and `sqrt(total x active)` for a mixture of experts, so a 35B model with 3B active counts as about 10.2B and ranks below a 27B dense model.

Worked example (16 GB card counted as 15.9 GB, flash attention and `q8_0` on): the 12 GB Qwen3.6 27B has `vram` 14.9 GB, so 2.9 GB is left; at 62.5 MiB per 1,000 tokens halved to 31.25, that is about 95,000 tokens, shown as "about 95K tokens of context (estimate; rpipe model fit measures it)". A 22 GB model would not fit on the card; if it is a mixture of experts and 20 GB of RAM is free, it is shown as a spill.

**Picking one.**
- Not installed: it shows `download X GB; free disk where Ollama keeps models: Y GB`. If X + 2 GB is more than Y it refuses (`not enough free disk`) and changes nothing. Otherwise it asks `Download NAME now (X GB)? [y/N]` (default no) and downloads with progress. The setup walkthrough uses the same check and question.
- It sets the model (switching the backend to `ollama` first if needed, and saying so). On the GPU the context is set to `auto` and measured right away with the same probe as `rpipe model fit`, which prints the real context. On spill or CPU both model and context (32,768) are pinned, because the automatic choice only accepts models that sit fully in GPU memory.
- It offers `Measure its speed now (about 1-5 minutes)? [y/N]` (the same benchmark as `rpipe model bench`).
- It prints how to go back, for example `To go back: rpipe model set auto, then rpipe model set-context auto`.

The setup walkthrough (`rpipe model setup`, see [setup.md](setup.md)) uses the same ranking to choose which model to offer.

**Why it's built this way.**
- **Estimate, then measure.** The context figure is arithmetic from catalog numbers, so it is labelled an estimate; the real figure comes from loading the model (`rpipe model fit`), which only makes sense for an installed model. Rejected: downloading models just to measure them.
- **No invented speed.** Time per paper appears only when measured on this machine. Rejected: speed constants per GPU, which one machine's data cannot support.
- **Quality first within a placement, by dense-equivalent size.** A bigger model that fits usually writes better summaries; the GPU-first order already protects speed. A mixture-of-experts model uses only a few experts per token, so its total size overstates its quality; the common rule of thumb `sqrt(total x active)` corrects for that. The owner's 10-paper test agreed: the 35B-A3B model covered 16 of 50 key facts against 19 for the 27B dense model, and produced unverified citations. Rejected: ranking by raw total parameters, which put the 35B first. The rule of thumb is only a starting order; measuring with `rpipe eval` on your own papers stays the final word.
- **Small GPUs still get CPU options.** A 4-8 GB card is too small for most summary models, but the machine's RAM may hold one on the CPU; without this, a small GPU would leave a machine worse off than no GPU at all.
- **4 GB of RAM kept free and 2 GB of disk to spare,** so the rest of the computer keeps working and a download never fills the disk.
- **Nothing changes without a yes**, and the go-back commands are printed every time.

**How to run / check it.**
```
rpipe model recommend
uv run pytest tests/test_recommender.py -q
```

**Where the code is.** `src/research_pipeline/recommender.py` (`band_for`, `kv_factor`, `place`, `rank`, `uncatalogued`), the `model recommend` command and `_use_candidate` in `src/research_pipeline/cli.py`, `recommend` in `src/research_pipeline/machine.py` (the walkthrough's backend choice), tests in `tests/test_recommender.py`.

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

## Server settings

**What it does.** `rpipe model tune` offers Ollama settings that let a model use a longer context in the same video memory. It changes nothing until you say yes, shows the exact commands first, checks afterwards that the settings are live, and `rpipe model tune --undo` takes them out again. It needs a terminal because it uses `sudo`.

**How it works.**
1. It scans the server (the same scan as `rpipe model scan`) and compares three settings with what `rpipe` wants:
   - `OLLAMA_FLASH_ATTENTION=1`: a leaner way of reading long text.
   - `OLLAMA_KV_CACHE_TYPE=q8_0`: stores the model's working memory (KV cache) in 8-bit numbers, half the space. Measured on a 24 GB card: the 27B model's context grew from 32,768 to 57,344 tokens, and the 14B model's from 141,312 to 223,232.
   - `OLLAMA_NUM_PARALLEL=1`: offered only when the current value is above 1, because each parallel slot reserves its own context memory and `rpipe` sends one paper at a time.
2. If the service listens on a different port from the one `rpipe` uses (`llm_url`), it first prints a one-line note: the settings shown belong to the service, and `rpipe` could not read the settings of the server it actually talks to. This note comes before everything else, including the "already has the recommended settings" message, so you are never told "all fine" about a server `rpipe` does not use. `rpipe model tune --undo` and `rpipe model recommend` print the same note.
3. Settings that already have the right value are skipped. If all three are fine it says "already has the recommended settings", names the file each one comes from (for example `kv-cache.conf`) and exits without touching anything.
4. If Ollama is not the system service (a user service, started by hand, Docker, the desktop app) it prints the exact instructions for that setup and stops: `rpipe` only edits the system service itself.
5. On that port mismatch it also warns that the change would not affect the server `rpipe` talks to, and asks `Continue anyway? [y/N]` (default no).
6. It shows each setting that changes (what it does, why, old value -> new), the file it would write, the commands and the undo, then asks `Apply? [y/N]`. The file always lists all three of `rpipe`'s settings, not just the ones that change. Why: the new file replaces the old `rpipe.conf` whole, so if it held only today's change, a value an earlier `rpipe model tune` put there would silently disappear. Worked example: an earlier tune wrote flash attention and `q8_0`; later another file sets `OLLAMA_NUM_PARALLEL=4`; the re-tune offers only the parallel change, but the file it writes still holds all three. The file goes to a temporary directory first; the commands are `sudo install -m 644 TEMP /etc/systemd/system/ollama.service.d/rpipe.conf`, `sudo systemctl daemon-reload`, `sudo systemctl restart ollama`. They run in your own terminal so `sudo` asks you for the password; `rpipe` never sees it. The first failing command stops the run and is named; later ones do not run. If the failure comes after the first command (the file is already in place), it also prints `To undo: rpipe model tune --undo`.
7. It waits up to 30 seconds for the restarted service to answer (at `rpipe`'s `llm_url`, or at the service's own `OLLAMA_HOST` address if you chose "Continue anyway" on a port mismatch; `0.0.0.0` means `127.0.0.1`) (shown as an "ollama restart" stage), re-scans, and confirms each value written to the file is live (all three). If the server does not answer, or a value is not live, the error ends with `To undo: rpipe model tune --undo`. With the `ollama` backend it then re-runs the fit and prints `context: before -> after`. On a port mismatch it skips that re-measure and says why: the fit measures the server `rpipe` uses, which is not the service that changed.

**Undo.** `rpipe model tune --undo` removes only `rpipe.conf` (other drop-in files are never touched), reloads and restarts, then waits for the service (same address rule). On a port mismatch it prints the same note as above first. If `rpipe.conf` does not exist it says "Nothing to undo".

**Why it's built this way.** Changing a system service is the riskiest thing `rpipe` does, so: consent before anything runs, exact commands on screen, argument lists instead of shell strings, a separate file instead of editing yours, verification after, and one command to undo. Rejected alternatives: editing `ollama.service` directly (hard to undo, lost on upgrades), and running `sudo` silently or storing a password.

**How to run / check it.**
```
rpipe model tune
rpipe model tune --undo
uv run pytest tests/test_ollama_tune.py -q
```

**Where the code is.** `src/research_pipeline/ollama_tune.py` (`advise`, `owned`, `host_note`, `render_dropin`, `apply_commands`, `undo_commands`, `manual_instructions`, `wait_ready`, `run_tty`), the `model tune` command in `src/research_pipeline/cli.py`, tests in `tests/test_ollama_tune.py`.

## When the machine changes

The context window `rpipe model fit` measures depends on the machine, so the saved choice is only reused while the machine looks the same. Its key includes the Ollama version, the three server settings that change memory use (`OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`, `OLLAMA_NUM_PARALLEL`) and the GPUs (name and memory). After an Ollama upgrade, an `rpipe model tune` change or a new graphics card, the next `rpipe run` or `rpipe model fit` measures again (about 1-3 minutes) instead of reusing a context that may no longer fit, or that is smaller than it needs to be. The first run after this was added probes once because the key changed.

Rejected alternative: re-probing every night, which costs minutes for no gain on a machine that did not change. Code: `scan.machine_key`, `ollama_fit._cache_key`.

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
- `src/research_pipeline/recommender.py`: the ranking (see Recommendations).
- `src/research_pipeline/ollama_tune.py`: the settings advice, drop-in text and commands (see Server settings).

Sharing measurements between users (opt-in) is not built; it is a possible later feature.
