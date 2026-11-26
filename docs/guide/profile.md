# Profile editing

## What it does

The **profile** (`profile.yaml` in your vault) says what you care about: topics, seed papers, followed authors, keywords and how many papers to keep per digest. `top_n` is in `profile.yaml`; the arXiv categories are in `config.yaml`, which the same library edits. `src/research_pipeline/profile_edit.py` is the small library that changes these files safely. The `rpipe profile ...` commands (documented in the command reference below) are thin wrappers around it, so nobody has to open a YAML file in an editor.

Each function takes the vault folder and changes one thing: `add_topic`, `remove_topic`, `add_author`, `remove_author`, `add_keyword`, `remove_keyword`, `add_seed_id`, `remove_seed`, `set_top_n`, `set_feedback_halflife`, `set_categories`, `set_arxiv_max_results`, `set_embedding_model`. The `add_*` and `remove_*` ones return `True` if the file changed and `False` if it was already in that state (adding something twice, removing something that is not there), so a command can say "already there" instead of failing.

## How it works

Every edit is the same five steps:

1. **Check the file as it is.** The normal loaders (`load_profile`, `load_config`) read it. If the file is somehow invalid (for example damaged by another program), the edit stops here with an error naming the file.
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

Normal YAML libraries (including the `pyyaml` the pipeline reads with) throw comments away when they load a file, so loading and saving would delete every explanation in your template. **Round-trip YAML** (the `ruamel.yaml` library) keeps comments, ordering and layout in the loaded structure and writes them back. The template's explanatory comments, and any note you added yourself, survive every command. Layout is kept too: the writer is set to the template's style (`  - item` indented two spaces under its key, `key: [a, b]` flow lists left as flow lists), so a profile written in that style is not re-indented when a command edits one list. Cosmetic changes remain only for hand-written files in a different style: a block list written as `- x` flush with its key is re-indented to `  - x` (all lists in the file, not just the edited one) and inline `# comments` after items may shift to line up.

### Validation and atomic writes protect the file

* **Validation first.** An invalid result is never written. `top_n: 0` or a category such as `CS-cr` is refused with an error naming `profile.yaml` or `config.yaml`.
* **Atomic write.** The new text goes to a temporary file in the *same folder*, is flushed to disk, and then `os.replace` swaps it over the real file in one step. Either you have the complete old file or the complete new one, never half of each, even if the machine loses power mid-write. If anything fails, the temporary file is deleted and the original is untouched.
* **Ids stay text.** In YAML, `2301.10000` looks like a number and would become `2301.1`, silently changing the paper. Seeds are written in double quotes, and the reader leaves unquoted float-looking values as text too.

### Normalisation rules

| Input | Stored as | Rule |
| --- | --- | --- |
| `"  LLM   Security "` | `LLM Security` | Strip the ends and collapse runs of whitespace to one space. |
| a topic containing an invisible right-to-left override character, or text with a terminal escape code | `safetopic`, escape replaced by a space | Control characters (C0, DEL and C1, U+0080-U+009F) become a space; invisible bidi controls (U+202A-202E, U+2066-2069, U+200E/200F, U+061C, which can make text display in a different order than it is stored) are dropped. The zero-width joiner and non-joiner (U+200D, U+200C) are deliberately **kept**: Persian, Indic scripts and emoji sequences need them to be spelled correctly. Ordinary letters of every script (Arabic, Hebrew, Chinese, accented Latin) are kept as typed. The same cleaning runs on topics, authors, keywords and on the topics and seed-search queries the onboarding model proposes, so stored data is consistent. |
| add `llm security` when `LLM Security` exists | not added (`False`) | Duplicates are compared case-insensitively (`casefold`) after normalising. The first spelling you added is kept. |
| `2307.15043v2` | `2307.15043` | The `vN` version suffix is removed; the paper is the same across versions. |
| `arXiv:2307.15043`, `https://arxiv.org/abs/2307.15043v2` | `2307.15043` | Prefixes and abs/pdf links are accepted and reduced to the bare id. |
| `cs.CR/0701001v1` | `cs.CR/0701001` | Old-style ids work too. |
| `hello`, `123.45` | `ValueError` | Anything that is not an arXiv id is refused. |

`remove_*` accepts either the exact value (compared the same way) or the 1-based number that `rpipe profile show` prints. An exact match wins over a number. A number that is out of range raises `ValueError`; a value that is not present returns `False`.

Categories must match `^[a-z-]+(\.[A-Za-z-]+)?$` (lowercase archive, optional subcategory in any case, with hyphens): `cs.CR`, `stat.ML`, `math.OC`, `hep-th`, `physics.comp-ph` and `cond-mat.stat-mech` are accepted; `CS.CR`, `CS-cr`, `cs.`, names with spaces or underscores, and the empty string are not, and the list cannot be empty. The one extra value is `all` (any capitalisation is stored as lowercase `all`): it must be given alone, and `set-categories all cs.CR` is refused. The same rule is enforced by the `Config` model, so a hand-edited `config.yaml` is checked too. An invalid category error points at the taxonomy URL below.

### Choosing categories

**What it is.** An arXiv category is the subject tag every paper carries. Only papers in your categories are collected, so this is the widest filter in the pipeline; topics and seeds then rank what was collected.

| Category | Subject |
| --- | --- |
| `cs.CR` | security and cryptography |
| `cs.LG` | machine learning |
| `cs.AI` | artificial intelligence |
| `cs.CL` | language and LLMs |
| `cs.OS` | operating systems |
| `cs.SE` | software engineering |
| `cs.DC` | distributed computing |
| `cs.NI` | networking |
| `stat.ML` | statistical machine learning |

The full list is at https://arxiv.org/category_taxonomy. The wizard prints this explanation before asking, and `rpipe profile set-categories --help` shows it too.

**Typos are caught.** Besides the format rule, every code is checked against a packaged copy of arXiv's own list (`src/research_pipeline/defaults/arxiv_categories.txt`, about 157 codes, including ones without a dot such as `hep-th` and `astro-ph`). `rpipe profile set-categories cs.ML` (and the wizard) is refused with `cs.ML is not an arXiv category (did you mean cs.LG?)`: the code before "did you mean" is what you typed, the code after is the closest real one, found by plain string similarity (`difflib`, cutoff 0.6) plus a tiny table of well-known mix-ups such as `cs.ML` to `cs.LG` and `cs.NLP` to `cs.CL`. If nothing is close, you only get "is not an arXiv category". Nothing is saved when any code is refused. `all` is always accepted.

**Already-stored bad codes still load.** The `Config` model checks the format only, so a `config.yaml` that already holds an unknown code keeps working (arXiv simply returns no papers for it). It is flagged instead: `rpipe profile show` prints `unknown categories: cs.ML`, `rpipe run` warns on stderr, and the digest's health line gains `unknown categories: cs.ML`. The line appears only when there are some.

**Refreshing the list.** The list is a snapshot copied by hand from the taxonomy page on 2026-11-22 (the date and URL are in the file's header comment). When arXiv adds a category, add its code to that file, one per line; until then the new code is refused as unknown.

**Trade-off.** Narrow means fewer papers to fetch, embed and rank each night, and a more focused digest, but a paper filed under a category you left out never appears. Broad means more coverage and more work every night.

**`all`.** Not sure yet? `rpipe profile set-categories all` (or typing `all` in the wizard) stores `arxiv_categories: ["all"]`, which means **every computer-science category (`cs.*`)**. The nightly query becomes `cat:cs.*`. Physics, maths, statistics and other fields are **not** included: to add, for example, `stat.ML`, list the categories explicitly instead (`rpipe profile set-categories cs.LG cs.CR stat.ML`); `all` cannot be combined with them. The cost is real: cs.* is roughly a few hundred new papers a day, so fetching takes more 3-second-spaced requests and more papers get embedded and ranked than with a handful of categories. `arxiv_max_results` still bounds it (paging stops at the cap with the usual "reached max_results" warning, or at the first page containing an already-stored paper). **Planned, not built:** a later phase will use your ratings to suggest dropping categories that never produce papers you like, so `all` is meant as a starting point. `rpipe profile show` prints it as `all computer science (cs.*)`.

### Adding a seed: link, id or title

A **seed** is a paper you already like; the ranker looks for new papers close to it. You should not have to dig out an id, so `rpipe profile add-seed` accepts whatever you have, and `seed_input.py` works out what it is.

| You type | Treated as | What happens |
| --- | --- | --- |
| `https://arxiv.org/abs/2307.15043` | id `2307.15043` | Looked up by id. |
| `arxiv.org/pdf/2307.15043v2.pdf` | id `2307.15043` | Scheme optional; `vN` and `.pdf` removed. |
| `http://arxiv.org/html/2307.15043v1` | id `2307.15043` | `abs`, `pdf` and `html` links all work. |
| `https://export.arxiv.org/abs/2307.15043/?context=cs#top` | id `2307.15043` | Any `*.arxiv.org` host; a trailing `/`, `?query` and `#fragment` are ignored. |
| `https://arxiv.org/list/cs.CR/recent`, `https://example.com/x` | error | Anything that looks like a link but holds no arXiv id is refused with `couldn't find an arXiv id in that link` instead of being title-searched as a string. |
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
| `rpipe profile suggest [DESCRIPTION] [--yes]` | Describe your research in a few sentences; the local model proposes topics and seed papers and you approve each (see "Guided suggestions"). |
| `rpipe profile show` | Print everything: numbered topics, numbered seeds with titles, authors, keywords, top N, categories. |
| `rpipe profile add-topic TEXT` / `remove-topic TEXT_OR_NUMBER` | Change topics. |
| `rpipe profile add-author NAME` / `rpipe profile remove-author NAME_OR_NUMBER` | Change followed authors. |
| `rpipe profile add-keyword WORD` / `rpipe profile remove-keyword WORD_OR_NUMBER` | Change keywords. |
| `rpipe profile add-seed INPUT [--yes]` | Add a seed from an arXiv link, id or title (see "Adding a seed"). `--yes` skips the question and takes the only (or first) match. |
| `rpipe profile remove-seed ID_OR_NUMBER` | Remove a seed by id or by its number in `show`. |
| `rpipe profile set-top-n N` | Papers kept per digest (at least 1). |
| `rpipe profile set-feedback-halflife DAYS` | Days until a vote or save counts half as much in the implicit profile (1 to 3650, default 60; see `triage.md`). Stored as `feedback_halflife_days`. |
| `rpipe profile set-categories CAT [CAT ...]` | Replace the arXiv categories (`cs.CR stat.ML hep-th`), or give the single word `all` for every computer-science category (`cs.*`). `--help` explains what a category is. |

Sample session (vault freshly made by `rpipe init`):

```
$ rpipe profile add-topic "prompt injection"
✓ Added topic: prompt injection
$ rpipe profile add-topic "Prompt  Injection"
"Prompt Injection" is already in your profile (topics).
$ rpipe profile add-seed "universal transferable adversarial attacks"
  1. Universal and Transferable Adversarial Attacks on Aligned Language Models (Andy Zou, 2023)
  2. ...
› Pick 1-5 or 0 to cancel: 1
✓ Added seed 2307.15043: Universal and Transferable Adversarial Attacks on Aligned Language Models
$ rpipe profile add-seed 2307.15043
2307.15043 is already in your profile.
$ rpipe profile set-top-n 10
✓ top_n is now 10.
$ rpipe profile show

── Topics ──────────────────────────────────────────────────────────────────────
  1. security of LLM agents and prompt injection
  2. prompt injection

── Seeds ───────────────────────────────────────────────────────────────────────
  1. 2307.15043  Universal and Transferable Adversarial Attacks on Aligned Language Models

── Followed authors ────────────────────────────────────────────────────────────
  (none)

── Keywords ────────────────────────────────────────────────────────────────────
  (none)

── Settings ────────────────────────────────────────────────────────────────────
  Top N               10
  Feedback half-life  60 days
  arXiv categories    cs.LG, cs.CL, cs.CR, cs.OS, cs.SE
```

(The `add-seed` lines are illustrative: the exact title results depend on arXiv.)

How `add-seed` decides, in order:

1. Empty or blank input is refused at once (exit 1), before any network call; otherwise an empty title search would be sent to arXiv.
2. If the input is an id or link and that id is already a seed, print "already in your profile" (exit 0) without contacting arXiv. Seeds are compared by bare id (`has_seed` in `profile_edit.py`: version, `arXiv:` and URL spellings all reduce to the same id), so a hand-written `"2307.15043v2"` counts as `2307.15043`. A hand-written entry that is not an id at all (for example `bogus-entry`) is compared as plain text, so `remove-seed bogus-entry` and `remove-seed 2307.15043` both work whatever else is in the list.
3. Ask arXiv (`find_seed_candidates`). arXiv unreachable: `Could not reach arXiv: <reason>. Nothing was changed.` (exit 1). No match: `No paper found for "<input>"` (exit 1).
4. One match: show title, first author, year and ask `Add this paper? [Y/n]`. Several: a numbered list and `Pick 1-N or 0 to cancel`. Declining or `0` prints "Cancelled. Nothing was changed." (exit 0); a number outside the range exits 1.
5. Cache the seed's text, then add the id. A title search can return a paper that is already a seed; that also prints "already in your profile".

Other messages: adding something already there prints "already in your profile" (exit 0); removing something absent prints "not in your profile. Nothing was changed." (exit 0); an out-of-range number, `set-top-n 0`, a bad category, empty text, or an invalid `profile.yaml` print the error and exit 1. A missing vault says to run `rpipe init`.

### Guided suggestions: `rpipe profile suggest`

While the model works, stderr shows `suggest: asking the model for topics and papers…` and the usual "still waiting" heartbeat, so a cold model load that takes a minute does not look like a freeze (no question is asked until that line finishes). If the summary backend is not local (Claude Code), the command first prints "Your description will be sent to claude-code (model sonnet) to suggest topics." so you know where the text goes. Under the `none` backend it prints "No summary model is set up..." and exits 1 (the init description step prints it and moves on). See `backends.md`.

**What it does.** You write a few sentences about your research ("I work on security of LLM agents, mostly prompt injection and sandboxing"). The local model (see `llm.md`) proposes 3 to 8 topic phrases and up to 10 titles of well-known papers. You approve each one; nothing is added without a yes (or `--yes`).

**Heads-up:** if `llm_model` is still `auto` (the default) and the model has not been fitted yet, this command first runs the full model-fit probe, which can take several minutes. `rpipe model fit` runs it ahead of time.

**How it works.**

1. A description under 20 characters is refused first (`Describe your research in at least 20 characters.`, exit 1; in the `init` wizard it is skipped with that message), before the model is even looked up. Then `propose` sends one request to the model. The description sits inside `<description>...</description>` and the system prompt (`ONBOARDING_SYSTEM`) says it is data, not instructions; any `<description>` tag typed inside it is defused so it cannot close the block (same idea as the `<paper>` block in `summaries.md`).
2. Topics are compared with your existing ones ignoring case and spacing, so a topic you already have is not offered again. Each topic is also cleaned before you see it: control and bidi-control characters (such as a terminal escape code) are stripped by the same helper as above and it is cut to 120 characters, so a model cannot paint your screen. If every proposal is a duplicate you see `All proposed topics are already in your profile.` Each remaining one is shown and asked as `Add topic '...'? [Y/n]`. If your topics were still the three template examples (a proposal equal to an example is still offered), the first topic you accept replaces the examples, exactly like the `init` wizard.
3. Paper titles go through `resolve_seeds`: each is searched on arXiv and kept only if a result shares at least 60% of the proposed title's *content words* (lower-cased; punctuation and hyphens split words; filler words such as on/the/of/in are ignored, so "On the Security of the Agents in the Wild" cannot match an unrelated title on filler alone). Queries with fewer than 2 content words are dropped, and arXiv's results are tried in order until one passes. A title with no such result is dropped. Language models invent plausible-looking titles, and arXiv search is fuzzy (the top hit for an invented title is often an unrelated paper), so the check is "does a real paper with this title exist", not "did the search return something". While the titles are searched, stderr shows `seeds: looking up proposed papers on arXiv…`, one `  [2/5] <title>` note per lookup and `seeds done (...)` (each lookup waits 3 seconds for arXiv's rate limit, so five take about fifteen seconds). The survivors are shown (`Found 3 of 5 proposed papers on arXiv`) and each asked `Add seed ...? [Y/n]`; an accepted one is saved by the same `_add_seed` helper as `rpipe profile add-seed`.
4. `--yes` accepts every proposal without asking. Without a description argument the command asks for one.

Worked example: you type the sentence above; the model proposes "prompt injection attacks on LLM agents", "agent sandboxing" and the title "Universal and Transferable Adversarial Attacks on Aligned Language Models". Topics are asked first, then seeds, so the three answers go in that order: `y` adds the first topic, `n` skips the second, and `y` accepts the paper (which resolves to arXiv 2307.15043) as a seed. The result is one topic added, one skipped and one seed added.

If the model is unreachable, not installed, or returns unusable output, the command prints `Local model unavailable; add topics by hand with `rpipe profile add-topic`` (plus the reason), changes nothing and exits 0: onboarding is a convenience, never a requirement. If arXiv cannot be reached the accepted topics are kept and no seeds are added.

**Why this way.** Rejected: letting the model write straight into `profile.yaml` (a wrong or invented entry would silently skew every ranking; a human approves each one). Rejected: trusting returned titles (invented ones would reach the seed list). The prompt is separate from the summary prompts and is not covered by `PROMPT_VERSION`, which only stamps stored summaries.

### The guided `rpipe init`

The wizard opens with `Setup has 5 short steps...` and every step starts with a header line `── Step N of 5 · <name> ──…` (Summaries, Describe your research, Topics, arXiv categories, Seed papers) plus a few lines saying what it needs from you and why; the exact wording is in `cli.py` (`SUMMARIES_HELP`, `DESCRIBE_HELP`, `TOPICS_HELP`, `CATEGORY_HELP`, `SEEDS_HELP`). The categories step is a numbered menu of 17 common areas (`categories.COMMON`, with plain descriptions) with the current selection ticked `✓` (unticked rows are dimmed); you type numbers (`3,5`), arXiv codes, or `all`, and `categories.parse_choice` turns that into the list, which replaces the selection. A number outside 1-17 prints `Skipped: There is no option 18...` and keeps what you had. Categories you already have that are not on the menu are listed after it as `Also selected:`. `rpipe profile set-categories` still takes codes and `all` only. The wizard ends with the commands to run next (`rpipe run`, `schedule install`, `app install`, `share setup`).

In a terminal, right after the Summaries step (it comes first because this step calls the model chosen there), `rpipe init` offers `Describe your research in a few sentences (Enter to skip)`: a non-empty answer runs the same flow as `rpipe profile suggest`, and if the model is unavailable it says so and carries on with the manual steps. Then it uses the same edit functions as the commands above to ask for topics, categories and seeds one at a time (see `vault-and-config.md` for a sample session). Its seed step is literally `rpipe profile add-seed`: both call one helper, `_add_seed`, so the look-up, confirm and pick flow, and the error messages, are identical. The cache is written before the profile entry, so if saving fails (for example a full disk) you get one line such as `Could not save the seed: ... Nothing was changed.` and no profile entry without its text; the wizard then skips that seed and continues.

## Why it's built this way

* **Commands rather than editing.** The pipeline is for people with different interests (the AI club), most of whom should not need to know YAML. A typo in a hand-edited file stops the nightly run; a command refuses the typo up front. The files stay plain YAML, which is only the storage format.
* **Round-trip YAML instead of `pyyaml`.** Rejected: loading with `pyyaml` and dumping back, which deletes every comment. Rejected: editing the text with regular expressions, which breaks on any layout we did not predict.
* **Validate with the real models.** One definition of "valid" (`Profile`, `Config`) for the pipeline and the editor, so the editor can never write a file the pipeline then refuses.
* **Refuse an already-invalid file.** Editing on top of a broken file could hide the problem or make it worse; the user gets the original error instead.
* **Accept links, ids and titles.** Rejected: id only, because club members rarely know ids but always have a link or a title. Rejected: auto-picking the top title hit, because a wrong seed silently skews every ranking; the user confirms instead.
* **Thin commands, logic in libraries.** `cli.py` only parses, calls `profile_edit` / `seed_input` and prints, so every rule is tested once, offline, and the commands cannot drift from the library. The arXiv client comes from one factory, `registry.make_arxiv(config)` (the same one the arXiv source uses; `cli.py` imports it by name), which tests replace with a stub (`cli.make_arxiv`) (same idea as `build_pipeline`). A command (or one `init` wizard run) builds exactly one client and passes it to `_add_seed`, closing it at the end: arXiv asks for at least 3 seconds between requests and `ArxivSource` enforces that gap per object, so a fresh client per seed would let successive lookups hit arXiv back to back (see `sources.md`).
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

`src/research_pipeline/onboarding.py` (`propose`, `resolve_seeds`), `src/research_pipeline/prompts.py` (`ONBOARDING_SYSTEM`), `src/research_pipeline/cli.py` (the `profile` sub-app, `_suggest_flow`), `src/research_pipeline/registry.py` (`make_arxiv`), `src/research_pipeline/profile_edit.py` (editing functions and normalisation), `src/research_pipeline/categories.py` (`known_categories`, `unknown`, `closest`) with its data file `src/research_pipeline/defaults/arxiv_categories.txt`, `src/research_pipeline/seed_input.py` (input parsing, candidate lookup), `src/research_pipeline/seeds.py` (`cache_seed`), `src/research_pipeline/sources/arxiv.py` (`search_title`), `src/research_pipeline/defaults/profile.yaml` (template), `src/research_pipeline/vault.py` (`load_profile`, `load_config`, `ConfigError`), `src/research_pipeline/models.py` (`Profile`, `Config`). Tests: `tests/test_categories.py`, `tests/test_profile_edit.py`, `tests/test_seed_input.py`, `tests/test_cli_profile.py`, `tests/test_onboarding.py` and `tests/test_init_wizard.py` (offline, fake model; the title fixture is a real arXiv response trimmed to the fields we use, plus one hand-written entry).
