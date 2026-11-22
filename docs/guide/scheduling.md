# Scheduling the nightly run

## What it does

`rpipe schedule` makes the pipeline run by itself every night, without cron and without you remembering. It writes two small files that tell systemd (the Linux service manager) to run `rpipe run` once a day at a time you choose (default 03:00), and switches them on.

Commands (`install` and `show` take `--time` and `--vault`; no file editing needed):

- `rpipe schedule install [--time HH:MM] [--vault PATH]` writes the units and enables the timer.
- `rpipe schedule show [--time HH:MM] [--vault PATH]` prints the units without writing anything, so you can review them first.
- `rpipe schedule status` shows when the timer last fired and when it fires next; if the timer is not enabled it says so and suggests `rpipe schedule install`.
- `rpipe schedule remove` disables the timer and deletes both files (and reloads systemd only if something was removed). If disabling fails it warns and still removes the files. With nothing installed it just says so.

## How it works

1. `install` finds your vault (the `--vault` option, `$RESEARCH_VAULT` or `~/research-vault`) and checks it exists. It uses the `rpipe` that is running right now (the absolute path of the program you typed, when it is called `rpipe` or `research-pipeline`), else the first `rpipe` on your PATH; if there is none it stops with an install hint. It then checks that this environment can import `sentence_transformers`; if not it **refuses** and prints `uv tool install --editable '.[embed]' --force`. Without the embed extra the 03:00 run would fail before recording anything. `show` does the same checks. A vault path containing a control character (such as a newline) is refused too.
2. It checks `systemctl` exists and that the user manager answers (`systemctl --user show-environment`); if not it stops with "nothing written".
3. It validates the time (`HH:MM`, 24-hour; `25:00` and `3am` are rejected) before touching any file.
4. It writes `~/.config/systemd/user/research-pipeline.service` (what to run) and `research-pipeline.timer` (when to run it).
5. It runs `systemctl --user daemon-reload` (make systemd re-read its files) and `systemctl --user enable --now research-pipeline.timer` (start now, and again at each login or boot; to run with nobody logged in you also need linger, below). If either fails, the files stay where they are, the error says so, and you can re-run `install` after fixing the cause.
6. It prints `loginctl enable-linger $USER`. This is only printed, never run: see "Linger" below.

Sample of what is generated (vault `/home/you/research vault`, `rpipe` at `/home/you/.local/bin/rpipe`):

```
# research-pipeline.service
[Unit]
Description=Research pipeline nightly run

[Service]
Type=oneshot
Environment="RESEARCH_VAULT=/home/you/research vault"
ExecStart="/home/you/.local/bin/rpipe" run

# research-pipeline.timer
[Unit]
Description=Run the research pipeline every night

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Quoting: systemd splits `ExecStart` on spaces and expands `%` and `$`, so paths are wrapped in double quotes, `\` and `"` are backslash-escaped, `%` is written `%%` and (in `ExecStart`) `$` as `$$`. A vault path with spaces therefore works. `Environment="NAME=value"` quotes the whole assignment.

**Persistent=true** means: if the machine was off or asleep at 03:00, systemd runs the missed job as soon as it can (once) instead of skipping the night. (Runs cannot overlap: `rpipe run` takes a lock on the vault, so if a catch-up run starts while one you launched by hand is still going, the second one exits at once with code 75 and the message `another rpipe run is already using ...`; systemd shows that night's unit as failed in the journal, which is harmless, and the next night runs normally. See `running.md`.)

**Linger.** A user timer normally only runs while you have a login session. `loginctl enable-linger $USER` (run once, by you) tells systemd to keep your user services alive while logged out, so a 03:00 run happens even if nobody is logged in. It changes machine-wide login behaviour, which is why `rpipe` never runs it for you.

## Why it's built this way

- **systemd user timer, not cron.** `Persistent=true` catches up missed runs; cron silently skips them. systemd also keeps the output in the journal and `systemctl --user list-timers` shows the next run. Cron rejected: no catch-up, no built-in status, and it needs the environment (vault path) smuggled into a crontab line.
- **User units, not system units.** The vault and Ollama belong to your user; a system-level unit would need root, run as the wrong user, and could not find your home directory. User units need no sudo.
- **`Type=oneshot`, not `Type=simple`.** The run starts, does its work and exits. `oneshot` tells systemd the service is "done" when the process exits, so the timer can start it again next night and `status` is meaningful. `simple` would mark it active the instant it starts.
- **Refusing an `rpipe` without the embed extra.** The nightly unit runs one fixed program. If that program lacks the embedding library, `rpipe run` used to fail silently at 03:00 (and now records the failure as `embed: ...` in `runs`). Refusing at install time catches it when you are looking. Rejected: warning only; a warning scrolls past and the first failure is at 03:00.
- **Only printing the linger command.** Changing login behaviour should be a conscious decision by the owner.
- **Injected runner.** `schedule.py` takes the function that runs `systemctl` as a parameter, so tests use a fake and never touch the real systemd or `~/.config`.

## How to run / check it

```
rpipe schedule show --time 03:30     # review
rpipe schedule install --time 03:30
loginctl enable-linger $USER         # once, yourself
rpipe schedule status
systemctl --user start research-pipeline.service   # run it now to test
journalctl --user -u research-pipeline.service     # its output
rpipe runs                           # the run record, as always
rpipe schedule remove
```

Tests: `uv run pytest tests/test_schedule.py -q` (offline, fake runner, temporary unit folder).

## Where the code is

`src/research_pipeline/schedule.py` (`render_service`, `render_timer`, `install`, `remove`, `status`, `ScheduleError`) and the `schedule` commands at the bottom of `src/research_pipeline/cli.py`. Tests: `tests/test_schedule.py`.
