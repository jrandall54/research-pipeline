# Research Pipeline — Guide

## What it does

Every night the pipeline collects new computer-science, security and AI papers, scores them against your personal profile, and writes a short digest of the best ones into your vault (a folder of your own files, kept outside this repo). Each paper in the digest says why it was picked. This is Phase 1: it works from titles and abstracts only. Later phases add full-text reading, summaries, voting and note-linking.

## How it works

```
 arXiv API --------+
                   +--> collect --> triage --> digest --> digests/YYYY-MM-DD.md
 HF Daily Papers --+       |          ^   |        |
                           v          |   v        v
                       papers: new   profile   papers: in_digest
                                     + seeds    + runs row (counts, errors)
```

One command, `rpipe run`, executes the whole flow and records it. Each stage in a paragraph:

1. **Collect** ([sources.md](sources.md)). Asks arXiv (newest papers in your categories, politely, one request per 3 seconds) and Hugging Face Daily Papers (community-upvoted papers with code links) for recent papers and stores each as a row with status `new`. A paper seen before is not duplicated; a higher version updates it. If one source is down, the other still runs and the failure is recorded.
2. **Triage** ([triage.md](triage.md)). Turns each new paper into an embedding (numbers capturing its meaning), compares it with your seed papers and topics, adds small boosts for upvotes, followed authors and keywords, keeps the top N as `triaged` and marks the rest `skipped` (still searchable). Every score comes with plain-language reasons.
3. **Digest** ([digest.md](digest.md)). Writes the kept papers, best first, as a markdown file with a one-line health summary on top, then marks them `in_digest`.
4. **The run record** ([running.md](running.md)). `run` also writes a row to the `runs` table with start and end times, counts per stage and any errors.

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
8. [llm.md](llm.md): the local model client and `rpipe model` commands
9. [fulltext.md](fulltext.md): fetching arXiv HTML full text and stripping hidden text
10. [summaries.md](summaries.md): cited summaries and critiques, the citation check and the context budget
11. [glossary.md](glossary.md): plain-word definitions of terms (keep it open while you read)

Just want to use it? Read [running.md](running.md) first, then [profile.md](profile.md) for setting up your profile.

## Where the code is

`src/research_pipeline/`: `cli.py` (commands), `vault.py`, `profile_edit.py`, `db.py`, `models.py`, `sources/`, `collect.py`, `seeds.py`, `embed.py`, `rank.py`, `triage.py`, `digest.py`, `llm.py`, `fulltext.py`, `prompts.py`, `summarize.py`. Tests are in `tests/` and run offline with `uv run pytest -q`.
