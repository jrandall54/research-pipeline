"""Nightly scheduling via a systemd *user* timer (see docs/guide/scheduling.md)."""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

SERVICE_NAME = "research-pipeline.service"
TIMER_NAME = "research-pipeline.timer"

Runner = Callable[[list[str]], int]


class ScheduleError(Exception):
    pass


def default_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def system_runner(cmd: list[str]) -> int:
    """Run a command with output going straight to the terminal; return its exit code."""
    return subprocess.run(cmd, check=False).returncode


def _quote(text: str) -> str:
    """Quote one word for systemd: backslash-escape \\ and ", double '%' (specifiers).

    The caller adds the surrounding double quotes.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def _quote_exec(text: str) -> str:
    # ExecStart additionally expands $VAR, so a literal $ must be written $$.
    return f'"{_quote(text).replace("$", "$$")}"'


def render_service(vault: Path, rpipe_cmd: str) -> str:
    return (
        "[Unit]\n"
        "Description=Research pipeline nightly run\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f'Environment="RESEARCH_VAULT={_quote(str(vault))}"\n'
        f"ExecStart={_quote_exec(rpipe_cmd)} run\n"
    )


def _parse_time(time: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{2}):(\d{2})", time)
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError(f"time must be HH:MM in 24-hour form (for example 03:00), got {time!r}")
    return int(m[1]), int(m[2])


def render_timer(time: str = "03:00") -> str:
    hour, minute = _parse_time(time)
    return (
        "[Unit]\n"
        "Description=Run the research pipeline every night\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def install(vault: Path, time: str, unit_dir: Path, runner: Runner, rpipe_cmd: str) -> list[Path]:
    service_text = render_service(vault, rpipe_cmd)
    timer_text = render_timer(time)  # validates before anything is written
    unit_dir.mkdir(parents=True, exist_ok=True)
    paths = [unit_dir / SERVICE_NAME, unit_dir / TIMER_NAME]
    paths[0].write_text(service_text)
    paths[1].write_text(timer_text)
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", TIMER_NAME]):
        code = runner(cmd)
        if code != 0:
            raise ScheduleError(
                f"`{' '.join(cmd)}` failed (exit {code}). Unit files were written to "
                f"{unit_dir} and left in place; fix the cause and run `rpipe schedule install` again."
            )
    return paths


def remove(unit_dir: Path, runner: Runner) -> list[Path]:
    """Disable the timer and delete the unit files; returns the files deleted."""
    runner(["systemctl", "--user", "disable", "--now", TIMER_NAME])  # fine if never installed
    removed = []
    for name in (TIMER_NAME, SERVICE_NAME):
        path = unit_dir / name
        if path.exists():
            path.unlink()
            removed.append(path)
    code = runner(["systemctl", "--user", "daemon-reload"])
    if code != 0:
        raise ScheduleError(f"files removed but `systemctl --user daemon-reload` failed (exit {code})")
    return removed


def status(runner: Runner) -> int:
    return runner(["systemctl", "--user", "list-timers", TIMER_NAME, "--all"])
