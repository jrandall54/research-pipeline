# Evaluation: the gold set

## What it does

The gold set is a short list of papers you know well, each with a list of **key facts**: things any correct summary of that paper must get right. It is stored in your vault as `gold/gold.json` and managed with `rpipe gold` commands. The second half of this page covers `rpipe eval`, which uses the gold set to check the summaries the model writes.

## How it works

1. You add a paper: `rpipe gold add 2307.15043` (an arXiv link, id or title all work). The command looks the paper up on arXiv, shows it, and asks you to confirm.
2. It then asks for key facts, one per line. A blank line finishes. Nothing is saved until you finish: Ctrl-C or end of input at that point leaves the file untouched.
3. At least one fact is required (the command warns and saves nothing otherwise); three or more make a better test.
4. `rpipe gold list` shows `n/10` and every paper with its facts. `rpipe gold add-fact ID "fact"` adds one more fact later, and `rpipe gold remove ID` removes a paper.
5. Facts are numbered in `rpipe gold list` (`1.`, `2.`, ...). `rpipe gold edit-fact ID 2 "new text"` replaces fact 2, and `rpipe gold remove-fact ID 2` deletes it after a yes/no confirmation (`--yes` skips the question). Leave out the id, the number or the text and the command asks for it, showing the paper's numbered facts first. A number out of range, an empty replacement or an unknown paper is refused with nothing changed. The last remaining fact cannot be removed (a gold paper needs at least one); use `rpipe gold remove ID` to drop the whole paper.

Ids are compared without version suffixes, so `2307.15043v2` and `2307.15043` are the same paper.

Worked example (illustrative output; your numbers will differ):

```
$ rpipe gold add 2307.15043
Universal and Transferable Adversarial Attacks on Aligned Language Models (Andy Zou, 2023)
Add this paper? [Y/n]: y
Fact 1: Finds adversarial suffixes automatically by greedy and gradient-based search
Fact 2: The suffixes found on open models transfer to closed models such as ChatGPT
Fact 3 (blank line to finish):
Saved with 2 fact(s); 3 or more make a better test (rpipe gold add-fact).
Added 2307.15043: Universal and Transferable Adversarial Attacks ...
```

Fact numbers, not fact text, identify a fact: the numbers are what `list` shows, and typing a whole sentence to match it would be error-prone. The trade-off is that numbers shift after a removal, so the commands re-read the file each time and the guided prompt always shows the current numbering. Both new commands write the file atomically like `add-fact`, so a refused edit never leaves a half-written gold set.

### Why a gold set at all

We cannot judge a summary without knowing the right answer. If the model says "the paper improves accuracy by 12%", only someone who knows the paper can say whether that is true. The gold set records the right answers (the **ground truth**) once, so every summary the model writes, and every change to the model or the prompt, can be checked against the same fixed answers.

### Picking the 10 papers

Choose a mix, because each kind breaks summarizers differently:

- several papers you know well, so your facts are trustworthy;
- one long paper (tests the context-window truncation, see `summaries.md`);
- one old paper with no arXiv HTML version (tests the abstract-only path);
- one maths-heavy paper (equations are where models invent things).

### What a good key fact looks like

Specific and checkable: someone reading a summary can say yes or no without interpretation.

- Bad: "It is about adversarial attacks." (too vague; nearly any summary passes)
- Bad: "It is a great paper." (not checkable)
- Good: "The suffixes are found automatically, by a greedy search guided by gradients, rather than written by hand."
- Good: "Suffixes optimised on open-source models also work on closed models such as ChatGPT and Bard."

One idea per fact, so a summary can be marked as having it or missing it.

## Why it's built this way

- **Commands, not a hand-edited file**: members of the club can build their own gold set without touching JSON. The file stays plain JSON so it can be read or fixed by hand.
- **Reuse of seed lookup**: `rpipe gold add` uses the same link/id/title parsing and candidate-choice step as `rpipe profile add-seed`, so both accept the same input.
- **Facts are collected before anything is written**: an interrupt never leaves a paper with no facts. Rejected: saving the paper first and the facts after.
- **Atomic writes** (temp file then rename), as for the seed cache, so a crash never leaves a half-written file.
- **A broken file is an error, not an empty set**: an unreadable `gold.json` raises a `ConfigError` naming the file; silently treating it as empty would let the next `add` overwrite your work.
- **Created lazily**: `gold/` already exists in a new vault (`init` creates it), and the code creates it if an older vault lacks it. Only `eval/` is first created by `rpipe eval run`.

## How to run / check it

```
uv run rpipe gold add 2307.15043
uv run rpipe gold list
uv run rpipe gold add-fact 2307.15043 "..."
uv run rpipe gold edit-fact 2307.15043 2 "..."
uv run rpipe gold remove-fact 2307.15043 2
uv run rpipe gold remove 2307.15043
uv run pytest tests/test_gold.py -q
```

## Where the code is

`src/research_pipeline/gold.py` (`GoldPaper`, `load_gold`, `add_gold`, `remove_gold`, `add_fact`, `edit_fact`, `remove_fact`, `GOLD_TARGET`), the `gold_app` section of `src/research_pipeline/cli.py`, and `tests/test_gold.py`. The eval code is listed at the end of the second half below.

---

# Evaluation: `rpipe eval` (the accuracy check)

## What it does

`rpipe eval` answers one question: **are the model's summaries accurate?** It summarizes every paper in your gold set, shows you each summary next to the key facts you wrote, and records your yes or no. The result is a **pass mark** check: the model passes when at least 90% of the papers are graded accurate (`PASS_MARK = 0.9`; with 10 gold papers, 9 must be accurate).

## How it works

The full loop:

```
rpipe gold add ...   (x10)   build the answer key
rpipe eval run               summarize every gold paper, write the report
rpipe eval grade             for each paper: is the summary accurate? y/n + note
rpipe eval report            counts, PASS or FAIL, and which papers failed and why
```

1. **`rpipe eval run`** refuses if the gold set is empty (and tells you to use `rpipe gold add`), warns if it has fewer than 10 papers, then for each gold paper fetches the full text (the same fetcher as the nightly run), writes a summary and critique with the same code as the nightly run, and records the result. A paper with no HTML version is summarized from its abstract (looked up on arXiv by id) and labelled `ABSTRACT ONLY`. A paper that fails (model error, no text and no abstract) gets an error entry and the others carry on.
2. It writes two files in `<vault>/eval/`, named by date: `2026-11-21.json` (entries and your grades) and `2026-11-21.md`, a report you can read in any editor. Per paper the markdown has the key facts as a checklist, the summary, and, under each claim, the text of the section it cites, so you can judge a claim without opening the PDF. It ends with the automatic result: how many papers have every citation verified (`citations_ok`).
3. **`rpipe eval grade`** works on the latest eval (the newest file in `eval/`). For each paper not yet graded it shows the title, your key facts and the summary, then asks `Is this summary accurate? [y/n]` and an optional note. Each grade is written to disk (atomically) the moment you give it. Ctrl-C or end of input stops cleanly: grades already given are kept, and running `rpipe eval grade` again continues with the next ungraded paper.
4. **`rpipe eval report`** prints the counts: graded, accurate, citation-clean, PASS or FAIL against the pass mark, then the failed papers with your notes and the papers not graded yet. Ungraded papers count as **not** accurate, so a PASS is never claimed early. A paper whose summary failed to generate counts as failed.

Re-running `rpipe eval run` on the same day replaces that day's file and **clears that day's grades** (the summaries changed, so the old grades no longer apply). The command says so. A run on a later day makes a new file and keeps the old ones.

Sample report (illustrative output; your numbers will differ):

```
Eval 2026-11-21
Graded: 10 of 10
Accurate: 8 of 10 (80%); pass mark 90%
Citation-clean: 10 of 10
Result: FAIL

Failed:
  2307.15043 Universal and Transferable Adversarial Attacks ...: says 12% gain, paper says 2%
  1706.03762 Attention Is All You Need: missed that it drops recurrence entirely
```

### How to read a failure

Open the `.md` for that day and find the paper. Compare each unticked key fact with the claim that should cover it and the cited section text beside it. Three typical causes:

- the claim is **wrong** (a number or direction changed): the model invented or misread;
- the claim is **missing** (a key fact never appears): the summary is too thin, or the context was truncated (look for "truncated" in the paper's basis line);
- the paper was **abstract only**: the abstract simply did not contain the fact; this is a data limit, not a model fault.

### If the pass mark is not reached

Change one thing at a time, then run `rpipe eval run` again and grade again:

- **Model**: try a bigger one with `rpipe model set NAME` (see `llm.md`).
- **Prompt**: the instructions live in `src/research_pipeline/prompts.py`; failures that repeat across papers (for example always dropping numbers) point to a prompt change.
- **`llm_num_ctx`**: if failures say "truncated", the paper did not fit; raise the context window with `rpipe model set-context N` (see `llm.md`) if your memory allows.

## Why it's built this way

- **Grading is human.** An LLM judge shares the generator's blind spots: a model that misreads a paper may well misread it the same way when asked to check. The gold set exists to catch exactly that, by comparing against facts a person who knows the paper wrote down. An LLM-as-judge variant, useful for many more papers than you can grade by hand, is deferred to Phase 6.
- **The report carries the evidence.** Showing the cited section text beside each claim means grading takes a minute per paper, not a trip to the PDF. Rejected: grading from the summary alone.
- **Dates as keys, one file per day.** Easy to compare runs after a model change. Rejected: a single results file that each run overwrites, which would lose the history.
- **Grades saved one at a time.** Ten papers is a long sitting; an interrupt must not lose work.
- **Abstract from arXiv, not the database.** The gold set stores only id, title and facts, and the eval must work for papers never collected. `run_eval` takes an `abstract_for` function (the CLI passes the same arXiv lookup used by `gold add`), so tests stay offline.

## How to run / check it

```
uv run rpipe eval run
uv run rpipe eval grade
uv run rpipe eval report
uv run pytest tests/test_eval.py -q
```

## Where the code is

`src/research_pipeline/evaluate.py` (`run_eval`, `save_grade`, `report`, `EvalEntry`, `Grade`, `PASS_MARK`), the `eval_app` section of `src/research_pipeline/cli.py`, and `tests/test_eval.py`.
