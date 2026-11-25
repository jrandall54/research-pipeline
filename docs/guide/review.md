# Reviewing the digest in the terminal (`rpipe review`)

## What it does

`rpipe review` walks you through the digest papers you have not decided about yet, one at a time, in the terminal. For each paper it shows the title, id, why it was picked, the summary (or the abstract when there is no summary yet) and the link. You answer with one letter: **u**p (thumbs up), **d**own (thumbs down), **s**kip (not now) or **q**uit. `rpipe review --later` goes through the papers you skipped earlier. Up and down are ordinary votes, exactly like `rpipe vote ID up|down` (where `ID` may be written `2611.25005`, `2611.25005v2`, `arXiv:2611.25005` or as an arxiv.org link; all mean the same paper), so they feed the implicit profile the next triage uses ([triage.md](triage.md)). A skip is not a vote and never changes your ranking.

## How it works

```
 digests (papers with digest_date)
        |
        v   review_queue(): no active vote, not skipped,
        |   oldest digest first, best triage score first
        v
 show paper --> you press u / d / s / q
        |
        +-- u or d: record_vote(+1 / -1), clear_skip   (votes table)
        +-- s:      record_skip                        (skips table)
        +-- q:      stop
        v
 "Voted V, skipped S this session. N left in the queue."
```

Worked example (illustrative output; the second paper has a stored summary, the first falls back to its abstract):

```
$ rpipe review

Digest 2026-11-21 · paper 1 of 3 · 0 voted, 0 skipped

Fuzzing eBPF Verifiers with Grammar-Based Mutation
id: 2411.01234
why: close to seed 2301.00001; keyword "fuzzing"

We present a grammar-based fuzzer for the Linux eBPF verifier ...

https://arxiv.org/abs/2411.01234

[u]p  [d]own  [s]kip  [q]uit: u

Digest 2026-11-21 · paper 2 of 3 · 1 voted, 0 skipped

Universal Adversarial Attacks on LLMs
id: 2307.15043
why: followed author

**Summary:** A suffix that makes aligned models comply with harmful requests.
...

https://arxiv.org/abs/2307.15043

[u]p  [d]own  [s]kip  [q]uit: s
...
[u]p  [d]own  [s]kip  [q]uit: q
Voted 1, skipped 1 this session. 1 left in the queue.
```

The rules:

- **Oldest digest first.** Papers are ordered by the day of the digest that listed them, then by triage score (best first), then by id. A paper with no score sorts last within its day. The header counts progress within that day: `paper 2 of 3` means two of that day's papers are already voted or skipped (in any session) and you are looking at the next.
- **Active vote.** A paper counts as *voted* when its latest `votes` row is not 0. No row, or a latest row of 0 (a cleared vote, `rpipe vote ID clear`), means *unvoted*, so a paper whose vote you cleared returns to the queue.
- **Skips are their own list.** A skipped paper leaves the main queue and waits in *review later*, oldest skip first. Skipping it again in `--later` refreshes its time, so it moves to the back. Voting on it (in `review`, `rpipe vote` or the `rpipe serve` forms) takes it out of the later list; `review` also deletes the skip row when you vote.
- **`digest_date` stays set.** If a paper gets a new version and falls back to status `triaged`, its `digest_date` is kept, so it stays eligible for the queue. This is intended: you still never decided about it.
- **Input** is a line (type the letter and press Enter). Only the first letter counts and case does not matter, so `U`, `up` and `Down` work. Anything else prints `Please answer u, d, s or q.` and asks again.
- **The queue is read once**, when the command starts. If you vote on a paper elsewhere during the session (for example through `rpipe serve`), `rpipe review` can still show it. Votes are append-only, so the vote you give in `review` is the later row and wins.
- **Ctrl-C or end of input** (Ctrl-D) stops the loop like `q` does, with one difference: after printing the same `Voted V, skipped S this session. N left in the queue.` line, the command exits non-zero with typer's usual `Aborted!`, so a script can tell you did not finish by choice. Votes and skips already given are kept, and the database is closed cleanly. Choosing `q` exits 0.
- **Needs a terminal.** If input is piped or redirected, `rpipe review` refuses (exit 1) with: `rpipe review needs a terminal. Use `rpipe vote ID up|down` instead.` It never guesses answers from a pipe.
- **Nothing to do** prints `Nothing to review.` (or `Nothing waiting in review later.`) and exits 0. `rpipe feedback` ends with a `Review later` section giving the number of skipped papers waiting.

## Just reading: `rpipe show`

`rpipe show` prints the same block `review` shows (title, `id:`, `why:`, then the summary and critique, or the abstract when there is no summary yet, then the link) but never asks for a vote and never writes to the vault.

```
rpipe show 2611.25005            # by id
rpipe show "prompt injection"    # by part of a title
rpipe show --recent 1            # everything in the latest digest
rpipe show --recent 3            # the latest three digest days, newest day first
```

Ids and titles are resolved exactly like `rpipe vote`: an id first, in any common spelling (`2611.25005`, `2611.25005v2`, `arXiv:2611.25005` or an arxiv.org link; see "Paper-id spellings" in [data-model.md](data-model.md)), then the exact text as an id, then a title fragment; if several titles match you pick from a numbered list, and if none match it exits 1 with `No paper in your vault matches ...`. `--recent N` takes N of 1 or more and lists the papers of the latest N distinct digest dates, newest date first, and within a date in digest order (triage score best first, then id), the same order `review` uses. With neither an id nor `--recent` it exits 1 with a hint. Code: `show` in `cli.py` (reuses `_resolve_paper` and `_show_paper`) and `recent_digest_papers` in `review.py`; tests in `tests/test_cli_show.py`.

## Why it's built this way

- **Skips in their own table, not as a vote value.** `votes` feeds the ranking; "not now" says nothing about taste. Storing it as a vote (say 0) would blur "cleared" with "undecided" and risk leaking into scoring. A separate `skips` table keeps the profile clean and makes "review later" a simple list. Rejected: a `status` column on papers (mixes a user decision with the pipeline's own states).
- **Thin loop over pure queue functions.** `review.py` only reads the database and is tested without a terminal; the CLI loop only prints, prompts and writes. Rejected: putting the queries in `cli.py` (hard to test, hard to explain).
- **Line input, not single keypress.** Needs no raw-terminal code and can be tested with plain input. The cost is pressing Enter.
- **Reuse `record_vote` and `render_summary`.** One way to vote and one way to render a summary, so the digest file, `rpipe vote` and `rpipe review` cannot drift apart.
- **Refuse when not a terminal.** A script feeding input would silently vote on papers it never showed you.
- **Oldest first.** Yesterday's papers are the ones that will otherwise never get read.

This feature adds no setting, so there is no profile or config command for it.

## How to run / check it

```
rpipe review            # the main queue
rpipe review --later    # papers you skipped
rpipe feedback          # shows how many are waiting in review later
uv run pytest tests/test_cli_review.py tests/test_review.py -v
```

Both commands accept `--vault PATH`.

## Where the code is

- `src/research_pipeline/cli.py`: the `review` command and `_show_paper`; `feedback` prints the review-later count.
- `src/research_pipeline/review.py`: `review_queue`, `later_queue`, `queue_progress`.
- `src/research_pipeline/library.py`: `record_vote`, `record_skip`, `clear_skip`; the `skips` table ([data-model.md](data-model.md)).
- `src/research_pipeline/db.py`: `get_summary`.
- `src/research_pipeline/digest.py`: `render_summary`, shared with the digest file.
- Tests: `tests/test_cli_review.py`, `tests/test_review.py`.
