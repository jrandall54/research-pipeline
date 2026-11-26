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
  .run.lock        held by the pipeline command now working (its name is written inside) so two jobs cannot overlap; safe to ignore, never edit
  digests/         one markdown digest per day  (Phase 1)
  cache/           small caches; seeds.json holds the text of your seed papers (Phase 1)
  raw/             downloaded PDFs              (reserved, unused so far)
  markdown/        PDFs converted to markdown   (reserved, unused so far)
  summaries/       generated summaries          (reserved; summaries live in vault.sqlite)
  my_work/         your own notes and writing (the shelf); .md/.txt files are read and indexed, see shelf.md
    .removed/      files taken off the shelf are moved here, never deleted
  gold/            gold.json: papers you know well plus the facts a summary must get right (see evaluation.md)
  eval/            one .json and .md per `rpipe eval run`, created on first use (see evaluation.md)
```

The pipeline currently writes `vault.sqlite` (which also holds full texts and summaries), `digests/`, `cache/`, `gold/` (created by `init`, filled by `rpipe gold`) and `eval/` (the only one created later, by `rpipe eval run`), plus the two YAML files. `my_work/` is read by the shelf (see [shelf.md](shelf.md)) and `rpipe shelf add` writes notes into it. `raw/`, `markdown/` and `summaries/` are created now but nothing writes to them yet; they are reserved so the layout never changes later.

### `config.yaml` fields

| Field | Default | Meaning |
| --- | --- | --- |
| `vault_path` | the vault's path (set by `init`) | Informational only: the vault actually used is chosen by `--vault`, then `$RESEARCH_VAULT`, then `~/research-vault`, and the folder that was opened is used instead of the value in the file (so a moved vault still works). Required. Set by `rpipe init`. |
| `arxiv_categories` | `cs.LG, cs.CL, cs.CR, cs.OS, cs.SE` | arXiv categories collected each night, or `["all"]` for every computer-science category (`cs.*`; much more to fetch and rank, see "Choosing categories" in `profile.md`). Change with `rpipe profile set-categories`. |
| `arxiv_max_results` | `1000` | Safety cap on arXiv papers fetched per run (50 to 5000). Change with `rpipe sources set-max-results N`. Paging stops earlier at the first page with an already-stored paper; hitting the cap first adds a warning to the health line (expected on the first run). See `sources.md`. |
| `embedding_model` | `BAAI/bge-small-en-v1.5` | Model that turns text into vectors for similarity (see `glossary.md`). Change with `rpipe model set-embedding NAME`; the next run downloads it and embeds your shelf, seeds and still-unranked papers with it; past votes and saves stop boosting rankings until you switch back (see `triage.md`). |
| `rss_feeds` | not in the template | Reserved: returns with the RSS source (with its own command). An older file that still has `rss_feeds: []` loads fine; the value is ignored. |
| `llm_model` | `auto` | Ollama model that writes summaries; `auto` picks the largest installed model that fits fully in GPU memory. Pin with `rpipe model set NAME`, go back with `rpipe model set auto`. See `llm.md`. |
| `llm_effort` | `low` | Claude Code only: how hard it thinks per call (`low`, `medium` or `high`); `low` is about ten times faster for the critique. Change with `rpipe model set-effort LEVEL`. A missing key means `low`. See `backends.md`. |
| `llm_url` | `http://localhost:11434` | Where the Ollama server listens. Change with `rpipe model set-url URL`. |
| `llm_num_ctx` | `auto` | Context window in tokens: how much text the model reads at once. `auto` finds the largest that fits; a pinned number must be at least 8192 (smaller leaves no room for the paper). Change with `rpipe model set-context N` or `auto`. See `llm.md`. Vaults created before `auto` existed keep their explicit values (`qwen3:14b`, `32768`) unchanged. |
| `llm_backend` | `ollama` | Which backend writes summaries: `ollama`, `claude-code` or an installed plug-in (letters, digits, `_`, `-`). Old vaults without the key use Ollama. See `backends.md`. |
| `llm_cli_path` | empty | Full path of the subscription CLI (such as `claude`), so the nightly timer finds it. Recorded by `rpipe model setup`. |
| `sources` | `arxiv, hf_daily` | Paper sources collected from, in order; at least one, no repeats. Change with `rpipe sources list`, `enable NAME`, `disable NAME`. See `sources.md`, `plugins.md`. |
| `night_deadline` | `"07:00"` | Morning time (24-hour, local) by which reruns of cut papers must finish, or `off` for no reruns. After it the nightly run also starts no new request from the request list. Normal papers always run. Change with `rpipe schedule set-deadline HH:MM` or `off`. The command writes it quoted; a hand-written unquoted `23:00` or `7:00` also works. A missing key means `07:00`. See "The morning deadline" in `scheduling.md`. |
| `share_enabled` | `false` | Whether this vault publishes its shared feed so other people can follow it. Off until you turn it on. Set by `rpipe share setup` (turns it on) and `rpipe share stop` (turns it off); the feed itself is described in the sharing guide once it exists. |
| `share_display_name` | empty | The name readers see for your feed: 1 to 60 characters on one line. Set by `rpipe share setup` and `rpipe share set-name`. |
| `share_handle` | empty | Your GitHub login (letters, digits and hyphens, up to 39 characters), the handle others use to follow you. Set by `rpipe share setup`. |

The three `share_*` keys are sharing settings (Phase 10). Nothing is shared until `share_enabled` is `true`, and you normally set them with commands rather than by hand: `rpipe share setup` asks for the display name and handle, `rpipe share set-name` changes the name, and `rpipe share stop` turns sharing off and keeps the name and handle. Each value is checked before the file is written, so a bad name or handle leaves the file unchanged.

When either is `auto`, the choice is remembered in `cache/llm_fit.json` (the chosen model and context, keyed by the Ollama address and every installed model's name and digest, the Ollama version, the memory-related server settings and the GPUs). It is safe to delete; the next run probes again. `rpipe model fit` rewrites it.

Both files reject unknown keys: a typo such as `keyword:` or `top-n:` stops the command with an error naming the file and the key, instead of being silently ignored.

### `profile.yaml` fields

| Field | Default | Meaning |
| --- | --- | --- |
| `topics` | 3 generic examples | Plain-language phrases for what you care about. Required. |
| `seeds` | `[]` | arXiv ids of papers you like (add with `rpipe profile add-seed`). May be empty (ranking then relies on topics). Required. |
| `followed_authors` | `[]` | Authors whose papers get a boost. |
| `keywords` | `[]` | Words in a title/abstract that give a small boost. |
| `top_n` | 15 | Papers kept per digest (at least 1). |
| `feedback_halflife_days` | 60 | Days after which a vote or save counts half as much in the implicit profile (1 to 3650). Change with `rpipe profile set-feedback-halflife DAYS`. See `triage.md`. |

Normal use needs no editing: `rpipe profile show` prints these settings and the `rpipe profile ...` commands change them, validating every change (see [profile.md](profile.md), which also explains how your comments are kept). The files stay plain YAML, so power users may edit them by hand; a mistake is reported with the file name and field the next time any command reads it.

Seed ids may be written with or without quotes (`2301.10000` or `"2301.10000"`); both load as the text `"2301.10000"`. See the YAML note under "Why it's built this way".

### What `init` does

`rpipe init` builds the vault and, in a terminal, walks you through setting it up so you never edit a file. It has two parts.

**Part 1, always:** resolve the vault path (`--vault`, else env `RESEARCH_VAULT`, else `~/research-vault`; `~` is expanded and relative paths made absolute, so `config.yaml` records the absolute path); create the folders above (existing ones are left alone); write `profile.yaml` and `config.yaml` from the packaged templates **only if they do not exist yet**; open `vault.sqlite` (creating missing tables); print what was created or kept.

**Part 2, only in a terminal (the wizard):**

-1. **Summary backend.** `rpipe model setup` runs first (see `setup.md`): the walkthrough checks your computer, recommends Ollama, Claude Code or abstracts only, saves your pick and tests it. If nothing is ready the wizard carries on without it.
0. **Describe your research (optional).** Skipped when the backend is `none`. `Describe your research in a few sentences (Enter to skip)`. Enter skips it. Otherwise the model you chose in the Summaries step (Ollama or Claude) proposes topics and seed papers and you approve each one (see "Guided suggestions" in `profile.md`). If the model is unavailable it says `Summary model unavailable; add topics by hand with `rpipe profile add-topic`` and the steps below run as normal. The steps below always run afterwards, so you can add more by hand. Note: with `llm_model: auto` (the default) a non-empty description runs the full model-fit probe first, which can take several minutes; press Enter to skip, or run `rpipe model fit` beforehand.
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
Describe your research in a few sentences (Enter to skip): > I work on security of LLM agents, mostly prompt injection.
Proposed topics:
  1. prompt injection attacks on LLM agents
  2. agent sandboxing
Add topic 'prompt injection attacks on LLM agents'? [Y/n]: > y
Added topic: prompt injection attacks on LLM agents
Add topic 'agent sandboxing'? [Y/n]: > n
Found 1 of 1 proposed papers on arXiv (the rest were dropped).
Add seed Universal and Transferable Adversarial Attacks on Aligned Language Models (Andy Zou, 2023)? [Y/n]: > y
Added seed 2307.15043: Universal and Transferable Adversarial Attacks on Aligned Language Models

Step 3 of 5: Topics
Topics are short phrases for what you want to read about. Every new paper is compared
with each topic, and the closest matches rank highest. Specific phrases work better
than broad fields: "prompt injection attacks on LLM agents" beats "security".
5 to 15 topics is plenty; near-duplicates add nothing, so use one per idea.
Topic (blank to finish): > 

Step 4 of 5: arXiv categories
arXiv (arxiv.org) is the free site where most computer-science and AI research appears
first. Every paper is filed under subject categories, and rpipe only collects papers
from the categories you pick here; your topics then rank what was collected. Pick the
areas your topics fall in. More categories means more papers to sort (slower, a bit
noisier); too few means good papers are never seen.

  [ ]  1. cs.AI   Artificial intelligence (general)
  [x]  2. cs.LG   Machine learning
  [x]  3. cs.CL   Language and LLMs
  ...
  [ ] 17. stat.ML Statistical machine learning

Type the numbers you want, separated by commas (e.g. 1,2,5), or arXiv codes, or all
for every computer-science category. Press Enter to keep the ticked ones.
Change it any time with rpipe profile set-categories. Full list: https://arxiv.org/category_taxonomy
Categories [cs.LG, cs.CL, cs.CR, cs.OS, cs.SE]: > 

Step 5 of 5: Seed papers
A seed paper is an example of exactly the kind of paper you want more of. You don't
need to have read it; it just has to be on target. New papers are compared with each
seed's title and abstract, which says far more than a short topic, so seeds are the
strongest way to sharpen your feed. Paste an arXiv link, its id (e.g. 2307.15043) or
its title; I look it up and you confirm. 3 to 10 is a good start.
Paper link, arXiv id or title (blank to finish): > https://arxiv.org/abs/2307.15043
Universal and Transferable Adversarial Attacks on Aligned Language Models (Andy Zou, 2023)
Add this paper? [Y/n]: > y
Added seed 2307.15043: Universal and Transferable Adversarial Attacks on Aligned Language Models
Paper link, arXiv id or title (blank to finish): > 
Tip: add more any time with rpipe profile add-seed. "More like this" in the app also nudges ranking, but fades over weeks; seeds don't.

Topics:
  1. prompt injection attacks on LLM agents
Seeds:
  1. 2307.15043  Universal and Transferable Adversarial Attacks on Aligned Language Models
...

All set. Next:
  rpipe run               collect today's papers, rank them and summarize the top ones
                          (the first run can take an hour or more with a local model)
  rpipe schedule install  do that every night by itself
  rpipe app install       read your feed on your phone and desktop
  rpipe share setup       optional: publish your feed so others can follow it
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

`src/research_pipeline/vault.py` (layout, `resolve_vault`, loaders, `ConfigError`), `src/research_pipeline/models.py` (the `Config` fields and the share name and handle checks), `src/research_pipeline/profile_edit.py` (`set_share`), `src/research_pipeline/gold.py` (the `gold/gold.json` file), `src/research_pipeline/cli.py` (`init`, `_wizard*`, and `_add_seed` shared with `rpipe profile add-seed`), `src/research_pipeline/defaults/` (templates), `tests/test_vault.py`, `tests/test_init_wizard.py`.
