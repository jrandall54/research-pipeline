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

- **Every GPU**, not just the biggest (`all_gpus`). NVIDIA cards come from `nvidia-smi`, AMD from `rocm-smi`, Apple Silicon from `sysctl`. `MachineInfo.gpu` is still the single best card, so older code is unchanged. For NVIDIA cards the scan also reads how much GPU memory is **free right now** (`memory.free`); the recommender uses it. AMD and Apple report only the total, so for them free memory is "unknown". Some NVIDIA drivers (often on laptops) cannot read a figure and print `[N/A]` instead of a number; the scan then keeps the card with its total memory and treats only the free memory as unknown, rather than dropping the card.
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

Flash attention counts as on when the value is `1` or `true` (any case), the rule Ollama itself uses; the scan, the recommender and `rpipe model tune` share that one check (`scan.flash_on`).

The last line is the **host mismatch** check: if the service's port differs from the port in `llm_url`, the service rpipe could tune may not be the server rpipe talks to, so changes might have no effect on your summaries. Before printing that note, the scan checks whether the two addresses are really one server behind a proxy (next section); only when they are not does the note appear.

### When rpipe talks to Ollama through a proxy

**What it does.** Some people put a small program between `rpipe` and Ollama, for example one that counts how many tokens each request uses. `rpipe` talks to that program (say on port 11434), and the program passes every request on unchanged to the real Ollama service (say on port 11436). This is a **pass-through proxy**. The ports differ, but there is only one Ollama: the proxy just relays. The scan recognises this case, so you do not get a false "different server" note, and the service's settings (flash attention, KV cache type) count as the settings of the server `rpipe` uses.

**How it works.**
1. The scan reads the service's `OLLAMA_HOST` as usual. If its port equals the port in `llm_url`, it is the same server and nothing more is asked.
2. If the ports differ, the scan asks both addresses for their list of installed models (`GET /api/tags`): rpipe's `llm_url` and the service's own address (from `OLLAMA_HOST`; `0.0.0.0` means "every address", so the scan uses `127.0.0.1`).
3. Each model in the list has a name and a **digest**: a fingerprint of the model's files, so two copies of the same name with different files have different digests. The scan sorts both lists of (name, digest) pairs and compares them.
4. Same non-empty list: it is one server behind a proxy. `host_matches` becomes true and a new flag `proxied` is set. Different lists, an address that does not answer, or two empty lists (nothing to compare): today's mismatch note stays.

Worked example. The service has `OLLAMA_HOST=127.0.0.1:11436` and `llm_url` is `http://localhost:11434`. Both addresses answer with `m:latest` (digest `abc`). The report then says, instead of the note:

```
  rpipe reaches this Ollama through a pass-through proxy on port 11434 (same models): its settings apply.
```

With a proxy recognised, `rpipe model recommend` uses the service's settings in its context estimates, and `rpipe model tune` neither warns nor asks "Continue anyway?". After a restart it waits at `llm_url` (the proxy passes the answer through), and it re-measures the context as usual.

**Why it's built this way.** Comparing model lists is a cheap, read-only question both addresses can answer. It is not proof: a digest is a fingerprint of the model's contents, so two separate Ollama servers that pulled the same models also match (for example an `ollama serve` started by hand on 11434 next to the system service on 11436, sharing one models folder). `rpipe` accepts that because such a setup is rare and the only cost is small: the service's settings are taken as the ones `rpipe` uses, and `rpipe model tune` skips its "different server" warning. Two empty lists prove nothing, so they do not count as a match. Rejected alternatives: asking the user to point `rpipe` straight at the service's port (that would skip the proxy, which the owner wants to keep), and comparing only the version number (two separate servers often run the same version).

**How to check it.** `rpipe model scan` prints the proxy line above. Tests: `uv run pytest tests/test_scan.py -q -k proxy`. Code: `ollama_tag_digests`, `_own_url` and the check at the end of `detect_ollama_server` in `src/research_pipeline/scan.py`.

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
2. Ollama server settings worth changing (from the same advice as `rpipe model tune`), followed by `Run rpipe model tune to apply them`. Nothing is printed when the settings are already right. When `rpipe` cannot read the server's settings at all (Ollama started by hand, Docker, the desktop app), it says `rpipe cannot read this server's settings` and lists the settings as "if these are not set yet", instead of claiming they are "not set". When the service it can read is not the server `rpipe` talks to (a port mismatch, see the scan), it prints the one-line note from `rpipe model tune` and works out the context estimates as if that server had default settings, since it cannot know them.
3. Up to five models from the catalog, best first, each with plain reasons: where it would run and with about how much context, `installed` or `download X GB`, the catalog's note, and the measured time per paper (or `speed not measured yet: rpipe model bench`).
4. Installed text models the catalog does not list (`installed, not in the catalog: NAME (rpipe model fit measures any installed model)`), and, when you are logged in to Claude Code, a line saying `rpipe model set-backend claude-code` would use it.
5. On a terminal only: `Use one? number, or Enter to keep your current model`. Without a terminal it stops after printing, so it is safe in scripts.

**How it works.** For each catalog model `place` decides where it would run. The numbers:

All sizes here are in GiB (1 GiB = 2^30 bytes, what `nvidia-smi` and Ollama report). The catalog's download sizes are in GB (10^9 bytes), so they are converted: 13.7 GB = 13.7 x 10^9 / 2^30 = 12.76 GiB. Mixing the two units made the old estimate about 7% off.

- `vram` = the GPU memory free at scan time, all cards added together (when the free amount cannot be read: total memory minus 0.5 GiB for the desktop), minus 2.0 GiB that Ollama leaves unused (`OLLAMA_RESERVE_GIB`).
- weights = the catalog's download size, converted to GiB.
- KV cache per token = the catalog's `kv_mib_per_1k` x a factor for the server settings (flash attention on plus `q8_0` halves it, `q4_0` quarters it, otherwise 1; always 1 on a port mismatch, because the settings `rpipe` can read belong to a different server).

Where the 2.0 GiB comes from: on the owner's 16 GB RTX 5070 Ti (15.0 GiB free, nothing else loaded), Ollama's own report (`/api/ps`) showed the 13.7 GB Qwen3.6-35B-A3B at a 16K context taking 12.92 GiB, all on the GPU: 12.76 GiB of weights plus 16K x 19.5 MiB per 1K halved = 0.16 GiB, exactly the measured total. At 49K it put only 13.03 GiB on the GPU and moved the rest to RAM. Gemma 4 26B behaved the same way (at most 12.62 GiB on the GPU). So Ollama stops at about 13.0 GiB of the 15.0 GiB free: about 2.0 GiB is never used.

Because free memory is read at scan time, an app holding GPU memory when you run `rpipe model recommend` (a game, a browser with hardware acceleration, a model still loaded) lowers the estimate. Close it and run the command again for the full figure.

1. **On the GPU** (`gpu`): if the weights are smaller than `vram`, the leftover memory is filled with context: `leftover / KV per token`, rounded down to a multiple of 1,024, and no more than the model's trained context or 131,072 (`CONTEXT_CEILING`). At least 16,384 tokens counts as a fit.
2. **Partly in RAM** (`spill`, mixture-of-experts models only, on a machine with at least 4 GB of GPU memory): if the weights plus a 32,768-token context fit in `vram` + free RAM - 4 GiB. Only a few experts work on each token, so the part sitting in RAM slows things down modestly; a dense model in the same spot would crawl, so dense models are never recommended for spilled nightly use. (The nightly rerun stage does spill any model, dense ones too, but only for the few cut papers; see "Reading cut papers whole: reruns" in `running.md`.)
3. **On the CPU** (`cpu`; checked after the GPU and spill steps): normally only on machines with less than 8 GB of GPU memory (the `cpu` and `small` bands). If no catalog model at all fits on the GPU, for example an 8 GB card with only 5 GB free (3 GiB left after Ollama's 2 GiB, too little for any model with a useful context), every model is placed again with the CPU allowed on any machine, so the machine still gets options instead of an empty list. When at least one model fits on the GPU, a bigger card never gets CPU choices. The test: if the model has at most 15 billion active parameters (`CPU_MAX_ACTIVE_B`; bigger ones are too slow for one night) and the model plus a 32,768-token context fits in total RAM - 4 GB (`RAM_SAFETY_GB`).
4. Otherwise the model is not shown.

**On the CPU tier, measure the speed.** Each model call normally may take at most 600 seconds (`CALL_TIMEOUT` in `llm.py`). On a slow CPU, reading a long paper alone can take longer than that: at 20 tokens/s a 22,000-token paper needs about 1,100 s. Once `rpipe model bench` has measured the model, the limit grows to fit the measured speed (see "The time limit for one call" in [llm.md](llm.md)), so long papers finish instead of timing out; without a measurement they would time out and fall back to their abstracts. "Slow but fine overnight" therefore assumes you run `rpipe model bench` (the recommend and setup flows offer it).

**Which placement reads papers in parts.** With the `summarize_mode` setting at `auto` (the default), each run looks at where the model actually sits and chooses how to read papers (see "Choosing the mode" in [summaries.md](summaries.md)):

| placement | what Ollama reports | mode |
| --- | --- | --- |
| On the GPU, with a context of 26,624 tokens or more (for example a 16 GB card at 57K) | all of the model in GPU memory | whole: one call per paper |
| On the GPU, but only a small context fits (below 26,624 tokens) | all in GPU memory | split: a typical paper would be cut otherwise |
| Partly in RAM (spill) | part of the model in RAM | split: shorter prompts, expected to read faster |
| On the CPU | none of it in GPU memory | split |
| Claude Code (no local model) | not asked | whole |

Split mode makes one notes call per part plus the summary and critique calls from the notes, each with a short prompt, instead of two calls that each read the whole paper; on a CPU or a spilled model, where reading a long prompt is the slow part, that is expected to be the cheaper way (the reasoning behind the rule, not yet measured on real machines; see "Choosing the mode" in [summaries.md](summaries.md)). Pin it either way with `rpipe model set-mode whole` or `rpipe model set-mode split`.

Order: GPU first, then spill, then CPU; inside each, models the catalog lists for this machine's band first, then bigger models by **dense-equivalent size** (quality first), then the bigger download (for two quantizations of the same model, the larger one keeps more detail, so a 16.8 GB Q4_K_M ranks above a 12 GB IQ3_XXS of the same 27B model when both fit), then ones already installed. The top five are shown. Dense-equivalent size is the parameter count for a dense model and `sqrt(total x active)` for a mixture of experts, so a 35B model with 3B active counts as about 10.2B and ranks below a 27B dense model.

Worked example (the owner's card: 16 GB RTX 5070 Ti, 15,364 MiB = 15.0 GiB free, flash attention and `q8_0` on): `vram` = 15.0 - 2.0 = 13.0 GiB. The 13.7 GB Qwen3.6-35B-A3B has 12.76 GiB of weights, so 0.245 GiB is left (13.004 - 12.759); at 19.5 MiB per 1,000 tokens halved to 9.75, that is 0.245 x 1024 x 1000 / 9.75 = about 25,700 tokens, rounded down to 25,600 and shown as "fits fully in the 13.0 GB of GPU memory free for models with about 26K tokens of context (estimate; rpipe model fit measures it)". The figure in that sentence is `vram`, the memory actually usable, not the card's 16 GB total. That matches the measurement: it fit fully at 16K and spilled at 49K. A 22 GB model would not fit on the card; if it is a mixture of experts and 20 GB of RAM is free, it is shown as a spill.

**Picking one.**
- Not installed: it shows `download X GB; free disk where Ollama keeps models: Y GB`. If X + 2 GB is more than Y it refuses (`not enough free disk`) and changes nothing. Otherwise it asks `Download NAME now (X GB)? [y/N]` (default no) and downloads with progress. The setup walkthrough uses the same check and question.
- It sets the model (switching the backend to `ollama` first if needed, and saying so). On the GPU the context is set to `auto` and measured right away with the same probe as `rpipe model fit`, which prints the real context. On spill or CPU both model and context (32,768) are pinned, because the automatic choice only accepts models that sit fully in GPU memory.
- It offers `Measure its speed now (about 1-5 minutes)? [y/N]` (the same benchmark as `rpipe model bench`).
- If a speed measurement exists for that model (just now or earlier; a measurement saved as `foo` counts for `foo:latest` and the other way round, as Ollama treats them as one model) and reading a typical paper would take longer than the usual 600-second call limit (`paper_tokens / reading speed > 600`), it explains that the measurement lifts the limit, for example `Note: at the measured 20 tokens/s, reading a 22,000-token paper takes about 1,100 s, longer than the usual 600-second limit for one model call. Because this speed was measured, calls to this model may take up to about 4,506 s, so long papers still finish; a night of them just takes longer. ...` (the limit is worked out as in "The time limit for one call" in [llm.md](llm.md)). Before B.5 this was a warning that long papers would time out.
- It prints how to go back, for example `To go back: rpipe model set auto, then rpipe model set-context auto`.

The setup walkthrough (`rpipe model setup`, see [setup.md](setup.md)) uses the same ranking to choose which model to offer.

**Why it's built this way.**
- **Estimate, then measure.** The context figure is arithmetic from catalog numbers, so it is labelled an estimate; the real figure comes from loading the model (`rpipe model fit`), which only makes sense for an installed model. Rejected: downloading models just to measure them.
- **No invented speed.** Time per paper appears only when measured on this machine. Rejected: speed constants per GPU, which one machine's data cannot support.
- **Quality first within a placement, by dense-equivalent size.** A bigger model that fits usually writes better summaries; the GPU-first order already protects speed. A mixture-of-experts model uses only a few experts per token, so its total size overstates its quality; the common rule of thumb `sqrt(total x active)` corrects for that. The owner's 10-paper test agreed: the 35B-A3B model covered 16 of 50 key facts against 19 for the 27B dense model, and produced unverified citations. Rejected: ranking by raw total parameters, which put the 35B first. The rule of thumb is only a starting order; measuring with `rpipe eval` on your own papers stays the final word.
- **Small GPUs still get CPU options.** A 4-8 GB card is too small for most summary models, but the machine's RAM may hold one on the CPU; without this, a small GPU would leave a machine worse off than no GPU at all. The same holds for a bigger card whose memory is mostly taken: when nothing fits on the GPU, CPU choices are allowed whatever the card's size. Rejected: always listing CPU choices, which on a machine with a working GPU would only add slow options below better ones.
- **Calibrated, not guessed.** The 2.0 GiB reserve and the GB-to-GiB conversion come from one measured machine (above); they replaced a flat 1 GB guess that over-estimated the context. Rejected: a reserve that scales with card size, which one card's data cannot support.
- **Free memory, not total.** The estimate is for the machine as it is now. Rejected: total memory, which promised context that a busy desktop did not have.
- **4 GB of RAM kept free and 2 GB of disk to spare,** so the rest of the computer keeps working and a download never fills the disk.
- **Nothing changes without a yes**, and the go-back commands are printed every time.

**How to run / check it.**
```
rpipe model recommend
uv run pytest tests/test_recommender.py -q
```

**Where the code is.** `src/research_pipeline/recommender.py` (`band_for`, `usable_vram`, `weights_gib`, `kv_factor`, `server_kv_factor`, `place`, `rank`, `uncatalogued`), the `model recommend` command, `_use_candidate` and `_warn_call_limit` in `src/research_pipeline/cli.py`, `recommend` in `src/research_pipeline/machine.py` (the walkthrough's backend choice), tests in `tests/test_recommender.py`.

## Measuring speed

**What it does.** `rpipe model bench` times your chosen model on your machine and prints how many tokens per second it reads and writes, plus an estimate of the time per paper and for a 15-paper night. Recommendations show this measured time instead of a guess. It needs the `ollama` backend (the cloud backend's speed is the provider's) and takes about 1-3 minutes on a GPU, up to 5 on a CPU.

**How it works.**
1. It resolves the model and context the same way `rpipe model fit` does (so a pinned model is used as is). When the context is `auto` and no measured context is saved yet, it says first that it will measure the context that fits before the speed, which loads and unloads models and can take up to 15 minutes, so a long wait is expected rather than looking frozen.
2. It sends one request to Ollama with a built-in text of about 3,000 tokens and asks for at most 200 tokens back, at temperature 0. The text starts with a random marker so Ollama cannot reuse reading work from an earlier run. The model is unloaded afterwards.
3. Ollama reports how long it spent reading the prompt and writing the answer (in nanoseconds). Reading speed is prompt tokens divided by reading time; writing speed is written tokens divided by writing time.
4. The estimate for one paper is `paper_tokens / read_speed + 2500 / write_speed`: the paper is read once (the critique reuses the cached paper) and about 2,500 tokens of summary and critique are written. `paper_tokens` is the median size of the full texts already in your vault (characters / 4), or 22,000 (the median of 40 recent papers) when fewer than 5 are stored.

Worked example: reads 1,000 tokens/s, writes 50 tokens/s, 22,000-token paper: 22 s + 50 s = 72 s per paper, so about 18 min for 15 papers.

**Why it's built this way.** One short call measures the machine as it is now, with no download. Rejected alternatives: timing a real paper (needs a download and varies with the paper), and using fixed speed constants per GPU (we have one machine's data, too little to be honest about others). The result is kept per model in `VAULT/cache/bench.json`; the command never changes `config.yaml`. The nightly run also reads it: a slow measurement raises the time limit for each model call ("The time limit for one call" in [llm.md](llm.md)).

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
2. If the service listens on a different port from the one `rpipe` uses (`llm_url`), and it is not the same server behind a pass-through proxy (see the scan), it first prints a one-line note: the settings shown belong to the service, and `rpipe` could not read the settings of the server it actually talks to. This note comes before everything else, including the "already has the recommended settings" message, so you are never told "all fine" about a server `rpipe` does not use. `rpipe model tune --undo` and `rpipe model recommend` print the same note.
3. Settings that already have the right value are skipped. If all three are fine it says "already has the recommended settings", names the file each one comes from (for example `kv-cache.conf`) and exits without touching anything.
4. If Ollama is not the system service (a user service, started by hand, Docker, the desktop app) it prints the exact instructions for that setup and stops: `rpipe` only edits the system service itself.
5. On that port mismatch it also warns that the change would not affect the server `rpipe` talks to, and asks `Continue anyway? [y/N]` (default no).
6. It shows each setting that changes (what it does, why, old value -> new), the file it would write, the commands and the undo, then asks `Apply? [y/N]`. The file always lists all three of `rpipe`'s settings, not just the ones that change. Why: the new file replaces the old `rpipe.conf` whole, so if it held only today's change, a value an earlier `rpipe model tune` put there would silently disappear. Worked example: an earlier tune wrote flash attention and `q8_0`; later another file sets `OLLAMA_NUM_PARALLEL=4`; the re-tune offers only the parallel change, but the file it writes still holds all three. The file goes to a temporary directory first; the commands are `sudo install -m 644 TEMP /etc/systemd/system/ollama.service.d/rpipe.conf`, `sudo systemctl daemon-reload`, `sudo systemctl restart ollama`. They run in your own terminal so `sudo` asks you for the password; `rpipe` never sees it. The first failing command stops the run and is named; later ones do not run. If the failure comes after the first command (the file is already in place), it also prints `To undo: rpipe model tune --undo`.
7. It waits up to 30 seconds for the restarted service to answer (at `rpipe`'s `llm_url`, which includes the proxy case since the proxy passes the answer through, or at the service's own `OLLAMA_HOST` address if you chose "Continue anyway" on a port mismatch; `0.0.0.0` means `127.0.0.1`) (shown as an "ollama restart" stage), re-scans, and confirms each value written to the file is live (all three). If the server does not answer, or a value is not live, the error ends with `To undo: rpipe model tune --undo`. With the `ollama` backend it then re-runs the fit and prints `context: before -> after`. On a port mismatch it skips that re-measure and says why: the fit measures the server `rpipe` uses, which is not the service that changed.

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

- `src/research_pipeline/scan.py`: the probes (`flash_on`, `detect_cpu`, `ram_free_gb`, `disk_free_gb`, `all_gpus`, `detect_ollama_server`, `ollama_tag_digests`, `service_url`, `parse_systemd_env`, `ollama_models_dir`, `setting_source`). `service_url` (where to wait after a restart) moved here from `ollama_tune.py`, which still offers it under the old name.
- `src/research_pipeline/machine.py`: `MachineInfo` and `detect_machine`, which call the scan.
- `src/research_pipeline/cli.py`: `model scan` and the report text.
- `tests/test_scan.py`: the tests.
- `src/research_pipeline/recommender.py`: the ranking (see Recommendations).
- `src/research_pipeline/ollama_tune.py`: the settings advice, drop-in text and commands (see Server settings).

Sharing measurements between users (opt-in) is not built; it is a possible later feature.
