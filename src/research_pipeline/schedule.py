"""Nightly scheduling via a systemd *user* timer (see docs/guide/scheduling.md)."""

import os
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
    """Run a command with output going straight to the terminal; return its exit code.

    The `show-environment` reachability probe is silent: its output is the whole systemd user
    environment (which can hold secrets) and only its exit code matters.
    """
    quiet = "show-environment" in cmd
    out = subprocess.DEVNULL if quiet else None
    try:
        return subprocess.run(cmd, check=False, stdout=out, stderr=out).returncode
    except OSError as exc:
        raise ScheduleError("systemctl not found (is systemd available?)") from exc


def _quote(text: str) -> str:
    """Quote one word for systemd: backslash-escape \\ and ", double '%' (specifiers).

    The caller adds the surrounding double quotes.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def _quote_exec(text: str) -> str:
    # ExecStart additionally expands $VAR, so a literal $ must be written $$.
    return f'"{_quote(text).replace("$", "$$")}"'


def _reject_control_chars(label: str, text: str) -> None:
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ScheduleError(f"{label} contains a control character (such as a newline), which a unit file cannot hold safely.")


def render_service(vault: Path, rpipe_cmd: str) -> str:
    _reject_control_chars("The vault path", str(vault))
    _reject_control_chars("The rpipe path", rpipe_cmd)
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
    # Probe the user manager BEFORE writing, so an unreachable systemd leaves nothing behind.
    if runner(["systemctl", "--user", "show-environment"]) != 0:
        raise ScheduleError("systemd user manager is not reachable (`systemctl --user` failed); nothing written.")
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


def remove(unit_dir: Path, runner: Runner) -> tuple[list[Path], list[str]]:
    """Disable the timer and delete the unit files; returns (files deleted, warnings)."""
    paths = [unit_dir / TIMER_NAME, unit_dir / SERVICE_NAME]
    warnings: list[str] = []
    present = [p for p in paths if os.path.lexists(p)]  # lexists: a broken symlink still counts
    if present and runner(["systemctl", "--user", "disable", "--now", TIMER_NAME]) != 0:
        warnings.append("`systemctl --user disable --now` failed; the timer may still be enabled "
                        f"(check `systemctl --user list-timers`). Files are being removed anyway.")
    for path in present:
        path.unlink(missing_ok=True)
    if present and runner(["systemctl", "--user", "daemon-reload"]) != 0:
        warnings.append("files removed but `systemctl --user daemon-reload` failed")
    return present, warnings


def status(runner: Runner) -> bool:
    """Print the timer schedule; return False (printing nothing) when it is not enabled.

    Raises ScheduleError when the systemd user manager cannot be reached, so "not installed"
    and "cannot ask systemd" are never confused.
    """
    if runner(["systemctl", "--user", "show-environment"]) != 0:
        raise ScheduleError(
            "Cannot reach the systemd user manager (`systemctl --user` failed), so the timer's "
            "state is unknown. Log in to a normal user session (or enable lingering) and try again."
        )
    if runner(["systemctl", "--user", "is-enabled", "--quiet", TIMER_NAME]) != 0:
        return False
    if runner(["systemctl", "--user", "list-timers", TIMER_NAME, "--all"]) != 0:
        raise ScheduleError("systemctl could not list the timer.")
    return True
