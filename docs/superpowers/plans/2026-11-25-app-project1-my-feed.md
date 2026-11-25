# App Project 1 (Your Own Feed) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A private website (`rpipe app`) that runs on the owner's computer and shows their feed on phone and desktop: Skim/Read cards, a full paper screen, More/Less like this, Save, Saved, Search and "Summarize this".

**Architecture:** A FastAPI server in a new app-group package `research_pipeline/webapp/` renders Jinja2 templates from data it gets only through `library_api`, opening one SQLite connection per request. A guard rejects wrong `Host` headers and cross-site writes. `rpipe app run|install|uninstall|status` runs it in the foreground or as an always-on systemd user service bound to the Tailscale address.

**Tech Stack:** Python 3.12, FastAPI + Starlette TestClient, Jinja2, uvicorn, plain CSS and a small vanilla JS file, self-hosted woff2 fonts.

**Spec:** `docs/superpowers/specs/2026-11-25-app-project1-my-feed-design.md` (read it first; mockups: https://claude.ai/artifact/JnycnAXpyjtqyybVmv9xMK, look A screens, look C colours for dark).

## Global Constraints

- New optional extra `app = ["fastapi>=0.110", "jinja2>=3.1", "uvicorn>=0.29"]`; the dev group also gets `fastapi` and `jinja2` so tests run. The nightly run never imports `webapp`.
- `webapp` is in the **app** group: it imports shared modules (`db`, `ids`, `vault`, `netguard`, `schedule`, `summary_models`) and `library_api`; never pipeline modules, `cli` or `progress_render`. `library_api` must still not reach `llm`/`backends`.
- App never takes the pipeline lock; every write is one `library_api` call.
- Pages make no third-party requests: fonts, CSS, JS, icons are served from `/static`.
- Default port **8002**; default host = machine's Tailscale IPv4; allowed hosts = the existing allowlist (localhost, loopback, Tailscale ranges).
- Service unit name `research-pipeline-app.service`.
- User-facing copy is plain words: "More like this", "Less like this", "Save", "Saved", "Summarize this", "Summary coming tonight", "Couldn't summarize this one", "Try again", "Why you're seeing this:", "About this summary", "Read the paper", "Earlier days". No model names, scores, "triage", "body cut" outside "About this summary".
- Upvote reason wording is "{n} people upvoted it on Hugging Face" (the mockups' "this week" is inaccurate: the count is not weekly).
- Every task updates `docs/guide/app.md` (and any other page it changes) in the same commit. Plain-word explanations; new terms go in `docs/guide/glossary.md`.
- Tests offline: `uv run pytest -q`; `uv run ruff check .` clean.

## Review Focus

- **A paper id with a version or odd spelling in a URL** (`/paper/2611.31205v2`): expect the paper page, via `ids.normalise_paper_id`; an id not in the vault gives the 404 page, never a 500. Test in Task 5.
- **The vault has no digest yet** (fresh install): feed shows a friendly empty state, not an error. Test in Task 4.
- **The nightly run is writing while you tap** (database busy): the tap waits up to 5 s (existing `open_db` timeout) and, if it still fails, the button shows "Couldn't save, try again" instead of a 500 page. Test in Task 6 with a monkeypatched `library_api.vote` raising `sqlite3.OperationalError`.
- **Text with HTML in it** (a title or abstract containing `<script>`): shown as text, never run. Jinja2 autoescape on; test in Task 4.
- **Phone width**: no sideways scrolling at 360 px. Pinned by CSS rules in Task 4 (no fixed widths above 100%) and checked by eye in Task 8.

---

### Task 1: Shared host allowlist (`netguard`)

**Files:**
- Create: `src/research_pipeline/netguard.py`
- Modify: `src/research_pipeline/serve.py` (remove `check_host`, `tailscale_ip`, `resolve_host`, `_TAILSCALE_NETS`, `_ALLOWED`; import them from `netguard`; `ServeError = netguard.HostError`)
- Modify: `tests/test_architecture.py` (add `"netguard"` to `SHARED`)
- Test: `tests/test_netguard.py` (move the host tests out of `tests/test_serve.py`; keep `test_serve.py` passing through the re-exports)
- Docs: `docs/guide/browsing.md` "Where the code is" line; `docs/guide/architecture.md` shared-module list.

**Interfaces:**
- Produces: `netguard.HostError(Exception)`; `netguard.Runner = Callable[[list[str]], subprocess.CompletedProcess]`; `check_host(host: str) -> str`; `tailscale_ip(runner: Runner) -> str`; `resolve_host(host: str | None, runner: Runner) -> str`; `system_runner(argv) -> CompletedProcess`. Behaviour and messages identical to today's `serve.py`, except messages say "this server" (unchanged wording works for both).

- [ ] **Step 1:** Move the existing host tests from `tests/test_serve.py` into `tests/test_netguard.py`, importing from `research_pipeline.netguard` and expecting `HostError`. Add `test_serve_reexports_netguard`: `serve.check_host is netguard.check_host and serve.ServeError is netguard.HostError`.
- [ ] **Step 2:** Run `uv run pytest tests/test_netguard.py -q` → FAIL (module missing).
- [ ] **Step 3:** Create `netguard.py` with the moved code; make `serve.py` import it.
- [ ] **Step 4:** Run `uv run pytest -q` → all pass (including `test_architecture.py`, `test_serve.py`).
- [ ] **Step 5:** Commit `refactor: move the host allowlist into shared netguard (for the app server)`.

### Task 2: `library_api` additions for the app

**Files:**
- Modify: `src/research_pipeline/library_api.py` (+ `__all__`)
- Test: `tests/test_library_api.py`
- Docs: `docs/guide/architecture.md` ("The app's door in detail": list the new functions).

**Interfaces:**
- Produces:
  - `class ReasonView(BaseModel): kind: Literal["topic", "liked", "upvotes", "author", "keyword", "shelf"]; label: str; paper_id: str | None = None`
  - `plain_reasons(reasons: list[str]) -> list[ReasonView]` (pure): parses stored strings `topic: NAME (0.80)` → topic/NAME; `liked similar: ID` → liked/"" with paper_id ID; `HF upvotes: N` → upvotes/"N"; `followed author: X` → author/X; `keyword: X` → keyword/X; `near your shelf: X` → shelf/X. `disliked similar: ...` and anything unrecognised are dropped. Order kept.
  - `digest_dates(conn, limit: int = 60) -> list[str]`: distinct digest dates, newest first.
  - `summarize_states(conn, paper_ids: list[str]) -> dict[str, Literal["pending", "running", "done", "failed"]]`: status of the newest `summarize` request per paper (`json_extract(payload, '$.paper_id')`); papers without a request are absent.

- [ ] **Step 1: Write the failing tests**

```python
def test_plain_reasons_maps_each_kind_and_drops_disliked_and_unknown():
    got = library_api.plain_reasons([
        "topic: agentic workflow (0.80)", "liked similar: 2611.25905", "disliked similar: 2611.25673",
        "HF upvotes: 56", "followed author: Ada Lovelace", "keyword: eBPF",
        "near your shelf: thesis-notes.md", "something new: x"])
    assert [(r.kind, r.label, r.paper_id) for r in got] == [
        ("topic", "agentic workflow", None), ("liked", "", "2611.25905"), ("upvotes", "56", None),
        ("author", "Ada Lovelace", None), ("keyword", "eBPF", None), ("shelf", "thesis-notes.md", None)]

def test_digest_dates_newest_first(conn):
    assert library_api.digest_dates(conn) == ["2026-11-02", "2026-11-01"]

def test_summarize_states_reports_newest_request_per_paper(conn):
    first = library_api.request(conn, "summarize", {"paper_id": "2611.25008"})
    db.mark_request(conn, first, "failed", error="x")
    library_api.request(conn, "summarize", {"paper_id": "2611.25008"})
    assert library_api.summarize_states(conn, ["2611.25008", "2611.25005"]) == {"2611.25008": "pending"}
```

- [ ] **Step 2:** `uv run pytest tests/test_library_api.py -q` → FAIL (names missing).
- [ ] **Step 3:** Implement the three functions and `ReasonView`; topic regex `^topic: (.+?) \(\d+(?:\.\d+)?\)$`.
- [ ] **Step 4:** `uv run pytest -q` → pass (architecture "returns data" test included).
- [ ] **Step 5:** Commit `feat: library_api reasons, digest dates and summarize states for the app`.

### Task 3: App server skeleton and the guard

**Files:**
- Modify: `pyproject.toml` (extra `app`, dev group adds `fastapi`, `jinja2`)
- Create: `src/research_pipeline/webapp/__init__.py`, `webapp/server.py`, `webapp/guard.py`, `webapp/templates/base.html`, `webapp/static/app.css`, `webapp/static/app.js`, `webapp/static/icon.svg`, `webapp/static/manifest.webmanifest`, `webapp/static/fonts/` (woff2 + `OFL.txt`)
- Modify: `tests/test_architecture.py` (add `"webapp"` to `APP`)
- Test: `tests/test_webapp_server.py`
- Docs: create `docs/guide/app.md` (What it does; How it works with the night-shift librarian / front desk picture and the diagram from the spec; Security: allowlist, Host and Origin checks, threat model; Where the code is); add `app.md` to `docs/guide/README.md`; glossary: *web server*, *template*, *web app manifest*, *same-origin check*.

**Interfaces:**
- Produces:
  - `server.create_app(vault_db: Path, allowed_hosts: set[str]) -> FastAPI`. Stores both on `app.state`. Mounts `/static`. Jinja2 environment with autoescape on, templates from `webapp/templates`, available as `server.templates`.
  - `server.get_conn(request) -> Iterator[sqlite3.Connection]` FastAPI dependency: `db.open_db(app.state.vault_db)`, closed after the request.
  - `guard.check(request, allowed_hosts: set[str]) -> Response | None` used by an HTTP middleware: returns `PlainTextResponse("Unknown host", 400)` unless the `Host` header (port stripped, IPv6 brackets stripped, lower-cased) is in `allowed_hosts`; for any method other than GET/HEAD returns `PlainTextResponse("Cross-site request refused", 403)` unless `Origin` (or, if absent, `Referer`) has scheme `http` and a host in `allowed_hosts`. `allowed_hosts` always includes the bind host; for a loopback bind it also includes `localhost`, `127.0.0.1` and `testserver` (Starlette's TestClient host).
  - `base.html` blocks: `title`, `content`; links `/static/app.css`, `/static/app.js` (defer), `/static/manifest.webmanifest`, `/static/icon.svg`; `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`; bottom nav (phone) / sidebar (≥ 900 px) with Feed, Saved, Search.
  - CSS tokens (copy from mockups): light `--bg #F6F7F9, --surface #FFFFFF, --ink #14171F, --muted #5B6270, --line #E3E6EB, --accent #2F45C8, --accent-soft #EEF0FB`; dark (via `prefers-color-scheme: dark`) `--bg #101318, --surface #161A21, --ink #E7EAF0, --muted #9AA3B2, --line #232833, --accent #9CC0FF, --accent-soft #1E2840`. Eight topic colours as `--topic-0` … `--topic-7`, each with light and dark values passing 4.5:1 on `--surface`. Fonts: Literata 600 (titles), Inter 400/500/600 (body), IBM Plex Mono 500 (ids, section tags), from `https://cdn.jsdelivr.net/npm/@fontsource/<family>@5/files/<family>-latin-<weight>-normal.woff2`, saved under `static/fonts/` with the OFL licence text.

- [ ] **Step 1: Write the failing tests** (`tests/test_webapp_server.py`, fixture builds `create_app(tmp vault db, {"testserver"})` and a `TestClient`):

```python
def test_static_css_served(client):
    assert client.get("/static/app.css").status_code == 200

def test_wrong_host_refused(client):
    assert client.get("/static/app.css", headers={"host": "evil.example"}).status_code == 400

def test_cross_site_post_refused(client):
    r = client.post("/static/app.css", headers={"origin": "http://evil.example"})
    assert r.status_code == 403

def test_same_origin_post_passes_guard(client):  # 404/405 from routing, not 403
    r = client.post("/nothing", headers={"origin": "http://testserver"})
    assert r.status_code in (404, 405)

def test_post_without_origin_or_referer_refused(client):
    assert client.post("/nothing").status_code == 403
```

- [ ] **Step 2:** `uv sync --extra embed --extra serve --extra app` then `uv run pytest tests/test_webapp_server.py -q` → FAIL.
- [ ] **Step 3:** Implement server, guard, base template, CSS tokens and layout skeleton, manifest (`name` "Research Feed", `display` "standalone", icon), fonts.
- [ ] **Step 4:** `uv run pytest -q` → pass.
- [ ] **Step 5:** Commit `feat: app server skeleton with host and same-origin guard`.

### Task 4: The feed

**Files:**
- Create: `webapp/present.py`, `webapp/routes_feed.py`, `webapp/templates/feed.html`, `webapp/templates/_card.html`, `webapp/templates/_empty.html`
- Modify: `webapp/server.py` (include the router), `webapp/static/app.css`, `webapp/static/app.js`
- Test: `tests/test_webapp_present.py`, `tests/test_webapp_feed.py`
- Docs: `docs/guide/app.md` sections "The feed", "Skim and Read", "Why you're seeing this" (the reason table from the spec, with the Hugging Face wording).

**Interfaces:**
- Consumes: Task 2's `plain_reasons`, `digest_dates`, `summarize_states`; `library_api.digest_papers`.
- Produces (`present.py`, pure):
  - `why_sentence(reasons: list[ReasonView]) -> str | None`: phrases `you follow {label}`, `it resembles a paper you liked`, `{label} people upvoted it on Hugging Face`, `it's by {label}, whom you follow`, `it mentions {label}`, `it's close to "{label}" on your shelf`; one → `x.`; two → `x and y.`; three+ → `x, y, and z.`; none → None.
  - `topic_of(reasons: list[ReasonView]) -> str | None`: first topic label, first letter upper-cased.
  - `topic_color(topic: str) -> int`: `zlib.crc32(topic.lower().encode()) % 8`.
  - `section_tag(sections: list[str]) -> str | None`: number = first word of a section title when it matches `^(\d+(\.\d+)*|[IVXLC]+(-[A-Z])?|[A-Z](\.\d+)+)$`; unnumbered titles ignored; one number → `§5.1`; several → `§{first}–{last}` (en dash) or `§5.1` when first == last; none → None.
  - `CardData` (dataclass): `id, title, url, topic, topic_color, one_line | None, abstract, problem, approach, results` (each `(text, tag) | None`), `why: str | None, vote: int, saved: bool, summarize_state: Literal["none", "pending", "running", "done", "failed"]`. `card(view: PaperView, state: str | None) -> CardData`.
- Routes: `GET /` → latest digest; `GET /day/{date}` (`YYYY-MM-DD`; unknown date → 404 page). Feed heading shows the date as `Wednesday, Nov 25`; an "Earlier days" link to the next older date from `digest_dates`.
- Card markup: Skim fields always rendered; Read fields inside `<div class="read-more">`; `<body data-depth="skim|read">` toggled by a Skim/Read `<button aria-pressed>` pair in `app.js`, choice saved in `localStorage["depth"]` (wrapped in try/catch). CSS hides `.read-more` when `data-depth="skim"`.
- Unsummarized card: first 300 characters of the abstract (cut at a word, with "…") and the summarize control by state: none → "Summarize this" button; pending/running → "Summary coming tonight"; failed → "Couldn't summarize this one" + "Try again" button. (Buttons get wired in Task 6; here they render with `data-action` attributes.)
- Empty state when no digest: heading "No papers yet" and "Your first feed appears after the next nightly run."

- [ ] **Step 1: Write the failing tests** (`present` unit tests with the exact strings above, e.g.)

```python
def test_why_sentence_three_parts():
    rs = library_api.plain_reasons(["topic: agentic workflow (0.80)", "liked similar: 2611.1", "HF upvotes: 56"])
    assert present.why_sentence(rs) == ("you follow agentic workflow, it resembles a paper you liked, "
                                        "and 56 people upvoted it on Hugging Face.")

def test_section_tag_range_and_roman_and_unnumbered():
    assert present.section_tag(["5.1 Efficiency", "5.2 Coordination"]) == "§5.1–5.2"
    assert present.section_tag(["IV-B Research agenda"]) == "§IV-B"
    assert present.section_tag(["Abstract"]) is None
```

and feed tests: `test_feed_lists_latest_digest_in_score_order`, `test_feed_shows_why_and_topic`, `test_card_read_fields_present_in_markup`, `test_unsummarized_digest_paper_shows_abstract_and_summarize_button`, `test_pending_request_shows_coming_tonight`, `test_failed_request_shows_try_again`, `test_empty_vault_shows_friendly_empty_state`, `test_title_html_is_escaped` (title `<script>alert(1)</script>` appears as `&lt;script&gt;`), `test_unknown_day_is_404`, `test_earlier_days_link_points_to_previous_date`.
- [ ] **Step 2:** Run them → FAIL.
- [ ] **Step 3:** Implement `present.py`, the router, templates, CSS (cards, topic label colours, depth toggle, phone layout ≤ 900 px single column with bottom nav; ≥ 900 px sidebar + feed + reader column hidden until Task 5), JS depth toggle.
- [ ] **Step 4:** `uv run pytest -q` → pass.
- [ ] **Step 5:** Commit `feat: app feed with Skim and Read`.

### Task 5: Paper screen, Saved, Search

**Files:**
- Create: `webapp/routes_pages.py`, `webapp/templates/paper.html`, `webapp/templates/list.html`, `webapp/templates/404.html`
- Modify: `webapp/server.py` (router + 404 handler rendering `404.html`), `webapp/present.py` (`paper_detail`), `app.css`
- Test: `tests/test_webapp_pages.py`
- Docs: `app.md` sections "Reading a paper", "Saved and Search", "About this summary".

**Interfaces:**
- Consumes: Task 4 `card`, `section_tag`, `why_sentence`; `library_api.get_paper`, `saved_papers`, `search_papers`; `ids.normalise_paper_id`.
- Produces: `present.paper_detail(view: PaperView, state: str | None) -> DetailData` (dataclass: `card: CardData`, `authors_short: str` ("A, B, C and N others" for > 3), `strengths/weaknesses: list[(text, tag)]`, `questions: list[str]` (each with any trailing `[sections: ...]` text removed), `about: list[str]`).
  - `about` lines, plain words: `Read from the full paper` / `Read from the abstract only`; `Read in N parts` when `parts > 1`; `Some of the paper was too long and was cut` when `body_cut`; `Summarized by {model} on {date}` from the stored provenance when present; one line per `unverified` claim: `Could not check: "{text}"`.
- Routes: `GET /paper/{paper_id}` (normalise; unknown → 404 page with "That paper isn't in your library."); `GET /saved` ("Nothing saved yet." when empty); `GET /search?q=` (empty `q` → search box only; no results → `No papers match "{q}".`; results as Skim cards, up to 30).
- Desktop (≥ 1100 px): the paper page also renders that paper's digest day as a compact list in a left column (current paper highlighted, each item linking to its own `/paper/{id}`), so desktop reads as feed beside reader; below 1100 px the column is hidden and the reader is full width (text max 680 px). Test: `test_paper_page_includes_day_list` (the other papers of the same digest date appear in an element with class `day-list`).

- [ ] **Step 1: Write the failing tests:** `test_paper_page_shows_brief_and_critique_with_tags`, `test_paper_page_includes_day_list`, `test_paper_page_accepts_version_suffix` (`/paper/2611.25005v2` → 200, same title), `test_unknown_paper_is_404_page`, `test_about_lists_read_mode_and_unverified`, `test_questions_strip_section_suffix`, `test_saved_lists_saved_newest_first`, `test_saved_empty_message`, `test_search_finds_by_title_word`, `test_search_no_match_message_escapes_query`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `uv run pytest -q` → pass.
- [ ] **Step 5:** Commit `feat: app paper screen, saved and search`.

### Task 6: Taps that change things

**Files:**
- Create: `webapp/routes_actions.py`
- Modify: `webapp/server.py`, `webapp/static/app.js`, `webapp/templates/_card.html`, `paper.html`
- Test: `tests/test_webapp_actions.py`
- Docs: `app.md` section "Voting, saving and asking for a summary" (with the request-list link to `running.md`).

**Interfaces:**
- Consumes: `library_api.vote`, `save`, `request`, `summarize_states`, `get_paper`.
- Produces: JSON endpoints (all POST, JSON body, same-origin guarded):
  - `/api/paper/{id}/vote` body `{"direction": "up"|"down"|"clear"}` → `{"vote": -1|0|1}`.
  - `/api/paper/{id}/save` body `{"saved": bool}` → `{"saved": bool}`.
  - `/api/paper/{id}/summarize` → `{"state": "pending"|"running"}`.
  - Unknown paper → 404 `{"error": "That paper isn't in your library."}`; bad body → 400 `{"error": ...}`; `sqlite3.OperationalError` → 503 `{"error": "Your library is busy. Try again in a moment."}`.
- JS: buttons with `data-action` POST via `fetch` (header `Content-Type: application/json`; the browser adds `Origin`), update `aria-pressed`/text from the response, show the error text in a small status line under the actions on failure. Tapping the active More/Less button sends `clear`.

- [ ] **Step 1: Write the failing tests:** `test_vote_up_then_clear`, `test_bad_direction_400`, `test_save_and_unsave`, `test_summarize_adds_request_and_repeat_is_same`, `test_unknown_paper_404_json`, `test_busy_database_503` (monkeypatch `library_api.vote` to raise `sqlite3.OperationalError("database is locked")`), `test_action_without_origin_403`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement routes and JS.
- [ ] **Step 4:** `uv run pytest -q` → pass.
- [ ] **Step 5:** Commit `feat: app votes, saves and summarize requests`.

### Task 7: `rpipe app` commands and the always-on service

**Files:**
- Create: `src/research_pipeline/webapp/service.py`
- Modify: `src/research_pipeline/cli.py` (new `app_app` Typer group named `app`), `pyproject.toml` if needed
- Test: `tests/test_webapp_service.py`, `tests/test_cli_app.py`
- Docs: `app.md` "Setting it up" (install the extra: `uv tool install --editable '.[embed,serve,app]' --force`; `rpipe app install`; open the printed address; Add to Home Screen on iPhone/Android; `rpipe app status`, `uninstall`; trying it with `rpipe app run`), `README.md` quick start line, `scheduling.md` pointer (two services now).

**Interfaces:**
- Consumes: `netguard.resolve_host`, `netguard.system_runner`; `schedule._quote`, `schedule._quote_exec`, `schedule._reject_control_chars`, `schedule.default_unit_dir`, `schedule.system_runner`; `server.create_app`.
- Produces:
  - `service.SERVICE_NAME = "research-pipeline-app.service"`
  - `service.render_service(vault: Path, rpipe_cmd: str, host: str, port: int) -> str`: `[Unit] Description=Research pipeline app`, `After=network-online.target tailscaled.service`, `Wants=network-online.target`; `[Service] Environment="RESEARCH_VAULT=..."`, `ExecStart="<rpipe>" app run --host <host> --port <port>`, `Restart=on-failure`, `RestartSec=10`; `[Install] WantedBy=default.target`.
  - `service.install(vault, rpipe_cmd, host, port, unit_dir, runner) -> Path` (probe `systemctl --user show-environment` first; write; `daemon-reload`; `enable --now`), `service.remove(unit_dir, runner) -> tuple[list[Path], list[str]]`, `service.address(unit_dir) -> str | None` (`http://host:port/` parsed from the unit's ExecStart), `service.is_active(runner) -> bool` (`systemctl --user is-active --quiet`).
  - CLI: `rpipe app run [--host H] [--port 8002] [--vault V]` (checks the extra with `importlib.util.find_spec("fastapi")`, else exits 1 with `The app is not installed. Install it with: uv sync --extra embed --extra serve --extra app (in the repo), or for plain rpipe: uv tool install --editable '.[embed,serve,app]' --force`; opens the vault once via `_open` for migrations; prints `Your feed is at http://H:P/ (only your own devices can reach it)`; runs `uvicorn.run(create_app(...), host=H, port=P, log_level="warning")`). `rpipe app install [--host] [--port]` (Linux + systemctl checks as `schedule install`; resolves and checks the host now, refusing with the netguard message; prints the address and the Add-to-Home-Screen hint). `rpipe app uninstall`. `rpipe app status` (`Running at http://H:P/` / `Installed but not running. Check: journalctl --user -u research-pipeline-app` / `Not installed. Set it up with: rpipe app install`).

- [ ] **Step 1: Write the failing tests:** service rendering (exact lines above, quoting of a vault path with spaces and `%`), install probe-before-write, remove warnings, `address` parsing, CLI tests with a fake runner and `_platform` patched: `test_app_run_without_extra_explains_install`, `test_app_install_refuses_public_host`, `test_app_install_writes_unit_and_prints_address`, `test_app_status_three_states`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `uv run pytest -q` → pass.
- [ ] **Step 5:** Commit `feat: rpipe app run/install/uninstall/status`.

### Task 8: End-to-end check and handoff

**Files:**
- Modify: `docs/superpowers/handoff.md`, `docs/guide/app.md` (screenshots not needed; a short "What to check after installing" list)

- [ ] **Step 1:** `uv sync --extra embed --extra serve --extra app`; `uv run rpipe app run --host 127.0.0.1 --port 8099` against a copy of a fixture vault (never the owner's vault); with `curl -s http://127.0.0.1:8099/` confirm 200 and the feed heading; `curl -s -X POST http://127.0.0.1:8099/api/paper/X/save` without Origin → 403. Stop the server.
- [ ] **Step 2:** Full `uv run pytest -q` and `uv run ruff check .` → pass, counts recorded in the handoff.
- [ ] **Step 3:** Handoff entry: what was built, how the owner installs it (`uv tool install --editable '.[embed,serve,app]' --force`, `rpipe app install`, open on phone, Add to Home Screen), what to look at (phone width, dark mode, Skim/Read, a vote showing up in `rpipe show`), parked items.
- [ ] **Step 4:** Commit `docs: handoff records app project 1 built`.
