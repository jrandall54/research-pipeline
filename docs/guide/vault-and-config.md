# Vault and Config

## What it does

The **vault** is a folder (default `~/research-vault/`) that holds everything personal: your settings, your profile, the database, and every digest. The repo holds only code and defaults, so you can reinstall or share the engine without leaking anything. `research-pipeline init` builds the vault; `load_config` and `load_profile` read the two settings files back into validated objects.

## How it works

### Folder layout

```
research-vault/
  config.yaml      pipeline settings            (Phase 1)
  profile.yaml     what you care about          (Phase 1)
  vault.sqlite     all paper state, runs, search index  (Phase 1)
  digests/         one markdown digest per day  (Phase 1)
  cache/           small caches; seeds.json holds the text of your seed papers (Phase 1)
  raw/             downloaded PDFs              (Phase 2)
  markdown/        PDFs converted to markdown   (Phase 2)
  summaries/       generated summaries          (Phase 2)
  my_work/         your own writing, used to bias ranking (Phase 4)
  gold/            papers you rated, for tuning (Phase 3)
```

Phase 1 only writes `vault.sqlite`, `digests/` and `cache/` (plus the two YAML files); the other folders are created now so the layout never changes later.

### `config.yaml` fields

| Field | Default | Meaning |
| --- | --- | --- |
| `vault_path` | the vault's path (set by `init`) | Informational only: the vault actually used is chosen by `--vault`, then `$RESEARCH_VAULT`, then `~/research-vault`; this field is never read back. Required. |
| `arxiv_categories` | `cs.LG, cs.CL, cs.CR, cs.OS, cs.SE` | arXiv categories collected each night. |
| `arxiv_max_results` | `1000` | Safety cap on arXiv papers fetched per run (at least 1). Paging stops earlier at the first page with an already-stored paper; hitting the cap first adds a warning to the health line (expected on the first run). See `sources.md`. |
| `embedding_model` | `BAAI/bge-small-en-v1.5` | Model that turns text into vectors for similarity (see `glossary.md`). |
| `rss_feeds` | `[]` | Extra feed URLs (read but not used yet: RSS collection is a later phase). |

Both files reject unknown keys: a typo such as `keyword:` or `top-n:` stops the command with an error naming the file and the key, instead of being silently ignored.

### `profile.yaml` fields

| Field | Default | Meaning |
| --- | --- | --- |
| `topics` | 3 generic examples | Plain-language phrases for what you care about. Required. |
| `seeds` | 3 placeholders | arXiv ids of papers you like. May be empty (ranking then relies on topics). Required. |
| `followed_authors` | `[]` | Authors whose papers get a boost. |
| `keywords` | `[]` | Words in a title/abstract that give a small boost. |
| `top_n` | 15 | Papers kept per digest (at least 1). |

Seed ids may be written with or without quotes (`2301.10000` or `"2301.10000"`); both load as the text `"2301.10000"`. See the YAML note under "Why it's built this way".

### What `init` does, step by step

1. Resolve the vault path: `--vault`, else env `RESEARCH_VAULT`, else `~/research-vault`. `~` is expanded and relative paths are made absolute, so `config.yaml` always records the absolute path.
2. If stdin is a terminal, ask for topics (comma-separated) and arXiv categories (default shown). When piped or run from a script it asks nothing and uses the defaults.
3. Create the folders above (existing ones are left alone).
4. Write `profile.yaml` and `config.yaml` from the packaged templates **only if they do not exist yet**.
5. Open `vault.sqlite` with `open_db`, which creates all tables if missing.
6. Print what was created or kept, and the next steps.

Running `init` again is safe: it never overwrites your edited files.

### Errors

A missing file, invalid YAML, or a value that breaks the schema (for example `top_n: 0`) raises `ConfigError` with a message that starts with the file's path.

### Picking good seed papers

Choose 10-20 papers you would be glad to see more of: ones you have read and learned from, spread over your topics rather than all on one narrow idea. Mix landmark papers with recent ones. Avoid papers you only skimmed or that are famous but outside your interests, since the ranker treats every seed as "more like this". Use the arXiv id (the part after `arxiv.org/abs/`, without a version suffix).

## Why it's built this way

- **Vault outside the repo**: personal data can never be committed by accident.
- **Templates shipped as package data** (`importlib.resources`) rather than copied from a path in the repo, so it works from an installed wheel.
- **Never overwrite**: your profile is the most valuable file here; re-running `init` must be harmless. Rejected: a `--force` flag (YAGNI; delete the file instead).
- **Prompts only on a TTY**: keeps `init` usable in scripts and tests.
- **Single `ConfigError`**: callers handle one exception, and users see which file is wrong rather than a pydantic traceback.
- **Float-free YAML loader**: YAML infers types. An unquoted `2301.10000` looks like a decimal number, so a standard loader turns it into the float 2301.1, dropping the trailing zeros and naming a different paper; the original text cannot be recovered afterwards. Our loader is `yaml.SafeLoader` with its implicit float rule removed, so float-looking scalars stay strings. Integers such as `top_n: 15` still parse as ints and no setting is a float. Rejected: telling users to quote every id (easy to forget, and the failure is silent).
- **`vault.sqlite`** holds everything in one file; plain files would need a hand-rolled index.

## How to run / check it

```
uv run research-pipeline init                 # prompts in a terminal
uv run research-pipeline init --vault /tmp/v  # custom location
uv run pytest tests/test_vault.py -q
```

## Where the code is

`src/research_pipeline/vault.py` (layout, `resolve_vault`, loaders, `ConfigError`), `src/research_pipeline/cli.py` (`init`), `src/research_pipeline/defaults/` (templates), `tests/test_vault.py`.
