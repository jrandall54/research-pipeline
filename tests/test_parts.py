"""Reading a paper in parts: groups, chunks, notes, condense, combine (parts.py)."""

import pytest
from conftest import FakeLLM

from research_pipeline.citations import claims, generate_checked
from research_pipeline.models import Section
from research_pipeline.parts import (
    FRONT_WORDS,
    MAX_CONDENSE_ROUNDS,
    SPLIT_BELOW_CTX,
    chunk_to_budget,
    read_in_parts,
    resolve_mode,
    split_groups,
)
from research_pipeline.prompts import COMBINE_SYSTEM, CONDENSE_SYSTEM, NOTES_SYSTEM
from research_pipeline.pruning import CHARS_PER_TOKEN, RESERVED_TOKENS
from research_pipeline.summary_models import Claim, Critique, Notes, Summary


def S(title: str, n: int = 100) -> Section:
    return Section(title=title, text="x" * n)


def budget(num_ctx: int) -> int:
    return (num_ctx - RESERVED_TOKENS) * CHARS_PER_TOKEN


def notes(*cites: str, text: str = "A note.") -> Notes:
    return Notes(claims=[Claim(text=f"{text} ({c})", sections=[c]) for c in cites])


def summary_citing(cite: str) -> Summary:
    c = Claim(text=f"From {cite}.", sections=[cite])
    return Summary(one_line="One line.", problem=c, approach=c, results=c, limitations=c)


def critique_citing(cite: str) -> Critique:
    c = Claim(text=f"About {cite}.", sections=[cite])
    return Critique(strengths=[c], weaknesses=[c], questions=["Why?"])


def first_heading(user: str) -> str:
    return user.split("\n## ", 1)[1].split("\n", 1)[0]


class SchemaLLM(FakeLLM):
    """Answers by schema and system prompt, like conftest.AlwaysSummary, recording every call."""

    def __init__(self, note, condensed, cite):
        super().__init__([])
        self.note, self.condensed, self.cite = note, condensed, cite

    def generate(self, system, user, schema):
        self.calls.append((system, user))
        if schema is Notes:
            return self.note(first_heading(user)) if system == NOTES_SYSTEM else self.condensed()
        if schema is Summary:
            return summary_citing(self.cite)
        return critique_citing(self.cite)


# --- split_groups ---------------------------------------------------------------------------


def test_front_words():
    assert FRONT_WORDS == (
        "abstract", "introduction", "conclusion", "discussion", "limitation", "future work", "summary",
    )


def test_split_groups_by_heading():
    groups, dropped = split_groups([S("Abstract"), S("1 Introduction"), S("2 Method"), S("3 Results"),
                                    S("4 Conclusion"), S("References")], 32768)
    assert [(f, [s.title for s in g]) for f, g in groups] == [
        ("front", ["Abstract", "1 Introduction", "4 Conclusion"]), ("body", ["2 Method", "3 Results"])]
    assert [d.title for d in dropped] == ["References"]


def test_subsection_follows_its_parent():
    groups, _ = split_groups([S("1 Introduction"), S("1.1 Motivation"), S("2 Method")], 32768)
    assert [(f, [s.title for s in g]) for f, g in groups] == [
        ("front", ["1 Introduction", "1.1 Motivation"]), ("body", ["2 Method"])]


def test_appendix_joins_body_when_it_fits():
    groups, dropped = split_groups([S("Abstract"), S("2 Method"), S("Appendix A Proofs")], 32768)
    assert [s.title for s in groups[1][1]] == ["2 Method", "Appendix A Proofs"]
    assert dropped == []


def test_appendix_dropped_when_body_too_long():
    b = budget(8192)
    groups, dropped = split_groups(
        [S("Abstract"), S("2 Method", b - 100), S("Appendix A Proofs", 500), S("References")], 8192)
    assert [s.title for s in groups[1][1]] == ["2 Method"]
    assert [(d.title, d.reason) for d in dropped] == [("Appendix A Proofs", "appendix"), ("References", "non_paper")]


def test_split_without_front_falls_back_to_chunks():
    groups, dropped = split_groups([S("1"), S("2"), S("3"), S("Appendix A")], 32768)
    assert [(f, [s.title for s in g]) for f, g in groups] == [("general", ["1", "2", "3", "Appendix A"])]
    assert dropped == []


# --- chunk_to_budget ------------------------------------------------------------------------


def test_chunks_are_consecutive_and_greedy():
    b = budget(8192)
    secs = [S("A", b // 2), S("B", b // 2), S("C", 10)]
    chunks = chunk_to_budget(secs, 8192)
    assert [[s.title for s in c] for c in chunks] == [["A"], ["B", "C"]]


def test_oversized_section_is_split_at_paragraphs():
    chunks = chunk_to_budget([Section(title="5 Experiments", text=("p" * 9000 + "\n\n") * 8)], 16384)
    assert len(chunks) > 1 and all(s.title == "5 Experiments" for c in chunks for s in c)
    assert all(sum(len(s.title) + len(s.text) for s in c) <= (16384 - 4000) * 3 for c in chunks)
    assert "".join(s.text for c in chunks for s in c).count("p") == 9000 * 8  # nothing lost


def test_paragraph_longer_than_budget_is_hard_cut():
    b = budget(8192)
    chunks = chunk_to_budget([Section(title="T", text="q" * (b * 2))], 8192)
    assert len(chunks) == 3
    assert all(sum(len(s.title) + len(s.text) for s in c) <= b for c in chunks)
    assert sum(len(s.text) for c in chunks for s in c) == b * 2


# --- citations on Notes --------------------------------------------------------------------


def test_claims_of_notes():
    n = notes("Intro", "Method")
    assert claims(n) == n.claims


def test_summarize_still_exports_citation_helpers():
    from research_pipeline import summarize

    assert summarize.claims is claims and summarize.unknown_citations is not None
    assert summarize.generate_checked is generate_checked


# --- read_in_parts -------------------------------------------------------------------------


def test_read_in_parts_calls_and_result():
    llm = FakeLLM([notes("Intro"), notes("Method"), summary_citing("Intro"), critique_citing("Method")])
    phases: list[str] = []
    ps = read_in_parts(llm, "T", [("front", [S("Intro")]), ("body", [S("Method")])], 32768,
                       read_mode="split", dropped=[], on_phase=phases.append)
    assert ps.read_mode == "split" and ps.parts == 2 and not ps.body_cut and ps.unverified == []
    assert ps.basis == "full_text" and ps.needed_ctx is None and ps.dropped == []
    assert phases == ["notes 1/2", "notes 2/2", "summary", "critique"]
    prompts = llm.calls
    assert [p[0] for p in prompts] == [NOTES_SYSTEM, NOTES_SYSTEM, COMBINE_SYSTEM, COMBINE_SYSTEM]
    assert "Look for: the core claim" in prompts[0][1] and "part 1 of 2" in prompts[0][1]
    assert "Look for: the method" in prompts[1][1] and "part 2 of 2" in prompts[1][1]
    assert prompts[2][1].startswith(prompts[3][1][: prompts[3][1].index("Task:")])  # shared prefix
    assert "(Intro)" in prompts[2][1] and "(Method)" in prompts[2][1]  # notes in paper order
    assert prompts[2][1].index("(Intro)") < prompts[2][1].index("(Method)")


def test_dropped_passed_through_and_map_reduce_mode():
    from research_pipeline.summary_models import Dropped

    d = [Dropped(title="References", chars=10, reason="non_paper")]
    llm = FakeLLM([notes("1"), summary_citing("1"), critique_citing("1")])
    ps = read_in_parts(llm, "T", [("general", [S("1")])], 32768, read_mode="map_reduce", dropped=d)
    assert ps.read_mode == "map_reduce" and ps.parts == 1 and ps.dropped == d
    assert "Look for: the core claim, what is new, the limitations the paper states, the method" in llm.calls[0][1]


def test_notes_see_only_their_chunk_titles_combine_sees_all():
    llm = FakeLLM([notes("Method"), notes("Method"), notes("Method"), summary_citing("Intro"),
                   critique_citing("Intro")])
    phases: list[str] = []
    ps = read_in_parts(llm, "T", [("front", [S("Intro")]), ("body", [S("Method")])], 32768,
                       read_mode="split", dropped=[], on_phase=phases.append)
    # "Method" is not in the front chunk: the front notes call retries, then it is verified there.
    assert phases == ["notes 1/2", "retry", "notes 2/2", "summary", "critique"]
    assert '"Intro"' in llm.calls[1][1].split("</paper>", 1)[1]
    # A notes miss is kept apart: `unverified` means the final claims only, as in whole mode.
    assert ps.notes_unverified == ["A note. (Method)"] and ps.unverified == []


def test_notes_bad_citation_retries_once():
    llm = FakeLLM([notes("Nope"), notes("Nope"), summary_citing("Intro"), critique_citing("Intro")])
    ps = read_in_parts(llm, "T", [("general", [S("Intro")])], 32768, read_mode="map_reduce", dropped=[])
    assert len(llm.calls) == 4
    assert "Nope" in llm.calls[1][1].split("</paper>", 1)[1]  # citation feedback on the retry
    assert ps.notes_unverified == ["A note. (Nope)"] and ps.unverified == []


def test_combine_unverified_is_reported():
    llm = FakeLLM([notes("Intro"), summary_citing("Gone"), summary_citing("Gone"), critique_citing("Intro")])
    ps = read_in_parts(llm, "T", [("general", [S("Intro")])], 32768, read_mode="map_reduce", dropped=[])
    assert ps.unverified == ["From Gone."] * 4 and ps.notes_unverified == []


def test_invented_titles_are_dropped_from_notes_before_combining():
    mixed = Notes(claims=[
        Claim(text="Half right.", sections=["Nope", "Intro"]),
        Claim(text="All wrong.", sections=["Nope"]),
        Claim(text="Fine.", sections=["Intro"]),
    ])
    llm = FakeLLM([mixed, mixed, summary_citing("Intro"), critique_citing("Intro")])
    ps = read_in_parts(llm, "T", [("general", [S("Intro")])], 32768, read_mode="map_reduce", dropped=[])
    combine = llm.calls[2][1]
    block = combine[combine.index("<notes>"): combine.index("</notes>")]
    assert "Nope" not in block and "All wrong." not in block
    assert '- Half right. [sections: ["Intro"]]' in block and "Fine." in block
    assert ps.notes_unverified == ["Half right.", "All wrong."]  # still recorded


def test_condense_misses_are_notes_unverified():
    num_ctx = 8192
    b = budget(num_ctx)
    titles = [f"{i} Part" for i in range(1, 13)]
    llm = SchemaLLM(
        note=lambda cite: Notes(claims=[Claim(text="y" * 200, sections=[cite])] * 10),
        condensed=lambda: Notes(claims=[Claim(text="Made up.", sections=["Nope"]),
                                        Claim(text="Real.", sections=[titles[0]])]),
        cite=titles[0],
    )
    ps = read_in_parts(llm, "T", [("general", [S(t, b - 50) for t in titles])], num_ctx,
                       read_mode="map_reduce", dropped=[])
    assert ps.unverified == [] and "Made up." in ps.notes_unverified
    combine = [u for s, u in llm.calls if s == COMBINE_SYSTEM][0]
    assert "Made up." not in combine and "Real." in combine


def test_notes_condense_when_too_long():
    num_ctx = 8192
    b = budget(num_ctx)
    titles = [f"{i} Part" for i in range(1, 13)]
    secs = [S(t, b - 50) for t in titles]  # one chunk each
    llm = SchemaLLM(
        note=lambda cite: Notes(claims=[Claim(text="y" * 200, sections=[cite])] * 10),
        condensed=lambda: notes(titles[0]),
        cite=titles[0],
    )
    phases: list[str] = []
    ps = read_in_parts(llm, "T", [("general", secs)], num_ctx, read_mode="map_reduce", dropped=[],
                       on_phase=phases.append)
    assert ps.parts == 12 and not ps.body_cut and ps.unverified == []
    assert phases.count("condense") >= 1
    systems = [s for s, _ in llm.calls]
    assert systems.count(NOTES_SYSTEM) == 12 and systems.count(CONDENSE_SYSTEM) >= 2
    for system, user in llm.calls:
        if system in (CONDENSE_SYSTEM, COMBINE_SYSTEM):
            block = user[user.index("<notes>"): user.index("</notes>") + len("</notes>")]
            assert len(block) <= b


def test_condense_gives_up_after_three_rounds():
    num_ctx = 8192
    b = budget(num_ctx)
    titles = [f"{i} Part" for i in range(1, 7)]
    big = Notes(claims=[Claim(text="z" * 300, sections=[titles[0]])] * 40)  # over the budget alone
    llm = SchemaLLM(note=lambda cite: Notes(claims=[Claim(text="z" * 300, sections=[cite])] * 10), condensed=lambda: big, cite=titles[0])
    phases: list[str] = []
    ps = read_in_parts(llm, "T", [("general", [S(t, b - 50) for t in titles])], num_ctx,
                       read_mode="map_reduce", dropped=[], on_phase=phases.append)
    assert ps.body_cut is True and ps.needed_ctx is None
    assert phases.count("condense") == MAX_CONDENSE_ROUNDS == 3
    _, user = llm.calls[-1]
    block = user[user.index("<notes>"): user.index("</notes>") + len("</notes>")]
    assert len(block) <= b


# --- B.6 Task 3 rulings -------------------------------------------------------------------


def test_numbered_subsection_of_body_stays_in_body():
    groups, _ = split_groups([S("Abstract"), S("1 Introduction"), S("4 Experiments"),
                              S("4.3 Discussion"), S("5 Analysis"), S("5.2 Summary"),
                              S("6 Conclusion")], 32768)
    assert [(f, [s.title for s in g]) for f, g in groups] == [
        ("front", ["Abstract", "1 Introduction", "6 Conclusion"]),
        ("body", ["4 Experiments", "4.3 Discussion", "5 Analysis", "5.2 Summary"])]


def test_unnumbered_and_top_level_front_words_still_count():
    groups, _ = split_groups([S("Introduction"), S("2 Method"), S("Discussion"), S("7 Limitations")], 32768)
    assert [(f, [s.title for s in g]) for f, g in groups] == [
        ("front", ["Introduction", "Discussion", "7 Limitations"]), ("body", ["2 Method"])]


def test_read_in_parts_refuses_a_context_below_the_floor():
    import pytest

    with pytest.raises(ValueError, match="8192"):
        read_in_parts(FakeLLM([]), "T", [("general", [S("1 A")])], 8191,
                      read_mode="split", dropped=[])


# --- resolve_mode (the summarize_mode setting, Decision 6) ---------------------------------


@pytest.mark.parametrize("setting,backend,share,ctx,mode", [
    ("whole", "ollama", 0.0, 8192, "whole"), ("split", "claude-code", None, 200_000, "split"),
    ("auto", "claude-code", None, 200_000, "whole"), ("auto", "ollama", 1.0, 57_344, "whole"),
    ("auto", "ollama", 0.93, 57_344, "split"), ("auto", "ollama", 0.0, 32_768, "split"),
    ("auto", "ollama", 1.0, 16_384, "split"), ("auto", "ollama", 1.0, 26_624, "whole"),
])
def test_resolve_mode(setting, backend, share, ctx, mode):
    assert resolve_mode(setting, backend, share, ctx)[0] == mode


def test_resolve_mode_unknown_share_is_whole():
    mode, reason = resolve_mode("auto", "ollama", None, 57_344)
    assert mode == "whole" and "could not" in reason


def test_resolve_mode_reasons_are_plain():
    assert resolve_mode("auto", "ollama", 0.62, 57_344) == (
        "split", "split: part of the model runs from RAM (GPU share 0.62)")
    assert "CPU" in resolve_mode("auto", "ollama", 0.0, 57_344)[1]
    assert "16384" in resolve_mode("auto", "ollama", 1.0, 16_384)[1]
    assert resolve_mode("split", "ollama", 1.0, 57_344)[1].startswith("split: ")
    assert resolve_mode("auto", "claude-code", None, 200_000)[1].startswith("whole: ")
    assert SPLIT_BELOW_CTX == 26_624


def test_resolve_mode_names_where_a_fixed_mode_was_set():
    assert resolve_mode("split", "ollama", None, 8192) == (
        "split", "split: set with `rpipe model set-mode split`")
    assert resolve_mode("whole", "ollama", 0.0, 8192, set_with="--mode for this eval") == (
        "whole", "whole: set with --mode for this eval")
