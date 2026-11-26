import itertools

import pytest

from research_pipeline.summary_models import Diagram, DiagramEdge, DiagramNode
from research_pipeline.webapp import diagram_svg


def _node(i, label=None):
    return DiagramNode(id=f"n{i}", label=label or f"Label {i}", sections=["3 Method"])


def _diagram(shape, n=4, edges=None, labels=None, caption="How it fits together"):
    nodes = [_node(i, labels[i] if labels else None) for i in range(n)]
    if edges is None:
        edges = [(0, i) for i in range(1, n)] if shape == "parts" else [(i, i + 1) for i in range(n - 1)]
    return Diagram(shape=shape, nodes=nodes, caption=caption,
                   edges=[DiagramEdge(source=f"n{a}", target=f"n{b}") for a, b in edges])


@pytest.mark.parametrize("shape", ["parts", "steps", "loop"])
def test_svg_parts_steps_loop_render(shape):
    d = _diagram(shape)
    out = str(diagram_svg.render(d))
    assert out.startswith('<figure class="diagram')
    assert 'role="img"' in out and 'aria-label="How it fits together"' in out
    assert "<figcaption>How it fits together</figcaption>" in out
    for n in d.nodes:
        assert n.label in out


def test_svg_escapes_labels():
    d = _diagram("steps", 2, labels=["<b>bold</b>", "ok"], caption='a "quoted" <i>caption</i>')
    out = str(diagram_svg.render(d))
    assert "<b>" not in out and "&lt;b&gt;" in out
    assert "<i>" not in out
    assert 'aria-label="a &#34;quoted&#34; &lt;i&gt;caption&lt;/i&gt;"' in out


@pytest.mark.parametrize("n", range(2, 9))
def test_svg_draws_any_valid_graph(n):
    pairs = [(a, b) for a, b in itertools.permutations(range(n), 2)]
    graphs = {
        "cycle": [(i, (i + 1) % n) for i in range(n)],
        "isolated": [(0, 1)],
        "none": [],
        "dense": pairs,
        "self": [(0, 0)],
    }
    for shape in ("parts", "steps", "loop"):
        for edges in graphs.values():
            d = _diagram(shape, n, edges=edges)
            out = str(diagram_svg.render(d))
            for node in d.nodes:
                assert node.label in out


def test_svg_wraps_long_labels_to_two_lines():
    out = str(diagram_svg.render(_diagram("parts", 6, labels=["Hub"] + ["Extraordinarily long words"] * 5)))
    assert out.count("<tspan") >= 11


@pytest.mark.parametrize("n", [6, 7, 8])
def test_svg_parts_connectors_come_before_boxes(n):
    out = str(diagram_svg.render(_diagram("parts", n)))
    assert out.rindex("<line") < out.index("<g class=\"d-node")


def test_svg_box_has_full_label_title():
    out = str(diagram_svg.render(_diagram("parts", 5, labels=["Hub"] + ["Extraordinarily long words"] * 4)))
    assert "<title>Extraordinarily long words</title>" in out
