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
    secs = [Section(title="2 Method", text="Each scheduler agent does plan work.")]
    d = Diagram(shape="steps", nodes=[node("a", "Scheduler agents"), node("b", "Planning work")],
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
    assert phases == ["diagram", "diagram_retry"] and len(llm.calls) == 2
    assert "Coordinator" in llm.calls[1][1].split("</paper>", 1)[1]


def test_make_diagram_retry_succeeds():
    bad = good().model_copy(update={"nodes": [node("h", "Coordinator"), node("a", "Arm agent")], "edges": []})
    llm = FakeLLM([DiagramAnswer(diagram=bad), DiagramAnswer(diagram=good())])
    assert make_diagram(llm, "T", SECS, "We study X.", 32768) == (good(), [])


def test_make_diagram_abstract_only_makes_no_call():
    llm = FakeLLM([])
    phases: list[str] = []
    assert make_diagram(llm, "T", None, "We study X.", 32768, phases.append) == (None, [])
    assert llm.calls == [] and phases == []


CODEREACT = Diagram(
    shape="loop",
    nodes=[node("u", "Natural Language Utterance"), node("t", "Thought"), node("a", "Action"),
           node("o", "Observation"), node("f", "Final Output")],
    edges=[DiagramEdge(source="u", target="t"), DiagramEdge(source="t", target="a"),
           DiagramEdge(source="a", target="o"), DiagramEdge(source="o", target="t")],
    caption="c",
)
LOOP_SECS = [Section(title="2 Method", text="A natural language utterance starts a thought, an action, "
                                            "an observation and a final output.")]
GENERIC = "diagram: too generic (fewer than 2 boxes name something specific to this paper)"


def test_check_diagram_generic_loop_is_too_generic():
    assert check_diagram(CODEREACT, LOOP_SECS, "") == [GENERIC[len("diagram: "):]]


def test_check_diagram_robot_agents_is_specific():
    d = Diagram(shape="parts",
                nodes=[node("h", "Hyper-agent"), node("a", "Arm + gripper"), node("t", "Torso"),
                       node("c", "Chassis")],
                edges=[DiagramEdge(source="h", target="a")], caption="c")
    secs = [Section(title="2 Method", text="A hyper-agent, arm and gripper, torso and chassis.")]
    assert check_diagram(d, secs, "") == []


def test_check_diagram_front_matter_only_citation_fails():
    for title in ("1 Introduction", "Abstract", "6. Conclusions", "introduction"):
        secs = [Section(title=title, text="A hyper-agent plans tasks for the arm agent and the torso agent.")]
        d = good().model_copy(update={"nodes": [node("h", "Hyper-agent", title), node("a", "Arm agent", title)],
                                      "edges": []})
        probs = check_diagram(d, secs, "")
        assert any("'Hyper-agent' cites only the abstract, introduction or conclusion" in p for p in probs), title


def test_check_diagram_method_and_results_sections_pass():
    secs = [Section(title="3 Method", text="hyper-agent"), Section(title="5.1 Main Results", text="arm agent")]
    d = good().model_copy(update={"nodes": [node("h", "Hyper-agent", "3 Method"),
                                            node("a", "Arm agent", "5.1 Main Results")], "edges": []})
    assert check_diagram(d, secs, "") == []


def test_check_diagram_one_method_citation_is_enough():
    secs = [Section(title="1 Introduction", text="hyper-agent"), Section(title="3 Method", text="hyper-agent arm")]
    n = DiagramNode(id="h", label="Hyper-agent", sections=["1 Introduction", "3 Method"])
    d = good().model_copy(update={"nodes": [n, node("a", "Arm agent", "3 Method")], "edges": []})
    assert check_diagram(d, secs, "") == []


def test_make_diagram_too_generic_is_dropped_without_retry():
    llm = FakeLLM([DiagramAnswer(diagram=CODEREACT)])
    phases: list[str] = []
    got = make_diagram(llm, "T", LOOP_SECS, "", 32768, phases.append)
    assert got == (None, [GENERIC])
    assert len(llm.calls) == 1 and phases == ["diagram"]


def test_make_diagram_front_matter_citation_still_retries():
    secs = [Section(title="1 Introduction", text="hyper-agent arm agent"),
            Section(title="3 Method", text="hyper-agent arm agent")]
    bad = good().model_copy(update={"nodes": [node("h", "Hyper-agent", "1 Introduction"),
                                              node("a", "Arm agent", "1 Introduction")], "edges": []})
    fixed = good().model_copy(update={"nodes": [node("h", "Hyper-agent", "3 Method"),
                                                node("a", "Arm agent", "3 Method")], "edges": []})
    llm = FakeLLM([DiagramAnswer(diagram=bad), DiagramAnswer(diagram=fixed)])
    assert make_diagram(llm, "T", secs, "", 32768) == (fixed, [])
    assert len(llm.calls) == 2


def test_diagram_task_makes_null_the_default():
    from research_pipeline.prompts import DIAGRAM_TASK, PROMPT_VERSION

    assert PROMPT_VERSION == "2026-11-p9b"
    assert "null is the default answer" in DIAGRAM_TASK
    assert "observe, think, act" in DIAGRAM_TASK and "input, process, output" in DIAGRAM_TASK
    assert "most papers" in DIAGRAM_TASK


def test_check_diagram_es_plurals_match():
    secs = [Section(title="2 Method", text="Each process fills a class and a box and a match.")]
    d = Diagram(shape="steps", nodes=[node("a", "Processes classes"), node("b", "Boxes matches")],
                edges=[DiagramEdge(source="a", target="b")], caption="c")
    assert check_diagram(d, secs, "") == []
    secs2 = [Section(title="2 Method", text="Many processes and classes, boxes, matches.")]
    d2 = Diagram(shape="steps", nodes=[node("a", "Process class"), node("b", "Box match")],
                 edges=[DiagramEdge(source="a", target="b")], caption="c")
    assert check_diagram(d2, secs2, "") == []


def test_stem_silent_e_meets_inflections():
    from research_pipeline.diagram import _stem

    for a, b in (("caches", "cache"), ("niches", "niche"), ("encoding", "encode"),
                 ("retrieved", "retrieve"), ("agents", "agent"), ("planning", "plan"),
                 ("processes", "process"), ("classes", "class"), ("boxes", "box"),
                 ("matches", "match")):
        assert _stem(a) == _stem(b), (a, b)
