"""Reading a paper in parts: groups, chunks, notes, condense, combine (parts.py)."""

from conftest import FakeLLM

from research_pipeline.citations import claims, generate_checked
from research_pipeline.models import Section
from research_pipeline.parts import (
    FRONT_WORDS,
    MAX_CONDENSE_ROUNDS,
    chunk_to_budget,
    read_in_parts,
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
    assert ps.unverified == ["A note. (Method)"]


def test_notes_bad_citation_retries_once():
    llm = FakeLLM([notes("Nope"), notes("Nope"), summary_citing("Intro"), critique_citing("Intro")])
    ps = read_in_parts(llm, "T", [("general", [S("Intro")])], 32768, read_mode="map_reduce", dropped=[])
    assert len(llm.calls) == 4
    assert "Nope" in llm.calls[1][1].split("</paper>", 1)[1]  # citation feedback on the retry
    assert ps.unverified == ["A note. (Nope)"]


def test_combine_unverified_is_reported():
    llm = FakeLLM([notes("Intro"), summary_citing("Gone"), summary_citing("Gone"), critique_citing("Intro")])
    ps = read_in_parts(llm, "T", [("general", [S("Intro")])], 32768, read_mode="map_reduce", dropped=[])
    assert ps.unverified == ["From Gone."] * 4


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
