# Research Pipeline — Guide

## What it does

Every night the pipeline collects new computer-science, security and AI papers, scores them against your personal profile, and writes a short digest of the best ones into your vault (a folder of your own files, kept outside this repo). Each paper in the digest says why it was picked and carries a short summary and critique written by a local model (Ollama) from the paper's full text, with every claim citing the section it came from; code checks the citations and flags any that do not exist. If a paper has no full text, the summary uses the abstract and says so; if Ollama is down, the digest is still written with abstracts. You can check the summaries against your own accuracy test: at least 9 of your 10 gold papers graded accurate with `rpipe eval` (see [evaluation.md](evaluation.md)). You can vote on and save digest items (`rpipe vote`, `rpipe save`, or buttons on your phone via `rpipe serve`) and the pipeline learns from them, and `rpipe schedule install` runs it every night by itself ([scheduling.md](scheduling.md), [browsing.md](browsing.md)). Your own notes go on the shelf (`rpipe shelf add`, [shelf.md](shelf.md)) and papers close to them rank higher.

## How it works

```
 arXiv API --------+
                   +--> collect --> triage --> summarize --> digest --> digests/YYYY-MM-DD.md
 HF Daily Papers --+       |          ^   |       |   ^          |
                           v          |   v       v   |          v
                       papers: new   profile   Ollama + arXiv   papers: in_digest
                                     + seeds   HTML full text   + runs row (counts, errors)
                                               (skipped if Ollama is down)
```

One command, `rpipe run`, executes the whole flow and records it. Each stage in a paragraph (the sixth is how you use it day to day, the seventh is your own material):

1. **Collect** ([sources.md](sources.md)). Asks arXiv (newest papers in your categories, politely, one request per 3 seconds) and Hugging Face Daily Papers (community-upvoted papers with code links) for recent papers and stores each as a row with status `new`. A paper seen before is not duplicated; a higher version updates it. If one source is down, the other still runs and the failure is recorded.
2. **Triage** ([triage.md](triage.md)). Turns each new paper into an embedding (numbers capturing its meaning), compares it with your seed papers and topics, adds small boosts for upvotes, followed authors and keywords, nudges scores toward papers resembling your own votes and saves (the implicit profile, with old votes fading by a half-life), keeps the top N as `triaged` and marks the rest `skipped` (still searchable). Every score comes with plain-language reasons.
3. **Summarize** ([fulltext.md](fulltext.md), [summaries.md](summaries.md), [llm.md](llm.md)). For each kept paper, downloads the arXiv HTML version and strips hidden text (a defence against prompt injection), then asks the local model for a summary and a critique. Each claim must cite paper sections; code checks the citations against the real section titles. The paper moves `triaged` to `fetched` to `summarized`. No HTML version means an abstract-only summary; Ollama being down means this stage is skipped and the cause is shown in the health line. One paper failing never stops the others.
4. **Digest** ([digest.md](digest.md)). Writes the kept papers, best first, with their summaries, as a markdown file with a one-line health summary on top, then marks them `in_digest`.
5. **The run record** ([running.md](running.md)). `run` also writes a row to the `runs` table with start and end times, counts per stage and any errors (list them with `rpipe runs`).
6. **The daily habit** ([scheduling.md](scheduling.md), [browsing.md](browsing.md)). A systemd timer (`rpipe schedule install`) runs `rpipe run` each night; you read the digest, or browse the vault from your phone with `rpipe serve`, or step through unvoted papers in the terminal with `rpipe review` ([review.md](review.md)), and vote or save papers. Those votes feed back into the next triage.
7. **The shelf** ([shelf.md](shelf.md)). Notes and files you add with `rpipe shelf add` live in `my_work/`, are embedded locally, and give papers close to them a ranking boost in triage.

Underneath all of this are the typed objects and the SQLite tables ([data-model.md](data-model.md)) and the vault folder with its two settings files ([vault-and-config.md](vault-and-config.md)).

## Why it's built this way

The repo holds code and defaults only. Everything personal lives in the vault, so the engine can be shared or reinstalled without leaking your data. Each stage is a small function with plain inputs and outputs, a failing source never stops the others, and nothing is thrown away: skipped papers stay searchable and every ranking decision can be explained from stored numbers. Real network access and the embedding model are created in one place, so tests run the real pipeline offline.

## Reading order

New to the project? Read in this order:

1. [data-model.md](data-model.md): the objects and statuses that flow through the pipeline
2. [vault-and-config.md](vault-and-config.md): the vault layout and the config and profile files
3. [sources.md](sources.md): where papers come from and how failures are isolated
4. [triage.md](triage.md): how papers are scored and why
5. [digest.md](digest.md): the digest format
6. [running.md](running.md): installing, the commands, a full real run, troubleshooting
7. [profile.md](profile.md): the `rpipe profile` commands and how they edit safely
8. [llm.md](llm.md): the local model client, automatic model and context selection, and the `rpipe model` commands
9. [fulltext.md](fulltext.md): fetching arXiv HTML full text and stripping hidden text
10. [summaries.md](summaries.md): cited summaries and critiques, the citation check and the context budget
11. [evaluation.md](evaluation.md): the gold set, the `rpipe gold` commands and the `rpipe eval` accuracy check
12. [scheduling.md](scheduling.md): `rpipe schedule` and the nightly systemd timer
13. [browsing.md](browsing.md): `rpipe serve`, browsing and voting from your phone over Tailscale
14. [review.md](review.md): `rpipe review`, reading and voting on the digest in the terminal, and the review-later list
15. [shelf.md](shelf.md): `rpipe shelf`, your notes in `my_work/`: how they are chunked, embedded, added and removed
16. [plugins.md](plugins.md): plug-ins, entry points, `rpipe plugins` and `rpipe sources`
17. [backends.md](backends.md): the summary backends (Ollama, Claude Code, none) and how they differ
18. [setup.md](setup.md): the setup walkthrough (`rpipe model setup`, also the first step of `rpipe init`): how your machine is checked, a backend recommended, chosen and tested
19. [hardware.md](hardware.md): `rpipe model scan` and how rpipe reads your machine (CPU, memory, disk, every GPU, the Ollama server's settings); later sections cover recommending, tuning and benchmarking
20. [glossary.md](glossary.md): plain-word definitions of terms (keep it open while you read)

Just want to use it? Follow the quickstart in the top-level `README.md` (install, `rpipe init`, `rpipe run`, `rpipe schedule install`), then read [running.md](running.md), then [profile.md](profile.md) for setting up your profile.

## Where the code is

`src/research_pipeline/` (tests are in `tests/` and run offline with `uv run pytest -q`):

- `cli.py`: every `rpipe` command.
- `vault.py`: creates and locates the vault folders and files.
- `profile_edit.py`: changes `profile.yaml` and `config.yaml` safely, keeping your comments.
- `seed_input.py`: turns a link, id or title you typed into candidate seed papers.
- `onboarding.py`: asks the model to propose topics and seed papers from a description; you approve each.
- `db.py`: the SQLite database (tables, upgrades, reads and writes).
- `models.py`: the data shapes (paper, profile, config) and their validation.
- `summary_models.py`: the data shapes of a stored summary and critique.
- `sources/`: the paper sources (`base.py` common interface, `arxiv.py`, `hf_daily.py`).
- `collect.py`: runs every enabled source and stores new papers.
- `seeds.py`: looks up your seed papers.
- `embed.py`: turns text into embeddings.
- `rank.py`: the scoring formula.
- `triage.py`: ranks new papers and keeps the top N.
- `digest.py`: writes the markdown digest.
- `llm.py`: the Ollama client.
- `ollama_fit.py`: finds the largest model and context that fit your GPU.
- `backends/`: summary backends other than Ollama (`claude_code.py`, the Claude Code CLI).
- `fulltext.py`: fetches and cleans a paper's arXiv HTML full text.
- `prompts.py`: the summary and critique prompts.
- `summarize.py`: summarizes and critiques papers and checks citations.
- `gold.py`, `evaluate.py`: the gold set and `rpipe eval`.
- `schedule.py`: the nightly systemd timer.
- `serve.py`: browsing and voting from your phone (Datasette).
- `review.py`: the terminal review loop.
- `shelf.py`: your own notes and writing, indexed for ranking.
- `registry.py`: built-in and installed plug-ins.
- `machine.py`: looks at your computer and recommends a backend.
- `scan.py`: the probes behind `rpipe model scan` (CPU, memory, disk, every GPU, Ollama server settings).
- `run_lock.py`: stops two `rpipe run`s working on one vault at once.
- `progress.py`: progress events (data objects) that stages emit instead of printing; no package imports, so it can be shared.
- `progress_render.py`: turns progress events into stderr output (a live spinner on a terminal, plain lines otherwise); see [running.md](running.md).
- `categories.py`: the list of real arXiv category codes and the did-you-mean check ([profile.md](profile.md)).
- `defaults/`: the packaged starting `profile.yaml`, `config.yaml` and `ollama_models.yaml`.
