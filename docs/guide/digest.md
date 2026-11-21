# Digest: the file you actually read

## What it does

The digest stage turns the night's kept papers into one markdown file, `<vault>/digests/YYYY-MM-DD.md`. It is the only output you normally open. Each paper gets its title (linked to arXiv), authors, the reasons it ranked where it did, its abstract and handy links. The very first line is a **health line** telling you how the run went, so a broken night is visible instead of looking like "nothing interesting happened".

It also changes the database: every paper it lists moves from `triaged` to `in_digest` and records the digest date.

## How it works

```
papers: triaged (+ in_digest for this date)
   --> sort by score desc, then id asc
   --> render markdown
   --> write digests/YYYY-MM-DD.md
   --> mark every listed paper in_digest, digest_date = that date
```

The entry point is `write_digest(conn, vault, digest_date, run_summary)`. `run_summary` is the health line, built by the caller (the `run` command, Task 9) from the collect/triage counts and source errors; the digest stage prints it verbatim.

1. **Select.** Papers with status `triaged`, plus papers with status `in_digest` whose `digest_date` equals this date (see "Same-day re-runs").
2. **Order.** `triage_score` highest first; equal scores break by `id` ascending, so the order is deterministic.
3. **Render** each paper (layout below).
4. **Write** the file, creating `digests/` if needed, replacing any file of that name.
5. **Mark** the listed papers `in_digest` with `digest_date` set (`mark_in_digest` in `db.py`) and commit. The file is written first, so a crash can never leave papers marked as delivered without a file.

### A sample digest, section by section

This is real output (two papers, one with upvotes and a repo, one without an abstract):

```markdown
Collected 214, kept 2, skipped 212. Source errors: none.

# Research digest — 2026-11-21

## 1. [Jailbreaking \[Aligned\] LLMs via Gradient Search](https://arxiv.org/abs/2411.01234)

**Authors:** Ann Lee, Bo Chen, Cy Diaz, Di Evans, Ed Fox et al.

**Why it matched** (score 0.81)

- closest seed: 2307.15043 (0.74)
- HF upvotes: 42 (+0.19)
- keyword: jailbreak

We show that suffixes found by gradient search transfer across models.

[abs](https://arxiv.org/abs/2411.01234) · [PDF](https://arxiv.org/pdf/2411.01234) · HF upvotes: 42 · [repo](https://github.com/example/gcg)

## 2. [Fuzzing eBPF Verifiers](https://arxiv.org/abs/2411.05555)

**Authors:** Gus Hall

**Why it matched** (score 0.65)

- closest topic: kernel security (0.65)

_No abstract available._

[abs](https://arxiv.org/abs/2411.05555) · [PDF](https://arxiv.org/pdf/2411.05555)
```

| Part | What it is |
| --- | --- |
| Line 1 | The **health line**, exactly as passed in. |
| `# Research digest — date` | The title, with the digest date. |
| `## N. [Title](abs url)` | Rank (1 = best) and the title as a link to the paper's page. Square brackets in a title are escaped with a backslash (`\[Aligned\]`) because an unescaped `]` would end the link early and break the line; newlines inside a title are collapsed to spaces. |
| Authors | The first 5 names, then "et al." if there are more. Paper 1 had 6 authors, so only 5 are shown. |
| **Why it matched** | The score to 2 decimals and the plain-language reasons triage stored (see `triage.md`). The score is only for comparing papers within the same night. |
| Abstract | The abstract as collected, or `_No abstract available._` when the source gave none. |
| Links line | `abs` (the paper page), `PDF` (`https://arxiv.org/pdf/<id>`, only for papers with an arXiv id), HF upvotes when above zero, and one `repo` link per code repository found. |

### The health line

One line at the top, written by the caller: how many papers were collected, kept and skipped, and any source errors, for example `Collected 214, kept 2, skipped 212. Source errors: arxiv: timeout`. If a source failed, the digest still gets written from whatever the other sources produced, and the failure is stated here. A quiet digest with `Source errors: none` means a quiet day; one with an error means some papers may be missing.

### When nothing qualified

The file is still written: health line, the heading, and the sentence "Nothing new matched your profile today." A missing file would be ambiguous (did the run crash, or was it a quiet day?), so there is always a file for every run.

### Status changes

| Before | After | Also set |
| --- | --- | --- |
| `triaged` | `in_digest` | `digest_date` = the digest's date (ISO, e.g. `2026-11-21`) |
| `in_digest` with this date | stays `in_digest` | nothing new |

Papers with other statuses (`new`, `skipped`, ...) are never touched. Score and reasons are left unchanged. If a newer version of a paper later arrives, the database moves it back to `triaged` (see `data-model.md`) and it can appear in a later digest.

### Same-day re-runs

Running the pipeline twice on the same date rewrites the same file. Without special handling the second run would find nothing `triaged` (the first run already moved those to `in_digest`) and would overwrite a good digest with "Nothing new". So the selection also includes papers that are `in_digest` **with this date's** `digest_date`: the rewritten file contains everything from the first run plus anything newly triaged since.

Example: the 08:00 run lists papers A and B. At 14:00 a re-run triages paper C. The 14:00 file lists A, B and C (ranked together), and all three are `in_digest` for that date. Papers delivered on an earlier date are not repeated.

## Why it's built this way

- **A markdown file in the vault**: plain text opens anywhere, works in Obsidian-style note tools, can be searched, and needs no server. Rejected: HTML or email, which add rendering and delivery problems for no gain in Phase 1.
- **Health line first**: you see failures at the same place you read results. Rejected: only logging errors, which nobody checks.
- **Date-matched `in_digest` on re-runs**: keeps the file complete and idempotent (same inputs, same file). Rejected: appending to the file (duplicates, messy ordering) and ignoring re-runs (loses content).
- **The caller supplies the health line**: the digest stage does not know about source failures or skipped counts; the run orchestration does. Keeps the writer simple and testable.
- **Reasons stored, not recomputed**: the digest prints what triage decided at the time, so the explanation always matches the ranking.

## How to run / check it

Normally via the `run` command (Task 9), which runs collect, triage, then digest. Open `<vault>/digests/<today>.md`. To inspect the effect in the database:

```
sqlite3 <vault>/vault.sqlite "select id, status, digest_date from papers where digest_date is not null"
```

Tests: `uv run pytest -q tests/test_digest.py tests/test_db.py` (offline; they use a temporary database and vault).

## Where the code is

- `src/research_pipeline/digest.py`: `write_digest` and the markdown rendering.
- `src/research_pipeline/db.py`: `mark_in_digest`.
- `tests/test_digest.py`, `tests/test_db.py`.
