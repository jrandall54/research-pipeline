import pytest

from research_pipeline.ids import normalise_paper_id


@pytest.mark.parametrize(
    "raw",
    [
        "2611.25005",
        "arXiv:2611.25005v2",
        "https://arxiv.org/abs/2611.25005v1",
        "https://arxiv.org/pdf/2611.25005.pdf",
        "  2611.25005  ",
    ],
)
def test_normalise_paper_id(raw):
    assert normalise_paper_id(raw) == "2611.25005"


def test_normalise_old_style():
    assert normalise_paper_id("cs/0112017v1") == "cs/0112017"


def test_normalise_rejects_text():
    pytest.raises(ValueError, normalise_paper_id, "hello")


def test_ids_imports_only_standard_library():
    import ast
    import sys
    from pathlib import Path

    import research_pipeline.ids as ids

    tree = ast.parse(Path(ids.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert name.split(".")[0] in sys.stdlib_module_names, name


def test_profile_edit_wrapper_matches():
    from research_pipeline.profile_edit import normalise_arxiv_id

    assert normalise_arxiv_id("arXiv:2611.25005v2") == "2611.25005"
    pytest.raises(ValueError, normalise_arxiv_id, "hello")
