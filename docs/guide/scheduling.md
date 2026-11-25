# Scheduling the nightly run

## What it does

`rpipe schedule` makes the pipeline run by itself every night, without cron and without you remembering. It writes two small files that tell systemd (the Linux service manager) to run `rpipe run` once a day at a time you choose (default 03:00), and switches them on.

Commands (`install` and `show` take `--time` and `--vault`; no file editing needed):

- `rpipe schedule install [--time HH:MM] [--vault PATH]` writes the units and enables the timer.
- `rpipe schedule show [--time HH:MM] [--vault PATH]` prints the units without writing anything, so you can review them first.
- `rpipe schedule status` shows when the timer last fired and when it fires next; if the timer is not enabled it says so and suggests `rpipe schedule install`. It first checks that `systemctl` exists (the same clean message as `install`, no traceback), then that the systemd user manager can be reached; if not, it says exactly that (and exits 1) instead of claiming the timer is not installed, because then the real state is unknown and reinstalling would not help.
- `rpipe schedule remove` disables the timer and deletes both files (and reloads systemd only if something was removed). If disabling fails it warns and still removes the files. With nothing installed it just says so.
- `rpipe schedule set-deadline HH:MM|off [--vault PATH]` sets the morning deadline for reruns of cut papers (default 07:00). See "The morning deadline" below. `show` prints the current value on its last line.

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

**Persistent=true** means: if the machine was off or asleep at 03:00, systemd runs the missed job as soon as it can (once) instead of skipping the night. (Runs cannot overlap: `rpipe run` takes a lock on the vault, so if a catch-up run starts while one you launched by hand is still going, the second one exits at once with code 75 and the message `` another `rpipe run` is already using <vault> ``; systemd shows that night's unit as failed in the journal, which is harmless, and the next night runs normally. See `running.md`.)

**Linger.** A user timer normally only runs while you have a login session. `loginctl enable-linger $USER` (run once, by you) tells systemd to keep your user services alive while logged out, so a 03:00 run happens even if nobody is logged in. It changes machine-wide login behaviour, which is why `rpipe` never runs it for you.

## The morning deadline

**What it governs: reruns only.** Some papers are too long for the model's context window, so part of their core text is cut before the model reads them (a *body cut*, see `glossary.md`). A later stage reads such papers again with a bigger context, which only fits with part of the model in ordinary RAM (a *spill*), so it is slow. These *reruns* happen after the normal work and must be finished by a morning time you choose, so the computer is free when you want it. Normal papers are never held back by the deadline: they always run, however late it gets. How the reruns use the deadline (time estimates, free RAM, grouping) is in `running.md`, "Reading cut papers whole: reruns".

- Default: `07:00`. Set it with `rpipe schedule set-deadline 06:30`, which answers `Reruns of cut papers must finish by 06:30.`
- `rpipe schedule set-deadline off` turns reruns off (answer: `Reruns of cut papers are off.`).
- Accepted: a 24-hour time (`7:00` is stored as `07:00`) or `off`, in any case. Anything else (`7pm`, `25:00`) is refused with an example and the setting is left unchanged (exit 1).
- It is stored as `night_deadline` in `config.yaml` (quoted, see below) and printed by `rpipe schedule show` as `# Reruns of cut papers: deadline 07:00`. Because of that line, `show` now reads `config.yaml`: if the file is invalid it prints the config error and exits 1.
- Reruns themselves are the next pipeline stage (being built); this setting is in place first.

How the clock time becomes a deadline (`schedule.deadline_at`): take the next 07:00 strictly after now, local time. If that is more than 12 hours away (`MAX_NIGHT_HOURS`) there is no deadline tonight and reruns do not start.

| Now | Setting | Deadline |
| --- | --- | --- |
| 03:00 | 07:00 | today 07:00 (4 h left) |
| 23:00 | 07:00 | tomorrow 07:00 (8 h left) |
| 10:00 | 07:00 | none: tomorrow 07:00 is 21 h away, so this is a daytime run |
| 07:00 exactly | 07:00 | none: the next 07:00 is 24 h away |
| any | off | none |

Why it's built this way:

- **Reruns only, never normal papers.** A late night must not leave you with no digest. Reruns improve papers you already have a summary for, so skipping them costs little.
- **A clock time, not a duration.** "Done by 07:00" is what you care about whether the run started at 03:00 or was caught up at 05:00 after the machine slept (`Persistent=true`). Rejected: "at most 4 hours of reruns", which would run past breakfast after a late start.
- **The 12-hour cut-off.** If you run `rpipe run` by hand at 10:00, "the next 07:00" is tomorrow morning; spending the whole day spilling would hog the machine, so reruns are skipped. Rejected: always using the next occurrence.
- **Stored quoted, either form loads.** The YAML reader treats an unquoted `23:00` as a number of minutes after midnight (base 60: 23 x 60 = 1380), so the command always writes `"23:00"` in quotes. A hand-written unquoted `23:00` or `7:00` still works: the config check turns 1380 back into `23:00` and pads `7:00` to `07:00`. Rejected: refusing the unquoted form, which would stop every command (even `set-deadline`, which reads the file first) until you fixed the file by hand.

## Checks when you install

- **Linux only.** `rpipe schedule install`, `status` and `remove` use systemd. On macOS or Windows they print "rpipe schedule uses systemd, which only exists on Linux. On <OS>, run `rpipe run` once a day from your own scheduler (see the README)." and exit 1 without writing anything.
- **Backend check.** After the timer is installed, rpipe builds and tests the summary backend the way a run would. If it is not ready (Ollama not running, Claude Code logged out, the recorded `claude` path moved) it prints a warning and the fix (`rpipe model setup`) but still installs: the nightly run would write abstracts only until you fix it, and the digest is still written.

## Why it's built this way

- **The absolute CLI path is recorded.** The nightly unit has no `~/.local/bin` on its PATH and none of your shell setup. `rpipe model setup` stores the full path of the `claude` program in the vault's config, and `rpipe schedule install` writes the full path of `rpipe` into the unit, so neither has to be found by searching at 03:00. Rejected: relying on PATH, which works in your terminal and fails silently under systemd.

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
rpipe schedule status                # exits 1 with a clear message if systemctl is missing or the systemd user manager is unreachable (it no longer exits 0 with "not installed" in that case)
systemctl --user start research-pipeline.service   # run it now to test
journalctl --user -u research-pipeline.service     # its output
rpipe runs                           # the run record, as always
rpipe schedule set-deadline 06:30   # reruns of cut papers must finish by 06:30
rpipe schedule set-deadline off     # no reruns
rpipe schedule remove
```

Tests: `uv run pytest tests/test_schedule.py -q` (offline, fake runner, temporary unit folder).

## Where the code is

`src/research_pipeline/schedule.py` (`render_service`, `render_timer`, `install`, `remove`, `status`, `ScheduleError`, `parse_time`, `deadline_at`, `MAX_NIGHT_HOURS`), `set_night_deadline` in `src/research_pipeline/profile_edit.py` (checks the time with `parse_time`), `Config.night_deadline` and `normalise_deadline` in `src/research_pipeline/models.py`, and the `schedule` commands at the bottom of `src/research_pipeline/cli.py`. Tests: `tests/test_schedule.py`.
