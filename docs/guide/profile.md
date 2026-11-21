# Profile editing

## What it does

The **profile** (`profile.yaml` in your vault) says what you care about: topics, seed papers, followed authors, keywords and how many papers to keep per digest. `top_n` is in `profile.yaml`; the arXiv categories are in `config.yaml`, which the same library edits. `src/research_pipeline/profile_edit.py` is the small library that changes these files safely. The `rpipe profile ...` commands (documented in the command reference below) are thin wrappers around it, so nobody has to open a YAML file in an editor.

Each function takes the vault folder and changes one thing: `add_topic`, `remove_topic`, `add_author`, `remove_author`, `add_keyword`, `remove_keyword`, `add_seed_id`, `remove_seed`, `set_top_n`, `set_categories`. The `add_*` and `remove_*` ones return `True` if the file changed and `False` if it was already in that state (adding something twice, removing something that is not there), so a command can say "already there" instead of failing.

## How it works

Every edit is the same five steps:

1. **Check the file as it is.** The normal loaders (`load_profile`, `load_config`) read it. If you hand-edited it into something invalid, the edit stops here with an error naming the file.
2. **Load it again for editing** with a round-trip YAML reader (see below), which remembers comments, blank lines and quoting.
3. **Make the one change** in memory (append a topic, set `top_n`, ...). If nothing needs to change, stop and return `False`.
4. **Validate the result** with the same `Profile` / `Config` models the pipeline uses. If it would be invalid, raise `ConfigError` naming the file; nothing was written.
5. **Write atomically** (see below).

Worked example. Starting from the template, `add_seed_id(vault, "2307.15043v2")`:

```
normalise_arxiv_id("2307.15043v2")  ->  "2307.15043"      (version stripped)
already in seeds?                   ->  no
append "2307.15043" (double-quoted) ->  seeds:\n- "2307.15043"
validate Profile                    ->  ok
write temp file, then rename over profile.yaml
```

A second call with `2307.15043` finds it and returns `False`.

### Round-trip YAML keeps your comments

Normal YAML libraries (including the `pyyaml` the pipeline reads with) throw comments away when they load a file, so loading and saving would delete every explanation in your template. **Round-trip YAML** (the `ruamel.yaml` library) keeps comments, ordering and layout in the loaded structure and writes them back. The template's explanatory comments, and any note you added yourself, survive every command. The one cosmetic change: lists the command touches may switch from `[a, b]` to one item per line.

### Validation and atomic writes protect the file

* **Validation first.** An invalid result is never written. `top_n: 0` or a category such as `CS-cr` is refused with an error naming `profile.yaml` or `config.yaml`.
* **Atomic write.** The new text goes to a temporary file in the *same folder*, is flushed to disk, and then `os.replace` swaps it over the real file in one step. Either you have the complete old file or the complete new one, never half of each, even if the machine loses power mid-write. If anything fails, the temporary file is deleted and the original is untouched.
* **Ids stay text.** In YAML, `2301.10000` looks like a number and would become `2301.1`, silently changing the paper. Seeds are written in double quotes, and the reader leaves unquoted float-looking values as text too.

### Normalisation rules

| Input | Stored as | Rule |
| --- | --- | --- |
| `"  LLM   Security "` | `LLM Security` | Strip the ends and collapse runs of whitespace to one space. |
| add `llm security` when `LLM Security` exists | not added (`False`) | Duplicates are compared case-insensitively (`casefold`) after normalising. The first spelling you added is kept. |
| `2307.15043v2` | `2307.15043` | The `vN` version suffix is removed; the paper is the same across versions. |
| `arXiv:2307.15043`, `https://arxiv.org/abs/2307.15043v2` | `2307.15043` | Prefixes and abs/pdf links are accepted and reduced to the bare id. |
| `cs.CR/0701001v1` | `cs.CR/0701001` | Old-style ids work too. |
| `hello`, `123.45` | `ValueError` | Anything that is not an arXiv id is refused. |

`remove_*` accepts either the exact value (compared the same way) or the 1-based number that `rpipe profile show` prints. An exact match wins over a number. A number that is out of range raises `ValueError`; a value that is not present returns `False`.

Categories must match `^[a-z-]+(\.[A-Z]{2})?$`: `cs.CR`, `stat.ML`, `math.OC` and `hep-th` are accepted; `CS-cr` and the empty string are not, and the list cannot be empty.

### Adding a seed: link, id or title

A **seed** is a paper you already like; the ranker looks for new papers close to it. You should not have to dig out an id, so `rpipe profile add-seed` accepts whatever you have, and `seed_input.py` works out what it is.

| You type | Treated as | What happens |
| --- | --- | --- |
| `https://arxiv.org/abs/2307.15043` | id `2307.15043` | Looked up by id. |
| `arxiv.org/pdf/2307.15043v2.pdf` | id `2307.15043` | Scheme optional; `vN` and `.pdf` removed. |
| `http://arxiv.org/html/2307.15043v1` | id `2307.15043` | `abs`, `pdf` and `html` links all work. |
| `2307.15043` or `arXiv:2307.15043` | id `2307.15043` | Bare new-style id. |
| `hep-th/9901001` | id `hep-th/9901001` | Old-style id. |
| `Universal and Transferable Adversarial Attacks` | title | Searched on arXiv; you pick from up to 5 results. |

Steps: (1) `parse_seed_input` returns `("id", ...)` or `("title", ...)`, reusing `normalise_arxiv_id` so there is one definition of an id. (2) `find_seed_candidates` asks arXiv: an id gives 0 or 1 paper, a title gives up to 5. (3) The user confirms one. (4) `cache_seed` stores its `title\nabstract` in `<vault>/cache/seeds.json` and `add_seed_id` adds the id to `profile.yaml`.

Why the text is cached at once: we already fetched the abstract to show you the choice, so storing it means the next `rpipe run` does not need the network to embed that seed (`resolve_seed_texts` reads this cache before fetching). The cache is written atomically, like the profile, and merging keeps the other seeds already in it.

Limits: title search matches words in the title, so the exact title or its distinctive words work best; vague descriptions ("that jailbreak paper") will not. If nothing matches, use the arXiv link or id.

## The `rpipe profile` commands

Every command takes `--vault PATH` (same resolution as `init`). Messages are plain sentences; a problem exits 1 with one line, never a traceback.

| Command | What it does |
| --- | --- |
| `rpipe profile show` | Print everything: numbered topics, numbered seeds with titles, authors, keywords, top N, categories. |
| `rpipe profile add-topic TEXT` / `remove-topic TEXT_OR_NUMBER` | Change topics. |
| `rpipe profile add-author NAME` / `remove-author ...` | Change followed authors. |
| `rpipe profile add-keyword WORD` / `remove-keyword ...` | Change keywords. |
| `rpipe profile add-seed INPUT [--yes]` | Add a seed from an arXiv link, id or title (see "Adding a seed"). `--yes` skips the question and takes the only (or first) match. |
| `rpipe profile remove-seed ID_OR_NUMBER` | Remove a seed by id or by its number in `show`. |
| `rpipe profile set-top-n N` | Papers kept per digest (at least 1). |
| `rpipe profile set-categories CAT [CAT ...]` | Replace the arXiv categories (`cs.CR stat.ML hep-th`). |

Sample session (vault freshly made by `rpipe init`):

```
$ rpipe profile add-topic "prompt injection"
Added topic: prompt injection
$ rpipe profile add-topic "Prompt  Injection"
"Prompt Injection" is already in your profile (topics).
$ rpipe profile add-seed "universal transferable adversarial attacks"
  1. Universal and Transferable Adversarial Attacks on Aligned Language Models (Andy Zou, 2023)
  2. ...
Pick 1-5 or 0 to cancel: 1
Added seed 2307.15043: Universal and Transferable Adversarial Attacks on Aligned Language Models
$ rpipe profile add-seed 2307.15043
2307.15043 is already in your profile.
$ rpipe profile set-top-n 10
top_n is now 10.
$ rpipe profile show
Topics:
  1. security of LLM agents and prompt injection
  2. prompt injection
Seeds:
  1. 2307.15043  Universal and Transferable Adversarial Attacks on Aligned Language Models
Followed authors:
  (none)
Keywords:
  (none)
Top N: 10
arXiv categories: cs.LG, cs.CL, cs.CR, cs.OS, cs.SE
```

(The `add-seed` lines are illustrative: the exact title results depend on arXiv.)

How `add-seed` decides, in order:

1. Empty or blank input is refused at once (exit 1), before any network call; otherwise an empty title search would be sent to arXiv.
2. If the input is an id or link and that id is already a seed, print "already in your profile" (exit 0) without contacting arXiv.
3. Ask arXiv (`find_seed_candidates`). arXiv unreachable: `Could not reach arXiv: <reason>. Nothing was changed.` (exit 1). No match: `No paper found for "<input>"` (exit 1).
4. One match: show title, first author, year and ask `Add this paper? [Y/n]`. Several: a numbered list and `Pick 1-N or 0 to cancel`. Declining or `0` prints "Cancelled. Nothing was changed." (exit 0); a number outside the range exits 1.
5. Cache the seed's text, then add the id. A title search can return a paper that is already a seed; that also prints "already in your profile".

Other messages: adding something already there prints "already in your profile" (exit 0); removing something absent prints "not in your profile. Nothing was changed." (exit 0); an out-of-range number, `set-top-n 0`, a bad category, empty text, or an invalid `profile.yaml` print the error and exit 1. A missing vault says to run `rpipe init`.

## Why it's built this way

* **Commands rather than editing.** The pipeline is for people with different interests (the AI club), most of whom should not need to know YAML. A typo in a hand-edited file stops the nightly run; a command refuses the typo up front. The files stay plain YAML so power users may still edit them.
* **Round-trip YAML instead of `pyyaml`.** Rejected: loading with `pyyaml` and dumping back, which deletes every comment. Rejected: editing the text with regular expressions, which breaks on any layout we did not predict.
* **Validate with the real models.** One definition of "valid" (`Profile`, `Config`) for the pipeline and the editor, so the editor can never write a file the pipeline then refuses.
* **Refuse an already-invalid file.** Editing on top of a broken file could hide the problem or make it worse; the user gets the original error instead.
* **Accept links, ids and titles.** Rejected: id only, because club members rarely know ids but always have a link or a title. Rejected: auto-picking the top title hit, because a wrong seed silently skews every ranking; the user confirms instead.
* **Thin commands, logic in libraries.** `cli.py` only parses, calls `profile_edit` / `seed_input` and prints, so every rule is tested once, offline, and the commands cannot drift from the library. The arXiv client comes from one factory, `make_arxiv(config)`, which tests replace with a stub (same idea as `build_pipeline`).
* **Ask before adding a seed, but allow `--yes`.** The question guards against the wrong paper; `--yes` keeps scripts and confident users fast. Rejected: prompting for every setting, because simple ones (a topic) have nothing to confirm.
* **Atomic write instead of overwrite in place.** A crash during a plain write can leave a truncated file, which would break every later run.

## How to run / check it

```
uv run pytest tests/test_profile_edit.py tests/test_seed_input.py tests/test_cli_profile.py -q
```

The tests build a throwaway vault with `init_vault`, make edits and assert on the loaded result, including that comments survive, that an invalid file is left byte-for-byte unchanged, and that a failing `os.replace` leaves no temporary file behind.

`tests/test_cli_profile.py` drives the real commands with a typer `CliRunner` and a stub arXiv client: output, exit codes, confirm/decline/pick, arXiv down leaves the file unchanged, empty input never reaches arXiv.

Fresh vaults now start with `seeds: []` (the old placeholder ids are gone); `rpipe profile add-seed` is the way to add real ones.

## Where the code is

`src/research_pipeline/cli.py` (the `profile` sub-app, `make_arxiv`), `src/research_pipeline/profile_edit.py` (editing functions and normalisation), `src/research_pipeline/seed_input.py` (input parsing, candidate lookup), `src/research_pipeline/seeds.py` (`cache_seed`), `src/research_pipeline/sources/arxiv.py` (`search_title`), `src/research_pipeline/defaults/profile.yaml` (template), `src/research_pipeline/vault.py` (`load_profile`, `load_config`, `ConfigError`), `src/research_pipeline/models.py` (`Profile`, `Config`). Tests: `tests/test_profile_edit.py`, `tests/test_seed_input.py`, `tests/test_cli_profile.py` (offline; the title fixture is a real arXiv response trimmed to the fields we use, plus one hand-written entry).
