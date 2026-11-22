# Research Pipeline

Nightly pipeline that collects CS/security/AI papers, ranks them against your profile, summarises and critiques the best ones with a local model (every claim cites the paper section it came from), and writes a digest.

Start with [docs/guide/README.md](docs/guide/README.md).

Reading order: [running.md](docs/guide/running.md) (install and commands), [profile.md](docs/guide/profile.md) (set up your profile with `rpipe profile ...`, no file editing), then the Phase 2 pages (Phase 3 adds the daily habit: voting, scheduling and browsing): [llm.md](docs/guide/llm.md) (the local model), [fulltext.md](docs/guide/fulltext.md) (paper text), [summaries.md](docs/guide/summaries.md) (cited summaries) and [evaluation.md](docs/guide/evaluation.md) (the accuracy check), then [scheduling.md](docs/guide/scheduling.md) (run it every night), [browsing.md](docs/guide/browsing.md) (browse and vote from your phone) and the rest of the guide.

Daily use: `rpipe schedule install` once, then vote with `rpipe vote ID up|down` or `rpipe save ID` (or from your phone with `rpipe serve`); `rpipe --help` lists every command.
