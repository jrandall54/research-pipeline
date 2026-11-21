# Triage: deciding which papers reach the digest

## What it does

Every night hundreds of new papers arrive with status `new`. Triage scores each one against your profile (topics, seed papers, followed authors, keywords), keeps the best **N** (default 15, `top_n` in `profile.yaml`) as `triaged`, and marks the rest `skipped`. Each kept paper also stores a **score** and a list of plain-language **reasons** ("closest seed: 2401.00001 (0.71)") that the digest later prints, so you can always see why something ranked where it did.

Skipped papers are not deleted. They stay in the database and in the full-text search index, so you can still find them by searching; they just do not appear in the digest.

## How it works

```
new papers --embed--> vectors --compare to--> seed/topic vectors --> score + boosts --> sort --> top N
```

1. **Load** all papers with status `new`.
2. **Embed** each paper's text (title plus abstract; just the title if there is no abstract). Vectors already stored for the same model are reused, never recomputed.
3. **Embed the profile**: the text of each seed paper and each topic string.
4. **Score** every paper (formula below) and write down the reasons.
5. **Sort** by score, highest first; ties break by paper id ascending so results are deterministic.
6. **Mark** the first N `triaged` (with score and reasons) and the rest `skipped` (also with score and reasons, useful when tuning), then commit.

### What is an embedding?

An **embedding** turns a piece of text into a list of numbers (a **vector**) so that texts with similar *meaning* get similar numbers. The model that does this was trained on huge amounts of text to place related ideas near each other.

A tiny 2-D intuition: pretend the model only has two dimensions, "how much is this about security" and "how much is this about machine learning".

| Text | security | ML |
| --- | --- | --- |
| "Fuzzing the Linux kernel" | 0.9 | 0.1 |
| "Adversarial attacks on neural networks" | 0.6 | 0.8 |
| "A new optimiser for deep learning" | 0.0 | 1.0 |

Plot these as arrows from the origin: the adversarial-attacks paper points between the other two, which matches its meaning (security *and* ML). Real models use hundreds of dimensions (our default uses 384) that are not human-labelled like this, but the idea is the same: **direction encodes meaning**.

### Cosine similarity

To compare two vectors we measure the angle between them: **cosine similarity**. Same direction gives 1.0, unrelated (at right angles) gives about 0, opposite gives -1. For vectors of length 1 it is just the dot product: multiply matching components and add.

Worked example, with a = (1, 0) and b = (0.6, 0.8) (both length 1, since 0.6² + 0.8² = 1):

```
cosine(a, b) = 1×0.6 + 0×0.8 = 0.6
```

Now c = (0, 1): cosine(a, c) = 0, cosine(b, c) = 0.8. So b is closer to c than to a. **L2 normalisation** is scaling a vector to length 1; our embedders do this so that the dot product *is* the cosine and every paper is compared on direction, not text length.

### Why embeddings rather than keyword search

Keyword search needs the same word. A paper on "defending language models against jailbreaks" shares no words with a seed on "adversarial robustness of LLMs", yet they are plainly related; embeddings see that. Embeddings also cope with synonyms, abbreviations and different phrasing. Where keywords still win: exact rare terms (a specific tool name, "eBPF", "CVE"), which embeddings can blur. Hence the small keyword **boost** on top of the similarity.

### The scoring formula

```
score = max over seeds & topics of cosine(paper, profile item)
      + 0.05 × ln(1 + hf_upvotes)
      + 0.1  × (number of followed authors on the paper)
      + min(0.2, 0.05 × (number of keywords found in title/abstract))
      + 0.0  (my_work similarity, Phase 4)
      − 0.0  (downvote penalty, Phase 3)
```

| Term | Weight | Why this size |
| --- | --- | --- |
| Similarity | 1.0 (cosine, about 0 to 1) | The main signal. A paper is judged by its *closest* seed or topic: being very close to one thing you like matters more than being vaguely near all of them. |
| HF upvotes | 0.05 × ln(1 + n) | ln compresses: 1 vote gives 0.035, 10 gives 0.12, 42 gives 0.19, 100 gives 0.23. Community interest helps, but a viral paper cannot beat a well-matched one on hype alone. |
| Followed author | 0.1 each | Equivalent to a clear jump in similarity; strong but not decisive. Case-insensitive exact name match (no fuzzy matching, so "Ada Lovelace Jr" does not match "Ada Lovelace"). |
| Keyword | 0.05 each, capped at 0.2 | Keywords are blunt (a word can appear in an unrelated paper), so each is a nudge and four of them are the most you can get. Each keyword counts once; matching is case-insensitive substring in title or abstract. |
| my_work | 0.0 (stub) | `TODO(phase4)`: similarity to your own notes. |
| Vote penalty | 0.0 (stub) | `TODO(phase3)`: penalty for topics you downvote. |

The weights are named constants at the top of `rank.py`.

### Reasons

Each paper gets reasons in this order: one similarity reason for the best match, formatted `closest seed: <id> (0.71)` or `topic: <topic> (0.64)`; then `HF upvotes: <n>` (if above 0); `followed author: <name>` per hit; `keyword: <kw>` per hit. With no seeds and no topics there is no similarity reason and the score comes from the boosts alone.

### Worked example

Profile: seed `2401.00001`, topic "adversarial robustness", followed author "Ada Lovelace", keywords "fuzzing" and "jailbreak", `top_n` 1.

**Paper A**: "Fuzzing LLM agents", by Ada Lovelace, 3 upvotes. Closest to seed `2401.00001` at 0.71; the topic scores 0.55.
- similarity 0.71
- upvotes 0.05 × ln(4) = 0.069
- followed author 0.10
- keyword "fuzzing" found, "jailbreak" absent: 0.05
- **score 0.929**; reasons: `closest seed: 2401.00001 (0.71)`, `HF upvotes: 3`, `followed author: Ada Lovelace`, `keyword: fuzzing`

**Paper B**: "Certified defences against jailbreak attacks", 42 upvotes. Closest to the topic at 0.64.
- similarity 0.64
- upvotes 0.05 × ln(43) = 0.188
- no followed author; keyword "jailbreak" found, "fuzzing" absent: 0.05
- **score 0.878**; reasons: `topic: adversarial robustness (0.64)`, `HF upvotes: 42`, `keyword: jailbreak`

A (0.929) beats B (0.878) despite far fewer upvotes: closer match plus a followed author. With `top_n` 1, A becomes `triaged`, B becomes `skipped` (still searchable, score and reasons kept).

### Caching embeddings

Vectors are stored in the `embeddings` table as raw float32 bytes, keyed by (paper id, model name). Triage reuses a stored vector when the model is the same, so re-running does not re-embed. A different model name gets its own rows, so switching models never mixes incompatible vectors. Profile vectors (seeds and topics) are few and cheap and are recomputed each run.

## Why it's built this way

- **Embeddings over keywords or an LLM judge**: no per-paper API cost, works offline on a CPU, fast for hundreds of papers, and tolerant of different wording. An LLM judge would be more nuanced but costs money and time on every paper; we use the LLM later, only on the shortlist.
- **Max similarity, not average**: a paper near any one interest is relevant. An average would punish papers for being unrelated to your *other* interests.
- **Small additive boosts with named constants**: easy to read, explain and tune; no hidden learned model.
- **Reasons from the same numbers as the score**: the explanation cannot disagree with the ranking.
- **Skipped, not deleted**: triage is a cheap filter and may be wrong; skipped papers remain searchable.
- **Model choice**: default `BAAI/bge-small-en-v1.5`: small (384 dimensions), good retrieval quality, runs on CPU. It is swappable via `embedding_model` in `config.yaml`. Open question: compare models (e.g. bge-base) by checking how well each ranks a gold set of papers you have judged relevant; until then bge-small is the default.
- The embedding library is an optional extra: `uv sync --extra embed`. Without it, triage reports that hint instead of crashing obscurely.

## How to run / check it

- Tuning: edit weights in `rank.py`; edit `top_n`, topics, seeds, authors and keywords in `profile.yaml`. Add a few seeds that are truly representative (10 to 20 is plenty); too-broad topics make everything look similar.
- Inspect results: a paper's `triage_score` and `triage_reasons` columns show exactly why it ranked (also for `skipped` ones, to see what just missed the cut).
- Tests: `uv run pytest tests/test_rank.py tests/test_triage.py -q`. They use a fake hashed bag-of-words embedder, so no model is downloaded.

## Where the code is

- `src/research_pipeline/embed.py`: `Embedder` protocol, `SentenceTransformerEmbedder`
- `src/research_pipeline/rank.py`: `build_profile_vectors`, `score_papers`, weights
- `src/research_pipeline/triage.py`: `run_triage`
- `tests/conftest.py`: `FakeEmbedder`
