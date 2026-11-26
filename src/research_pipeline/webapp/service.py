"""The always-on app service: a systemd *user* unit (see docs/guide/app.md, Setting it up).

Imports no web framework, so `rpipe app install` can check the extra before anything is written.
"""

import ipaddress
import os
import re
from pathlib import Path

from research_pipeline.schedule import Runner, ScheduleError, _quote, _quote_exec, _reject_control_chars

SERVICE_NAME = "research-pipeline-app.service"

_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


def allowed_hosts(bind: str) -> set[str]:
    """The Host values the app accepts: the bind host, plus every loopback spelling for a loopback bind."""
    host = bind.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    return {host} | _LOOPBACK_NAMES if loopback else {host}


def render_service(vault: Path, rpipe_cmd: str, host: str, port: int) -> str:
    _reject_control_chars("The vault path", str(vault))
    _reject_control_chars("The rpipe path", rpipe_cmd)
    _reject_control_chars("The host", host)
    return (
        "[Unit]\n"
        "Description=Research pipeline app\n"
        "After=network-online.target tailscaled.service\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        f'Environment="RESEARCH_VAULT={_quote(str(vault))}"\n'
        f"ExecStart={_quote_exec(rpipe_cmd)} app run --host {host} --port {int(port)}\n"
        "Restart=on-failure\n"
        "RestartSec=10\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def install(vault: Path, rpipe_cmd: str, host: str, port: int, unit_dir: Path, runner: Runner) -> Path:
    text = render_service(vault, rpipe_cmd, host, port)  # validates before anything is written
    if runner(["systemctl", "--user", "show-environment"]) != 0:
        raise ScheduleError("systemd user manager is not reachable (`systemctl --user` failed); nothing written.")
    unit_dir.mkdir(parents=True, exist_ok=True)
    path = unit_dir / SERVICE_NAME
    path.write_text(text)
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", SERVICE_NAME]):
        code = runner(cmd)
        if code != 0:
            raise ScheduleError(
                f"`{' '.join(cmd)}` failed (exit {code}). The unit file was written to "
                f"{unit_dir} and left in place; fix the cause and run `rpipe app install` again."
            )
    return path


def remove(unit_dir: Path, runner: Runner) -> tuple[list[Path], list[str]]:
    """Stop and disable the service and delete its unit file; returns (files deleted, warnings)."""
    path = unit_dir / SERVICE_NAME
    warnings: list[str] = []
    present = [path] if os.path.lexists(path) else []
    if present and runner(["systemctl", "--user", "disable", "--now", SERVICE_NAME]) != 0:
        warnings.append("`systemctl --user disable --now` failed; the app may still be running "
                        "(check `systemctl --user status research-pipeline-app`). The file is being removed anyway.")
    for p in present:
        p.unlink(missing_ok=True)
    if present and runner(["systemctl", "--user", "daemon-reload"]) != 0:
        warnings.append("file removed but `systemctl --user daemon-reload` failed")
    return present, warnings


def address(unit_dir: Path) -> str | None:
    """`http://host:port/` read from the installed unit's ExecStart, or None if not installed."""
    try:
        text = (unit_dir / SERVICE_NAME).read_text()
    except OSError:
        return None
    m = re.search(r"^ExecStart=.* app run --host (\S+) --port (\d+)\s*$", text, re.MULTILINE)
    if not m:
        return None
    host = f"[{m[1]}]" if ":" in m[1] else m[1]
    return f"http://{host}:{m[2]}/"


def is_active(runner: Runner) -> bool:
    return runner(["systemctl", "--user", "is-active", "--quiet", SERVICE_NAME]) == 0
