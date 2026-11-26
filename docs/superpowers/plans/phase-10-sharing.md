# Phase 10 — Sharing: Implementation Plan

> **Phase 10** · Status: built (awaiting merge) · Design: [phase-10-sharing-design.md](../specs/phase-10-sharing-design.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish your feed to GitHub Pages every night, find other people's feeds through a GitHub tag, follow whole feeds (past and future), and read them in the app under Following and Mix.

**Architecture:** A shared, pure `feed_format` module defines the published files. Pipeline modules `share_publish` (build and push, through a fakeable `git`/`gh` runner), `share_fetch` (download followed feeds into three new tables) and `share_directory` (GitHub search for the `rpipe-feed` topic) do all network work. `library_api` gains reads and writes for the new tables (no network). The web app adds the Mine/Following/Mix switch, labelled cards, a "Showing" list and the People page from the Phase 8 mockups. `cli.py` adds `rpipe share …` and two nightly stages after the digest.

**Tech Stack:** Python 3.12, Pydantic 2, httpx, sqlite3, Typer, FastAPI + Jinja2, `git` and `gh` as external programs; tests with pytest + respx, offline.

**Spec:** `docs/superpowers/specs/phase-10-sharing-design.md` (read it first; the "Settled after the builder check" section is binding).

## Global Constraints

- Tests run offline: `uv run pytest -q`; `uv run ruff check .` clean. No real `git`, `gh` or network in tests (fake runner, `respx`).
- Architecture groups: `feed_format` → SHARED; `share_publish`, `share_fetch`, `share_directory`, `share_github` → PIPELINE. Add each to `tests/test_architecture.py` and the group table in `docs/guide/architecture.md` in the task that creates it. The app group (`library_api`, `webapp`) makes no network calls.
- Every waiting step reports through `progress.stage(...)` (stages named `publish`, `fetch`, `directory`, `pages`).
- `SCHEMA_VERSION` 4 → 5, new tables only.
- Publishing is opt-in; nothing is published unless `share_enabled` is true in config.
- Never published: votes, saves, skips, shelf, `my_work/`, notes, runs, config, diagrams; reasons only of kinds `topic`, `upvotes`, `author`, `keyword`.
- Fixed values: repository name `rpipe-feed`; topic `rpipe-feed`; feed address `https://<handle>.github.io/rpipe-feed/feed.json`; feed id `github:<handle>/rpipe-feed` (lower case); `feed.json` last 30 days, ≤300 items, ≤5 MB; archive ≤2 000 items, ≤20 MB, split as `archive/YYYY-MM-1.json`, `-2`, … only when needed (else `archive/YYYY-MM.json`); fetch timeout 20 s; https only; ≤500 archive files; caps: title 500 chars, abstract 10 000, claim text 2 000; `_rpipe.format = 1`; "stopped publishing" after 7 consecutive failed fetches or `active: false`; directory ≤100 hits.
- Copy for the publish warning (exact): "This makes your feed public. Anyone can read it. Copies people have already downloaded can't be pulled back if you stop."
- Every task updates its `docs/guide/` page(s) in the same commit (page shape: What it does → How it works → Why → How to run/check → Where the code is). New terms go in `docs/guide/glossary.md`.
- Existing screens look exactly as today when you follow nobody (the switch is hidden). Mine = today's feed.
- Use `rpipe` in all messages and docs.

## Review Focus

1. **A hostile or broken feed** (script tags in titles, `javascript:` links, a 50 MB file, invalid JSON, a newer `format`) must be stored escaped or skipped, never crash a fetch of the other feeds. → Task 5 tests `test_fetch_rejects_oversize`, `test_fetch_skips_bad_items`, `test_one_bad_feed_does_not_stop_others`; Task 9 test `test_followed_title_is_escaped`.
2. **The nightly push failing** (no credentials at 3 a.m., offline) must leave the digest written, put "sharing: push failed, will retry tomorrow: …" in errors, and push everything next time. → Task 8 test `test_run_push_failure_is_reported_not_fatal`.
3. **Following a handle that has no feed** (404, typo) must say so plainly and store nothing. → Task 5 test `test_follow_unknown_handle_stores_nothing`.
4. **A paper in both your feed and a followed feed** must show once in Mix, labelled "You · Alex M.", and voting on it must hit your own row (no copy). → Task 7 tests `test_mix_dedupes_with_own_paper`, `test_vote_on_followed_paper_you_have_copies_nothing`.
5. **Following yourself** (your own handle) is refused with "That's your own feed." → Task 5 test `test_follow_own_handle_refused`.

---

### Task 1: Schema v5, storage helpers, feed-row takeover

**Files:**
- Modify: `src/research_pipeline/db.py` (SCHEMA, `SCHEMA_VERSION`, `_migrate`, new helpers, `upsert_paper`)
- Test: `tests/test_db_share.py` (new)
- Docs: `docs/guide/data-model.md` (three tables, the takeover rule)

**Interfaces:**
- Produces (all in `db.py`, none commit unless stated; callers own transactions except where "commits"):
  - Tables exactly as in the spec: `follows(feed_id TEXT PK, feed_url, handle, display_name, topics JSON, in_mix INT DEFAULT 1, followed_at, unfollowed_at NULL, last_fetched_at NULL, last_error NULL, failures INT DEFAULT 0, active INT DEFAULT 1, archive_hashes JSON DEFAULT '{}')`; `followed_posts(feed_id, paper_id, digest_date, rank INT, title, abstract, authors JSON, url, published NULL, summary JSON NULL, reasons JSON, provenance JSON NULL, fetched_at, PRIMARY KEY(feed_id, paper_id))` with index on `digest_date`; `directory_entries(feed_id PK, feed_url, handle, display_name, topics JSON, posts_per_day REAL, preview JSON, checked_at)`.
  - `upsert_follow(conn, feed_id, feed_url, handle, display_name, topics) -> None` (insert or re-activate: clears `unfollowed_at`)
  - `get_follow(conn, feed_id) -> sqlite3.Row | None`; `list_follows(conn, include_unfollowed=False) -> list[sqlite3.Row]` (follow order = `followed_at, feed_id`)
  - `record_fetch(conn, feed_id, *, ok: bool, error: str | None, display_name=None, topics=None, active=None, archive_hashes=None) -> None` (ok → failures=0, last_error NULL; not ok → failures+1)
  - `replace_followed_posts(conn, feed_id, posts: list[dict]) -> int` (upsert by (feed_id, paper_id); returns count new)
  - `mark_unfollowed(conn, feed_id, keep_paper_ids: set[str]) -> None` (sets `unfollowed_at`, deletes posts not in keep set)
  - `set_in_mix(conn, feed_id, on: bool) -> None`
  - `replace_directory(conn, entries: list[dict]) -> None`
- `upsert_paper`: when the stored row has `source = 'feed'`, the incoming paper takes it over: `source`, all metadata from the incoming one, `status = 'new'`; returns `"updated"`.

- [ ] **Step 1: Write failing tests** in `tests/test_db_share.py`: `test_v4_database_migrates_to_v5` (open a v4 file, `PRAGMA user_version` becomes 5, three tables exist, old rows intact); `test_upsert_follow_reactivates_unfollowed`; `test_record_fetch_counts_failures` (two failures → `failures == 2`, then ok → 0); `test_replace_followed_posts_counts_new_and_replaces` (same paper twice with a new `digest_date` → one row, latest date); `test_mark_unfollowed_keeps_listed_posts`; `test_collector_takes_over_feed_row` (insert a `source='feed', status='skipped'` paper, `upsert_paper` with an arXiv `PaperMeta` of the same id → `source == 'arxiv'`, `status == 'new'`, votes untouched).
- [ ] **Step 2: Run** `uv run pytest tests/test_db_share.py -q` → FAIL (no tables).
- [ ] **Step 3: Implement** the tables, `SCHEMA_VERSION = 5`, the `_migrate` step comment (`v4 -> v5: follows, followed_posts, directory_entries via CREATE TABLE IF NOT EXISTS`), the helpers and the takeover branch in `upsert_paper`.
- [ ] **Step 4: Run** `uv run pytest -q` → all pass (existing schema-version tests updated to 5).
- [ ] **Step 5: Commit** `feat: schema v5 tables for sharing (Phase 10)` with the data-model.md update.

### Task 2: `feed_format` — published file shapes, reason parsing, building and parsing

**Files:**
- Create: `src/research_pipeline/feed_format.py` (SHARED)
- Modify: `src/research_pipeline/library_api.py` (`plain_reasons` delegates to `feed_format.parse_reason`), `tests/test_architecture.py` (SHARED += `feed_format`)
- Test: `tests/test_feed_format.py`
- Docs: `docs/guide/sharing.md` (new page: "What is published" section with a trimmed example `feed.json`), `docs/guide/architecture.md` table, `docs/guide/glossary.md` (JSON Feed, Atom, GitHub Pages)

**Interfaces:**
- Produces:
  - `FORMAT = 1`, `RECENT_DAYS = 30`, `FEED_MAX_ITEMS = 300`, `FEED_MAX_BYTES = 5_000_000`, `ARCHIVE_MAX_ITEMS = 2000`, `ARCHIVE_MAX_BYTES = 20_000_000`, `PUBLIC_REASON_KINDS = ("topic", "upvotes", "author", "keyword")`.
  - `parse_reason(text: str) -> tuple[str, str, str | None] | None` — (kind, label, paper_id) for `topic|liked|upvotes|author|keyword|shelf`, None otherwise (moved from `library_api`; behaviour identical).
  - `public_reasons(stored: list[str]) -> list[str]` — keeps only stored strings whose kind is in `PUBLIC_REASON_KINDS`, in order.
  - Pydantic models (`extra="ignore"`, string caps from Global Constraints): `PaperBlock(id, title, abstract, authors: list[str], url, published: str | None)`; `PostMeta(paper: PaperBlock, rank: int, summary: PaperSummary | None, reasons: list[str], provenance: dict | None)`; `Item(id, url, title, summary: str, date_published: str, rpipe: PostMeta = Field(alias="_rpipe"))`; `ArchiveRef(path, count: int, sha256)`; `FeedMeta(format: int, display_name, topics: list[str], generated_at, active: bool, archives: list[ArchiveRef] = [])`; `FeedFile(version="https://jsonfeed.org/version/1.1", title, home_page_url | None, feed_url | None, items: list[Item], rpipe: FeedMeta = alias "_rpipe")`.
  - `Post` dataclass input to building: `paper: PaperBlock, digest_date: str, rank: int, summary: PaperSummary | None, reasons: list[str], provenance: dict | None`.
  - `build_files(posts: list[Post], *, display_name: str, topics: list[str], active: bool, now: str, base_url: str | None) -> dict[str, bytes]` — returns `{"feed.json", "feed.xml", "README.md", "archive/…json"…}`; archives grouped by month, split by the caps; `feed.json` holds posts of the last `RECENT_DAYS` before `now` (newest first, ≤300) and lists every archive with count and sha256 of its bytes; output is byte-stable for the same input (sorted keys, `ensure_ascii=False`, fixed separators).
  - `parse_feed(data: bytes) -> tuple[FeedFile, int]` — raises `FeedError(message)` on size > `FEED_MAX_BYTES`, bad JSON, missing `_rpipe`, or `format > FORMAT` ("This feed needs a newer rpipe."); invalid items are dropped and counted (second value).
  - `parse_archive(data: bytes) -> tuple[list[Item], int]` — same rules, `ARCHIVE_MAX_BYTES`.
  - `class FeedError(Exception)`.

- [ ] **Step 1: Write failing tests:** `test_parse_reason_matches_library_api_cases` (the existing `plain_reasons` cases still pass via `library_api`); `test_public_reasons_drop_liked_disliked_shelf` (input `["topic: agents (0.80)", "liked similar: 2611.1", "disliked similar: 2611.2", "near your shelf: thesis.md", "HF upvotes: 5", "weird"]` → `["topic: agents (0.80)", "HF upvotes: 5"]`); `test_build_files_round_trip` (3 posts across two months → `parse_feed` + `parse_archive` give the same items; `feed.json` lists 2 archives); `test_build_files_is_byte_stable`; `test_archive_month_splits_over_cap` (monkeypatch `ARCHIVE_MAX_ITEMS = 2`, 5 posts in one month → `archive/2026-11-1.json`..`-3.json`); `test_feed_json_keeps_last_30_days`; `test_atom_is_valid_xml_and_escapes` (title `<b>&</b>` parses with `xml.etree` and round-trips the text); `test_parse_feed_rejects_newer_format`; `test_parse_feed_drops_bad_items` (one item with a 600-char title → dropped count 1).
- [ ] **Step 2: Run** `uv run pytest tests/test_feed_format.py tests/test_library_api.py -q` → FAIL.
- [ ] **Step 3: Implement** `feed_format.py`; Atom via `xml.etree.ElementTree` (id = `urn:rpipe:<paper id>`, link = paper url, summary = one-line or abstract excerpt); `README.md` one paragraph naming rpipe and the feed address.
- [ ] **Step 4: Run** `uv run pytest -q` → pass (architecture tests include the new module).
- [ ] **Step 5: Commit** `feat: feed_format, the published feed files (Phase 10)` with docs.

### Task 3: Share settings in config, with setters

**Files:**
- Modify: `src/research_pipeline/models.py` (`Config`), `src/research_pipeline/profile_edit.py`
- Test: `tests/test_profile_edit.py` (add cases)
- Docs: `docs/guide/vault-and-config.md` (the three keys and the commands that set them)

**Interfaces:**
- Produces: `Config.share_enabled: bool = False`, `Config.share_display_name: str | None = None` (1–60 chars, stripped, no newlines), `Config.share_handle: str | None = None` (GitHub login, `^[A-Za-z0-9-]{1,39}$`); `profile_edit.set_share(vault, *, enabled: bool | None = None, display_name: str | None = None, handle: str | None = None) -> None` (validates, writes only given keys).

- [ ] **Step 1: Write failing tests:** `test_set_share_writes_keys`; `test_set_share_rejects_bad_name` (`""`, 61 chars, `"a\nb"` → `ValueError` with a plain message); `test_set_share_rejects_bad_handle`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: share settings in config (Phase 10)`.

### Task 4: Building and pushing the site; GitHub setup operations

**Files:**
- Create: `src/research_pipeline/share_publish.py`, `src/research_pipeline/share_github.py` (both PIPELINE)
- Modify: `tests/test_architecture.py`
- Test: `tests/test_share_publish.py`, `tests/test_share_github.py`; fake runner in `tests/fakes.py`
- Docs: `docs/guide/sharing.md` ("Publishing" section: setup steps, nightly push, credentials, stopping)

**Interfaces:**
- Consumes: `feed_format.build_files`, `feed_format.Post`, `feed_format.public_reasons`, `db.get_summary`, `db.get_provenance`.
- Produces:
  - `share_github.Runner` protocol: `run(args: list[str], cwd: Path | None = None, input: str | None = None) -> subprocess.CompletedProcess[str]`; `SubprocessRunner` (real; `check=False`, `capture_output=True`, `text=True`, env `GIT_TERMINAL_PROMPT=0`).
  - `share_github.GithubError(Exception)` with a plain message and a fix.
  - `share_github.check_tools(runner) -> None` (raises with install hints for missing `git`/`gh`); `login(runner) -> str` (returns handle via `gh api user --jq .login`; if not logged in raises `GithubError("Sign in first: gh auth login")` — the CLI runs `gh auth login` interactively before calling); `repo_status(runner, handle) -> Literal["missing", "rpipe", "other"]` (`gh api repos/<h>/rpipe-feed`, then contents `feed.json` with `_rpipe`); `create_repo(runner, handle, clone_dir)`; `set_topic(runner, handle, on: bool)` (`gh api -X PUT repos/<h>/rpipe-feed/topics` with names `["rpipe-feed"]` or `[]`); `enable_pages(runner, handle)` (branch `main`, path `/`; already-enabled is fine); `setup_git_credentials(runner)` (`gh auth setup-git`); `delete_repo(runner, handle)` (refreshes `delete_repo` scope first).
  - `share_publish.feed_url(handle: str) -> str` = `https://<handle lower>.github.io/rpipe-feed/feed.json`.
  - `share_publish.collect_posts(conn) -> list[Post]` (every paper with a `digest_date`; rank by `triage_score DESC, id ASC` within the day; reasons via `public_reasons`; diagrams never included).
  - `share_publish.write_site(clone_dir: Path, files: dict[str, bytes]) -> bool` (writes files, deletes `archive/*.json` not in `files`; True if anything changed).
  - `share_publish.push(runner, clone_dir, message: str) -> bool` (`git add -A`, commit if staged changes, `git push`; returns whether a push happened; raises `GithubError` with stderr on failure).
  - `share_publish.publish(conn, clone_dir, runner, *, display_name, topics, active, now, reporter) -> Literal["pushed", "unchanged"]` (stage `publish`).
  - `share_publish.wait_live(http: httpx.Client, url: str, reporter, *, timeout_s=300, sleep=time.sleep) -> bool` (stage `pages`, polls every 10 s until 200 with valid `_rpipe`).

- [ ] **Step 1: Write failing tests:** `test_collect_posts_filters_private_reasons` (paper with `liked similar` + `near your shelf` reasons → only topic in output; assert no file in `build_files` output contains `"liked similar"` or `"near your shelf"` or `"thesis.md"`); `test_collect_posts_ranks_within_day`; `test_collect_posts_excludes_diagrams`; `test_write_site_removes_stale_archives`; `test_publish_unchanged_does_not_push` (FakeRunner records no `git push`); `test_publish_pushes_changes`; `test_push_failure_raises_with_stderr`; `test_repo_status_other_repo` (FakeRunner returns a repo without `feed.json` → `"other"`); `test_check_tools_missing_gh_message`; `test_wait_live_polls_until_ok` (respx: 404, 404, 200 → True; fake sleep).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: build and push the published feed (Phase 10)` with docs.

### Task 5: Fetching followed feeds

**Files:**
- Create: `src/research_pipeline/share_fetch.py` (PIPELINE)
- Modify: `tests/test_architecture.py`
- Test: `tests/test_share_fetch.py`; fixtures in `tests/fixtures/feeds/` (a valid `feed.json` + archive built with `feed_format.build_files` in a helper, not hand-written)
- Docs: `docs/guide/sharing.md` ("Following" section: first fetch takes the whole past, later fetches, limits, what "stopped publishing" means)

**Interfaces:**
- Consumes: Task 1 helpers, Task 2 `parse_feed`, `parse_archive`, `FeedError`.
- Produces:
  - `resolve(target: str) -> tuple[str, str, str | None]` → `(feed_id, feed_url, handle)`; handle form `alex` or `@alex`; URL form must be `https://…`; raises `ValueError` otherwise. URL-form `feed_id` = `url:<url>`.
  - `get_limited(http, url, max_bytes) -> bytes` (streams; aborts over the cap; https-only redirects; 20 s timeout).
  - `fetch_one(conn, feed_id, http, reporter=None) -> FetchResult(new_posts: int, skipped_items: int, error: str | None)` — downloads `feed.json`; downloads archives whose sha256 differs from `follows.archive_hashes` (all on first fetch; ≤500); stores posts with `replace_followed_posts`; `record_fetch` with display name, topics, active; commits once at the end; on any error stores nothing new and records the failure.
  - `follow(conn, target, http, *, own_handle: str | None, reporter) -> FetchResult` — refuses own handle (`ValueError("That's your own feed.")`); fetches `feed.json` first and stores nothing if it fails (`ValueError("No rpipe feed at <url>: <reason>")`); then `upsert_follow` and `fetch_one`.
  - `fetch_all(conn, http, reporter) -> tuple[dict[str, int], list[str]]` — counts `followed_feeds`, `followed_new`; errors like `"fetch Alex M.: <reason>"`; one feed's failure never stops the rest (stage `fetch`, step per feed `fetching 2 of 3: Alex M.`).
  - `STOPPED_AFTER = 7`; `is_stopped(row) -> bool` (`failures >= 7` or `active == 0`).

- [ ] **Step 1: Write failing tests:** `test_follow_takes_whole_past` (feed with 2 archives → all posts stored); `test_second_fetch_skips_unchanged_archives` (respx call count); `test_fetch_rejects_oversize`; `test_fetch_skips_bad_items`; `test_one_bad_feed_does_not_stop_others`; `test_follow_unknown_handle_stores_nothing` (404 → `ValueError`, no `follows` row); `test_follow_own_handle_refused`; `test_http_redirect_to_http_refused`; `test_stopped_after_seven_failures`; `test_fetch_progress_steps` (ListReporter has `fetching 1 of 2: …`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: fetch followed feeds (Phase 10)` with docs.

### Task 6: The directory

**Files:**
- Create: `src/research_pipeline/share_directory.py` (PIPELINE)
- Modify: `tests/test_architecture.py`
- Test: `tests/test_share_directory.py`
- Docs: `docs/guide/sharing.md` ("The directory" section: the tag, no gatekeeper, how junk is filtered, "Feeds like yours")

**Interfaces:**
- Consumes: `share_fetch.get_limited`, `feed_format.parse_feed`, `db.replace_directory`.
- Produces:
  - `SEARCH_URL = "https://api.github.com/search/repositories?q=topic:rpipe-feed+fork:false&per_page=100"`.
  - `refresh(conn, http, reporter) -> tuple[int, list[str]]` — stage `directory`; for each hit with name `rpipe-feed`, fetch its `feed.json` (owner login → `feed_url`); keep only valid feeds; store `posts_per_day` = items in the last 30 days / 30 rounded to one decimal; `preview` = up to 3 `{title, one_line}` newest first; replaces the table only when the search itself succeeded (a failed search keeps the old list).
  - `shared_topics(theirs: list[str], mine: list[str]) -> int` (case and whitespace insensitive).

- [ ] **Step 1: Write failing tests:** `test_refresh_keeps_only_valid_feeds` (3 hits, one 404, one junk JSON → 1 entry); `test_failed_search_keeps_old_list`; `test_shared_topics_ignores_case_and_spaces`; `test_preview_and_rate`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `feat: directory of published feeds (Phase 10)` with docs.

### Task 7: `library_api` — Following, Mix, directory, follows, and acting on followed posts

**Files:**
- Modify: `src/research_pipeline/library_api.py`, `src/research_pipeline/triage.py` (embed voted/saved papers lacking an embedding)
- Test: `tests/test_library_api_share.py`, `tests/test_triage.py` (add case)
- Docs: `docs/guide/architecture.md` ("The app's door in detail": the new functions), `docs/guide/triage.md` (copied-in papers)

**Interfaces:**
- Consumes: Task 1 helpers.
- Produces (all return data, no network, added to `__all__`):
  - `class SourceView(BaseModel): feed_id: str | None` (None = you)`, name: str` ("You" or display name)`, handle: str | None`.
  - `class FollowedPostView(BaseModel): feed_id, paper: PaperView, digest_date, rank, sources: list[SourceView], reasons: list[ReasonView]` — `paper.summary` is the publisher's summary; `paper.vote`/`saved` are yours (0/False if not copied in).
  - `class FollowView(BaseModel): feed_id, handle, display_name, topics, in_mix: bool, last_fetched_at, last_error, stopped: bool, posts: int, waiting: bool` (followed, never fetched).
  - `class DirectoryEntryView(BaseModel): feed_id, handle, display_name, topics, posts_per_day, preview: list[dict], shared_topics: int, following: bool`.
  - `follows(conn) -> list[FollowView]`; `follow_request(conn, handle: str) -> None` (app's Follow button: `upsert_follow` with the directory entry's details, no fetch; raises `ValueError` for a handle not in the directory); `unfollow(conn, feed_id) -> None` (keeps posts of papers you saved or voted on); `set_in_mix(conn, feed_id, on) -> None`.
  - `following_days(conn, limit=60) -> list[str]`; `following_posts(conn, day) -> list[FollowedPostView]` (active follows; order by follow order, then rank).
  - `mix_days(conn, limit=60) -> list[str]`; `mix_posts(conn, day) -> list[FollowedPostView]` (your digest papers of that day first in score order with `sources=[You]`, then followed posts of `in_mix` feeds; one entry per paper id with every source listed, you first; a paper sorts under its earliest date among sources).
  - `directory(conn, my_topics: list[str]) -> list[DirectoryEntryView]` (sorted by `shared_topics` desc, then name).
  - `followed_post(conn, paper_id) -> FollowedPostView | None` (for the paper screen when the paper isn't yours, or is a copied-in `source='feed'` row).
  - `vote` and `save` change: if the paper id is not in `papers` but is in `followed_posts`, first insert it into `papers` (`source='feed'`, `status='skipped'`, metadata from the newest post, indexed for search), then record as today. A paper already in `papers` is used as is.
- Triage change: `_rank_and_store` (or `triage` before it) embeds papers in `wanted` (votes ∪ saves) that have no stored embedding, in the same embed call, so feedback vectors include them. Triage never changes `source='feed'` rows' status.

- [ ] **Step 1: Write failing tests:** `test_following_posts_order`; `test_mix_dedupes_with_own_paper` (same id in your digest and Alex's → one entry, sources `["You", "Alex M."]`); `test_mix_excludes_not_in_mix`; `test_vote_on_followed_post_copies_paper_in` (row `source='feed'`, `status='skipped'`, vote recorded, paper not in `digest_papers`); `test_vote_on_followed_paper_you_have_copies_nothing`; `test_unfollow_keeps_saved_posts`; `test_follow_request_unknown_handle`; `test_directory_sorted_by_shared_topics`; `test_followed_post_returns_publisher_summary`; in `test_triage.py`: `test_triage_embeds_voted_feed_paper` (FakeEmbedder; after triage, the copied-in paper has an embedding and a later similar paper gets a `liked similar` reason).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q` → pass (architecture: `library_api` still local-only).
- [ ] **Step 5: Commit** `feat: library_api for following and Mix (Phase 10)` with docs.

### Task 8: `rpipe share` commands and the nightly stages

**Files:**
- Modify: `src/research_pipeline/cli.py` (new `share_app` Typer group; `_run_locked` stages after the digest; `health_line`)
- Test: `tests/test_cli_share.py`, `tests/test_runs.py` (add cases)
- Docs: `docs/guide/sharing.md` ("How to run it": every command with an example session), `docs/guide/running.md` (the two nightly stages, health-line wording), `docs/guide/README.md` (link the new page)

**Interfaces:**
- Consumes: Tasks 3–7. A module-level `_share_runner()` and `_share_http()` factory each, so tests monkeypatch them with `FakeRunner` and a respx-backed client.
- Produces commands exactly as the spec's table: `setup`, `status`, `publish`, `set-name NAME`, `stop [--delete]`, `browse [SEARCH]`, `follow TARGET`, `unfollow HANDLE`, `following`, `fetch`, `mix add HANDLE`, `mix remove HANDLE`. Locked (via `_pipeline_lock`): `setup`, `publish`, `fetch`, `follow`, `browse`, `stop`, and `set-name`'s republish. The share clone lives at `<vault>/share/rpipe-feed/`.
- `setup` order: `check_tools` → if not signed in run `gh auth login` (interactive, outside the lock as `_prompt` does) → handle → `repo_status` (`"other"` stops with "github.com/<h>/rpipe-feed already exists and isn't an rpipe feed. Rename or delete it on GitHub, then run `rpipe share setup` again.") → display-name prompt (default: GitHub profile name, validated by `set_share`) → print the exact warning from Global Constraints and confirm → create/clone → `setup_git_credentials` → `publish` → `set_topic(on)` → `enable_pages` → `wait_live` → test push without prompts (`git push --dry-run` with `GIT_TERMINAL_PROMPT=0`) → `set_share(enabled=True, …)` → print the address. `stop`: publish once with `active=False`, `set_topic(off)`, `set_share(enabled=False)`; with `--delete`, confirm, then `delete_repo` and remove the local clone.
- Nightly, after `write_digest`, each in its own `try`: stage `publish` when `share_enabled` (error text `sharing: push failed, will retry tomorrow: <reason>`); then `fetch_all` and `share_directory.refresh` when there are follows or sharing is on (errors appended, counts `followed_feeds`, `followed_new`). The digest is already written, so these record into the run's `counts`/`errors` via `finish_run`; `health_line` adds ` · followed: N feeds, M new posts` when `followed_feeds`.

- [ ] **Step 1: Write failing tests** (CliRunner, FakeRunner, respx): `test_setup_happy_path` (answers name, confirms; config has `share_enabled: true`; runner saw `gh api -X PUT …topics`, pages call, `git push`); `test_setup_shows_public_warning_and_aborts_on_no`; `test_setup_refuses_other_repo`; `test_setup_missing_gh_says_how_to_install`; `test_follow_then_following_lists_feed`; `test_unfollow_unknown_handle_message`; `test_mix_remove_sets_flag`; `test_stop_removes_topic_and_disables`; `test_stop_delete_asks_first`; `test_status_shows_publish_and_follows`; in `test_runs.py`: `test_run_publishes_after_digest`, `test_run_push_failure_is_reported_not_fatal` (digest file exists; run `errors` contains `sharing: push failed, will retry tomorrow`), `test_run_fetch_counts_in_health_line`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: rpipe share commands and nightly sharing stages (Phase 10)` with docs.

### Task 9: App — Mine / Following / Mix and labelled cards

**Files:**
- Modify: `src/research_pipeline/webapp/routes_feed.py`, `present.py` (`CardData.sources: list[str]`, `why` prefix), `templates/feed.html`, `templates/_card.html`, `templates/paper.html`, `routes_pages.py` (`/paper/{id}` falls back to `library_api.followed_post`; Saved includes copied-in papers with the publisher's summary), `static/app.js`, `static/app.css` (only rules for the switch and label line, using existing variables)
- Test: `tests/test_webapp_share.py`
- Docs: `docs/guide/app.md` (the switch, labels, Following/Mix rules; screenshots not needed)

**Interfaces:**
- Consumes: Task 7 views.
- Produces: `GET /?view=mine|following|mix` and `/day/{day}?view=…` (default `mine`; unknown → `mine`); the switch is rendered only when `library_api.follows(conn)` has an active follow; links carry `?view=`; `app.js` remembers the choice in `localStorage["view"]` and, on `/` without `?view=`, redirects once to the stored view. Label line above the title: `"<sources joined by ' · '> · <topic>"` (topic omitted when none). "Why you're seeing this" on a followed post: `"<Name>'s pipeline picked it because <why>"`. Followed posts show no Summarize button.

- [ ] **Step 1: Write failing tests:** `test_switch_hidden_without_follows` (HTML of `/` has no `data-view-set`; byte-compare the cards section with a vault that has no share tables populated is not needed — assert absence); `test_following_view_lists_followed_posts_with_label` (`"Alex M. · Robotics"`); `test_mix_view_shows_you_and_feed_label` (`"You · Alex M."`); `test_followed_title_is_escaped` (title `<script>x</script>` appears as `&lt;script&gt;`); `test_followed_unsafe_link_not_linked` (`javascript:` url → no `href`); `test_paper_page_for_followed_post` (shows publisher summary, "Alex M.'s pipeline picked it because"); `test_no_summarize_button_on_followed`; `test_vote_on_followed_post_via_api` (POST vote → 200, then the paper is in `papers`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: Following and Mix in the app (Phase 10)` with docs.

### Task 10: App — People page and the "Showing" list

**Files:**
- Create: `src/research_pipeline/webapp/routes_people.py`, `templates/people.html`
- Modify: `server.py` (include router), `templates/base.html` (People nav link, after Search), `templates/feed.html` (desktop "Showing" checkboxes in Mix), `static/app.js` (follow and mix ticks), `static/app.css`
- Test: `tests/test_webapp_people.py`
- Docs: `docs/guide/app.md` (People screen; Follow from the app = posts arrive tonight)

**Interfaces:**
- Consumes: `library_api.directory`, `follows`, `follow_request`, `set_in_mix`; profile topics are read by the CLI's `app run` and passed into `create_app(..., my_topics: list[str] = [])` (the app does not import `profile_edit`; `create_app` stores them on `app.state`).
- Produces: `GET /people` (the "Your feed" box: private with the `rpipe share setup` command, or published with the address and the `rpipe share stop` command; "Feeds like yours" list with Follow/Following buttons and `shares N of your topics`, `about X papers a day`; tapping a row expands its preview; a "Following" section lists your follows, each with its Mix tick and, when `stopped`, "<Name> stopped publishing"); `POST /api/feed/{handle}/follow` → `{"following": true}` (button then reads "Following · posts arrive tonight"); `POST /api/feed/{feed_id}/mix` body `{"on": bool}` → `{"in_mix": bool}`. Errors as JSON `{"error": …}`; both go through the existing guard (same-origin).

- [ ] **Step 1: Write failing tests:** `test_people_page_lists_directory_sorted`; `test_people_page_shows_setup_command_when_private`; `test_follow_button_records_follow`; `test_follow_cross_site_refused` (403 via guard); `test_mix_tick_sets_in_mix`; `test_nav_has_people_link`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: People page and Mix ticks (Phase 10)` with docs.

### Task 11: Finish — roadmap, handoff, whole-page check

**Files:**
- Modify: `docs/superpowers/ROADMAP.md` (Phase 10 row: merged, links), `docs/superpowers/handoff.md` (replace State/Next), spec and plan headers (Status: merged), `docs/guide/sharing.md` (final read-through: What it does → How → Why (rejected approaches from the spec) → How to run/check → Where the code is)

- [ ] **Step 1:** Run `uv run pytest -q` and `uv run ruff check .` → all pass, clean.
- [ ] **Step 2:** Render the feed (Mine, Following, Mix) and People pages from a fixture vault with the test client, save the HTML to the scratchpad and read them through once as the owner would see them (no follows → page identical to today apart from the People link).
- [ ] **Step 3:** Commit `docs: Phase 10 merged` with ROADMAP and handoff.
