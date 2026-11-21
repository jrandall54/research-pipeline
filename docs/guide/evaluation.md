# Evaluation: the gold set

## What it does

The gold set is a short list of papers you know well, each with a list of **key facts**: things any correct summary of that paper must get right. It is stored in your vault as `gold/gold.json` and managed with `rpipe gold` commands. A later step (`rpipe eval`, documented in the second half of this page once it exists) uses it to score the summaries the model writes.

## How it works

1. You add a paper: `rpipe gold add 2307.15043` (an arXiv link, id or title all work). The command looks the paper up on arXiv, shows it, and asks you to confirm.
2. It then asks for key facts, one per line. A blank line finishes. Nothing is saved until you finish: Ctrl-C or end of input at that point leaves the file untouched.
3. At least one fact is required (the command warns and saves nothing otherwise); three or more make a better test.
4. `rpipe gold list` shows `n/10` and every paper with its facts. `rpipe gold add-fact ID "fact"` adds one more fact later, and `rpipe gold remove ID` removes a paper.

Ids are compared without version suffixes, so `2307.15043v2` and `2307.15043` are the same paper.

Worked example:

```
$ rpipe gold add 2307.15043
Universal and Transferable Adversarial Attacks on Aligned Language Models (Andy Zou, 2023)
Add this paper? [Y/n]: y
Fact 1: Finds adversarial suffixes automatically by greedy and gradient-based search
Fact 2: The suffixes found on open models transfer to closed models such as ChatGPT
Fact 3:
Saved with 2 fact(s); 3 or more make a better test (rpipe gold add-fact).
Added 2307.15043: Universal and Transferable Adversarial Attacks ...
```

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
- **Created lazily**: `gold/` already exists in a new vault, and the code creates it if an older vault lacks it.

## How to run / check it

```
uv run rpipe gold add 2307.15043
uv run rpipe gold list
uv run rpipe gold add-fact 2307.15043 "..."
uv run rpipe gold remove 2307.15043
uv run pytest tests/test_gold.py -q
```

## Where the code is

`src/research_pipeline/gold.py` (`GoldPaper`, `load_gold`, `add_gold`, `remove_gold`, `add_fact`, `GOLD_TARGET`), the `gold_app` section of `src/research_pipeline/cli.py`, and `tests/test_gold.py`.
