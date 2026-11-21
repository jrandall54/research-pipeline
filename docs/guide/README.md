# Research Pipeline — Guide

> Stub from Task 1; the final version is written in Task 9.

## What it does

Every night the pipeline collects new computer-science, security and AI papers, scores them against your personal profile, and writes a short digest of the best ones into your vault (a folder of your own files, kept outside this repo).

## How it works

```
collect  ->  triage  ->  digest
```

1. **collect** asks sources (arXiv, Hugging Face Daily Papers) for new papers and stores them with status `new`. Papers already stored are skipped unless a higher version appears.
2. **triage** scores each `new` paper against your profile, keeps the top N as `triaged` and marks the rest `skipped` (still searchable).
3. **digest** writes a markdown file listing the kept papers, each with a short reason for its rank, and marks them `in_digest`.

## Why it's built this way

The repo holds code and defaults only. Everything personal lives in the vault, so the engine can be shared or reinstalled without leaking your data.

## Reading order

1. `data-model.md` — the objects and statuses that flow through the pipeline
2. `vault-and-config.md` — the vault layout and config files
3. `sources.md` — where papers come from and how failures are isolated
4. `triage.md` — how papers are scored and why
5. `digest.md` — the digest format
6. `running.md` — commands and a full run walkthrough
7. `glossary.md` — plain-word definitions of terms

Pages not yet written are added by later tasks.

## Where the code is

`src/research_pipeline/` (see each page for the specific files).
