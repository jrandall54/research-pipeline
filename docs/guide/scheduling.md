# Scheduling the nightly run

## What it does

`rpipe schedule` makes the pipeline run by itself every night, without cron and without you remembering. It writes two small files that tell systemd (the Linux service manager) to run `rpipe run` once a day at a time you choose (default 03:00), and switches them on.

Commands (all take the time or vault as options; no file editing needed):

- `rpipe schedule install [--time HH:MM] [--vault PATH]` writes the units and enables the timer.
- `rpipe schedule show [--time HH:MM] [--vault PATH]` prints the units without writing anything, so you can review them first.
- `rpipe schedule status` shows when the timer last fired and when it fires next.
- `rpipe schedule remove` disables the timer and deletes both files.

## How it works

1. `install` finds your vault (the `--vault` option, `$RESEARCH_VAULT` or `~/research-vault`) and checks it exists. It finds the `rpipe` program with `which rpipe`; if it is not on your PATH it stops with an install hint.
2. It validates the time (`HH:MM`, 24-hour; `25:00` and `3am` are rejected) before touching any file.
3. It writes `~/.config/systemd/user/research-pipeline.service` (what to run) and `research-pipeline.timer` (when to run it).
4. It runs `systemctl --user daemon-reload` (make systemd re-read its files) and `systemctl --user enable --now research-pipeline.timer` (start now and on every login/boot). If either fails, the files stay where they are, the error says so, and you can re-run `install` after fixing the cause.
5. It prints `loginctl enable-linger $USER`. This is only printed, never run: see "Linger" below.

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

**Persistent=true** means: if the machine was off or asleep at 03:00, systemd runs the missed job as soon as it can (once) instead of skipping the night.

**Linger.** A user timer normally only runs while you have a login session. `loginctl enable-linger $USER` (run once, by you) tells systemd to keep your user services alive while logged out, so a 03:00 run happens even if nobody is logged in. It changes machine-wide login behaviour, which is why `rpipe` never runs it for you.

## Why it's built this way

- **systemd user timer, not cron.** `Persistent=true` catches up missed runs; cron silently skips them. systemd also keeps the output in the journal and `systemctl --user list-timers` shows the next run. Cron rejected: no catch-up, no built-in status, and it needs the environment (vault path) smuggled into a crontab line.
- **User units, not system units.** The vault and Ollama belong to your user; a system-level unit would need root, run as the wrong user, and could not find your home directory. User units need no sudo.
- **`Type=oneshot`, not `Type=simple`.** The run starts, does its work and exits. `oneshot` tells systemd the service is "done" when the process exits, so the timer can start it again next night and `status` is meaningful. `simple` would mark it active the instant it starts.
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
