# Browsing the vault from your phone (`rpipe serve`)

## What it does

`rpipe serve` starts a small web server that shows your vault database in a browser and lets you vote on and save papers. You open it from your phone (or any device on your Tailscale network) and see the latest digest and your saved papers, plus a small form for each action: vote up, vote down, clear vote, save and unsave. Each form asks you to type or paste the paper's id (shown in the digest and in the `id` column of the lists) and press a button; there is no one-tap button per paper row. Votes and saves go into the same `votes` and `saves` tables that `rpipe vote` and `rpipe save` use (see [data-model.md](data-model.md)), so the next run's feedback loop sees them.

The server is [Datasette](https://datasette.io), an existing open-source tool that turns a SQLite file into a browsable website. It is an optional extra, not part of the default install.

## How it works

```
 phone --(Tailscale, encrypted)--> rpipe serve --> datasette --> vault.sqlite
                                   (this machine, bound to its Tailscale IP only)
```

1. `rpipe serve` opens the vault database the normal way (`open_db`), so the WAL journal mode and any schema upgrade are applied before Datasette touches the file. It then closes its own connection.
2. It picks the address to listen on: your machine's Tailscale IPv4 address (from `tailscale ip -4`), or the `--host` you gave. The host is checked against an **allowlist**: `localhost`, a loopback IP (`127.0.0.0/8`, `::1`) or a Tailscale IP (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`). `localhost` is matched case-insensitively; IPv6 scope ids (anything containing `%`) and IPv4-mapped IPv6 forms such as `::ffff:127.0.0.1` are refused. Anything else (all-interfaces spellings such as `0.0.0.0`, `0`, `::`, empty, a LAN or public IP, any other hostname) is refused with the reason and the allowed set. The address from `tailscale ip -4` goes through the same check.
3. It writes Datasette's *metadata* (a JSON description of the database and its canned queries) to a private temporary file created with `tempfile.mkstemp`, runs `python -m datasette serve vault.sqlite --host H --port 8001 --metadata FILE`, and deletes the temporary file when the server stops (Ctrl-C or SIGTERM; only SIGKILL can leave it behind in the temp folder).
4. Datasette lists the **canned queries** (named SQL statements stored in the metadata) on the database page. Reading queries: `todays_digest` (papers in the latest digest, best first) and `saved`. Writing queries (marked `write: true`, which Datasette shows as a form with a button): `vote_up`, `vote_down`, `vote_clear`, `save`, `unsave`. Each takes one parameter, `paper_id`, declared explicitly in the metadata (`"params": ["paper_id"]`) because Datasette finds parameters with a text pattern that would otherwise also pick up the `:00` inside the timestamp literal and show a bogus input box.

Worked example: you open `http://100.64.0.5:8001/vault/vote_up?paper_id=2401.00001` and press the button. Datasette runs

```sql
INSERT INTO votes (paper_id, vote, created_at)
SELECT :paper_id, 1, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
WHERE EXISTS (SELECT 1 FROM papers WHERE id = :paper_id)
```

A new `votes` row appears with a timestamp in the same format Python writes (`2026-11-21T09:30:00+00:00`, see "Timestamps" in [data-model.md](data-model.md)), and `rpipe feedback` reads it like any other vote. A typo in the id inserts nothing (Datasette still shows its normal success message, so check with `rpipe feedback` if unsure), because SQLite does not enforce foreign keys unless asked to, so the queries check that the paper exists themselves. `save` is `INSERT OR IGNORE` (saving twice is harmless) and `unsave` is a `DELETE`.

## Why it's built this way

- **Datasette, not a custom web UI.** Browsing tables, search and one-tap forms for named queries come for free, and there is no web code of our own to write, secure or explain. A custom UI would be the better product but a much larger thing to own.
- **Not Open WebUI** (or another chat front-end): that is for talking to models. We want to look at rows and press a button, not chat.
- **Optional extra.** The nightly pipeline never needs a web server, so `datasette` is installed only as an extra: `uv sync --extra embed --extra serve` for repo use (`uv sync` is an exact sync, so name every extra you want or it removes the others), or `uv tool install --editable '.[embed,serve]' --force` for the plain `rpipe` command (the uv-tool environment is separate; `uv sync` never touches it). Tests never import it or start a server.
- **Tailscale, bound to its address only.** Tailscale gives your devices a private network, so the phone can reach the machine without opening a port to the internet. We never bind every interface (`0.0.0.0` or `::`, which include local Wi-Fi and anything exposed to the internet). The check is an allowlist, not a blocklist of those spellings: there are too many ways to write "everywhere" (`0`, `0.0.0`, `::0`, `::ffff:0.0.0.0`, an empty string), and a blocklist misses one eventually; an allowlist fails safe. Rejected: exposing it publicly behind a password; more machinery, more to get wrong.
- **No login, on purpose, and that is only acceptable because of the network.** The write queries have no authentication. The threat model is: **anyone who can reach the Tailscale address can vote on and save papers**, and read your whole vault database. Datasette also does not check the `Host` header, so in principle a malicious web page open on a device in your tailnet could use DNS rebinding to make that device's browser read the vault; this is accepted under the same threat model (a personal tailnet and your own browsing). That is fine for a personal tailnet; do not share the machine into someone else's tailnet and do not forward this port anywhere. `--host 127.0.0.1` serves to the local machine only.
- **Writable, not immutable.** Datasette can open a file read-only and "immutable" (it assumes nothing changes), which would also break the write queries and hide the nightly run's changes. WAL mode lets Datasette read while `rpipe run` writes.
- **A second write path, kept on purpose for now.** The canned queries write to `votes` and `saves` with their own SQL. They bypass `library_api.py`, the app's one door that `rpipe vote` and `rpipe save` go through (see "The app's door" in [architecture.md](architecture.md)), so the rules for a valid vote live in two places. Datasette is a stopgap until the app has its own web view, so this is accepted and noted rather than rebuilt. These writes do not take the pipeline lock (`.run.lock`, see "One pipeline job at a time" in [running.md](running.md)), like `rpipe vote` and `rpipe save`: each is one small row, and SQLite's busy timeout lets it wait a moment if a nightly run is writing at that instant.
- **Datasette pinned below 1.0** (`datasette>=0.65,<1`). Datasette 1.0 moved canned queries out of `--metadata` into `datasette.yaml` ("stored queries"). The metadata shape used here (`databases.<db>.queries`, `write: true`) was checked against the 0.65.x docs only, so the extra stays on that line until the 1.x shape is verified. The shape was run against a real Datasette 0.65.6: the forms appear, the write queries run and are refused for unknown ids, and `rpipe feedback` sees the rows.
- **Subprocess, not an import.** Datasette runs as its own process (`python -m datasette`), so `serve.py` stays importable without it and a crash in the server cannot take down the CLI.

## How to run / check it

```
uv sync --extra embed --extra serve   # once, in the repo; installs Datasette and keeps the embed extra
uv tool install --editable '.[embed,serve]' --force   # instead, if you run the plain `rpipe`
rpipe serve                  # listens on your Tailscale IPv4, port 8001
rpipe serve --host 127.0.0.1 --port 8123    # local only
```

Open the printed address, click `vault`, and use the canned queries. Stop with Ctrl-C.

Errors: `Tailscale not running; use --host 127.0.0.1 to serve locally` (Tailscale is off or not installed); `Datasette is not installed. Install it with: uv sync --extra embed --extra serve (in the repo), or for plain `rpipe`: uv tool install --editable '.[embed,serve]' --force`; `Refusing to listen on '0.0.0.0': it is not a loopback or Tailscale address, and this server has no login. Allowed: ...` (you passed `0.0.0.0`, `::` or another non-allowlisted address). To check a vote landed: `rpipe feedback`.

The tests (`tests/test_serve.py`) run each canned query's SQL against a temporary vault with plain `sqlite3` and read the result back with `library.current_votes`, `library.list_saved` and `library.is_saved`; they inject a fake Tailscale runner and never start Datasette.

## Where the code is

`src/research_pipeline/serve.py` (`build_metadata`, `tailscale_ip`, `resolve_host`, `command`) and the `serve` command in `src/research_pipeline/cli.py`. The extra is declared in `pyproject.toml`.
