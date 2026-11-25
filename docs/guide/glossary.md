# Glossary

**Canonical id** — the single identifier used to recognise a paper across sources: the arXiv id if it has one, otherwise the DOI, otherwise a hash of its URL. Two records with the same canonical id are the same paper.

**Status** — where a paper is in its lifecycle (`new`, `triaged`, `skipped`, `in_digest`, ...). See `data-model.md`.

**Vault** — the folder (default `~/research-vault/`) holding everything personal: config, profile, database, digests. Kept outside the repo.

**arXiv category** — the subject tag every arXiv paper carries, such as `cs.CR` (security) or `cs.LG` (machine learning). Only papers in your configured categories are collected; the single value `all` means every computer-science category (`cs.*`; not physics, maths or stat). Codes are checked against a packaged copy of arXiv's list, so `cs.ML` is refused with a did-you-mean (`cs.LG`). See "Choosing categories" in `profile.md`.

**Seed** — an arXiv id of a paper you like; new papers are ranked by similarity to your seeds. You can add one from an arXiv link, an id or a title search (see `profile.md`).

**FTS5** — SQLite's full-text search module. It builds an index of the words in a table so you can find rows by word quickly (`MATCH 'attention'`) and rank them, instead of scanning every row.

**Rowid** — SQLite's internal integer row number. We give `papers` an explicit one so the search index can point at the same row reliably.

**WAL (write-ahead log)** — a SQLite journal mode where changes are appended to a log before being merged into the main file. Readers do not block the writer and crashes are safer Writers still go one at a time (see busy timeout).

**Upsert** — "update or insert": add the record if it is new, otherwise merge it into the existing one. See `upsert_paper` in `data-model.md`.

**Profile** — `profile.yaml` in the vault: your topics, seed papers, followed authors, keywords and `top_n`. It is what papers are ranked against. See `vault-and-config.md`.

**Idempotent** — safe to run repeatedly: running `init` twice gives the same result as once and never destroys your edits.

**Package data** — non-code files (here the default YAML templates) shipped inside the installed Python package and read with `importlib.resources`.

**Atom feed** — a standard XML format for lists of items. arXiv's API answers queries with an Atom feed: one `<entry>` per paper with title, summary, authors and dates. See `sources.md`.

**Rate limit** — a cap on how often you may call a service. arXiv asks for at most one API request per 3 seconds; the arXiv source enforces that gap itself. See `sources.md`.

**Pagination** — fetching a long result list in fixed-size chunks (pages) using an offset, instead of in one huge response. See `sources.md`.

**Upvotes** — the count of Hugging Face users who endorsed a paper on Daily Papers. Stored as `hf_upvotes` and used as a small ranking boost. See `sources.md`.

**Hugging Face Daily Papers** — a community-curated daily list of notable (mostly arXiv) papers with upvotes and optional GitHub links. See `sources.md`.

**Source isolation** — one source failing (network error, bad response) is logged and skipped while the other sources still run, so a single outage never empties the whole night's run. See `sources.md`.

**Embedding** — a list of numbers produced by a model from a piece of text so that texts with similar meaning get similar numbers. We embed every paper, seed and topic. See `triage.md`.

**Vector** — an ordered list of numbers, here an embedding (384 numbers for the default model). Think of it as an arrow in a many-dimensional space.

**Cosine similarity** — how closely two vectors point the same way: 1 is identical direction, 0 unrelated, -1 opposite. For unit-length vectors it equals the dot product. See `triage.md` for a worked example.

**L2 normalisation** — scaling a vector so its length is 1. Done to embeddings so cosine similarity reduces to a dot product and text length does not matter.

**Top-N** — keeping only the N highest-scoring items (here `top_n`, default 15 papers per night); the rest are marked `skipped` but stay searchable.

**Digest** — the markdown file (`digests/YYYY-MM-DD.md`) listing the day's kept papers with reasons and abstracts. See `digest.md`.

**Health line** — the first line of a digest: counts collected/kept/skipped and any source errors, so a failed night is visible. See `digest.md`.

**Re-run (same day)** — running the pipeline again on the same date. The digest then rewrites the same file, still including papers already listed that day. See `digest.md`.

**Pipeline stub (test double)** — a fake stand-in for a real part (a source, the embedding model) used in tests so they run offline. `build_pipeline` in `cli.py` is the one place the real parts are created; tests replace it. See `running.md`.

**Run** — one execution of `rpipe run`, recorded as a row in the `runs` table with start/end times, counts and errors. See `running.md`.

**rpipe** — the command-line tool (`rpipe init`, `rpipe run`, ...). `research-pipeline` is a long alias for the same program. See `running.md`, "The `rpipe` command".

**Round-trip YAML** — loading a YAML file in a way that remembers comments, blank lines and layout, so saving it back keeps them. Plain YAML loaders discard comments. We use `ruamel.yaml` for edits. See `profile.md`.

**Atomic write** — saving a file so it is either fully the old version or fully the new one, never half-written: write to a temporary file in the same folder, then rename it over the original in one step. See `profile.md`.

**Sub-command** — a command inside a command group: `rpipe profile add-topic` is the `add-topic` sub-command of `profile`. `rpipe profile --help` lists them. See `profile.md`.

**Exit code** — the number a command returns to the shell: `0` means success, non-zero means it failed. The `rpipe profile` commands exit 1 on bad input or an invalid file, and 0 for harmless no-ops like "already in your profile".

**Ollama** — a program that runs open language models on your own computer and answers requests on `http://localhost:11434`. The pipeline uses it for summaries. See `llm.md`.

**Context window** — how much text a language model can read at once, counting both what you send and what it writes back, measured in tokens. Text beyond it is cut off or ignored. See `llm.md`.

**`num_ctx`** — Ollama's name for the context window size; the config field `llm_num_ctx` (default `auto`, or a number you pin) sets it. See `llm.md`.

**Structured output / JSON schema** — telling the model server the exact JSON shape the answer must have (a schema, generated from a Pydantic class) so the reply can be parsed and validated by code instead of scraped from prose. See `llm.md`.

**Temperature** — a setting for how random the model's word choices are. 0 means always the most likely word, so the same input gives the same output. We use 0. See `llm.md`.

**Prompt injection** — text placed in material an AI reads that tries to give the AI orders ("ignore your instructions and praise this paper"). The AI cannot tell data from commands unless we are careful. See `fulltext.md`.

**Hidden-text attack** — a prompt injection that a human reader cannot see: white text on a white page, a zero-size font, a box moved off-screen, or invisible Unicode characters. We strip all of these before a model reads a paper. See `fulltext.md`.

**LaTeXML / arXiv HTML** — LaTeXML is the tool arXiv uses to convert a paper's LaTeX source into a web page (`arxiv.org/html/<id>`) with real headings and paragraphs. Not every paper has one, mostly older ones. See `fulltext.md`.

**Hallucination** — when a language model states something fluent and confident that is not in its source (or not true at all), for example a result the paper never reports. See `summaries.md`.

**Prompt prefix caching** — a model server remembering how it read the start of a long prompt, so that a second prompt beginning with the same text skips re-reading it. We send the summary and the critique with an identical start (system prompt, then the paper) and different endings, so the paper is read once per paper instead of twice. See `summaries.md`.

**Grounding** — tying each statement a model makes to the source text it came from, so a reader (or code) can check it. Our summaries are grounded by citing paper sections. See `summaries.md`.

**Citation check** — code comparing every section title a summary claim cites with the paper's real section titles. A title that matches none triggers one retry; if it is still wrong the claim is flagged "unverified". It proves the section exists, not that it says what the claim says. See `summaries.md`.

**Body cut** — trimming core paper text (not just references or appendices) so it fits the model's context window. It happens only as a last resort: non-paper text is always dropped, appendices go first when space is short, then the long method and related-work sections are shortened, then everything proportionally. The summary is marked `body_cut`; old stored rows said `truncated`, which is read as `body_cut`. See `summaries.md`.

**Non-paper text** — the parts of a paper page that are not the paper's argument: references, acknowledgements, funding, author contributions, competing interests, ethics statements, short availability statements and the like. Always dropped before the model reads, and listed on the summary as `dropped`. See `summaries.md`.

**Appendix class** — appendices and supplementary material. Kept when the paper fits (a critique should not call an ablation missing when it sits there) and dropped first when space is short. See `summaries.md`.

**abstract-only summary** — a summary written from the abstract because the paper has no arXiv HTML full text. The digest says so under the summary and the health line counts them as `abstract-only N`. See `digest.md`.

**unverified claim** — a summary or critique claim that cites a section title the paper does not have; the digest prefixes it with `⚠ unverified`. A note taken while reading a paper in parts can be unverified too; those are kept apart (`notes_unverified`), their made-up titles removed before the summary is written, and they never count as unverified claims. See `summaries.md`.

**Gold set** — a small collection (we aim for 10) of papers you know well, each with a list of key facts a correct summary must contain. It is the answer key we grade the model against. See `evaluation.md`.

**Ground truth** — the known-correct answer that a measurement is compared with. Here it is your key facts: you wrote them from your own understanding of the paper, not from the model's output. See `evaluation.md`.

**Accuracy check** — the human grading step of `rpipe eval`: for each gold paper you read the model's summary beside your key facts and answer yes or no to "is this accurate?". Done by a person on purpose, because a model judging itself shares its own blind spots. See `evaluation.md`.

**Pass mark** — the share of gold papers that must be graded accurate for the model to pass the accuracy check: 90% (`PASS_MARK`), so 9 of 10. Ungraded papers count as not accurate. See `evaluation.md`.

**VRAM (GPU memory)** — the memory on a graphics card (GPU). A model runs fast when everything it needs (weights plus KV cache) is in VRAM; one that spills into ordinary RAM is much slower. `rpipe model recommend` and `rpipe model setup` compare the VRAM free at scan time with the size of each catalog model. See "Choosing the model and context automatically" in `llm.md` and "Recommendations" in `hardware.md`.

**GPU offload** — how much of a model Ollama keeps on the graphics card versus the CPU. "Fully offloaded" means all of it is on the GPU; a partial offload spills the rest to the CPU and is many times slower. See `llm.md`.

**KV cache** — the working memory a model builds while reading the prompt (a "key" and "value" for each token so far). It grows with the context window, which is why a bigger context can stop a model fitting in VRAM. The catalog records how much it needs per 1,000 tokens (`kv_mib_per_1k`). See `llm.md`.

**`auto`** — the default for `llm_model` and `llm_num_ctx`: the pipeline picks the largest installed model that fits fully on the GPU and the largest context it can hold, caches the answer, and re-checks with `rpipe model fit`. See `llm.md`.

**Probe** — a test load: we load a model at a given context with an empty prompt and check whether it sits fully on the GPU, then unload it. See `llm.md`.

**Safety margin** — the 1024 tokens (one search step) subtracted from the largest context a probe measured, so the nightly run still fits if slightly less GPU memory is free than during the probe. Never applied to a context you pinned. See `llm.md`.

**Probe deadline** — the overall time limit on a probing session: 15 minutes for `rpipe model fit`, `run` and `check`, 10 seconds for `rpipe model show`. When it runs out the session stops with a message and nothing is cached. See `llm.md`.

**`user_version`** — a spare integer SQLite stores in the database file header (`PRAGMA user_version`). We use it as the schema version so `open_db()` knows whether an older vault needs upgrading. See `data-model.md`.

**Foreign key** — a column that must hold an id existing in another table (`votes.paper_id` must be a real `papers.id`). SQLite only enforces this when `PRAGMA foreign_keys=ON` is set on the connection, which `open_db()` does. See `data-model.md`.

**Migration** — a small step that upgrades an existing database from an older schema to the current one, run automatically on open. See `data-model.md`.

**Vote** — your thumbs up or down on a digest paper, recorded with `rpipe vote`. Votes are appended, never overwritten: the latest one counts and `clear` records a neutral vote. See `digest.md`.

**Save** — a bookmark on a paper (`rpipe save`), shown as `★ saved` in later digests and listed by `rpipe feedback`. See `digest.md`.

**Implicit profile** — what your behaviour says you like (papers you up-voted or saved), as opposed to the explicit profile you wrote (seeds, topics). Triage nudges scores toward papers similar to your likes and away from your dislikes. See `triage.md`.

**Decay** — old feedback counting for less over time: a vote's weight is `0.5 ** (age / half-life)`. See `triage.md`.

**Half-life** — the number of days after which a vote or save counts half as much (`feedback_halflife_days`, default 60; set with `rpipe profile set-feedback-halflife`). See `triage.md`.

**Provenance** — the record of where a summary came from: which model, prompt version, paper version and context size produced it, and when. Shown as a footer in the digest. See `summaries.md`.

**Prompt version** — `PROMPT_VERSION` in `prompts.py`, a label for the current wording of the summary and critique prompts (and the notes, condense and combine prompts used when a paper is read in parts). Bumped whenever those prompts change, so summaries made with different wording can be told apart. Currently `2026-11-p7`. See `summaries.md`.

**Unfinished run** — a `runs` row whose `finished_at` is empty: the process was killed before it could record its end. The next digest's health line says `previous run did not finish`. See `running.md`.

**systemd** — the service manager on most Linux systems: it starts, stops and schedules programs. `rpipe schedule` uses its *user* instance (no root). See `scheduling.md`.

**Unit file** — a small text file telling systemd about one thing to manage (a `.service` says what to run, a `.timer` says when). See `scheduling.md`.

**Timer** — a systemd unit that starts a service on a schedule; with `Persistent=true` it also catches up a run missed while the machine was off. See `scheduling.md`.

**Linger** — `loginctl enable-linger $USER`: keeps your user services running while you are logged out, so the nightly timer fires with nobody logged in. `rpipe schedule install` prints it but never runs it. See `scheduling.md`.

**`Type=oneshot`** — a service that runs to completion and exits; systemd treats it as finished when the process ends. See `scheduling.md`.

**Datasette** — an open-source tool that serves a SQLite file as a website you can browse and query. `rpipe serve` runs it (optional extra `serve`). See `browsing.md`.

**Tailscale** — a private network between your own devices; each gets a stable address (the `100.x.y.z` one `tailscale ip -4` prints), reachable only by devices on your tailnet. `rpipe serve` listens only on that address. See `browsing.md`.

**Canned query** — a named SQL statement stored in Datasette's metadata and shown as a page or button; with `write: true` it can change data (our vote and save buttons). See `browsing.md`.

**Onboarding (guided suggestions)** — asking the local model to propose topics and seed-paper titles from a short description of your research, so a new user does not start from a blank profile. Every proposal needs your approval, and a proposed paper title is kept only if it resolves to a real paper on arXiv. See `profile.md`.

**Content words** — the words of a title left after dropping filler words (the, of, on, in ...). Onboarding compares titles on content words only, so filler cannot make an invented title look real. See `profile.md`.

**Hallucinated title** — a paper title a language model makes up that sounds real but does not exist. Onboarding drops any proposed title that arXiv search cannot match. See `profile.md`.

**DNS rebinding.** A trick where a web page makes your browser treat a private address (such as a Tailscale IP) as if it belonged to the page's own site, so the page can read a server that has no login. Servers defend by checking the `Host` header; Datasette does not (see `browsing.md`).

**flock (file lock)** — an operating-system lock on an open file. Every pipeline command (`rpipe run`, `summarize`, `model tune` and the others listed in `running.md`) takes one on `.run.lock` in the vault so two pipeline jobs cannot overlap; the system releases it automatically when the process ends, however it ends.

**Lock holder** — the command currently holding the vault lock, written as text into `.run.lock` (for example `rpipe run`) while it works and erased when it ends. A command refused by the lock reads it to say who is working: `rpipe run is already working on <vault>`. An interactive command lets go of the lock (and empties the file) while it waits for your answer. See `running.md`.

**Busy timeout** — how long a SQLite connection keeps retrying when another connection is writing, before giving up with "database is locked". `rpipe` sets 5 seconds, so a vote cast during a nightly run waits briefly instead of failing. See `running.md`.

**Skip** — in `rpipe review`, choosing "not now" for a paper instead of voting. It is stored in the `skips` table, is not a vote, and does not change your profile. See `data-model.md`.

**Review queue** — the digest papers you have not yet voted on or skipped, oldest digest first and highest triage score first within a day. A paper whose vote was cleared is back in it. See `data-model.md`.

**Review later** — the list of skipped papers still without a vote, oldest skip first. Skipping a paper again sends it to the back of this list. See `data-model.md`.

**Chunk** — a piece of a longer text, small enough for the embedding model to read at once (here 200 to 1500 characters). Shelf notes are split into chunks, each embedded separately and prefixed with its note name and nearest heading. See `shelf.md`.

**Shelf** — the notes, ideas and writing you keep in `my_work/` so the pipeline can compare new papers with your own work. Add and remove items at any time. See `shelf.md`.

**Nearest neighbour** — the stored item whose vector is closest (highest cosine similarity) to a given vector. For the shelf, a paper's nearest neighbour is the single shelf chunk most like it; only that one counts, so a big shelf does not dilute the signal. See `shelf.md`.

**Backend** — the thing that actually runs the language model: `ollama` (a model on your own machine, `local = True`), `claude-code` (your Claude Code login, `local = False`) or `none`; choose with `rpipe model set-backend`. Each backend has a `name` and a `local` flag; the name is stored in each summary's provenance and shown in the digest footer. See `llm.md`.

**Usage limit** — the cap a subscription plan puts on how much you can use a model in a period. When a backend reports it, the summarize stage stops without counting a failure, and the remaining papers wait for the next run. See `summaries.md`.

**Headless mode** — running an interactive tool without its screen: `claude -p` takes the prompt, prints one answer and exits, so a timer can use it. See `backends.md`.

**Subscription CLI** — a command-line AI tool (such as Claude Code) that uses your paid plan login instead of a pay-per-call API key. See `backends.md`.

**Plug-in** — an installed Python package that adds a paper source or a model backend to the pipeline without changing its code. List them with `rpipe plugins`. See `plugins.md`.

**Entry point** — a line in a package's `pyproject.toml` that registers a name in a named group (for example `research_pipeline.sources`), so another program can find and load it after the package is installed. See `plugins.md`.

**Abstracts-only mode** — running with the `none` backend: no summary model is used, so the digest shows each paper's abstract instead of a summary. The health line says `summaries off (abstracts-only mode)`. See `backends.md`.

**Unified memory** — Apple Silicon's single memory pool shared by CPU and GPU. rpipe counts 66% of it as GPU memory. See `setup.md`.

**Pull (a model)** — downloading a model's files into Ollama so it can run locally (`ollama pull NAME`). `rpipe model setup` offers to do it for you, after a yes/no, and shows download progress. See `setup.md`.

**Walkthrough** — the guided, step-by-step prompts of `rpipe model setup` (also the first step of `rpipe init`): look at your computer, recommend a backend, set it up, test it. See `setup.md`.

**Backlog** — papers that were listed in an earlier digest but never got a summary (the model failed that night, or summaries did not exist yet). `rpipe summarize --backlog` works through them newest first, a few at a time. See `running.md`.

**Effort** — a setting (`low`, `medium`, `high`) telling Claude how much to think before it answers. Lower is faster and cheaper but can be shallower. rpipe passes it to Claude Code as `--effort` and defaults to `low`; change it with `rpipe model set-effort`. See `backends.md`.

**Thinking tokens** — the words (counted in tokens) a model writes to itself while working out an answer, before the answer you see. You do not see them, but you wait for them and they use up your subscription allowance. Without a limit, Haiku wrote about 13,000 of them for one critique. See Effort.

**LaTeXML** — the tool arXiv uses to turn a paper's LaTeX source into its HTML version. It writes each formula as MathML plus the original TeX (an `<annotation>` and an `alttext` attribute), and marks macros it does not understand with `ltx_ERROR` spans. Our extractor picks one copy of each formula and deletes those spans. See `fulltext.md`.

**MathML** — the XML vocabulary for maths on web pages (`<mi>x</mi><mo>=</mo>...`); its visible text is the Unicode symbols (`α`, `∑`). Not to be confused with the TeX source stored beside it.

**Refetch** — re-downloading and re-parsing a paper's full text and replacing the stored copy: `rpipe summarize ID... --refetch`. See `fulltext.md`.

**Token** — the small piece of text a language model reads and writes at a time: a short word or part of a longer one (roughly three-quarters of an English word). Context windows, prompt budgets and usage limits are all counted in tokens. See `llm.md`.

**Prompt / system prompt** — the text sent to a language model to tell it what to do. The system prompt is the fixed instructions (for us, "summarize this paper and cite sections"); the rest is the material it works on, such as the paper. In our layout the task (summarise or critique) comes after the paper. See `summaries.md`.

**Pydantic** — a Python library for describing the shape of data (which fields, what types) and checking it. We use it for the profile, the config and the model's JSON answers, so bad data is rejected with a clear error. See `llm.md`.

**sentence-transformers** — the Python library that runs the embedding model on your computer. The default model is `BAAI/bge-small-en-v1.5` (config field `embedding_model`), which turns text into 384 numbers. See `running.md`.

**RSS** — a standard format for a website to publish a list of new items that programs can read. The config model keeps a reserved `rss_feeds` setting for it, but there is no RSS source yet and the default config no longer lists it. See `vault-and-config.md`.

**MCP (Model Context Protocol)** — a standard way to plug extra tool servers into an AI tool. The Claude Code backend runs with `--strict-mcp-config` so none are loaded. See `backends.md`.

**YAML** — a plain-text file format for settings, written as `name: value` lines and `- item` lists. `profile.yaml` and `config.yaml` use it; the `rpipe` commands edit them for you. See `vault-and-config.md`.

**Inference** — using a trained model to produce an answer, as opposed to training it. Every summary the pipeline writes is one inference run on your machine or through your subscription. See `llm.md`.

**Dedupe (deduplicate)** — recognising that two records are the same paper and keeping one. We do it with the canonical id. See `data-model.md`.

**Cache** — a saved answer kept so work need not be repeated, and safe to delete because it can be rebuilt. For example `cache/llm_fit.json` remembers which model and context fit. See `vault-and-config.md`.

**Fixture** — a small fixed sample input kept for tests (for example a saved paper page), so tests run offline and give the same result every time. See `running.md`.

**Progress event** — a small record saying what a stage is doing: `start`, `step` (one item finished, with `current` of `total` and the time so far), `note` (a message while waiting) or `done` (with its outcome). Stages create these and hand them to a reporter; they never print. See `progress.py` in the module map of `README.md`.

**TTY** — a "teletype": a real interactive terminal. A program can ask whether its output is a TTY; if so it may redraw lines in place with colours, otherwise (a pipe, a file, the systemd journal) it should write plain lines. `make_reporter()` uses this to choose the display. See `running.md`, Progress.

**stderr** — the second output channel of a program, meant for messages about what it is doing rather than its results. Progress goes there so that stdout (the results) stays clean when piped. See `running.md`, Progress.

**Heartbeat** — a "still waiting: ..." line printed every 30 seconds of silence while a long step runs, so a slow model call is visibly alive and not frozen. See `running.md`, Progress.

**AST (abstract syntax tree)** — the parsed structure of a Python file: every import, function and expression as a node in a tree. Tests read source files with Python's `ast` module to check what they import, without running them. See `shelf.md`, What never leaves your machine.

**Boundary test** — a test that enforces a rule about the architecture rather than a behaviour, for example "no module that talks to a model may import the private shelf". It fails the build if the rule is broken. See `shelf.md`, What never leaves your machine.

**Judge** — a model asked to grade or write checks for another model's work. Here it is Claude Code: it drafts key facts for a paper and says, fact by fact, whether a summary covered, missed or contradicted each. See `evaluation.md`, The judge.

**Self-graded** — a grade where the judge is the same model that produced the thing graded. It tends to share the author's blind spots, so such grades are flagged as less trustworthy. See `evaluation.md`, The judge.

**Answer key (gold set)** — the small set of papers with hand-checked key facts that summaries are measured against. "Answer key" and "gold set" are two names for the same thing. See `evaluation.md`.

**Setup (eval)** — the record of which backend, model, context size, prompt version and judge produced an eval result, stored in the eval file so two runs can be compared fairly. See `evaluation.md`, Automatic grading.

**Pass bar (eval)** — the rule for a judged run versus the previous one: facts covered drop by at most 1, contradictions and unverified citations do not rise. See `evaluation.md`, Comparing runs.

**Unreviewed (gold paper)** — a gold paper whose key facts Claude drafted and saved with `rpipe gold auto` without a person checking them. The report says so, because the score then compares runs with each other instead of proving accuracy. See `evaluation.md`, Filling the set with no questions.

**num_predict** — the Ollama option that caps how many tokens the model may write in one answer. We send 4096 with every call so a runaway answer cannot stall a night's run. See `llm.md`.

**CPU vector instructions (AVX2, AVX512, NEON)** — extra processor instructions that do the same arithmetic on many numbers at once. Model software uses them to run much faster on a CPU; `rpipe model scan` lists which ones your CPU has (NEON is the Apple/ARM equivalent). See `hardware.md`, How it works.

**Systemd drop-in file** — a small `.conf` file in a folder like `/etc/systemd/system/ollama.service.d/` that adds to or overrides a service's settings without editing the service file itself. Ollama's `OLLAMA_*` settings are often kept in one. See `hardware.md`, How it works.

**Quantization** — storing a model's numbers with fewer bits (for example 4 instead of 16) so the file and its memory use shrink a lot, at a small cost in quality. Names like `Q4_K_M` or `UD-IQ3_S` say how it was done. See `hardware.md`.

**Mixture of experts (MoE) and active parameters** — a model made of many specialist sub-networks where each token uses only a few. "30B total, 3B active" means all 30 billion must fit in memory, but only 3 billion do work per token, so it runs about as fast as a small model. See `hardware.md`.

**Trained context** — the longest text (in tokens) a model was trained to handle. Asking for more than this makes quality drop, so the catalog records it and the recommender never goes past it. See `hardware.md`.

**Tokens per second** — how fast a model works: how many tokens (small pieces of words) it processes each second. Reading speed (prompt tokens per second) is usually much higher than writing speed (generated tokens per second), because reading can be done in parallel and writing one token depends on the previous one. See `hardware.md`.

**Prompt reading vs writing** — the two phases of a model call. Reading (also "prompt evaluation") takes in the paper; writing (also "generation") produces the summary. Ollama times them separately, which is what `rpipe model bench` uses.

**Benchmark** — a short, repeatable test that measures speed on your own machine, so time estimates come from a measurement and not a guess. See `rpipe model bench` in `hardware.md`.

**Flash attention** — a faster, more memory-frugal way of computing the "attention" step where a model looks back over the text. Ollama turns it on with `OLLAMA_FLASH_ATTENTION=1`; it is needed before the KV cache can be stored in smaller numbers. See `hardware.md`, Server settings.

**KV cache type (`q8_0`)** — how the KV cache numbers are stored. The default keeps 16 bits per number; `q8_0` keeps 8, so the cache takes about half the memory and a longer context fits, with a very small quality cost. Set with `OLLAMA_KV_CACHE_TYPE=q8_0`. See `hardware.md`, Server settings.

**Parallel slots** — how many requests Ollama serves at once (`OLLAMA_NUM_PARALLEL`). Each slot reserves its own context memory, so more slots mean a shorter context for each. `rpipe` sends one paper at a time, so 1 is enough. See `hardware.md`, Server settings.

**sudo** — a Linux command that runs another command with administrator rights after asking for your password. `rpipe model tune` runs its commands through `sudo` in your own terminal and never sees the password. See `hardware.md`, Server settings.

**Band (size class)** — a rough label for how big a computer is for running models, from its total GPU memory: `cpu` (under 4 GB), `small` (under 8 GB), `mid` (up to about 16 GB), `high` (up to about 24 GB), `very_large` (more). Each catalog model lists the bands it suits, and the recommender puts those models first. See "Recommendations" in `hardware.md`.

**Spill / offload to RAM** — running a model that does not fit in GPU memory with part of it in the computer's ordinary memory (RAM). The CPU then does that part, which is slower. For a mixture-of-experts model only a few experts work per token, so the slowdown is modest; for a dense model it is severe, so rpipe only *recommends* mixture-of-experts models for running spilled every night. The nightly rerun stage is different: it spills any model, dense ones too, but only for the few papers whose text was cut, because a slow paper or two is acceptable overnight. See "Recommendations" in `hardware.md` and "Reading cut papers whole: reruns" in `running.md`.

**GB vs GiB** — two units for memory. A GB is 1,000,000,000 bytes; a GiB is 2^30 = 1,073,741,824 bytes, about 7% more. Download sizes are usually in GB, while `nvidia-smi` and Ollama report GPU memory in GiB (often labelled "GB" anyway). The recommender converts download sizes to GiB before comparing them with GPU memory. See "Recommendations" in `hardware.md`.

**Pass-through proxy** — a small program that sits between `rpipe` and Ollama and passes every request on unchanged, for example to count tokens. `rpipe` talks to the proxy's port, Ollama listens on another, but there is only one server. `rpipe model scan` recognises it when both addresses list the same models (same names and digests). See "When rpipe talks to Ollama through a proxy" in `hardware.md`.

**Digest (of a model)** — a fingerprint of a model's files, a long code computed from their contents. Two models with the same name but different files have different digests, so comparing digests tells whether two servers hold exactly the same model. See `hardware.md`.

**`needed_ctx`** — on a summary whose core text was cut (`body_cut`), the context window in tokens that would have fit that core text whole: its characters divided by 3, plus the 4,000 reserved, rounded up to a multiple of 1024. 300,000 characters of core text gives 104,448. Empty when nothing was cut. Used to find papers worth reading again at a bigger context. See `summaries.md`.

**GPU share (`gpu_share`)** — how much of the model sat in GPU memory while it wrote a summary (and, with `summarize_mode: auto`, at the start of a run, to choose the mode), from 0 to 1 (`1.0` = all of it; `0.93` = 7% spilled to ordinary RAM). Read from Ollama's list of loaded models and stored in the summary's provenance; empty when unknown or for a cloud backend. See `summaries.md`.

**Rerun (of a cut paper)** — reading a paper whose core text was cut (`body_cut`) a second time, at a context big enough for all of it, with part of the model spilled to RAM. Slow, so it happens after the normal work and must finish by the morning deadline. Only with the local Ollama backend. A failed rerun keeps the old summary. The health line counts `rerun N` (read whole tonight) and `rerun waiting N` (left for a later night). Not the same as a same-day re-run of the whole pipeline. See "Reading cut papers whole: reruns" in `running.md` and "The morning deadline" in `scheduling.md`.

**Bucket (of reruns)** — a group of cut papers reread at the same context window. Each paper's needed context is rounded up to a multiple of 8,192 tokens; papers that land on the same number share one bucket, so the model is loaded once for all of them instead of once per paper (each load takes tens of seconds). Papers needing 61,440 and 63,488 tokens both go in the 65,536 bucket. See "Reading cut papers whole: reruns" in `running.md`.

**Morning deadline (`night_deadline`)** — the clock time (default 07:00) by which reruns of cut papers must finish, or `off`. It never stops normal papers. If the next such time is more than 12 hours away there is no deadline and reruns are skipped. Set with `rpipe schedule set-deadline`. See `scheduling.md`.

**Call time limit** — how long one request to the local model may take before rpipe gives up on it (a timeout). Normally 600 seconds; on a machine `rpipe model bench` measured as slow it is raised to 1.5 times the time needed to read a full context and write the longest allowed answer at the measured speeds. See "The time limit for one call" in `llm.md`.

**Notes (reading in parts)** — what the model writes about one part of a long paper: a short list of claims, each with the section titles it comes from (the `Notes` schema). The summary and critique are then written from the notes instead of the paper. See "Reading a paper in parts" in `summaries.md`.

**Task splitting** — reading a long paper as two jobs instead of one: notes on the front matter (abstract, introduction, conclusion: what is claimed) and notes on the body (method and results: how well it is backed), then one step that combines the notes. Stored as `read_mode: split`. See `summaries.md`.

**Map-reduce** — a general way to handle input too big for one pass: cut it into pieces, do the same job on each piece separately ("map"), then merge the pieces' results into one ("reduce"). Here the pieces are size-based chunks of a paper, the job is taking notes, and the merge is condensing the notes and writing the summary from them. The last resort for a paper that does not fit any other way: used only when a whole read would cut the core text and no rerun could read it whole (see *Fallback threshold*). Stored as `read_mode: map_reduce`; the health line counts these papers as `in parts N`. See `summaries.md`.

**Front matter** — the parts of a paper that state what it claims: abstract, introduction, conclusion (also discussion, limitations, future work, summary). When a paper is read in parts, the front matter is one group and everything else (method, experiments, results) is the body. Numbered subsections go with their parent, whatever their heading says (`4.3 Discussion` under `4 Experiments` is body). See "Reading a paper in parts" in `summaries.md`.

**Fallback threshold (`fallback_above`)** — the context size, in tokens, above which a paper cut in a whole read is read in parts (map-reduce) instead. Set to the model's context cap when reruns can happen (so only papers no rerun could read whole are split), `0` when there are no reruns (every cut paper is split), "none" for reruns themselves (never split), and `0` for `rpipe eval run` (an eval never waits for a rerun). See "Which way a paper is read" in `summaries.md`.

**Summarize mode (`summarize_mode`)** — the setting that says how papers are read: `whole` (the model reads the whole paper in one go), `split` (notes on the front matter and the body, then combined; see *Task splitting*) or `auto` (the default: split when the model is not fully in GPU memory, runs on the CPU, or has a context below 26,624 tokens; whole otherwise and for Claude Code). Change it with `rpipe model set-mode`; `rpipe eval run --mode` overrides it for one eval without saving. See "Choosing the mode" in `summaries.md` and "Comparing reading modes" in `evaluation.md`.

**Context cap (model cap)** — the biggest context window a model can be run at here: the context it was trained for, as Ollama reports it, but at most `CONTEXT_CEILING` (131,072 tokens). `spill.model_cap` reads it. A paper needing more than this can never be read whole.

**Condensing notes** — when the notes from all parts of a paper are too long to send in one call, they are sent back to the model in batches to be merged into fewer notes, keeping every cited section. At most 3 rounds; if they still do not fit, every note is shortened by the same share and the summary is marked `body_cut`. See `summaries.md`.

**CUDA context** — the working state a program sets up on an NVIDIA graphics card the first time it uses it (through CUDA, NVIDIA's GPU programming toolkit): loaded GPU code, memory bookkeeping and the like. It takes a few hundred MB of GPU memory and stays until the program exits; it cannot be handed back earlier. This is why rpipe runs its embedding model on the CPU: a context created for embedding would hold GPU memory the summarizing model needs for the rest of the run. See "Why it's built this way" in `triage.md`.

**Request list** — the `requests` table: work the app asks the pipeline to do later, such as "summarise this paper". Each request has a kind, details (payload) and a status (`pending`, `running`, `done`, `failed`). Asking for the same thing twice while it is still waiting gives back the first request. See "The request list" in `data-model.md`.

**Draining (the request list)** — working through the waiting requests until none is left to do; the nightly `rpipe run` and `rpipe requests run` do it. See "The request list" in `running.md`.

**Retry rule (requests)** — a request gets 3 tries (`requests.tries` counts them). A failed try puts it back to `pending`; the third failure leaves it `failed` for good (ask again to queue a fresh one). A usage-limit stop gives the try back, because it was not the request's fault; a try cut short by a crash or Ctrl-C still counts. See "The request list" in `running.md`.

**Canonical JSON** — JSON written one fixed way (here with keys sorted), so two copies of the same data are always the same text and can be compared directly. See `data-model.md`.

**Paper-id normalisation** — turning the ways people write an arXiv id (`2611.25005v2`, `arXiv:2611.25005`, an arxiv.org link) into the bare id the database stores (`2611.25005`). Done by `ids.normalise_paper_id`. See "Paper-id spellings" in `data-model.md`.

**Data object (view)** — what `library_api.py` hands back instead of printed text: a Pydantic object with named, typed fields (`PaperView` has `title`, `vote`, `saved`, `summary`, ...) that turns into JSON with one call. The terminal commands print it; a future web or desktop app can lay out the same object its own way. See "The app's door" in `architecture.md`.
