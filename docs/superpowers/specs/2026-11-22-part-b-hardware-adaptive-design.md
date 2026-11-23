# Phase 6 Part B — Hardware-adaptive summarizing (design)

Status: draft for owner approval, 2026-11-22. Replaces the old Part B ("long papers") in the master plan.

## Intent

The pipeline must run well on almost any machine an AI club member brings: CPU only, a small GPU, a mid or high GPU, a very large GPU, or a cloud AI subscription through a CLI backend. The owner's own machine (16 GB NVIDIA GPU, 29 GB RAM, 6-core CPU with AVX-512) is the first concrete test case, but nothing may be designed around one model.

Two levers make that work:

1. **Match the machine to the right model and settings**, including models the user has not installed and server settings the user has not set, and confirm each choice by measuring on the machine.
2. **Send the model only the paper.** Strip every piece of non-paper text (bibliography, acknowledgements, funding, author notes, boilerplate, appendices and supplementary material) so a larger, better model fits fully in GPU memory with room to spare, and so the model's attention is not spread over filler. Core paper text is never cut for this purpose.

Quality comes first; speed matters only as far as a night's run must finish before morning.

## Evidence (measured 2026-11-22 on the owner's machine)

| Measurement | Result |
|---|---|
| Largest context fully on GPU, 27B dense model (IQ3_S), default f16 KV cache | 32,768 tokens |
| Same with `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` | **57,344 tokens** (+75%) |
| 14B dense model, same change | 141,312 → 223,232 tokens |
| 3 comparison papers read whole by the 27B | 1/3 → 2/3, speed unchanged (62–88 s), 0 unverified citations |
| The 3rd paper at 66,560 tokens, 7% of the model spilled to RAM | whole body read, 155 s instead of 88 s, 0 unverified |
| Size of 40 recent arXiv papers, without back matter | median ~22K tokens, 90% under 31K, max 41K; all 40 fit in 56K |
| CPU only (`num_gpu: 0`), 14B model, 4K-token prompt | reads 80 tok/s, writes 18 tok/s |
| Estimated night of 15 papers | GPU ≈ 15–25 min; CPU 14B ≈ 3–4.5 h (before the savings in parts 3 and 6) |

Findings that shaped the design:
- `rpipe model fit` picked up the new headroom with no code change, because it measures instead of looking up a table. Measuring stays the final word.
- The back-matter rule missed `Supplementary Note …`, `Funding`, `Instructions for reporting errors` and an untitled `6 Supplementary Information` heading.
- `truncated=True` is also set when only back matter was dropped, so "the paper was cut" and "the bibliography was dropped" look the same.
- Summary and critique each read the whole paper, and each call begins with a different system prompt, so Ollama cannot reuse the first reading for the second (no shared prefix).
- On CPU nearly all the time is spent reading the prompt, not writing the answer.

## Goals

- Every tier (CPU only → very large GPU → cloud) gets a working, measured setup chosen for it, with plain reasons shown.
- Recommendations may name models that are not installed; downloading needs the user's yes after seeing size and free disk space.
- Server settings that need admin rights (sudo) are offered with full disclosure: what changes, why, the exact command, how to undo it; applied only with consent; verified afterwards.
- No non-paper text reaches the model; reports tell "back matter dropped" apart from "paper body cut".
- A night's run uses the machine to the full: it fits on the GPU when it can, and spills into RAM for a paper that needs more context, within a deadline and a RAM limit.
- Quality is measured, not guessed: an answer key (the gold set) drafted by the cloud model, reviewed by the owner, and graded automatically.

## Non-goals

- LLMLingua-style word dropping, "soft token" / latent compression, KV eviction: rejected (small local models degrade on compressed text; risk of losing words like "not"; Ollama does not support the last two).
- PDF fallback for papers without HTML: 0 of 40 sampled papers lacked HTML. Deferred to Ongoing.
- Embedding-model changes and hybrid (BM25) search: app Phase 9, not here.
- Vision models for figures: later.
- Schedulers other than systemd: separate project.

## Anti-goals (failure even if it "works")

- Locking the design to one model, or moving every user silently to a smaller model.
- Changing a system setting without consent, or in a way the user cannot undo.
- Cutting core paper text to save room when it could have been read whole by spilling to RAM.
- A "best" model chosen on speed alone, with no quality check.
- Any documented workflow that needs hand-editing a file (project rule).

## Constraints carried forward

- Any change to `auto` must work on an empty vault, must not silently move every user to a smaller model, and is checked against the gold set.
- Tests stay offline (fixtures and a fake LLM); measuring and probing go through injected runners as `machine.py` and `ollama_fit.py` do now.
- Every waiting step reports through `progress.py`.
- Every user setting gets an `rpipe` command; every change updates `docs/guide/` in the same commit; new terms go in the glossary.
- Owner model names stay out of the repo.

## The six parts

Build order: 1 and 2 first (they are what everything else is judged by and benefits from), then 3, 4, 5, 6. Parts 2 and 3 are independent of each other and can be built in parallel.

### 1. Answer key (the gold set), drafted and graded by the cloud model

Plain words: a gold set is an answer key — about 10 papers, each with 3–5 facts any good summary must get right (main claim, method, key number, main weakness). It is how we measure quality when we change a model, a setting or a pruning rule.

The storage (`gold/gold.json`, `GoldPaper.key_facts`) and commands (`rpipe gold add/list/add-fact/edit-fact/remove-fact`, `rpipe eval run/grade/report`) exist. New:

- **`rpipe gold draft ID…`** — the cloud backend (Claude Code; the owner's usage cost is negligible overnight) reads the paper's full text (pruned as in part 2) and drafts 3–5 key facts. The user reviews each draft fact: keep / edit / drop. Nothing is saved until the review finishes. Without a cloud backend the command says so and points to `rpipe gold add` (typing facts by hand).
- **`rpipe gold suggest`** — proposes papers for the key from the vault (papers already summarized, mixed lengths: short, median, long, one very long), so the set covers the length range.
- **Automatic grading** — `rpipe eval run --judge` asks the cloud backend, per summary and per key fact: *covered / missed / contradicted*, with a one-line reason. Results go into the existing eval file next to the human grade; the report shows a score per model/setting (facts covered, contradictions, unverified citations, truncation, time per paper). Human `rpipe eval grade` stays and wins over the judge where both exist.
- Each eval run records the setup it measured (backend, model, context, KV cache type, prompt layout, pruning version) so runs can be compared.

### 2. Strict pruning of non-paper text

- Two classes (see Settled, item 10): **non-paper text, always dropped**, and **appendices/supplementary, dropped first when space is short**. Widen the rules to cover: `supplementary`, `extended data` (second class); `funding`, `author contributions`, `competing interests`, `data and code availability` (when it is only a statement), `ethics statement`, `reproducibility checklist`, `instructions for reporting errors`, and similar; plus DOM-level dropping of `section.ltx_bibliography` / `ltx_appendix` and arXiv page boilerplate in `fulltext.py`.
- Children of a dropped heading are dropped with it (the empty `6 Supplementary Information` parent today lets its notes through).
- Keep a list of what was dropped, with sizes, on every summary.
- **Split the flag:** `back_matter_dropped` (fine, expected) and `body_cut` (only when core text was trimmed to fit). The digest, health line (`truncated N`) and eval show `body_cut`; existing `truncated` stored rows are read as `body_cut` (old meaning: could be either; documented).
- If the body must still be cut to fit, cut in an order instead of the same fraction everywhere: never the abstract, introduction, results, discussion or conclusion first; trim long method detail and related work first. (The rerun ladder in part 5 usually avoids this.)
- Bump `PROMPT_VERSION` (what the model sees changed).

### 3. One prompt layout for caching and attention

Today: `[summary system prompt][paper][task]` then `[critique system prompt][paper][task]` — no shared prefix.

New layout for both calls: `[shared system prompt: role, rules, both schemas' guidance][paper][the task's instructions and rubric, repeated at the end]`. The summary and the critique then share the same long prefix, so Ollama reuses its reading of the paper for the second call (prompt-prefix caching), and the questions sit at the end, where models pay most attention.

- Must be checked with the answer key (part 1): the layout change must not lower facts covered or raise unverified citations.
- Measure reading time of the second call before and after (`prompt_eval_count`/`prompt_eval_duration` from Ollama's reply). Expected: the critique call reads only its task text.
- Add `num_predict` (an output token limit) to every Ollama call as a guard against runaway output.
- Cloud backend: same layout; caching there is the provider's business.

### 4. Machine scan, recommender, and guided settings

Extends `machine.py` (`detect_machine`, `recommend`) and `defaults/ollama_models.yaml`, which today offer three Qwen3 sizes by GPU memory and send CPU-only machines to "none".

- **Scan:** GPU (vendor, model, memory), system RAM total and free, CPU cores and vector support (AVX2/AVX-512/ARM), free disk, OS, Ollama version and its server settings (flash attention, KV cache type, parallel slots, max loaded models), installed models, cloud CLI backend logged in.
- **Catalog:** a data file of candidate models with, per entry: family, size, architecture (dense or MoE and active parameters), quantization, download size, trained context, the hardware band it suits (CPU / VRAM range), and notes. It is a *starting guess*; measurement decides. It is versioned data, updatable without a code release (`rpipe model catalog update` fetches the latest published copy from the project repository, shows what changed, asks before saving); the shipped copy is the fallback.
- **Bands** (initial, to be filled by part 4's research and measurement): CPU only (by RAM and memory bandwidth), small GPU (<8 GB), mid GPU (8–16 GB), high GPU (16–24 GB), very large (>24 GB or several GPUs), cloud. MoE models with few active parameters are candidates where VRAM is short but RAM is plentiful (they tolerate RAM offload better than dense models).
- **Recommend:** for this machine, a short ranked list (installed and not installed), each with the reason, expected fitted context and expected time per paper; and the server settings it needs.
- **Guided settings:** `rpipe setup ollama-tuning` (name to settle in the plan) shows each recommended server setting, what it does, why, the exact command, and how to undo it; with consent it writes one separate drop-in file (Linux systemd: `/etc/systemd/system/ollama.service.d/rpipe.conf`; macOS/Windows: the platform's equivalent or printed instructions), restarts Ollama, and verifies the setting is live and the fit improved. Undo: `rpipe setup ollama-tuning --undo` removes that file and restarts. Settings needing admin rights run through `sudo` in the user's terminal; the pipeline never stores a password.
- **Download:** with consent, after showing download size against free disk; progress through `progress.py`.
- **Measure:** after any change, `rpipe model fit` runs, and a short benchmark (one median paper) records time per paper for this machine; results are saved so the nightly run can plan.
- **Re-check on change:** the fit cache already keys on model and context; also key on Ollama version, server settings and GPU, so a driver/Ollama upgrade or setting change triggers a fresh fit.
- **Opt-in sharing (later, design only here):** a club member may export their scan + timings (no paper content, no profile) to help improve the catalog.

### 5. Using the whole machine overnight: RAM spill reruns within a deadline

- Normal papers run at the GPU-fitting context (today's behaviour).
- A paper whose body would be cut is queued, with the context it needs (measured from its pruned size).
- After the normal papers, queued papers rerun at a larger context, allowing part of the model in RAM. All reruns are grouped so the model reloads once per context size, not per paper; sorted by needed size.
- Limits: the model's trained context; free RAM minus a safety margin (never swap); a per-night deadline (default "finish by 07:00 local", settable by command) checked against measured time per paper — papers that cannot make it keep their cut summary and are tried again on a later night when time allows.
- Reports: the health line shows `body_cut N` and `rerun N`; each summary records the context and the share on GPU it ran with.
- Cloud backend: no spill (context is large); the deadline still applies.

### 6. Task splitting and map-reduce, where measurement says they help

- **Task splitting:** instead of two whole-paper calls, run narrow passes: (a) abstract + introduction + conclusion → core claim and what is new; (b) method + experiments → assumptions, baselines, missing ablations; (c) combine (a) and (b) → summary, critique, verdict. On CPU this reads ~13K tokens instead of ~44K.
- **Map-reduce:** for a paper still too long after pruning and spill: per-section notes with section citations, then one combining pass.
- Which mode a machine uses (whole paper with caching, task splitting, map-reduce) is chosen per machine tier by measurement **and** the answer key — not assumed. Expected: whole paper on strong GPUs; splitting on CPU and small GPUs. Recorded as a setting with an `rpipe` command; `auto` by default.
- Citation checking keeps working: every claim still cites real section titles.

## Approaches considered

**A. How the recommender knows about models the user has not installed**
- *A1. Curated catalog shipped with the code* (extend `ollama_models.yaml`): simple, testable, reviewed; goes stale between releases.
- *A2. Discover live from the Ollama library website*: always current; no official API for sizes/quantizations, scraping is fragile, and it says nothing about fitness for a band.
- *A3. Curated catalog as versioned data, updatable without a release, with on-machine measurement as the final word* — **chosen (my call)**: keeps A1's review and testability, removes the staleness, and a wrong entry costs a slower night, not a broken one.

**B. How quality is judged**
- *B1. Owner writes every key fact and grades every output*: most trustworthy; costs evenings; owner declined this cost.
- *B2. Automatic signals only* (unverified citations, JSON failures, truncation): free, but blind to shallow critiques.
- *B3. Cloud model drafts the key facts, owner reviews them once, cloud model grades every run* — **chosen (owner approved in conversation)**: owner effort is a skim of ~10 papers once; every later experiment is graded automatically; automatic signals still reported alongside.

**C. How long papers are handled**
- *C1. Bigger fixed context for all* (old Part B option 1): wastes memory, slows every paper, still fails on the longest.
- *C2. Per-paper context tiers dispatched in sorted batches* (Neon guide): every change of `num_ctx` reloads the model; per-paper tiers thrash.
- *C3. GPU-fitting context for all, grouped spill reruns for the few that need more, chunking as last resort* — **chosen**: measured on real papers; most papers never need it (40/40 fit at 56K here), and the rare one is read whole for about a minute extra.

## Decided in conversation (owner)

- The hardware spectrum CPU only → very large GPU → cloud is a requirement, not an option.
- Pruning means non-paper text only; never core text.
- Admin-rights changes are fine with consent, full disclosure, easy undo, and explanation.
- Cloud subscription usage for a nightly run is negligible; using it for drafting and grading is fine.
- Slower generation when spilling to RAM is acceptable overnight.
- Recommendations include models not yet installed.

## Settled after the builder check (my calls unless marked)

1. **Judge and drafter.** The judge is the configured cloud CLI backend (Claude Code today; any later CLI adapter that can return JSON). It receives the same pruned full text the summarizer gets (from `fulltexts`, fetched if missing). When the model under test is the same cloud model, the report marks the result **self-graded**; allowed, not hidden. No cloud backend: `--judge` and `gold draft` explain and point to the hand-written commands.
2. **Pass bar.** Ollama runs at temperature 0, so runs are close to deterministic; one run per setup. A change passes when facts covered does not drop by more than 1 across the whole set, contradictions do not rise, and unverified citations do not rise. The report shows the comparison against the previous recorded setup; anything else is the owner's call on reading the report.
3. **`auto` with no answer key.** Uses the tier default (whole paper + caching on GPUs where the median paper fits; task splitting on CPU and small GPUs). The owner's answer-key results set those shipped defaults; a user with their own answer key can run `rpipe eval` to check. Empty vault works.
4. **Ordered trimming.** Only a fallback (rerun limits or deadline hit). Sections are classified by heading words; anything unclassified is trimmed by the uniform fraction, as today.
5. **Deadline.** Applies to reruns only: normal papers always run, as today. A rerun starts only if its measured estimate ends before the deadline. Which papers need a rerun is recomputed from stored summaries (`body_cut` and the needed size recorded on the summary), so no new table; at most a new field on the summary JSON.
6. **Benchmark.** Measures read and write speed (tokens per second) with a short built-in prompt, then estimates time per paper from the median paper size; no paper download, capped at a few minutes, skippable. Tests use a fake runner.
7. **Catalog source.** The project repository is public on GitHub; `rpipe model catalog update` fetches the catalog file over HTTPS from the main branch, validates it against its schema, shows the diff, and saves only on yes. It only suggests models; nothing downloads without a separate yes.
8. **How Ollama is run.** The scan detects a systemd service, a user-run `ollama serve`, Docker, or the macOS/Windows app. Automatic tuning only for the systemd service at first; other setups get printed, exact instructions plus how to undo them.
9. **Versions and baselines.** Parts 2 and 3 each bump `PROMPT_VERSION` in their own change; each part's eval compares against the run recorded just before it.
10. **Appendices and supplementary material** *(owner, 2026-11-22: option A)*. Always dropped: references, bibliography, acknowledgements, funding, author contributions, competing interests, boilerplate. Appendices and supplementary material are **kept when the paper fits** and **dropped first when space is short** — before any core text is trimmed and before a paper is queued for a spill rerun. (Always dropping them was rejected: critiques would call an ablation "missing" that sits in an appendix.) *My call, to check:* on CPU-only machines the tier default drops appendices and supplementary always, because each 1,000 tokens costs about 15 s per call there; a setting with an `rpipe` command can change it.

## Left to the builder (plan)

- Exact command names and flags (proposed above: `rpipe gold draft`, `rpipe gold suggest`, `rpipe eval run --judge`, `rpipe setup ollama-tuning [--undo]`, `rpipe model catalog update`, a deadline setting command, a summarize-mode setting command).
- The catalog file's exact schema and initial entries (part 4 starts with a research task: published benchmarks and model cards for candidates per band, then measure what is available on the owner's machine).
- The judge prompt and schema.
- The ordered-trimming priorities beyond "abstract, introduction, results, discussion, conclusion last".
- Platform details for applying Ollama settings on macOS and Windows (Linux first; others may print instructions only at first).
- Splitting the work into plans: likely one plan per part, in the build order above.
