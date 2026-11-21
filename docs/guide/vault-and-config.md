# Vault and Config

## What it does

The **vault** is a folder (default `~/research-vault/`) that holds everything personal: your settings, your profile, the database, and every digest. The repo holds only code and defaults, so you can reinstall or share the engine without leaking anything. `rpipe init` builds the vault; `load_config` and `load_profile` read the two settings files back into validated objects.

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
| `arxiv_categories` | `cs.LG, cs.CL, cs.CR, cs.OS, cs.SE` | arXiv categories collected each night, or `["all"]` for every computer-science category (`cs.*`; much more to fetch and rank, see "Choosing categories" in `profile.md`). Change with `rpipe profile set-categories`. |
| `arxiv_max_results` | `1000` | Safety cap on arXiv papers fetched per run (at least 1). Paging stops earlier at the first page with an already-stored paper; hitting the cap first adds a warning to the health line (expected on the first run). See `sources.md`. |
| `embedding_model` | `BAAI/bge-small-en-v1.5` | Model that turns text into vectors for similarity (see `glossary.md`). |
| `rss_feeds` | `[]` | Extra feed URLs (read but not used yet: RSS collection is a later phase). |
| `llm_model` | `qwen3:14b` | Ollama model that writes summaries. Change with `rpipe model set NAME`. See `llm.md`. |
| `llm_url` | `http://localhost:11434` | Where the Ollama server listens. |
| `llm_num_ctx` | `32768` | Context window in tokens (at least 2048): how much text the model reads at once. See `llm.md`. |

Both files reject unknown keys: a typo such as `keyword:` or `top-n:` stops the command with an error naming the file and the key, instead of being silently ignored.

### `profile.yaml` fields

| Field | Default | Meaning |
| --- | --- | --- |
| `topics` | 3 generic examples | Plain-language phrases for what you care about. Required. |
| `seeds` | `[]` | arXiv ids of papers you like (add with `rpipe profile add-seed`). May be empty (ranking then relies on topics). Required. |
| `followed_authors` | `[]` | Authors whose papers get a boost. |
| `keywords` | `[]` | Words in a title/abstract that give a small boost. |
| `top_n` | 15 | Papers kept per digest (at least 1). |

Normal use needs no editing: `rpipe profile show` prints these settings and the `rpipe profile ...` commands change them, validating every change (see [profile.md](profile.md), which also explains how your comments are kept). The files stay plain YAML, so power users may edit them by hand; a mistake is reported with the file name and field the next time any command reads it.

Seed ids may be written with or without quotes (`2301.10000` or `"2301.10000"`); both load as the text `"2301.10000"`. See the YAML note under "Why it's built this way".

### What `init` does

`rpipe init` builds the vault and, in a terminal, walks you through setting it up so you never edit a file. It has two parts.

**Part 1, always:** resolve the vault path (`--vault`, else env `RESEARCH_VAULT`, else `~/research-vault`; `~` is expanded and relative paths made absolute, so `config.yaml` records the absolute path); create the folders above (existing ones are left alone); write `profile.yaml` and `config.yaml` from the packaged templates **only if they do not exist yet**; open `vault.sqlite` (creating missing tables); print what was created or kept.

**Part 2, only in a terminal (the wizard):**

1. **Topics.** On a new vault it explains what a topic is and asks `Topic (blank to finish)` repeatedly. Each topic is saved the moment you press Enter. Once you enter a topic of your own, the template examples you did not type are removed; enter none and the examples stay. A topic you type is never removed, even if it happens to match an example (typing only an example keeps all three examples). On an existing vault it shows your topics and asks `Add more topics? [y/N]`; the default is no, so Enter changes nothing. Whether the topics are still the template examples is decided from their content, not from whether this run created the file: if an earlier run was interrupted before any topic (or `init` first ran outside a terminal), the list is labelled "these are the template examples, not topics of your own", and the first topic of your own replaces them exactly as on a new vault. A profile whose topics differ from the template at all is treated as yours and nothing is removed.
2. **Categories.** On a new vault it first explains in a few lines what an arXiv category is (with common examples, the taxonomy URL, `all` (every cs.* category) for "not sure", and the command to change it later), then asks for the categories (default shown; Enter keeps it, `all` collects every computer-science category). On an existing vault it shows them and asks `Change categories? [y/N]`.
3. **Seeds.** It explains in a line what a seed paper is, then asks `Paper link, arXiv id or title (blank to finish)` repeatedly. Each entry goes through the same look-up, confirm and pick flow as `rpipe profile add-seed` and is saved at once. If arXiv cannot be reached, or the seed cannot be saved, that entry is skipped with a one-line message and the wizard carries on. Fewer than 5 seeds prints a tip to add more later.
4. **Summary.** It prints the `rpipe profile show` view and the next step, `rpipe run`.

A sample session (new vault; `>` marks what you type):

```
Vault ready at /home/you/research-vault
  config.yaml: created
  profile.yaml: created
  vault.sqlite, raw/, markdown/, summaries/, my_work/, gold/, digests/, cache/ ready
Topics are short phrases for what you care about, e.g. prompt injection attacks on LLM agents.
Topic (blank to finish): > prompt injection attacks on LLM agents
Added topic: prompt injection attacks on LLM agents
Topic (blank to finish): > 
An arXiv category is the subject tag every paper carries (cs.CR = security). Only papers in your categories are collected.
Common ones: cs.CR security, cs.LG machine learning, cs.AI AI, cs.CL language/LLMs, cs.OS operating systems, cs.SE software engineering, cs.DC distributed computing, cs.NI networking, stat.ML statistical ML.
Full list: https://arxiv.org/category_taxonomy
Press Enter to keep the default, or type all if you are not sure (every computer-science category (cs.*); more to fetch and rank). Change it any time with rpipe profile set-categories.
arXiv categories (comma-separated) [cs.LG, cs.CL, cs.CR, cs.OS, cs.SE]: > 
Seed papers are papers you like; the ranker finds new papers similar to them.
Paper link, arXiv id or title (blank to finish): > https://arxiv.org/abs/2307.15043
Universal and Transferable Adversarial Attacks on Aligned Language Models (Andy Zou, 2023)
Add this paper? [Y/n]: > y
Added seed 2307.15043: Universal and Transferable Adversarial Attacks on Aligned Language Models
Paper link, arXiv id or title (blank to finish): > 
Tip: 10-20 seeds work best; add more later with rpipe profile add-seed

Topics:
  1. prompt injection attacks on LLM agents
Seeds:
  1. 2307.15043  Universal and Transferable Adversarial Attacks on Aligned Language Models
...

Next step: rpipe run
```

**Interrupting.** Ctrl-C (or end of input) at any prompt prints `Saved what you entered so far. Re-run rpipe init to continue.` and exits with code 130 (the conventional "interrupted" code). Every answer you already confirmed is saved, because each one is written by its own atomic edit; nothing is ever half-written. Re-running `init` is safe: it never overwrites your files, and on an existing vault it asks before adding or changing anything, so earlier answers are never silently dropped.

**Not in a terminal** (piped or run from a script): no questions are asked; the folders and template files are created, and the next steps are printed.

### Errors

A missing file, invalid YAML, or a value that breaks the schema (for example `top_n: 0`) raises `ConfigError` with a message that starts with the file's path.

### Picking good seed papers

Add them in the `rpipe init` wizard or later with `rpipe profile add-seed`. Choose 10-20 papers you would be glad to see more of: ones you have read and learned from, spread over your topics rather than all on one narrow idea. Mix landmark papers with recent ones. Avoid papers you only skimmed or that are famous but outside your interests, since the ranker treats every seed as "more like this". You can paste the arXiv link, the id or part of the title; the command shows the paper and asks you to confirm.

## Why it's built this way

- **Vault outside the repo**: personal data can never be committed by accident.
- **Templates shipped as package data** (`importlib.resources`) rather than copied from a path in the repo, so it works from an installed wheel.
- **Never overwrite**: your profile is the most valuable file here; re-running `init` must be harmless. Rejected: a `--force` flag (YAGNI; delete the file instead).
- **Prompts only on a TTY**: keeps `init` usable in scripts and tests.
- **A wizard that saves as it goes**: each answer is committed through the same validated edit functions as the `rpipe profile` commands, so an interrupt loses at most the question you were on. Rejected: collecting everything and writing at the end (Ctrl-C would lose all of it). Rejected: a second copy of the seed look-up for the wizard (one shared helper keeps `add-seed` and `init` identical).
- **Ask before changing on re-run**: an earlier version asked for topics even on an existing vault and silently threw the answers away. Now an existing vault shows its values and asks yes/no first.
- **Single `ConfigError`**: callers handle one exception, and users see which file is wrong rather than a pydantic traceback.
- **Float-free YAML loader**: YAML infers types. An unquoted `2301.10000` looks like a decimal number, so a standard loader turns it into the float 2301.1, dropping the trailing zeros and naming a different paper; the original text cannot be recovered afterwards. Our loader is `yaml.SafeLoader` with its implicit float rule removed, so float-looking scalars stay strings. Integers such as `top_n: 15` still parse as ints and no setting is a float. Rejected: telling users to quote every id (easy to forget, and the failure is silent).
- **`vault.sqlite`** holds everything in one file; plain files would need a hand-rolled index.

## How to run / check it

```
uv run rpipe init                 # guided wizard in a terminal
uv run rpipe init --vault /tmp/v  # custom location
uv run pytest tests/test_vault.py tests/test_init_wizard.py -q
```

## Where the code is

`src/research_pipeline/vault.py` (layout, `resolve_vault`, loaders, `ConfigError`), `src/research_pipeline/cli.py` (`init`, `_wizard*`, and `_add_seed` shared with `rpipe profile add-seed`), `src/research_pipeline/defaults/` (templates), `tests/test_vault.py`, `tests/test_init_wizard.py`.
