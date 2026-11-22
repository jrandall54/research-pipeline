# Research Pipeline

A nightly pipeline that collects new computer-science, security and AI papers, ranks them against your own profile (topics, seed papers, authors, keywords, your votes), summarises and critiques the best ones, and writes a digest into a folder of your own files (the vault). Every summary claim cites the paper section it came from, and code checks the citations. You set everything up with `rpipe` commands and guided prompts; you never edit a file by hand.

## What you need

Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and one of three ways to get summaries:

| Tier | You need | What you get |
|------|----------|--------------|
| Ollama | [Ollama](https://ollama.com) running locally, plus a model (a GPU helps) | Cited summaries and critiques, fully local and private |
| Claude Code | The `claude` CLI, logged in | Cited summaries written by Claude, no GPU needed. Paper text is sent to Anthropic, and runs use your plan's usage quota |
| None | Nothing extra | Ranked digest with abstracts only |

`rpipe model setup` checks your computer, recommends a tier and tests it. `rpipe init` runs it as its first step.

## Install

```
uv tool install "research-pipeline[embed] @ git+https://github.com/jrandall54/research-pipeline.git"
```

Add `serve` (`research-pipeline[embed,serve]`) if you want to browse and vote from your phone's browser. The `embed` extra pulls in PyTorch, so the first install is large (gigabytes).

## First run

```
rpipe init              # creates your vault, then guides you through backend, topics, categories and seed papers
rpipe run               # collect, rank, summarise, write today's digest
rpipe schedule install  # Linux: run it every night by itself
```

On macOS and Windows, `rpipe schedule install` is not available: run `rpipe run` from your own scheduler (launchd, Task Scheduler, cron).

Your digest appears in the vault under `digests/` (default vault `~/research-vault`).

## Daily use

- Read the digest, then vote: `rpipe vote ID up` or `rpipe vote ID down`. `ID` is the `id:` under each item.
- Keep a paper with `rpipe save ID`.
- Step through unvoted papers in the terminal with `rpipe review`.
- Browse and vote from your phone with `rpipe serve` (needs the `serve` extra).

Your votes shape the next night's ranking. `rpipe --help` lists every command; `rpipe sources list` shows paper sources and `rpipe plugins` shows installed plug-ins.

## Learn how it works

The guide in [docs/guide/README.md](docs/guide/README.md) explains every stage in plain words. [docs/guide/running.md](docs/guide/running.md) covers installing, every command and troubleshooting.

## Licence

MIT, see [LICENSE](LICENSE). If the optional Marker PDF converter is ever added it is GPL-licensed and would be an opt-in extra.
