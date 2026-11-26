# Phase 9.1 — Stricter diagrams: Implementation Plan

> **Phase 9.1** · Status: building · Design: none (small phase; decided with the owner 2026-11-25, after the Phase 9 eval) · Roadmap: [ROADMAP.md](../ROADMAP.md)

**Goal:** A diagram only for papers whose method really has a shape. Phase 9's eval drew a diagram for all 10 gold papers, 8 of them the same generic loop (e.g. "Natural Language Utterance → Thought → Action → Observation"). The owner: many papers don't make sense as a diagram. "No diagram" must become the normal answer, and generic diagrams must be dropped by code.

**Approach (Claude's call, owner said "yes please"):**

1. **Prompt: no diagram is the default.** The diagram task says: answer `null` unless the paper itself describes a method or system with distinct, named parts or stages (in its method section, or a figure of its method). Generic agent loops (observe, think, act; input, process, output) and plain "the model is evaluated on a benchmark" flows do not count. Expect most papers to get `null`.
2. **Code: drop generic diagrams.** New check in `diagram.check_diagram`: a diagram is "too generic" when fewer than 2 of its node labels contain a specific word, where a specific word is a content word (the existing stemming) that is not in a small generic list (`agent, action, observation, thought, input, output, feedback, evaluation, evaluator, user, environment, model, result, query, response, llm, data, step, task, final, process, system, module, tool, memory, plan, answer, prompt, utterance, natural, language`, each compared by stem). Reason string: `diagram: too generic (fewer than 2 boxes name something specific to this paper)`. A too-generic diagram is dropped without the retry (a retry would just relabel the same generic loop).
3. **Code: method sections only.** Every node must cite at least one section that is not the abstract, introduction or conclusion (title, after any leading section number, starts with "abstract", "introduction" or "conclusion", any case). An abstract-only paper therefore never gets a diagram (no call is made: `make_diagram` returns `(None, [])` for abstract-only papers without calling the model). Reason: `node 'X' cites only the abstract, introduction or conclusion`. This one keeps the retry.
4. `PROMPT_VERSION` bumped to `2026-11-p9b`.

**Rejected:** turning diagrams off by default (they help on papers with a real architecture, like the robot-agents mockup); asking the model to rate its own diagram (it approves its own output); an image check (no tool can tell a useful diagram from a generic one better than these rules for the cost).

## Tasks

- [x] Prompt text (item 1) and version bump; tests that the diagram task says null is the default and names the generic loops.
- [x] `check_diagram` generic rule (item 2) and method-section rule (item 3); `make_diagram`: no call for abstract-only papers, no retry when the only problem is "too generic". Tests: the CodeReAct-style loop (Natural Language Utterance, Thought, Action, Observation, Final Output) is too generic; the robot-agents diagram (hyper-agent, arm + gripper, torso, chassis) passes; a node citing only "1 Introduction" fails; "5.1 Main Results" and "3 Method" pass; abstract-only makes zero model calls; too-generic makes one call, not two.
- [x] Docs: `docs/guide/summaries.md` "The diagram" (the new rules, why, worked example with the two diagrams above), `docs/guide/glossary.md` if a term is new, `evaluation.md` if the report lines change.
- [ ] Gold eval re-run (controller): expect far fewer diagrams; facts covered unchanged (the summary prompt is unchanged). Record in `summaries.md` "Measured".
- [ ] Roadmap row 9.1, handoff, merge, push, restart the app.
