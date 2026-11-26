from research_pipeline.summary_models import Diagram, DiagramAnswer, DiagramEdge, KeyNumber


def test_diagram_edge_accepts_from_to_keys():
    e = DiagramEdge.model_validate({"from": "a", "to": "b", "label": "x"})
    assert (e.source, e.target, e.label) == ("a", "b", "x")
    assert DiagramEdge(source="a", target="b").source == "a"
    assert set(e.model_dump(by_alias=True)) == {"from", "to", "label"}


def test_diagram_answer_null_means_no_shape():
    assert DiagramAnswer.model_validate({"diagram": None}).diagram is None


def test_key_number_needs_a_section():
    import pytest

    with pytest.raises(ValueError):
        KeyNumber(value="28%", label="gain", sections=[])


def test_diagram_shape_is_checked():
    import pytest

    with pytest.raises(ValueError):
        Diagram(shape="tree", nodes=[], edges=[], caption="c")


def test_schema_requires_hook_and_key_numbers_but_hook_may_be_null():
    from research_pipeline.summary_models import Summary

    schema = Summary.model_json_schema()
    assert {"hook", "key_numbers"} <= set(schema["required"])
    assert schema["properties"]["key_numbers"]["maxItems"] == 3
    assert list(schema["properties"])[:3] == ["one_line", "hook", "key_numbers"]
    s = Summary.model_validate({**{k: {"text": "t", "sections": ["a"]} for k in
                                   ("problem", "approach", "results", "limitations")},
                                "one_line": "x", "hook": None, "key_numbers": []})
    assert s.hook is None
