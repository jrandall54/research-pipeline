# Glossary

**Canonical id** — the single identifier used to recognise a paper across sources: the arXiv id if it has one, otherwise the DOI, otherwise a hash of its URL. Two records with the same canonical id are the same paper.

**Status** — where a paper is in its lifecycle (`new`, `triaged`, `skipped`, `in_digest`, ...). See `data-model.md`.

**Vault** — the folder (default `~/research-vault/`) holding everything personal: config, profile, database, digests. Kept outside the repo.

**arXiv category** — the subject tag every arXiv paper carries, such as `cs.CR` (security) or `cs.LG` (machine learning). Only papers in your configured categories are collected; the single value `all` means every computer-science category (`cs.*`; not physics, maths or stat). See "Choosing categories" in `profile.md`.

**Seed** — an arXiv id of a paper you like; new papers are ranked by similarity to your seeds. You can add one from an arXiv link, an id or a title search (see `profile.md`).

**FTS5** — SQLite's full-text search module. It builds an index of the words in a table so you can find rows by word quickly (`MATCH 'attention'`) and rank them, instead of scanning every row.

**Rowid** — SQLite's internal integer row number. We give `papers` an explicit one so the search index can point at the same row reliably.

**WAL (write-ahead log)** — a SQLite journal mode where changes are appended to a log before being merged into the main file. Readers do not block the writer and crashes are safer.

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

**Grounding** — tying each statement a model makes to the source text it came from, so a reader (or code) can check it. Our summaries are grounded by citing paper sections. See `summaries.md`.

**Citation check** — code comparing every section title a summary claim cites with the paper's real section titles. A title that matches none triggers one retry; if it is still wrong the claim is flagged "unverified". It proves the section exists, not that it says what the claim says. See `summaries.md`.

**Truncation** — cutting text so it fits the model's context window. We drop references and appendices first, then trim every section by the same fraction, and mark the summary `truncated`. See `summaries.md`.

**abstract-only summary** — a summary written from the abstract because the paper has no arXiv HTML full text. The digest says so under the summary and the health line counts them as `abstract-only N`. See `digest.md`.

**unverified claim** — a summary or critique claim that cites a section title the paper does not have; the digest prefixes it with `⚠ unverified`. See `summaries.md`.

**Gold set** — a small collection (we aim for 10) of papers you know well, each with a list of key facts a correct summary must contain. It is the answer key we grade the model against. See `evaluation.md`.

**Ground truth** — the known-correct answer that a measurement is compared with. Here it is your key facts: you wrote them from your own understanding of the paper, not from the model's output. See `evaluation.md`.

**Accuracy check** — the human grading step of `rpipe eval`: for each gold paper you read the model's summary beside your key facts and answer yes or no to "is this accurate?". Done by a person on purpose, because a model judging itself shares its own blind spots. See `evaluation.md`.

**Pass mark** — the share of gold papers that must be graded accurate for the model to pass the accuracy check: 90% (`PASS_MARK`), so 9 of 10. Ungraded papers count as not accurate. See `evaluation.md`.

**VRAM** — the memory on your graphics card. Models run fast when everything they need is in VRAM. See "Choosing the model and context automatically" in `llm.md`.

**GPU offload** — how much of a model Ollama keeps on the graphics card versus the CPU. "Fully offloaded" means all of it is on the GPU; a partial offload spills the rest to the CPU and is many times slower. See `llm.md`.

**KV cache** — the working memory a model builds while reading the prompt (a "key" and "value" for each token so far). It grows with the context window, which is why a bigger context can stop a model fitting in VRAM. See `llm.md`.

**`auto`** — the default for `llm_model` and `llm_num_ctx`: the pipeline picks the largest installed model that fits fully on the GPU and the largest context it can hold, caches the answer, and re-checks with `rpipe model fit`. See `llm.md`.

**Probe** — a test load: we load a model at a given context with an empty prompt and check whether it sits fully on the GPU, then unload it. See `llm.md`.
