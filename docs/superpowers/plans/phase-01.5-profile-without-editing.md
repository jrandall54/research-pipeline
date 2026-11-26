# Phase 1.5 — Your Profile Without Editing Files: Implementation Plan

> **Phase 1.5** · Status: merged · Design: the master plan (archived, see ../ROADMAP.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new user, with the owner as the first, goes from nothing to a first digest that matches their interests without opening a YAML file. They do this through `rpipe profile …` commands and a guided `rpipe init`.

**Architecture:** One new module edits `profile.yaml` and `config.yaml` in place. It uses round-trip YAML so comments and hand edits survive, re-validates every change through the existing `load_profile`/`load_config`, and writes atomically. The CLI gets a `profile` sub-app on top of that module. `init` reuses the same functions in an interactive loop. Seeds are entered as an arXiv URL, an id or a title. A resolver turns that input into candidate papers through the existing `ArxivSource` and caches the chosen paper's text, so `run` never re-fetches it.

**Tech Stack:** Python 3.12, typer, pydantic v2, ruamel.yaml (round-trip editing; new dependency), httpx/respx, pytest.

**Spec:** the Claude Docs doc "Research Pipeline — Master Implementation Plan" (https://claude.ai/artifact/Ajw2s52HwWuZfrYZXsZjfa). See the goal "Let anyone … set up and tune their own interests through short rpipe commands …", the principle "Guided, not hand-edited", and the section "Phase 1.5 — Your profile without editing files". Also binding: `CLAUDE.md` ("Users never hand-edit files"; docs in the same commit).

## Global Constraints

- The CLI is `rpipe`. Every message, help text and doc example uses `rpipe`.
- No documented workflow may require hand-editing `profile.yaml` or `config.yaml`. The files stay valid, hand-editable storage.
- Hand edits and comments in `profile.yaml` and `config.yaml` survive every command.
- Each change is validated with `Profile`/`Config` before it is written. An invalid result is never written, and the error names the file.
- Writes are atomic: write a temp file in the same folder, then `os.replace`.
- arXiv: at least 3 s between requests (reuse `ArxivSource`), generic User-Agent, no personal data sent.
- Tests run offline (respx and fixtures). Test output is pristine.
- Every task updates its docs page in the same commit, using the page shape in `CLAUDE.md`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Duplicates in another spelling.** Adding a topic or author that differs only in case or spaces, or a seed given as `2307.15043v2`, `https://arxiv.org/pdf/2307.15043v2.pdf` or `2307.15043`, is one entry, and the command says it already exists.
2. **arXiv unreachable or nothing matched during `add-seed`.** The user gets a clear message, the exit code is non-zero, and `profile.yaml` is unchanged.
3. **A hand-edited or commented profile.** Comments, key order and unknown-but-valid formatting survive a command. A hand edit that broke validation makes the command refuse with the file named, and it never overwrites the file.
4. **Ctrl-C or EOF partway through the `init` wizard.** Every item already confirmed is saved, nothing half-written exists, and re-running `init` continues from what is there.
5. **A title search with many matches.** The user picks one by number or cancels, and nothing is added without confirmation unless `--yes` is given.

## File Structure

```
src/research_pipeline/profile_edit.py   # round-trip load/save, add/remove/set operations, normalisation
src/research_pipeline/seed_input.py     # parse URL/id/title, resolve candidates, cache chosen seed text
src/research_pipeline/sources/arxiv.py  # + search_title()
src/research_pipeline/cli.py            # + `profile` sub-app; guided init
src/research_pipeline/defaults/profile.yaml  # seeds: [] (no placeholders)
tests/test_profile_edit.py, tests/test_seed_input.py, tests/test_cli_profile.py, tests/test_e2e.py (init)
docs/guide/profile.md (new), vault-and-config.md, running.md, README.md, glossary.md
```

---

### Task 1: Profile editing core

**Files:**
- Create: `src/research_pipeline/profile_edit.py`, `tests/test_profile_edit.py`, `docs/guide/profile.md` (first sections)
- Modify: `pyproject.toml` (add `ruamel.yaml`), `src/research_pipeline/defaults/profile.yaml` (seeds become `[]` with a comment pointing to `rpipe profile add-seed`, placeholders removed), `src/research_pipeline/vault.py` only if the template change needs it.

**Interfaces:**
- Consumes: `Profile`, `Config` (`models.py`), `load_profile`, `load_config`, `ConfigError` (`vault.py`).
- Produces (all take the vault path, and each returns `True` if something changed, `False` if it was already in that state):
  - `add_topic(vault: Path, text: str) -> bool`, `remove_topic(vault: Path, text_or_index: str) -> bool`
  - `add_author`, `remove_author`, `add_keyword`, `remove_keyword`: the same shapes.
  - `add_seed_id(vault: Path, arxiv_id: str) -> bool`, `remove_seed(vault: Path, id_or_index: str) -> bool`
  - `set_top_n(vault: Path, n: int) -> None`, `set_categories(vault: Path, cats: list[str]) -> None` (edits `config.yaml`)
  - `normalise_text(s: str) -> str` (strip, collapse whitespace); comparison is on `.casefold()` of that.
  - `normalise_arxiv_id(s: str) -> str` (strips a `vN` suffix; raises `ValueError` for non-ids)
  - `remove_*` accepts the 1-based number shown by `rpipe profile show`, or the exact value.
  - Every function raises `ConfigError` (naming the file) when the result would fail validation, or when the file on disk is already invalid.
- Category format accepted: `^[a-z-]+(\.[A-Za-z-]+)?$` (widened later to allow subcategories like `physics.comp-ph`) (e.g. `cs.CR`, `stat.ML`, `math.OC`, `hep-th`). Empty strings are rejected.

- [ ] **Step 1: Write failing tests**
  - `test_add_topic_appends_and_is_idempotent`: a second add of `"  LLM   Security "` after `"llm security"` returns False, and there is one entry.
  - `test_remove_topic_by_number_and_by_value`
  - `test_add_seed_normalises_version`: adding `2307.15043v2` stores `2307.15043`, and a second add returns False.
  - `test_comments_survive_edit`: the template's comment lines are still present after `add_topic`.
  - `test_invalid_hand_edit_refuses_and_leaves_file`: if `top_n: 0` is on disk, `add_topic` raises a `ConfigError` mentioning `profile.yaml`, and the file bytes are unchanged.
  - `test_set_top_n_rejects_zero`
  - `test_set_categories_validates_format`: `cs.CR` and `stat.ML` are accepted; `CS-cr` and `""` are rejected.
  - `test_write_is_atomic`: monkeypatch `os.replace` to raise, and check that the original file is unchanged and no temp file is left behind.
  - `test_default_template_has_no_placeholder_seeds`: a fresh `init_vault` gives `load_profile(...).seeds == []`.
- [ ] **Step 2:** Run `uv run pytest tests/test_profile_edit.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `profile_edit.py`.** Use `ruamel.yaml` `YAML(typ="rt")` for load and dump. Validate a plain-dict copy with `Profile.model_validate`/`Config.model_validate`, keeping the no-float rule: ids stay strings, so quote seed ids on write. Add `ruamel.yaml` to the dependencies and run `uv lock`.
- [ ] **Step 4:** Run the same test command. Expected: PASS. Then run the full suite.
- [ ] **Step 5: Docs.** In `docs/guide/profile.md`, cover:
  - What the profile is.
  - Why commands rather than editing.
  - How round-trip editing keeps your comments.
  - How validation and atomic writes protect the file.
  - The normalisation rules, with examples.
  Add glossary entries for "round-trip YAML" and "atomic write". Commit with `feat: profile editing core with validated, atomic, comment-preserving writes`.

### Task 2: Seeds from a link, an id or a title

**Files:**
- Create: `src/research_pipeline/seed_input.py`, `tests/test_seed_input.py`, `tests/fixtures/arxiv_title_search.xml` (3 entries)
- Modify: `src/research_pipeline/sources/arxiv.py` (`search_title`), `src/research_pipeline/seeds.py` (expose `cache_seed(cache_path, meta)`), `docs/guide/profile.md` (seed section), `docs/guide/sources.md` (title search)

**Interfaces:**
- Consumes: `ArxivSource.fetch_by_ids`, `normalise_arxiv_id`, `add_seed_id` (Task 1), the seed cache format in `seeds.py` (`{id: "title\nabstract"}`).
- Produces:
  - `parse_seed_input(text: str) -> tuple[Literal["id","title"], str]`. It recognises `arxiv.org/abs|pdf|html/<id>[vN][.pdf]` URLs (with or without the scheme), new-style ids `\d{4}\.\d{4,5}` and old-style ids like `hep-th/9901001`. Anything else is treated as a title.
  - `ArxivSource.search_title(self, query: str, max_results: int = 5) -> list[PaperMeta]`. It uses `search_query=ti:"<query>"` and keeps the shared 3 s spacing.
  - `find_seed_candidates(text: str, arxiv: ArxivSource) -> list[PaperMeta]`. An id gives 0 or 1 results; a title gives up to 5.
  - `cache_seed(cache_path: Path, meta: PaperMeta) -> None`, in `seeds.py`, which merges into the existing JSON cache atomically.

- [ ] **Step 1: Write failing tests**
  - `test_parse_seed_input_variants`: parametrised over `https://arxiv.org/abs/2307.15043`, `arxiv.org/pdf/2307.15043v2.pdf`, `http://arxiv.org/html/2307.15043v1`, `2307.15043`, `hep-th/9901001`, and `"Universal and Transferable Adversarial Attacks"`, which is a title.
  - `test_search_title_builds_ti_query` (respx): asserts the query parameter.
  - `test_find_seed_candidates_by_id_returns_one`
  - `test_find_seed_candidates_unknown_id_returns_empty`
  - `test_find_seed_candidates_title_returns_up_to_five`
  - `test_cache_seed_merges_and_resolve_uses_it`: after `cache_seed`, `resolve_seed_texts` with a fetch that raises still returns that seed.
  - `test_search_title_http_error_raises_source_error`
- [ ] **Step 2:** Run `uv run pytest tests/test_seed_input.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Shape the fixture from one live title query, then keep it offline.
- [ ] **Step 4:** Run the same command. Expected: PASS. Then run the full suite.
- [ ] **Step 5: Docs.** Cover the accepted seed inputs (a table of examples), how title search works and its limits (exact-ish title words work best), and why the chosen seed's text is cached. Commit with `feat: add seeds by arXiv link, id or title search`.

### Task 3: `rpipe profile` commands

**Files:**
- Modify: `src/research_pipeline/cli.py` (the `profile` typer sub-app); `docs/guide/profile.md` (a command reference with sample output); `docs/guide/running.md` ("Edit your profile" becomes "Set up your profile", using commands); `docs/guide/vault-and-config.md` (the file reference keeps its "power users may edit by hand" note); `README.md` reading order.
- Create: `tests/test_cli_profile.py`

**Interfaces:**
- Consumes: everything in Tasks 1 and 2, and `_open` / `resolve_vault` in `cli.py`.
- Produces these commands, each with `--vault PATH`:
  - `rpipe profile show`: numbered topics, numbered seeds with titles from the cache (`(title not cached)` otherwise), authors, keywords, top-N and categories.
  - `rpipe profile add-topic TEXT` and `remove-topic TEXT_OR_NUMBER`. The same pair exists for `-author` and `-keyword`.
  - `rpipe profile add-seed INPUT [--yes]`:
    - one candidate: print its title, first author and year, then ask `Add this paper? [Y/n]`;
    - several candidates: a numbered list, then `Pick 1-N or 0 to cancel`;
    - `--yes` takes the single or first candidate;
    - on success, cache the seed text and print `Added seed <id>: <title>`.
  - `rpipe profile remove-seed ID_OR_NUMBER`
  - `rpipe profile set-top-n N` and `rpipe profile set-categories CAT [CAT…]`
- Messages:
  - An "already in your profile" message gives exit 0.
  - `No paper found for "<input>"` gives exit 1.
  - A `ConfigError` gives exit 1 and prints the message.
  - An arXiv `SourceError` gives exit 1 with `Could not reach arXiv: <msg>. Nothing was changed.`
- [ ] **Step 1: Write failing tests** (CliRunner, with a stub `ArxivSource` injected through a `make_arxiv(config)` factory in `cli.py` that tests monkeypatch):
  - `test_profile_show_lists_everything_numbered`
  - `test_add_topic_then_duplicate_message`
  - `test_add_seed_single_candidate_confirm_yes`
  - `test_add_seed_declined_changes_nothing`
  - `test_add_seed_title_pick_second`
  - `test_add_seed_not_found_exit_1`
  - `test_add_seed_arxiv_down_exit_1_file_unchanged`
  - `test_remove_seed_by_number`
  - `test_set_categories_invalid_exit_1`
  - `test_commands_on_missing_vault_say_run_rpipe_init`
- [ ] **Step 2:** Run `uv run pytest tests/test_cli_profile.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Keep the command bodies thin: parse, call `profile_edit` or `seed_input`, print.
- [ ] **Step 4:** Run the same command. Expected: PASS. Then run the full suite.
- [ ] **Step 5: Docs, then commit** with `feat: rpipe profile commands`.

### Task 4: Guided `rpipe init`

**Files:**
- Modify: `src/research_pipeline/cli.py` (`init`), `tests/test_e2e.py` or a new `tests/test_init_wizard.py`, `docs/guide/vault-and-config.md` ("What init does"), `docs/guide/running.md`, `docs/guide/profile.md`

**Interfaces:**
- Consumes: `init_vault`, `profile_edit.*`, `find_seed_candidates`, `cache_seed`, and the `make_arxiv` factory from Task 3.
- Behaviour, on a TTY only:
  1. Create the vault as now.
  2. Topics: if the profile already has topics, show them and ask `Add more topics? [y/N]`. Otherwise prompt `Topic (blank to finish)` in a loop, saving each through `add_topic`.
  3. Categories: as now, but saved through `set_categories`, and only when `config.yaml` was just created or the user opts in.
  4. Seeds: explain in one line what a seed is, then loop on `Paper link, arXiv id or title (blank to finish)`, using the Task 3 confirm and pick flow. Each confirmed seed is saved immediately. After the loop, if there are fewer than 5 seeds, print `Tip: 10-20 seeds work best; add more later with rpipe profile add-seed`.
  5. Finish with the `rpipe profile show` summary and the next step `rpipe run`.
  Non-TTY behaviour is unchanged (no prompts). Ctrl-C or EOF exits cleanly with `Saved what you entered so far. Re-run rpipe init to continue.` and exit code 130.
- [ ] **Step 1: Write failing tests** (CliRunner with `input=`, `_is_tty` patched to True, stub arXiv):
  - `test_wizard_saves_topics_and_seeds`
  - `test_wizard_rerun_keeps_existing_and_skips_prompts_when_declined`: this also fixes the deferred Phase 1 minor where answers were silently discarded.
  - `test_wizard_eof_midway_keeps_confirmed_items`
  - `test_non_tty_init_unchanged`
  - `test_wizard_arxiv_down_skips_seed_and_continues`
- [ ] **Step 2:** Run `uv run pytest tests/test_init_wizard.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the same command. Expected: PASS. Then run the full suite.
- [ ] **Step 5: Docs.**
  - Rewrite "What init does" as the wizard walkthrough, with a sample session.
  - Run a docs audit: grep `docs/guide` for any instruction to edit `profile.yaml` or `config.yaml` by hand, and rewrite each one to use commands. Only the file-reference section in `vault-and-config.md` stays.
  - Commit with `feat: guided rpipe init`.

### After the tasks (owner, interactive; not an agent task)

- [ ] The owner runs `rpipe init` on their own machine (it creates `~/research-vault`), enters real topics and 10–20 seeds through the wizard, then runs `rpipe run` and judges the first digest. The outcome (how well the top 15 fits) is recorded in the master plan's Phase 1.5 section. Seeds the owner knows well are noted as gold-set candidates for Phase 2.

## Self-Review Notes

- **Spec coverage:** the four unticked Phase 1.5 items map to Tasks 1–3 (the commands), Task 2 (seeds by URL, id or title), Task 4 (guided init) and the owner step (real vault and digest). The `rpipe` name is already done (commit `5609a81`).
- **Deliberately not here:** model-assisted onboarding (Phase 3, which needs the Phase 2 client), a web form (later), and negative topics (Phase 3 votes).
- **Folded-in Phase 1 deferred minors:** the TTY re-run prompts (Task 4) and the error for a number-like seed (Task 1, normalisation).
