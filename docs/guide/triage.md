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
      + 0.01 × ln(1 + hf_upvotes)
      + 0.02 × (number of followed authors on the paper)
      + min(0.04, 0.01 × (number of keywords found in title/abstract))
      + 0.20 × max(0, cosine to your nearest shelf chunk)
      + 0.20 × max over liked papers of (decay weight × cosine)
      − 0.15 × max over disliked papers of (decay weight × cosine)
```

**Scale matters.** Cosine similarity is *not* spread over 0 to 1 in practice. It can in principle be anywhere from -1 to 1 (we do not clamp it, so an unrelated paper can score below 0 before boosts), but real embedding models bunch related-sounding text together. In the first real run (about 340 papers, `bge-small`) similarity ran from 0.57 to 0.85 with a standard deviation of only 0.045: *every* paper that mentions LLMs looks fairly similar to an LLM seed. The first version of the weights used boosts up to 0.19 for a 42-upvote paper, which is four standard deviations, and the top of the digest filled with popular papers off your topics. The boosts below are sized against that real spread, so they reorder close calls but do not override a clear similarity gap.

| Term | Weight | Why this size |
| --- | --- | --- |
| Similarity | 1.0 (cosine; typically 0.5 to 0.9 for real models, can be lower or even negative) | The main signal. A paper is judged by its *closest* seed or topic: being very close to one thing you like matters more than being vaguely near all of them. |
| HF upvotes | 0.01 × ln(1 + n) | ln compresses: 1 vote gives 0.007, 10 gives 0.024, 42 gives 0.038, 100 gives 0.046. Community interest helps break ties; a similarity gap larger than about 0.04 to 0.05 outweighs even a very popular paper, but a small gap does not. |
| Followed author | 0.02 each | Worth about half a standard deviation of similarity: a clear nudge, not decisive. Case-insensitive exact name match (no fuzzy matching, so "Ada Lovelace Jr" does not match "Ada Lovelace"). |
| Keyword | 0.01 each, capped at 0.04 | Keywords are blunt (a word can appear in an unrelated paper), so each is a nudge and four of them are the most you can get. Each keyword counts once; matching is case-insensitive substring in title or abstract. |
| Shelf (my_work) | +0.20 × nearest | Similarity to your own notes in `my_work/` (see `shelf.md`). Only the *nearest* chunk of your own work counts, so a big shelf does not dilute it, and it is clamped at 0. Same size and reasoning as the liked-paper term, with no decay: identical text gains 0.20, one at the typical 0.6 gains 0.12, so a near neighbour beats an average paper by about 0.2 × 0.2 = 0.04, roughly one standard deviation of similarity (0.045). It reorders close calls without overriding a clear topical gap. The reason `near your shelf: <note name>` is written when the raw cosine is at least 0.70. |
| Liked papers | +0.20 × nearest | Your **implicit profile**: papers you up-voted or saved (`rpipe vote`, `rpipe save`). Only the *nearest* liked paper counts: the largest of (decay weight × cosine). A paper almost identical to one fresh like gains 0.20; one at the typical unrelated similarity of 0.6 gains 0.12, so the real difference between a near neighbour and an average paper is about 0.2 × 0.2 = 0.04, roughly one standard deviation of similarity: it reorders close calls without overriding a clear topical gap. |
| Disliked papers | −0.15 × nearest | Same calculation over down-voted papers, slightly weaker because a downvote is often "not today" rather than "never". |

The weights are named constants at the top of `rank.py`; if you change the embedding model, re-check the similarity spread first, because a model with a wider spread can carry larger boosts.

### Reasons

Each paper gets reasons in this order: one similarity reason for the best match, formatted `closest seed: <id> (0.71)` or `topic: <topic> (0.64)`; then `HF upvotes: <n>` (if above 0); `followed author: <name>` per hit; `keyword: <kw>` per hit; then `near your shelf: <note name>` (the nearest chunk of your own work, shown at raw cosine 0.70 or more); then `liked similar: <paper id>` and `disliked similar: <paper id>` (the nearest liked/disliked paper). The two feedback reasons appear only when that paper's raw cosine (before decay) reaches `FEEDBACK_REASON_FLOOR` = 0.70, about two similarity standard deviations above the 0.6 typical for unrelated papers, so a reason always means a real neighbour, however old the vote. Decay scales only the score effect, never whether the reason is shown. With no seeds and no topics there is no similarity reason and the score comes from the boosts alone.

### Worked example

Profile: seed `2401.00001`, topic "adversarial robustness", followed author "Ada Lovelace", keywords "fuzzing" and "jailbreak", `top_n` 1.

**Paper A**: "Fuzzing LLM agents", by Ada Lovelace, 3 upvotes. Closest to seed `2401.00001` at 0.71; the topic scores 0.55.
- similarity 0.71
- upvotes 0.01 × ln(4) = 0.014
- followed author 0.02
- keyword "fuzzing" found, "jailbreak" absent: 0.01
- **score 0.754**; reasons: `closest seed: 2401.00001 (0.71)`, `HF upvotes: 3`, `followed author: Ada Lovelace`, `keyword: fuzzing`

**Paper B**: "Certified defences against jailbreak attacks", 42 upvotes. Closest to the topic at 0.70.
- similarity 0.70
- upvotes 0.01 × ln(43) = 0.038
- no followed author; keyword "jailbreak" found, "fuzzing" absent: 0.01
- **score 0.748**; reasons: `topic: adversarial robustness (0.70)`, `HF upvotes: 42`, `keyword: jailbreak`

A (0.754) narrowly beats B (0.748) despite far fewer upvotes: slightly closer match plus a followed author. Had B's similarity been 0.75 instead, B would win (0.798): the boosts decide close calls, similarity decides clear ones. With `top_n` 1, A becomes `triaged`, B becomes `skipped` (still searchable, score and reasons kept).

### Seed papers: where their text comes from

Seeds are arXiv ids in `profile.yaml`; triage needs their text. `resolve_seed_texts` (`seeds.py`) finds it in this order: (1) the `papers` table, if you have already collected that paper; (2) `<vault>/cache/seeds.json`; (3) one arXiv lookup by id for whatever is still missing, written to the cache so later runs make no request. The text is title, a newline, then the abstract. Seeds are never inserted into `papers`, otherwise they would be ranked and listed as new papers. If the lookup fails, or arXiv does not know an id (for example a mistyped id), the run carries on with the seeds it has and the problem appears in the health line and in `runs.errors`.

### Caching embeddings

**The embedding model always runs on the CPU**, even on a machine with a GPU (`SentenceTransformer(..., device="cpu")` in `embed.py`). Embedding a night's papers is a small job (about 22 s for 600 abstracts with the default model on a desktop CPU); the GPU is kept whole for the summarizing model that runs right after. See "Why it's built this way" for the measurement.

While the model loads, the library's own progress bar and its unauthenticated-HF_TOKEN warning are switched off (`embed.quiet_model_load`; see the Progress section of running.md): the `embedding model` stage line and heartbeat replace them, and the bar's carriage returns would garble a journal.

Vectors are stored in the `embeddings` table as raw float32 bytes, keyed by (paper id, model name). Triage reuses a stored vector when the model is the same, so re-running does not re-embed. A different model name gets its own rows, so switching models never mixes incompatible vectors. Profile vectors (seeds and topics) are few and cheap and are recomputed each run.

### Implicit profile: votes and saves with decay

Your explicit profile (seeds, topics) is what you *said* you like. Your votes and saves are what you *did* like. Each night triage turns them into a second, **implicit profile** and nudges scores toward papers resembling your up-votes and saves, and away from papers resembling your down-votes.

1. `run_triage` reads the latest non-neutral vote per paper (`library.current_votes`) and every save (`library.list_saved`), and loads those papers' stored embeddings. Papers with no embedding are skipped (no crash). An up vote that was later cleared contributes nothing.
2. `rank.build_feedback_vectors` gives each one a weight `0.5 ** (age_days / half-life)`. The **half-life** is the number of days after which a vote counts half as much; the default is 60 days (`feedback_halflife_days` in your profile). An up vote and a save both start at weight 1.0; a paper that is both counts once with the larger (fresher) weight. A down vote beats a save of the same paper. Timestamps may be ISO with `T`/`Z` or plain `YYYY-MM-DD HH:MM:SS`.
3. For each candidate paper, `score_papers` finds the liked paper with the largest weight × cosine; the score gains `0.20 ×` that. The same over disliked papers subtracts `0.15 ×`.

Worked example: you up-voted paper L 60 days ago (weight 0.5^(60/60) = 0.5) and a candidate has cosine 0.9 to it. Gain = 0.20 × 0.5 × 0.9 = 0.09, exactly half of the 0.18 a fresh vote would give. The reason `liked similar: L` is added because the raw cosine 0.9 is at or above the 0.70 floor; decay does not hide it. At cosine 0.65 the gain would still be 0.065 but no reason is shown.

**Copied-in papers.** When you vote on or save a post from a feed you follow, the app copies the paper into your database (`source='feed'`, status `skipped`, see "The app's door in detail" in [architecture.md](architecture.md)). Such a paper is never `new`, so nothing else would embed it. Triage therefore also embeds every voted or saved paper that has no stored embedding, in the same embed call as the night's new papers, so your feedback vectors include it and later similar papers get a `liked similar` reason. This runs even on a night with no new papers (only the embedding is done; no paper's status changes). With nothing to embed and nothing new, triage still does nothing, as before. Triage never changes a `source='feed'` paper's status.

### Your shelf

Just before triage, `rpipe run` and `rpipe triage` sync the shelf: they call `library_api.shelf_sync` on the vault's `my_work/` folder, which embeds any new or edited notes (unchanged notes are not re-embedded) and drops deleted ones. This happens every time, even when there are no new papers, so the index follows your files on quiet nights too. Then they call `run_triage(..., use_shelf=True)`. Triage itself only *reads* the shelf: `rank.build_shelf_vectors` loads every chunk's stored vector, and `score_papers` adds `0.20 ×` the cosine to the nearest chunk. With an empty shelf, or `use_shelf=False` (the default), scores are exactly what they would be without a shelf.

Why the sync sits in the command, not in `run_triage`: syncing writes shelf rows from your own files, which is the app's business (the shelf commands do the same through `library_api`), while triage is a pipeline stage. Keeping the write out of `triage.py` means pipeline code never imports the shelf code; `cli.py` is the one place that joins the two halves (see [architecture.md](architecture.md)). Rejected: leaving the sync inside `run_triage` (simpler call, but it tied the pipeline to the app's module).

Worked example: a note `cooking_pasta.md` holds "tomato basil". A paper whose title and abstract are "Cooking pasta / tomato basil" has cosine 1.0 to that chunk (idealised: the text is nearly identical; a real model scores even close matches lower, typically 0.7 to 0.9), gains 0.20 and gets the reason `near your shelf: cooking_pasta`. A paper at cosine 0.55 gains 0.11 and no reason (below the 0.70 floor).

The 0.70 reason floor is borrowed from the feedback floor and has not been tuned separately for note chunks. A note chunk compared with an abstract may score lower than paper-to-paper, so the reason may fire rarely on short or informal notes; the boost itself applies at any cosine.

## Why it's built this way

- **Embeddings over keywords or an LLM judge**: no per-paper API cost, works offline on a CPU, fast for hundreds of papers, and tolerant of different wording. An LLM judge would be more nuanced but costs money and time on every paper; we use the LLM later, only on the shortlist.
- **Max similarity, not average**: a paper near any one interest is relevant. An average would punish papers for being unrelated to your *other* interests.
- **Implicit profile uses the nearest liked paper, not an average**: averaging weight × cosine over all likes dilutes with history (with 20 diverse likes a near neighbour's gain shrinks about twenty-fold, from ~0.04 to ~0.002, so the feature would fade as you use it); dividing by the sum of weights has the same problem and also cancels decay for a lone vote. Max similarity matches how the explicit profile works (above): near any one thing you liked is enough. Cost: one stray like can pull in its neighbourhood; the 0.20 cap and decay bound that. A constant shift affecting every paper cannot change the order. Each feedback term is clamped at 0: a paper opposite to something you liked is simply not boosted (it is not penalised), and likewise for dislikes.
- **Shelf uses the nearest chunk, not an average**: same argument as the implicit profile. Your notes cover many topics, and a paper only needs to be close to one of them; averaging would let a large shelf drown every match. No decay, because a note you keep on the shelf is still something you care about.
- **Small additive boosts with named constants**: easy to read, explain and tune; no hidden learned model.
- **Reasons from the same numbers as the score**: the explanation cannot disagree with the ranking.
- **Skipped, not deleted**: triage is a cheap filter and may be wrong; skipped papers remain searchable.
- **Model choice**: default `BAAI/bge-small-en-v1.5`: small (384 dimensions), good retrieval quality, runs on CPU. The model name is the `embedding_model` setting (change it with `rpipe model set-embedding NAME`; see the file reference in `vault-and-config.md`). Open question: compare models (e.g. bge-base) by checking how well each ranks a gold set of papers you have judged relevant; until then bge-small is the default.
- **Embedding on the CPU, never the GPU**: the summarizing model that runs after triage is fastest when all of it fits in GPU memory. When the embedding model ran on the GPU, the `rpipe` process kept a *CUDA context* (see the glossary) and a little cached memory on the card for the rest of the run, even after the embedder was dropped and PyTorch's cache emptied. Ollama then had less free GPU memory, so about 8% of the summarizing model spilled to ordinary RAM: measured on the owner's 16 GB card, a GPU share of 0.92 all night instead of 1.0 with nothing else on the card. That slowed every paper, and made the `auto` reading mode pick split (see "Choosing the mode" in `summaries.md`). On the CPU the embedder never touches the GPU, so the share should read 1.0, as it did with nothing else on the card. Cost: 600 abstracts take about 22 s on that CPU, so a normal night is about 20 s slower; a few hours of summaries win far more than that back. Rejected: (1) freeing GPU memory after embedding: `cli._release_gpu_memory` already does what can be done (garbage collection and `torch.cuda.empty_cache()`), but a CUDA context cannot be released while the process lives; (2) embedding in a separate child process that exits and takes its context with it: it works, but adds moving parts (passing texts and vectors between processes, error handling, progress) to save about 20 s.
- The embedding library is an optional extra: `uv sync --extra embed` in the repo, `uv tool install --editable '.[embed]' --force` for the plain `rpipe`. Without it, `rpipe run` stops with that hint instead of crashing obscurely (see `running.md`).

## How to run / check it

- Progress (stderr; the counts the command prints stay on stdout). `rpipe triage` and the triage part of `rpipe run` show, in order: `embedding model BAAI/bge-small-en-v1.5` (loading the model), `seeds` with one `looking up seed 2401.00001` line for each seed not yet cached (cached seeds are silent), `shelf` with one step per new or edited note file (see `shelf.md`), then `triage` with the note `embedding 40 papers`. The papers are embedded in one call, so there is no per-paper bar (the stage has no total either, so a terminal shows only the spinner, never a bar stuck at 0 of N). When there is nothing to do (no new papers, no changed notes, no uncached seeds) the stage just starts and finishes, with no bar. The **first ever run downloads the embedding model** (hundreds of MB, about 130 MB for the default) and that can take minutes; the "still waiting" heartbeat (every 30 s, see `running.md`) shows it is alive, not frozen. Later runs load it from the local cache in seconds. Code: `run_triage`, `resolve_seed_texts` and `cli._load_embedder` each take an optional `reporter`.
- The embedding model runs on the CPU: while `rpipe run` is ranking, `nvidia-smi` lists no `rpipe`/Python process on the GPU. `tests/test_embed_quiet.py` (`test_embedder_runs_on_cpu`) checks that the model is created with `device="cpu"`.

- Changing the embedding model: `rpipe model set-embedding NAME` (any name `sentence-transformers` can load, for example `all-MiniLM-L6-v2`). Vectors from different models cannot be compared, and stored vectors are keyed by model name, so the next run downloads the new model (the first download can take a while; the progress described above shows it) and embeds your shelf, the seed papers and the papers still waiting to be ranked with it. Papers ranked earlier are not re-embedded, and the implicit profile (votes and saves) only uses vectors from the current model (`triage._rank_and_store`), so while the new model is active your past votes and saves add nothing to the ranking (new votes on newly ranked papers do count). The old vectors stay in the database, unused. Go back any time with `rpipe model set-embedding BAAI/bge-small-en-v1.5`: the old vectors are used again and past votes count again. A blank name exits 1. Code: `profile_edit.set_embedding_model`, `model_set_embedding` in `cli.py`.
- Tuning: edit weights in `rank.py`; change `top_n`, topics, seeds, authors and keywords with `rpipe profile ...` (see `profile.md`). Add a few seeds that are truly representative (10 to 20 is plenty); too-broad topics make everything look similar.
- Implicit profile: `rpipe vote <id> up` or `rpipe save <id>`; the effect shows on the next `rpipe run` (or `rpipe collect` then `rpipe triage`), because `rpipe triage` only ranks papers that are still `new`, not ones already ranked. Look for `liked similar:` in a paper's reasons. Change the fade speed with `rpipe profile set-feedback-halflife DAYS` (1 to 3650). Tests: `tests/test_rank.py` (decay ratio exact to 1e-9, no dilution with 20 likes, floor both sides, an old dislike keeps its reason and a decayed penalty), `tests/test_triage.py`.
- Inspect results: a paper's `triage_score` and `triage_reasons` columns show exactly why it ranked (also for `skipped` ones, to see what just missed the cut).
- Tests: `uv run pytest tests/test_rank.py tests/test_triage.py tests/test_seeds.py -q`. They use a fake hashed bag-of-words embedder, so no model is downloaded.

## Where the code is

- `src/research_pipeline/embed.py`: `Embedder` protocol, `SentenceTransformerEmbedder` (loads the model on the CPU)
- `src/research_pipeline/rank.py`: `build_profile_vectors`, `score_papers`, `FeedbackVectors`, `build_feedback_vectors`, weights (`FEEDBACK_BOOST`, `FEEDBACK_PENALTY`, `FEEDBACK_REASON_FLOOR`)
- `src/research_pipeline/triage.py`: `run_triage` (also loads votes, saves and their embeddings)
- `src/research_pipeline/profile_edit.py`: `set_feedback_halflife`, `set_embedding_model`
- `src/research_pipeline/seeds.py`: `resolve_seed_texts`
- `tests/conftest.py`: `FakeEmbedder`
