"""Boundary tests: the owner's private my_work/ text must stay out of prompt and model code.

The shelf (see docs/guide/shelf.md) holds the owner's own notes. Only the local embedding
model may see shelf text, and ranking reads shelf *embeddings* (numbers), so rank.py and
triage.py are deliberately not checked. Every module that builds a prompt or calls a model
must not import research_pipeline.shelf, in any import form.
"""

import ast
from pathlib import Path

import pytest

import research_pipeline

SHELF = "research_pipeline.shelf"
PACKAGE_ROOT = Path(research_pipeline.__file__).parent

# Top-level modules that build prompts or call models; backends/ is added below.
MODEL_MODULES = ["prompts.py", "llm.py", "summarize.py", "onboarding.py", "evaluate.py"]


def _package_of(path: Path) -> str:
    """Dotted package a file in src/research_pipeline (or a subfolder) belongs to."""
    parts = path.relative_to(PACKAGE_ROOT).parts[:-1]
    return ".".join(["research_pipeline", *parts])


def _resolve(node: ast.ImportFrom, package: str) -> str:
    """Absolute dotted name of a `from X import ...` module, resolving leading dots."""
    if node.level == 0:
        return node.module or ""
    parts = package.split(".")
    base = parts[: len(parts) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _offending_imports(path: Path, package: str) -> list[str]:
    """Return one line per import in `path` that reaches research_pipeline.shelf.

    Covers `import research_pipeline.shelf [as s]`, `from research_pipeline import shelf`,
    `from research_pipeline.shelf import X`, and relative forms (`from . import shelf`,
    `from .shelf import X`, `from .. import shelf`, ...) resolved against `package`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve(node, package)
            candidates = [resolved, *(f"{resolved}.{alias.name}" for alias in node.names)]
        if any(c == SHELF or c.startswith(SHELF + ".") for c in candidates):
            found.append(f"{path.name}:{node.lineno}: imports {', '.join(candidates)}")
    return found


def test_model_modules_do_not_import_shelf():
    files = [PACKAGE_ROOT / name for name in MODEL_MODULES]
    files += sorted((PACKAGE_ROOT / "backends").glob("*.py"))
    for f in files:
        assert f.exists(), f"{f} is missing; update MODEL_MODULES in tests/test_boundaries.py"
    assert any(f.parent.name == "backends" for f in files), "no backend modules found"

    offending: list[str] = []
    for f in files:
        offending += _offending_imports(f, _package_of(f))
    assert offending == [], "shelf text must not reach model code:\n" + "\n".join(offending)


@pytest.mark.parametrize(
    ("source", "package"),
    [
        ("from research_pipeline import shelf\n", "research_pipeline"),
        ("import research_pipeline.shelf as s\n", "research_pipeline"),
        ("from research_pipeline.shelf import chunk_text\n", "research_pipeline.backends"),
        ("from . import shelf\n", "research_pipeline"),
        ("from .shelf import chunk_text\n", "research_pipeline"),
        ("from .. import shelf\n", "research_pipeline.backends"),
        ("from ..shelf import chunk_text\n", "research_pipeline.backends"),
    ],
)
def test_checker_flags_planted_shelf_import(tmp_path, source, package):
    planted = tmp_path / "planted.py"
    planted.write_text(source, encoding="utf-8")
    assert _offending_imports(planted, package), f"checker missed: {source.strip()}"


def test_checker_accepts_unrelated_imports(tmp_path):
    clean = tmp_path / "clean.py"
    clean.write_text("from research_pipeline import llm\nfrom . import rank\nimport shelfish\n", encoding="utf-8")
    assert _offending_imports(clean, "research_pipeline.backends") == []
