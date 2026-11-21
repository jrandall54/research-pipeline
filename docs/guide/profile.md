# Profile editing

## What it does

The **profile** (`profile.yaml` in your vault) says what you care about: topics, seed papers, followed authors, keywords and how many papers to keep per digest. `top_n` is in `profile.yaml`; the arXiv categories are in `config.yaml`, which the same library edits. `src/research_pipeline/profile_edit.py` is the small library that changes these files safely. The `rpipe profile ...` commands (added in later steps of Phase 1.5) are thin wrappers around it, so nobody has to open a YAML file in an editor.

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

## Why it's built this way

* **Commands rather than editing.** The pipeline is for people with different interests (the AI club), most of whom should not need to know YAML. A typo in a hand-edited file stops the nightly run; a command refuses the typo up front. The files stay plain YAML so power users may still edit them.
* **Round-trip YAML instead of `pyyaml`.** Rejected: loading with `pyyaml` and dumping back, which deletes every comment. Rejected: editing the text with regular expressions, which breaks on any layout we did not predict.
* **Validate with the real models.** One definition of "valid" (`Profile`, `Config`) for the pipeline and the editor, so the editor can never write a file the pipeline then refuses.
* **Refuse an already-invalid file.** Editing on top of a broken file could hide the problem or make it worse; the user gets the original error instead.
* **Atomic write instead of overwrite in place.** A crash during a plain write can leave a truncated file, which would break every later run.

## How to run / check it

```
uv run pytest tests/test_profile_edit.py -q
```

The tests build a throwaway vault with `init_vault`, make edits and assert on the loaded result, including that comments survive, that an invalid file is left byte-for-byte unchanged, and that a failing `os.replace` leaves no temporary file behind.

Fresh vaults now start with `seeds: []` (the old placeholder ids are gone); `rpipe profile add-seed` is the way to add real ones.

## Where the code is

`src/research_pipeline/profile_edit.py` (editing functions and normalisation), `src/research_pipeline/defaults/profile.yaml` (template), `src/research_pipeline/vault.py` (`load_profile`, `load_config`, `ConfigError`), `src/research_pipeline/models.py` (`Profile`, `Config`). Tests: `tests/test_profile_edit.py`.
