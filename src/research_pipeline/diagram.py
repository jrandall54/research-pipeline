"""The diagram call (Phase 9): one more model call that draws how a paper's method works, as data.

The model answers with boxes and arrows (`Diagram`), or `null` when the method has no clear
shape. Code (not the model) checks the diagram; one retry with the problems listed, then it is
dropped. See "The diagram" in docs/guide/summaries.md.
"""

import re
from collections.abc import Callable

from research_pipeline.citations import section_texts
from research_pipeline.llm import LLM
from research_pipeline.models import Section
from research_pipeline.prompts import build_diagram_prompt, diagram_feedback
from research_pipeline.pruning import fit_to_budget
from research_pipeline.summary_models import Diagram, DiagramAnswer

MIN_NODES, MAX_NODES, MAX_LABEL_WORDS = 2, 8, 4

_STOP = frozenset(
    "the and for with from into over under that this via per its our are was not but can use "
    "using based of".split()
)
_WORD = re.compile(r"[a-z]{3,}")


def _stem(word: str) -> str:
    """Drop a trailing `s`/`es`, `ing`, `ed` or silent `e` (and a doubled last letter: planning -> plan)."""
    if word.endswith(("sses", "xes", "ches", "shes")):
        word = word[:-2]  # processes -> process, boxes -> box, matches -> match
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        word = word[:-1]
    for ending in ("ing", "ed"):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            word = word[: -len(ending)]
            break
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]  # cache/caches, encode/encoding, retrieve/retrieved meet at one stem
    if len(word) > 3 and word[-1] == word[-2]:
        word = word[:-1]
    return word


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP}


# Words that fit almost any paper's diagram; a box needs a word outside this list (compared by
# stem) to count as naming something specific. See "The diagram" in docs/guide/summaries.md.
GENERIC_WORDS = frozenset(
    "agent action observation thought input output feedback evaluation evaluator user environment "
    "model result query response llm data step task final process system module tool memory plan "
    "answer prompt utterance natural language".split()
)
_GENERIC_STEMS = frozenset(_stem(w) for w in GENERIC_WORDS)
MIN_SPECIFIC_NODES = 2
TOO_GENERIC = "too generic (fewer than 2 boxes name something specific to this paper)"
_FRONT = ("abstract", "introduction", "conclusion")
_NUMBER_PREFIX = re.compile(r"^[\d.\s]*")


def _is_front(title: str) -> bool:
    """An abstract, introduction or conclusion title (after any leading section number)."""
    return _NUMBER_PREFIX.sub("", title.strip().casefold()).startswith(_FRONT)


def check_diagram(d: Diagram, sections: list[Section], abstract: str) -> list[str]:
    """Plain-words problems with a diagram; empty means it passes."""
    problems: list[str] = []
    n = len(d.nodes)
    if not MIN_NODES <= n <= MAX_NODES:
        problems.append(f"it has {n} nodes; it needs {MIN_NODES} to {MAX_NODES}")
    ids = [x.id for x in d.nodes]
    if len(set(ids)) != len(ids):
        problems.append("node ids are not unique")
    for e in d.edges:
        for end in (e.source, e.target):
            if end not in ids:
                problems.append(f"an arrow points to {end!r}, which is not a node")
    paper_words = _stems("\n".join([abstract, *(f"{s.title}\n{s.text}" for s in sections)]))
    for x in d.nodes:
        if len(x.label.split()) > MAX_LABEL_WORDS:
            problems.append(f"label {x.label!r} is more than {MAX_LABEL_WORDS} words")
        if section_texts(x.sections, sections, abstract) is None:
            problems.append(f"node {x.label!r} cites a section the paper does not have: {x.sections}")
        if x.sections and all(_is_front(t) for t in x.sections):
            problems.append(f"node {x.label!r} cites only the abstract, introduction or conclusion")
        missing = sorted(_stems(x.label) - paper_words)
        if missing:
            problems.append(f"label {x.label!r} uses words the paper never uses: {', '.join(missing)}")
    if sum(1 for x in d.nodes if _stems(x.label) - _GENERIC_STEMS) < MIN_SPECIFIC_NODES:
        problems.append(TOO_GENERIC)
    return problems


def make_diagram(
    llm: LLM, title: str, sections: list[Section] | None, abstract: str, num_ctx: int,
    on_phase: Callable[[str], None] | None = None,
) -> tuple[Diagram | None, list[str]]:
    """Ask for a diagram, check it, retry once with the problems listed, then drop it.

    Returns ``(diagram, [])``; ``(None, [])`` when the model says the paper has no clear shape;
    ``(None, ["diagram: <problems>"])`` when it is dropped. ``sections=None`` means abstract only:
    no call is made (a diagram must cite a method section). A diagram whose only problem is
    `TOO_GENERIC` is dropped without the retry (a retry would relabel the same generic loop).
    ``on_phase`` is told ``diagram``, then ``diagram_retry`` before the second call. A model failure
    raises `LlmError` (the caller decides what that means).
    """
    if sections is None:
        return None, []
    basis = "full_text"
    shown = fit_to_budget(sections, num_ctx).sections
    system, user = build_diagram_prompt(title, shown, basis)
    if on_phase is not None:
        on_phase("diagram")
    answer = llm.generate(system, user, DiagramAnswer)
    if answer.diagram is None:
        return None, []
    problems = check_diagram(answer.diagram, sections, abstract)
    if not problems:
        return answer.diagram, []
    if problems == [TOO_GENERIC]:
        return None, ["diagram: " + TOO_GENERIC]
    if on_phase is not None:
        on_phase("diagram_retry")
    answer = llm.generate(system, user + diagram_feedback(problems, [s.title for s in shown]), DiagramAnswer)
    if answer.diagram is None:
        return None, []
    problems = check_diagram(answer.diagram, sections, abstract)
    if not problems:
        return answer.diagram, []
    return None, ["diagram: " + "; ".join(problems)]
