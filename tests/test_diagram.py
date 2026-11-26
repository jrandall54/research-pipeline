from conftest import FakeLLM

from research_pipeline.diagram import check_diagram, make_diagram
from research_pipeline.models import Section
from research_pipeline.summary_models import Diagram, DiagramAnswer, DiagramEdge, DiagramNode

SECS = [
    Section(title="Abstract", text="We study X."),
    Section(title="2 Method", text="A hyper-agent plans tasks and sends them to the arm agent, "
                                    "the torso agent and the chassis agent."),
]


def node(i: str, label: str, sec: str = "2 Method") -> DiagramNode:
    return DiagramNode(id=i, label=label, sections=[sec])


def good() -> Diagram:
    return Diagram(
        shape="parts",
        nodes=[node("h", "Hyper-agent"), node("a", "Arm agent"), node("t", "Torso agent")],
        edges=[DiagramEdge(source="h", target="a"), DiagramEdge(source="h", target="t", label="tasks")],
        caption="One hyper-agent hands tasks to the part agents.",
    )


def test_check_diagram_ok():
    assert check_diagram(good(), SECS, "We study X.") == []


def test_check_diagram_bad_edge_and_count():
    d = good().model_copy(update={"edges": [DiagramEdge(source="h", target="zzz")]})
    probs = check_diagram(d, SECS, "")
    assert any("zzz" in p for p in probs)
    one = good().model_copy(update={"nodes": [node("h", "Hyper-agent")], "edges": []})
    assert any("nodes" in p for p in check_diagram(one, SECS, ""))
    many = good().model_copy(update={"nodes": [node(str(i), "Arm agent") for i in range(9)], "edges": []})
    assert any("nodes" in p for p in check_diagram(many, SECS, ""))


def test_check_diagram_duplicate_ids_long_label_and_unknown_section():
    d = good().model_copy(update={"nodes": [node("h", "Arm agent"), node("h", "Arm agent plans tasks arm agent"),
                                            node("x", "Arm agent", "9 Nope")], "edges": []})
    probs = " | ".join(check_diagram(d, SECS, ""))
    assert "unique" in probs and "words" in probs and "9 Nope" in probs


def test_check_diagram_invented_label():
    d = good().model_copy(update={"nodes": [node("h", "Coordinator"), node("a", "Arm agent")], "edges": []})
    probs = check_diagram(d, SECS, "We study X.")
    assert any("Coordinator" in p for p in probs)


def test_check_diagram_plural_and_ing_match():
    secs = [Section(title="2 Method", text="Each agent does plan work.")]
    d = Diagram(shape="steps", nodes=[node("a", "Agents"), node("b", "Planning work")],
                edges=[DiagramEdge(source="a", target="b")], caption="c")
    assert check_diagram(d, secs, "") == []


def test_make_diagram_null_answer():
    llm = FakeLLM([DiagramAnswer(diagram=None)])
    phases: list[str] = []
    assert make_diagram(llm, "T", SECS, "We study X.", 32768, phases.append) == (None, [])
    assert phases == ["diagram"] and len(llm.calls) == 1


def test_make_diagram_ok_one_call():
    llm = FakeLLM([DiagramAnswer(diagram=good())])
    d, probs = make_diagram(llm, "T", SECS, "We study X.", 32768)
    assert d == good() and probs == []


def test_make_diagram_retry_then_drop():
    bad = good().model_copy(update={"nodes": [node("h", "Coordinator"), node("a", "Arm agent")], "edges": []})
    llm = FakeLLM([DiagramAnswer(diagram=bad), DiagramAnswer(diagram=bad)])
    phases: list[str] = []
    d, probs = make_diagram(llm, "T", SECS, "We study X.", 32768, phases.append)
    assert d is None and len(probs) == 1 and probs[0].startswith("diagram: ")
    assert "Coordinator" in probs[0]
    assert phases == ["diagram", "retry"] and len(llm.calls) == 2
    assert "Coordinator" in llm.calls[1][1].split("</paper>", 1)[1]


def test_make_diagram_retry_succeeds():
    bad = good().model_copy(update={"nodes": [node("h", "Coordinator"), node("a", "Arm agent")], "edges": []})
    llm = FakeLLM([DiagramAnswer(diagram=bad), DiagramAnswer(diagram=good())])
    assert make_diagram(llm, "T", SECS, "We study X.", 32768) == (good(), [])


def test_make_diagram_abstract_only():
    d = Diagram(shape="steps", nodes=[node("a", "Study", "Abstract"), node("b", "X", "Abstract")],
                edges=[DiagramEdge(source="a", target="b")], caption="c")
    llm = FakeLLM([DiagramAnswer(diagram=d)])
    got, probs = make_diagram(llm, "T", None, "We study X.", 32768)
    assert got == d and probs == []
    assert "only the abstract" in llm.calls[0][1]
