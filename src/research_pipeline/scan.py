"""Read this computer and the running Ollama server: CPU, memory, disk, every GPU, server settings.

Every command goes through an injected runner and every file read through an injected reader, so
tests never run a real tool. A missing tool gives (127, "") and means "unknown", never an error.
`Gpu`, `CmdRunner` and `system_cmd` live here (machine.py re-exports them) so machine.py can import
this module without a cycle.
"""
from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx

PROBE_TIMEOUT = 5.0
GIB = 1024**3
# Apple Silicon shares one memory pool between CPU and GPU, and macOS lets the GPU use roughly
# two thirds of it by default, so only that share is counted as GPU memory.
APPLE_GPU_FRACTION = 0.66
DEFAULT_OLLAMA_PORT = 11434
SYSTEMD_MODELS_DIR = Path("/usr/share/ollama/.ollama/models")

CmdRunner = Callable[[list[str], float], tuple[int, str]]


@dataclass
class Gpu:
    name: str
    vram_gb: float  # total GPU memory, GiB
    kind: Literal["nvidia", "amd", "apple"]
    free_gb: float | None = None  # free at scan time, GiB (NVIDIA only; None = unknown)


@dataclass
class CpuInfo:
    threads: int
    vector: list[str]  # subset of ["avx2", "avx512", "neon"], in that order


@dataclass
class OllamaServer:
    version: str | None
    how: Literal["systemd", "systemd-user", "manual", "docker", "app", "unknown"]
    env: dict[str, str]
    drop_ins: list[str]
    host_matches: bool | None  # service OLLAMA_HOST port == port of config.llm_url; None if unknown


def system_cmd(argv: list[str], timeout: float) -> tuple[int, str]:
    """Run a command; (127, "") if the binary is missing, (124, "") on timeout. Never raises."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return done.returncode, done.stdout


# ---- GPUs ----

def _nvidia_all(run: CmdRunner) -> list[Gpu]:
    code, out = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                    PROBE_TIMEOUT)
    if code != 0:
        return []
    gpus: list[Gpu] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        numbers: list[float] = []
        while len(parts) > 1 and len(numbers) < 2:  # up to two numbers at the end: total, free (MiB)
            try:
                numbers.insert(0, float(parts[-1]))
            except ValueError:
                break
            parts.pop()
        name = ", ".join(parts)  # a card name may itself contain a comma
        if not numbers or not name:
            continue
        total = numbers[0] / 1024  # MiB -> GiB
        free = numbers[1] / 1024 if len(numbers) == 2 else None
        gpus.append(Gpu(name, total, "nvidia", free))
    return gpus


def _amd_all(run: CmdRunner) -> list[Gpu]:
    code, out = run(["rocm-smi", "--showmeminfo", "vram", "--json"], PROBE_TIMEOUT)
    if code != 0:
        return []
    try:
        items = list(json.loads(out).items())
    except (ValueError, AttributeError):
        return []
    gpus: list[Gpu] = []
    for card, vals in items:
        try:
            gb = int(vals["VRAM Total Memory (B)"]) / GIB
        except (ValueError, KeyError, TypeError):
            continue  # one unreadable card (or a non-card key) must not hide the others
        gpus.append(Gpu(card, gb, "amd"))
    return gpus


def _apple_all(run: CmdRunner) -> list[Gpu]:
    code, out = run(["sysctl", "-n", "hw.memsize"], PROBE_TIMEOUT)
    try:
        total = int(out.strip())
    except ValueError:
        return []
    if code != 0:
        return []
    return [Gpu("Apple Silicon (unified memory)", total / GIB * APPLE_GPU_FRACTION, "apple")]


def _probes(system: str, arch: str) -> list[Callable[[CmdRunner], list[Gpu]]]:
    probes: list[Callable[[CmdRunner], list[Gpu]]] = [_nvidia_all, _amd_all]
    if system == "Darwin" and arch == "arm64":
        probes.append(_apple_all)
    return probes


def all_gpus(run: CmdRunner, system: str, arch: str) -> list[Gpu]:
    """Every NVIDIA and AMD card (plus Apple Silicon on an M-series Mac), not just the best one."""
    return [g for probe in _probes(system, arch) for g in probe(run)]


def best_gpu(run: CmdRunner, system: str, arch: str) -> Gpu | None:
    """The card with the most memory from the first vendor that answers (NVIDIA, AMD, then Apple)."""
    for probe in _probes(system, arch):
        found = probe(run)
        if found:
            return max(found, key=lambda g: g.vram_gb)  # max keeps the first of equals
    return None


# ---- CPU, memory, disk ----

def _read_cpuinfo() -> str:
    try:
        return Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def detect_cpu(run: CmdRunner, system: str, read_text: Callable[[], str] = _read_cpuinfo) -> CpuInfo:
    """Thread count and the vector instruction sets llama.cpp uses to run models faster on a CPU."""
    threads = os.cpu_count() or 1
    vector: list[str] = []
    if system == "Linux":
        flags: set[str] = set()
        for line in read_text().splitlines():
            if line.startswith("flags"):
                flags = set(line.partition(":")[2].split())
                break
        if "avx2" in flags:
            vector.append("avx2")
        if "avx512f" in flags:
            vector.append("avx512")
    elif system == "Darwin" and platform.machine() == "arm64":
        vector.append("neon")
    return CpuInfo(threads, vector)


def ram_free_gb(system: str, read_text: Callable[[], str] | None = None) -> float | None:
    """Memory available right now (Linux: MemAvailable); None where it is not easy to read."""
    if system != "Linux":
        return None
    try:
        text = read_text() if read_text else Path("/proc/meminfo").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024 / GIB
    except (OSError, ValueError, IndexError):
        pass
    return None


def disk_free_gb(path: Path) -> float | None:
    """Free space on the disk holding `path` (or its nearest existing parent)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / GIB
    except OSError:
        return None


# ---- Ollama server ----

def ollama_version(url: str) -> str | None:
    try:
        resp = httpx.get(f"{url}/api/version", timeout=PROBE_TIMEOUT)
        resp.raise_for_status()
        version = resp.json().get("version")
        return str(version) if version else None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


def flash_on(value: str | None) -> bool:
    """Ollama reads OLLAMA_FLASH_ATTENTION as on for "1" or "true" (any case)."""
    return value is not None and value.strip().lower() in ("1", "true")


MACHINE_KEY_SETTINGS = ("OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_NUM_PARALLEL")


def machine_key(url: str, run: CmdRunner = system_cmd) -> dict:
    """What a measured context depends on: Ollama version, memory-affecting settings, GPUs.

    Cheap (one /api/version call, a few `systemctl` calls, `nvidia-smi`/`rocm-smi`) and never
    raises: anything that cannot be read is left out, so the key just stays the same.
    """
    version: str | None = None
    env: dict[str, str] = {}
    gpus: list[str] = []
    try:
        server = detect_ollama_server(url, run, version=ollama_version)
        version = server.version
        env = {k: server.env[k] for k in MACHINE_KEY_SETTINGS if k in server.env}
    except Exception:  # a key must never stop a run
        pass
    try:
        gpus = sorted(f"{g.name}:{round(g.vram_gb, 1)}"
                      for g in all_gpus(run, platform.system(), platform.machine()))
    except Exception:
        pass
    return {"ollama": version, "env": env, "gpus": gpus}


def parse_systemd_env(line: str) -> dict[str, str]:
    """`Environment=A=1 "B=x y"` -> {"A": "1", "B": "x y"}, keeping only OLLAMA_* names."""
    text = line.strip()
    if text.startswith("Environment="):
        text = text[len("Environment="):]
    try:
        parts = shlex.split(text)
    except ValueError:
        return {}
    env: dict[str, str] = {}
    for part in parts:
        key, sep, value = part.partition("=")
        if sep and key.startswith("OLLAMA_"):
            env[key] = value
    return env


def _property(run: CmdRunner, user: bool, name: str) -> str:
    argv = ["systemctl", *(["--user"] if user else []), "show", "ollama", f"--property={name}"]
    code, out = run(argv, PROBE_TIMEOUT)
    return out.strip() if code == 0 else ""


def _port_of_host(value: str | None) -> int:
    """Port in an OLLAMA_HOST value ("127.0.0.1:11436", ":11436", "http://h:1", "0.0.0.0")."""
    if not value:
        return DEFAULT_OLLAMA_PORT
    parsed = urlparse(value if "//" in value else f"//{value}")
    try:
        return parsed.port or DEFAULT_OLLAMA_PORT
    except ValueError:
        return DEFAULT_OLLAMA_PORT


def detect_ollama_server(
    url: str, run: CmdRunner, version: Callable[[str], str | None] = ollama_version
) -> OllamaServer:
    """How the Ollama server runs and which OLLAMA_* settings it was started with."""
    system = platform.system()
    ver = version(url)
    how: Literal["systemd", "systemd-user", "manual", "docker", "app", "unknown"]
    if run(["systemctl", "is-active", "ollama"], PROBE_TIMEOUT)[1].strip() == "active":
        how = "systemd"
    elif run(["systemctl", "--user", "is-active", "ollama"], PROBE_TIMEOUT)[1].strip() == "active":
        how = "systemd-user"
    elif run(["docker", "ps", "--filter", "ancestor=ollama/ollama", "-q"], PROBE_TIMEOUT)[1].strip():
        how = "docker"
    elif ver is not None:
        how = "app" if system in ("Darwin", "Windows") else "manual"
    else:
        how = "unknown"
    env: dict[str, str] = {}
    drop_ins: list[str] = []
    host_matches: bool | None = None
    if how in ("systemd", "systemd-user"):
        user = how == "systemd-user"
        env = parse_systemd_env(_property(run, user, "Environment"))
        drop_ins = _property(run, user, "DropInPaths").removeprefix("DropInPaths=").split()
        try:
            config_port = urlparse(url).port or DEFAULT_OLLAMA_PORT
        except ValueError:
            config_port = DEFAULT_OLLAMA_PORT
        host_matches = _port_of_host(env.get("OLLAMA_HOST")) == config_port
    return OllamaServer(ver, how, env, drop_ins, host_matches)


def ollama_models_dir(server: OllamaServer) -> Path:
    """Where this server keeps pulled models: OLLAMA_MODELS, else the service or per-user default."""
    if server.env.get("OLLAMA_MODELS"):
        return Path(server.env["OLLAMA_MODELS"])
    if server.how == "systemd":
        return SYSTEMD_MODELS_DIR
    return Path.home() / ".ollama" / "models"


def setting_source(server: OllamaServer, key: str) -> str | None:
    """Name of the drop-in file that mentions `key` (e.g. "kv-cache.conf"), or None if not found."""
    for path in server.drop_ins:
        try:
            if key in Path(path).read_text(encoding="utf-8"):
                return Path(path).name
        except OSError:
            continue
    return None
