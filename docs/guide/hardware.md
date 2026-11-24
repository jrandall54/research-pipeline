# Hardware scan, model recommendations and Ollama tuning

## What it does

`rpipe` runs a model on your own computer through Ollama, and how well that works depends on the machine: how much memory the graphics card has, how many CPU threads there are, how much disk is free, and how the Ollama server was started. The B.4 flow teaches `rpipe` to read all of that and act on it, in four steps:

1. **Scan** (`rpipe model scan`): read the machine and the Ollama server's settings. Changes nothing.
2. **Recommend**: pick models that fit this machine, including ones you have not downloaded yet.
3. **Tune**: turn on Ollama settings that let a model use a longer context in the same memory, after showing you the exact change and how to undo it.
4. **Bench**: time a real summary so the "how long per paper" figure comes from your machine, not a guess.

This page grows with each step. For now it covers the scan.

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
