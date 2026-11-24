"""Guided Ollama server settings: what to change, the exact commands, and how to undo.

Nothing here runs by itself. `rpipe model tune` shows the plan, asks, and only then runs the
commands in the user's own terminal so `sudo` can ask for the password (rpipe never sees it).
Every command is an argv list: no shell string is ever built.
"""
from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from research_pipeline.scan import OllamaServer, flash_on, ollama_version

DROPIN = Path("/etc/systemd/system/ollama.service.d/rpipe.conf")  # read at call time (tests patch it)
UNDO_COMMAND = "rpipe model tune --undo"
FLASH = "OLLAMA_FLASH_ATTENTION"
KV_TYPE = "OLLAMA_KV_CACHE_TYPE"
PARALLEL = "OLLAMA_NUM_PARALLEL"


@dataclass(frozen=True)
class Setting:
    name: str
    value: str
    current: str | None
    what: str
    why: str


def _above_one(value: str | None) -> bool:
    try:
        return value is not None and int(value) > 1
    except ValueError:
        return False


def advise(server: OllamaServer) -> list[Setting]:
    """The settings worth changing: only those whose current value differs from the recommendation."""
    env = server.env
    out: list[Setting] = []
    if not flash_on(env.get(FLASH)):
        out.append(Setting(
            FLASH, "1", env.get(FLASH),
            "uses a faster, leaner way of reading long text (flash attention)",
            "it lowers the memory a long context needs and is a prerequisite for the next setting"))
    if env.get(KV_TYPE) != "q8_0":
        out.append(Setting(
            KV_TYPE, "q8_0", env.get(KV_TYPE),
            "stores the model's working memory (KV cache) in 8-bit numbers instead of 16-bit, half the space",
            "measured on a 24 GB card: the 27B model's context grew from 32,768 to 57,344 tokens"))
    if _above_one(env.get(PARALLEL)):
        out.append(Setting(
            PARALLEL, "1", env.get(PARALLEL),
            "serves one request at a time instead of several",
            "each parallel slot reserves its own context memory; rpipe sends one paper at a time"))
    return out


def owned(server: OllamaServer) -> list[Setting]:
    """Every setting rpipe owns, at its recommended value: what rpipe.conf always holds.

    Writing all three every time (not just the ones `advise` found missing) means a re-tune never
    drops a value an earlier tune put in rpipe.conf: the new file replaces the old one whole."""
    advised = {s.name: s for s in advise(server)}
    env = server.env
    return [
        advised.get(FLASH) or Setting(FLASH, "1", env.get(FLASH), "", ""),
        advised.get(KV_TYPE) or Setting(KV_TYPE, "q8_0", env.get(KV_TYPE), "", ""),
        advised.get(PARALLEL) or Setting(PARALLEL, "1", env.get(PARALLEL), "", ""),
    ]


def host_note(server: OllamaServer, rpipe_url: str) -> str | None:
    """One line for when the service rpipe can read is not the server rpipe talks to."""
    if server.host_matches is not False:
        return None
    return (f"Note: the Ollama service listens on {server.env.get('OLLAMA_HOST') or 'its default address'} "
            f"but rpipe talks to {rpipe_url}. The settings shown belong to the service; rpipe could not "
            "read the settings of the server it uses.")


def render_dropin(settings: list[Setting]) -> str:
    lines = [
        "# Written by rpipe (rpipe model tune). Remove with: " + UNDO_COMMAND,
        "[Service]",
    ]
    lines += [f'Environment="{s.name}={s.value}"' for s in settings]
    return "\n".join(lines) + "\n"


def _reload_restart() -> list[list[str]]:
    return [["sudo", "systemctl", "daemon-reload"], ["sudo", "systemctl", "restart", "ollama"]]


def apply_commands(tmp: Path) -> list[list[str]]:
    return [["sudo", "install", "-m", "644", str(tmp), str(DROPIN)], *_reload_restart()]


def undo_commands() -> list[list[str]]:
    return [["sudo", "rm", "-f", str(DROPIN)], *_reload_restart()]


def manual_instructions(server: OllamaServer, settings: list[Setting]) -> str:
    pairs = " ".join(f"{s.name}={s.value}" for s in settings)
    where = {
        "systemd-user": (
            "Ollama runs as a user service. Run `systemctl --user edit ollama`, add under [Service]:\n"
            + "\n".join(f'  Environment="{s.name}={s.value}"' for s in settings)
            + "\nthen `systemctl --user restart ollama`."),
        "manual": f"Ollama was started by hand. Stop it and start it again with:\n  {pairs} ollama serve",
        "docker": "Ollama runs in Docker. Recreate the container with these added:\n  "
                  + " ".join(f"-e {s.name}={s.value}" for s in settings),
        "app": "Ollama runs as the desktop app. Set each variable (macOS: `launchctl setenv NAME VALUE`; "
               "Windows: Settings > Environment Variables), then quit and reopen Ollama:\n  "
               + "\n  ".join(f"{s.name}={s.value}" for s in settings),
    }.get(server.how, f"rpipe could not tell how Ollama was started. Set these where it starts: {pairs}")
    return (f"rpipe only changes the system service itself; for your setup, do this yourself.\n{where}\n"
            "To undo, remove those settings and restart Ollama.")


def service_url(server: OllamaServer, rpipe_url: str) -> str:
    """Where to wait for the service just restarted: rpipe's own URL when it is the same server,
    else the service's OLLAMA_HOST (default host 127.0.0.1; 0.0.0.0 or empty means 127.0.0.1)."""
    if server.host_matches is not False:
        return rpipe_url
    host_value = server.env.get("OLLAMA_HOST", "")
    parsed = urlparse(host_value if "//" in host_value else f"//{host_value}")
    host = parsed.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    try:
        port = parsed.port or 11434
    except ValueError:
        port = 11434
    return f"http://{host}:{port}"


def run_tty(argv: list[str]) -> int:
    """Run in the user's terminal (stdin/stdout inherited) so sudo can prompt for a password."""
    return subprocess.run(argv).returncode


def wait_ready(url: str, seconds: float = 30.0, version: Callable[[str], str | None] = ollama_version,
               sleep: Callable[[float], None] = time.sleep) -> bool:
    waited = 0.0
    while True:
        if version(url) is not None:
            return True
        if waited >= seconds:
            return False
        sleep(1.0)
        waited += 1.0
