# Evaluation: the gold set

## What it does

The gold set is a short list of papers you know well, each with a list of **key facts**: things any correct summary of that paper must get right. It is stored in your vault as `gold/gold.json` and managed with `rpipe gold` commands. The second half of this page covers `rpipe eval`, which uses the gold set to check the summaries the model writes.

## How it works

1. You add a paper: `rpipe gold add 2307.15043` (an arXiv link, id or title all work). The command looks the paper up on arXiv, shows it, and asks you to confirm.
2. It then asks for key facts, one per line. A blank line finishes. Nothing is saved until you finish: Ctrl-C or end of input at that point leaves the file untouched.
3. At least one fact is required (the command warns and saves nothing otherwise); three or more make a better test.
4. `rpipe gold list` shows `n/10` and every paper with its facts. `rpipe gold add-fact ID "fact"` adds one more fact later, and `rpipe gold remove ID` removes a paper. The facts in `list` are numbered (see the sample below).
5. Facts are numbered in `rpipe gold list` (`1.`, `2.`, ...). `rpipe gold edit-fact ID 2 "new text"` replaces fact 2, and `rpipe gold remove-fact ID 2` deletes it after a yes/no confirmation (`--yes` skips the question). Leave out the id, the number or the text and the command asks for it, showing the paper's numbered facts first. A number out of range, an empty replacement or an unknown paper is refused with nothing changed. The last remaining fact cannot be removed (a gold paper needs at least one); use `rpipe gold remove ID` to drop the whole paper.

Ids are compared without version suffixes (and `arXiv:` prefixes and letter case are ignored), so `2307.15043v2` and `2307.15043` are the same paper.

Fact text is cleaned the same way as topics (see `profile.md`): control characters such as a terminal escape code become a space, invisible bidi-control characters are dropped, and runs of spaces collapse. This applies to facts typed in `gold add`, `add-fact` and `edit-fact`; a fact that is empty after cleaning is refused.

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

Afterwards `rpipe gold list` shows the numbers that `edit-fact` and `remove-fact` use (illustrative output):

```
$ rpipe gold list
Gold set: 1/10 papers

2307.15043  Universal and Transferable Adversarial Attacks on Aligned Language Models
  1. Finds adversarial suffixes automatically by greedy and gradient-based search
  2. The suffixes found on open models transfer to closed models such as ChatGPT
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

### Choosing papers

**What it does.** `rpipe gold suggest` looks through the papers your vault has already summarized and proposes some for the gold set, picked so that they run from short to long.

**How it works.**

1. It takes every paper that has both a stored summary and stored full text and is not already in the gold set.
2. It measures each one: the characters in all sections except back matter (references, acknowledgements, appendices), divided by 3 (the pipeline's rough characters-per-token ratio). This is the approximate length of the text the summarizer actually reads.
3. It sorts by that length and picks `--count` papers (default 10) evenly along the list. The shortest and the longest are always included. With 9 candidates and `--count 3` you get the 1st, 5th and 9th. Fewer candidates than `--count` means you get all of them.

**Why a spread of lengths.** Short papers fit the model's context window whole, so they are the easy case. Long papers are where the pipeline cuts text (truncation, and dropping back matter first; see `summaries.md`), which is exactly where a summary can silently lose facts. An answer key made only of short papers would never notice that failure.

**How to run it.**

```
uv run rpipe gold suggest
uv run rpipe gold suggest --count 5
```

It prints each id with `~N tokens` and the title, then reminds you of the next step, `rpipe gold draft ID ...`. With nothing summarized yet it says so and points you to `rpipe run` or `rpipe gold add`.

**Where the code is.** `suggest_papers` and `Suggestion` in `src/research_pipeline/gold.py`, the `gold suggest` command in `src/research_pipeline/cli.py`, tests in `tests/test_gold.py`.

### Drafting key facts

**What it does.** `rpipe gold draft ID [ID ...]` asks the judge (Claude, see "The judge" below) to draft 3 to 5 key facts for each paper, then walks you through them so that you keep, edit or drop each one. You stay the author of the answer key; the model only saves you typing.

**How it works.** For each id, in order:

1. If the paper is already in the gold set it is skipped with a message.
2. The title comes from arXiv and the full text from arXiv's HTML version. If arXiv has no paper for the id, or no usable HTML text, the command says so, points to `rpipe gold add ID` (type the facts yourself) and moves on to the next id.
3. The judge drafts the facts. This is the slow step, so it shows progress (stage `gold draft`, one step per paper). The progress display is closed before any question is asked, so prompts are never overwritten.
4. Each drafted fact is shown numbered, and you answer `Keep, edit or drop? [k/e/d]`. Enter alone means keep. `e` asks for the replacement text (cleaned like any fact; an empty reply keeps the draft). `d` discards it.
5. You are then asked for extra facts of your own, one per line, a blank line to finish.
6. If at least one fact remains the paper is saved; otherwise "No facts kept, so nothing was saved".

Ctrl-C or end of input during the review prints "Stopped. Nothing was saved for ID." and stops; papers finished earlier stay saved. If the judge is unavailable (Claude Code not installed or not logged in) the command stops with one error before fetching anything.

Worked example (illustrative output; the drafts will differ):

```
$ rpipe gold draft 2307.15043
The paper text is sent to Claude to draft the facts. You review every one before it is saved.

Universal and Transferable Adversarial Attacks on Aligned Language Models
  1. Adversarial suffixes are found automatically by greedy, gradient-guided search.
Keep, edit or drop? [k/e/d]: k
  2. The paper is about attacks.
Keep, edit or drop? [k/e/d]: d
  3. Suffixes found on open models also work on closed models such as ChatGPT.
Keep, edit or drop? [k/e/d]: e
New text (empty keeps the draft): Suffixes transfer to ChatGPT, Bard and Claude.
Key facts a correct summary must get right (specific and checkable).
Fact 1:
Added 2307.15043 with 2 fact(s).
```

**Why it's built this way.** Writing good facts from scratch is the part people skip; reviewing drafts is quicker and still puts your judgement in the key. Facts are saved only after the whole review of that paper, so an interrupt never leaves half a paper. The paper text goes to Anthropic (see "The judge"), which is why the command says so each time. Rejected: saving drafts unreviewed (the key would then be graded by the same kind of model that writes the summaries).

**How to run it.** `uv run rpipe gold draft 2307.15043 2401.01234`. Pick ids from `rpipe gold suggest`. Tests: `uv run pytest tests/test_gold_draft.py -q`.

**Where the code is.** `gold_draft` and `_review_drafted` in `src/research_pipeline/cli.py` (the judge itself is in `judge.py`), `Pipeline.make_judge`, tests in `tests/test_gold_draft.py`.

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

## Evaluation: `rpipe eval` (the accuracy check)

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
   Progress goes to stderr (stdout is unchanged): a live bar on a terminal, plain lines otherwise, one step per gold paper (`ok` or `failed`, the same outcome the report records), and a `still waiting: ...` line after 30 s of silence:

   ```
   eval…
     [1/10] 2401.00000 ok
     [2/10] 2401.00001 failed: model fell over
   eval done (6m12s)
   ```

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

- **Grading is human.** An LLM judge shares the generator's blind spots: a model that misreads a paper may well misread it the same way when asked to check. The gold set exists to catch exactly that, by comparing against facts a person who knows the paper wrote down. The automatic judge (see "Automatic grading") is a second opinion that scales to more papers, not a replacement: where you have graded a paper, your grade wins and the "Accurate" line counts human grades only.
- **The report carries the evidence.** Showing the cited section text beside each claim means grading takes a minute per paper, not a trip to the PDF. Rejected: grading from the summary alone.
- **Dates as keys, one file per day.** Easy to compare runs after a model change. Rejected: a single results file that each run overwrites, which would lose the history.
- **Grades saved one at a time.** Ten papers is a long sitting; an interrupt must not lose work.
- **Abstract from arXiv, not the database.** The gold set stores only id, title and facts, and the eval must work for papers never collected. `run_eval` takes an `abstract_for` function (the CLI passes the same arXiv lookup used by `gold add`), so tests stay offline.

## How to run / check it

The full flow, from an empty answer key to a compared result:

```
uv run rpipe gold suggest                  # pick papers across short and long
uv run rpipe gold draft ID [ID ...]        # the judge drafts key facts; you review them
uv run rpipe eval run --judge              # summarize each paper, grade every key fact
uv run rpipe eval report                   # counts, and the comparison with the last run
uv run rpipe eval grade                    # optional: your own yes/no (your grade wins)
uv run pytest tests/test_eval.py -q
```

Without `--judge`, `rpipe eval run` works as before and needs no Claude.

## Where the code is

`src/research_pipeline/evaluate.py` (`run_eval`, `save_grade`, `report`, `judged_counts`, `compare`, `EvalEntry`, `EvalSetup`, `Grade`, `PASS_MARK`), the `eval_app` section of `src/research_pipeline/cli.py`, and `tests/test_eval.py`.

## Automatic grading

### What it does

`rpipe eval run --judge` also asks the judge (Claude Code, see "The judge") to grade each summary against the key facts of that paper. The verdicts are stored beside the summary in the eval file, so the report can say how many facts the summaries got right without you reading every one.

### How it works

1. Before anything is summarized, the command builds the judge. If Claude Code is missing or not logged in it stops with the reason (exit code 1) and nothing is run. It also reminds you that the paper text is sent to Claude.
2. Each paper is summarized as before. Then the judge gets the same sections the summarizer got, the numbered key facts and the summary text. The progress line for that paper says `judging` while it waits.
3. Each key fact ends up with one of four marks:
   - **covered**: the summary states it correctly;
   - **missed**: the summary does not mention it;
   - **contradicted**: the summary says something that conflicts with it (the worst case);
   - **not judged**: the judge gave no ruling for that fact. This is counted separately and never as covered.
4. The file also records a **setup** (backend, model, context size, prompt version, judge model), so you can tell later which configuration produced a result.
5. If the judge is the same backend and model as the summarizer, the run is marked **self-graded** and the report says so. It is allowed, but remember the judge then shares the summarizer's blind spots.
6. If Claude's usage limit is reached, judging stops for the rest of the run but summaries carry on; the remaining papers get the note "usage limit reached". Any other judge error skips only that paper. The report shows `Judged: N of M papers`.

Sample (illustrative output):

```
Judged: 9 of 10 papers
Facts covered: 31 of 38 (missed 4, contradicted 1, not judged 2)
Setup: ollama/some-model, context 32768, prompts 2026-11-p4
```

### Why it's built this way

- The key facts were reviewed by you, so the judge only compares text against facts you approved. It is cheaper than grading 40 facts by hand and consistent between runs.
- Human grades stay and win. The judge's verdicts never change the "Accurate" line.
- A fact the judge skipped is "not judged", not covered, so a sloppy judge cannot inflate the score.
- A usage limit stops only the judging, so a long summarizing run is never thrown away.

## Comparing runs

### What it does

After a judged run, `rpipe eval report` compares it with the latest earlier eval that also has verdicts, and ends with `Change: PASS` or `Change: FAIL (reason)`. This is the yardstick for any change to the model, the prompts or the context size.

### How it works

Only papers judged in both runs count, and for each paper only the facts judged in both. The change passes when all three hold:

- facts covered dropped by **at most 1**;
- contradicted facts did **not rise**;
- unverified citations (claims citing a section that does not exist) did **not rise**.

With no paper in common, or no earlier judged run, the report says "No earlier judged run to compare."

### Why it's built this way

One run is enough because the summaries are made at temperature 0 (the model always picks its most likely word, so the same input gives nearly the same output); a difference between two runs is then mostly your change, not luck. The allowed drop of 1 absorbs the small wobble that remains and the judge's own noise. Contradictions and bad citations get no allowance because they are the failures that mislead a reader.

### Where the code is

`run_eval`, `judged_counts`, `compare` and `report` in `src/research_pipeline/evaluate.py`, the `--judge` option of `eval_run` in `src/research_pipeline/cli.py`, tests in `tests/test_eval.py`.

## The judge

### What it does

The judge is a second model that does two jobs for the answer key (the gold set): it drafts 3 to 5 key facts for a paper, which you then review and edit, and it grades a summary fact by fact, saying for each one whether the summary **covered** it, **missed** it or **contradicted** it, with a one-line reason.

### How it works

1. `make_judge` finds Claude Code (the `claude` program) and checks you are logged in. It makes no model call, so it is quick and costs nothing.
2. `draft_facts` sends the paper (fitted to the model's size limit, with any text that imitates our `<paper>` tag defused) and asks for the facts a correct summary must get right: main claim, method, a key number, the main limitation. Replies are cleaned (whitespace tidied, empties and duplicates dropped) and capped at 5. If nothing usable is left it raises an error instead of saving an empty key.
3. `judge_summary` sends the numbered facts, the paper and the summary rendered as plain text, and gets one verdict per fact. Each verdict names its fact by number. The result has one slot per fact, in order; a fact the judge did not rule on, or a verdict with a number that matches no fact, leaves that slot as `None` ("not judged"), which callers show as unknown rather than guessing.

Worked example: facts `["f1","f2","f3"]` and the judge answers for numbers 2, 2, 1, 9. The result is `[verdict 1, first verdict 2, None]`: the repeat and the stray 9 are ignored and fact 3 is shown as not judged.

### Why it's built this way

- **Always Claude Code, whichever backend summarizes.** A small local model is a weak judge. This means the paper text is sent to Anthropic when you draft or grade, even if your summaries are made locally; use the judge only for papers you are happy to share.
- **It may grade its own work.** If Claude Code also wrote the summary, the judge is the same model and shares its blind spots. `is_self_graded` detects this so the report can say so. The human review of the facts stays the real check.
- **Verdicts are matched by number, not position**, so a judge that skips or reorders facts cannot shift a verdict onto the wrong fact.
- Rejected: asking for a pass/fail score only. Per-fact verdicts show what was missed.

### How to run / check it

```
uv run pytest tests/test_judge.py -q
```

`rpipe gold draft` uses the judge to draft key facts and `rpipe eval run --judge` uses it to grade. If it is unavailable the error says to install Claude Code, log in, or run `rpipe model setup`.

### Where the code is

`src/research_pipeline/judge.py` (`make_judge`, `draft_facts`, `judge_summary`, `is_self_graded`, `JudgeUnavailable`) and `tests/test_judge.py`.
