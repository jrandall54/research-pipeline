# Browsing the vault from your phone (`rpipe serve`)

## What it does

`rpipe serve` starts a small web server that shows your vault database in a browser and lets you vote on and save papers with one tap. You open it from your phone (or any device on your Tailscale network) and see the latest digest, your saved papers, and a vote up / vote down / clear vote / save / unsave button for each paper. Votes and saves go into the same `votes` and `saves` tables that `rpipe vote` and `rpipe save` use (see [data-model.md](data-model.md)), so the next run's feedback loop sees them.

The server is [Datasette](https://datasette.io), an existing open-source tool that turns a SQLite file into a browsable website. It is an optional extra, not part of the default install.

## How it works

```
 phone --(Tailscale, encrypted)--> rpipe serve --> datasette --> vault.sqlite
                                   (this machine, bound to its Tailscale IP only)
```

1. `rpipe serve` opens the vault database the normal way (`open_db`), so the WAL journal mode and any schema upgrade are applied before Datasette touches the file. It then closes its own connection.
2. It picks the address to listen on: your machine's Tailscale IPv4 address (from `tailscale ip -4`), or the `--host` you gave. `0.0.0.0` and `::` (every network interface) are refused outright.
3. It writes Datasette's *metadata* (a JSON description of the database and its canned queries) to a private temporary file created with `tempfile.mkstemp`, runs `python -m datasette serve vault.sqlite --host H --port 8001 --metadata FILE`, and deletes the temporary file when the server stops.
4. Datasette lists the **canned queries** (named SQL statements stored in the metadata) on the database page. Reading queries: `todays_digest` (papers in the latest digest, best first) and `saved`. Writing queries (marked `write: true`, which Datasette shows as a form with a button): `vote_up`, `vote_down`, `vote_clear`, `save`, `unsave`. Each takes one parameter, `paper_id`.

Worked example: you open `http://100.64.0.5:8001/vault/vote_up?paper_id=2401.00001` and press the button. Datasette runs

```sql
INSERT INTO votes (paper_id, vote, created_at)
SELECT :paper_id, 1, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
WHERE EXISTS (SELECT 1 FROM papers WHERE id = :paper_id)
```

A new `votes` row appears with a timestamp in the same format Python writes (`2026-11-21T09:30:00+00:00`, see "Timestamps" in [data-model.md](data-model.md)), and `rpipe feedback` reads it like any other vote. A typo in the id inserts nothing, because SQLite does not enforce foreign keys unless asked to, so the queries check that the paper exists themselves. `save` is `INSERT OR IGNORE` (saving twice is harmless) and `unsave` is a `DELETE`.

## Why it's built this way

- **Datasette, not a custom web UI.** Browsing tables, search and one-tap forms for named queries come for free, and there is no web code of our own to write, secure or explain. A custom UI would be the better product but a much larger thing to own.
- **Not Open WebUI** (or another chat front-end): that is for talking to models. We want to look at rows and press a button, not chat.
- **Optional extra.** The nightly pipeline never needs a web server, so `datasette` is installed only with `uv sync --extra serve` (or `uv tool install --editable '.[embed,serve]'`). Tests never import it or start a server.
- **Tailscale, bound to its address only.** Tailscale gives your devices a private network, so the phone can reach the machine without opening a port to the internet. We never bind `0.0.0.0` or `::` (every interface, including the local Wi-Fi and anything exposed to the internet). Rejected: exposing it publicly behind a password; more machinery, more to get wrong.
- **No login, on purpose, and that is only acceptable because of the network.** The write queries have no authentication. The threat model is: **anyone who can reach the Tailscale address can vote on and save papers**, and read your whole vault database. That is fine for a personal tailnet; do not share the machine into someone else's tailnet and do not forward this port anywhere. `--host 127.0.0.1` serves to the local machine only.
- **Writable, not immutable.** Datasette can open a file read-only and "immutable" (it assumes nothing changes), which would also break the write queries and hide the nightly run's changes. WAL mode lets Datasette read while `rpipe run` writes.
- **Subprocess, not an import.** Datasette runs as its own process (`python -m datasette`), so `serve.py` stays importable without it and a crash in the server cannot take down the CLI.

## How to run / check it

```
uv sync --extra serve        # once; installs Datasette
rpipe serve                  # listens on your Tailscale IPv4, port 8001
rpipe serve --host 127.0.0.1 --port 8123    # local only
```

Open the printed address, click `vault`, and use the canned queries. Stop with Ctrl-C.

Errors: `Tailscale not running; use --host 127.0.0.1 to serve locally` (Tailscale is off or not installed); `Datasette is not installed. Install it with: uv sync --extra serve`; `Refusing to listen on all interfaces` (you passed `0.0.0.0` or `::`). To check a vote landed: `rpipe feedback`.

The tests (`tests/test_serve.py`) run each canned query's SQL against a temporary vault with plain `sqlite3` and read the result back with `current_votes`, `list_saved` and `is_saved`; they inject a fake Tailscale runner and never start Datasette.

## Where the code is

`src/research_pipeline/serve.py` (`build_metadata`, `tailscale_ip`, `resolve_host`, `command`) and the `serve` command in `src/research_pipeline/cli.py`. The extra is declared in `pyproject.toml`.
