"""Architecture rules: which code may talk to which. See docs/guide/architecture.md."""

import ast
from pathlib import Path

import research_pipeline

PACKAGE_ROOT = Path(research_pipeline.__file__).parent
CLI = PACKAGE_ROOT / "cli.py"

# The commands that belong to the app (the person using rpipe), by function name in cli.py.
APP_COMMANDS = {
    "vote", "_save_command", "feedback", "review", "show", "_show_paper",
    "shelf_add", "shelf_list", "shelf_remove", "shelf_sync", "requests_add",
}
# Write helpers: by name pattern, wherever they come from.
WRITE_PREFIXES = ("record_", "upsert_", "delete_", "mark_", "save_")
WRITE_NAMES = {"set_saved", "clear_skip", "start_run", "finish_run", "execute", "executemany"}
# Modules an app command must not call into directly (reads included): the storage layer and
# the pipeline stages. The app goes through library_api instead.
FORBIDDEN_MODULES = {
    "db", "library", "review", "shelf", "collect", "triage", "rank", "summarize", "digest",
    "fulltext", "llm", "evaluate", "gold", "spill", "sources", "backends", "profile_edit",
}
# Formatting helpers from pipeline modules that an app command may still use.
ALLOWED = {"render_summary", "UnknownPaperError"}


def _forbidden_imported_names(tree: ast.Module) -> set[str]:
    """Names cli.py imports from forbidden modules (``from research_pipeline.db import get_paper``)."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("research_pipeline."):
            top = node.module.split(".")[1]
            if top in FORBIDDEN_MODULES:
                names |= {alias.asname or alias.name for alias in node.names}
    return names - ALLOWED


def _called_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _offences(fn: ast.FunctionDef, forbidden: set[str]) -> list[str]:
    found: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name is None:
            continue
        module = node.func.value.id if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) else None
        if module == "library_api":
            continue
        if (
            name in forbidden
            or name in WRITE_NAMES
            or name.startswith(WRITE_PREFIXES)
            or module in FORBIDDEN_MODULES
        ):
            found.append(f"{fn.name}:{node.lineno}: calls {module + '.' if module else ''}{name}")
    return found


def test_app_commands_use_only_library_api():
    tree = ast.parse(CLI.read_text(encoding="utf-8"), filename=str(CLI))
    forbidden = _forbidden_imported_names(tree)
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in APP_COMMANDS}
    assert set(fns) == APP_COMMANDS, f"missing from cli.py: {APP_COMMANDS - set(fns)}"
    offences = [line for fn in fns.values() for line in _offences(fn, forbidden)]
    assert offences == [], "app commands must reach the database through library_api only:\n" + "\n".join(offences)


# ---- the pipeline/app boundary (see docs/guide/architecture.md, "The groups") ----

SHARED = {"models", "summary_models", "db", "vault", "embed", "llm", "backends", "defaults",
          "seeds", "profile_edit", "ids", "progress", "categories", "schedule", "netguard", "feed_format"}
PIPELINE = {"collect", "sources", "triage", "rank", "fulltext", "summarize", "prompts", "digest",
            "evaluate", "gold", "judge", "pruning", "parts", "citations", "numbers", "extras", "diagram", "spill",
            "ollama_fit", "ollama_tune", "bench", "scan", "catalog", "recommender", "machine",
            "registry", "run_lock", "requests_queue", "seed_input", "onboarding", "share_github", "share_publish",
            "share_fetch"}
APP = {"library", "shelf", "review", "serve", "library_api", "workbench", "webapp"}
COMPOSITION = {"cli", "progress_render"}
GROUPS = {"shared": SHARED, "pipeline": PIPELINE, "app": APP, "composition": COMPOSITION}


def _top_level_names() -> set[str]:
    """Every module (``x.py``) and package (folder with ``__init__.py``) directly in the package."""
    names = {p.stem for p in PACKAGE_ROOT.glob("*.py") if p.stem != "__init__"}
    names |= {p.name for p in PACKAGE_ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    return names


def _source_files(name: str) -> list[Path]:
    """The .py files of a top-level module or package."""
    path = PACKAGE_ROOT / name
    return sorted(path.rglob("*.py")) if path.is_dir() else [PACKAGE_ROOT / f"{name}.py"]


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _import_nodes(tree: ast.AST):
    """Import statements that can run: skips the body of ``if TYPE_CHECKING:`` (never runs)."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.orelse:
                yield from _import_nodes_in(child)
            continue
        yield from _import_nodes_in(node)


def _import_nodes_in(node: ast.AST):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        yield node
    else:
        yield from _import_nodes(node)


def _resolve(file: Path, node: ast.ImportFrom) -> list[str]:
    """Dotted absolute names an ImportFrom refers to (module, plus module.name per alias)."""
    if node.level == 0:
        base = node.module or ""
    else:
        rel = file.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts
        pkg = list(rel[:-1])  # the package the file is in (for __init__.py, the package itself)
        pkg = pkg[: len(pkg) - (node.level - 1)]
        base = ".".join(pkg + ([node.module] if node.module else []))
    return [base] + [f"{base}.{a.name}" for a in node.names]


def _top(dotted: str) -> str | None:
    """``research_pipeline.x.y`` -> ``x``; anything outside the package -> None."""
    parts = dotted.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "research_pipeline" else None


def _internal_imports(module: str) -> set[str]:
    """Top-level package modules that ``module`` imports (itself excluded)."""
    package = _top_level_names()
    found: set[str] = set()
    for file in _source_files(module):
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        for node in _import_nodes(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else _resolve(file, node)
            for dotted in names:
                top = _top(dotted)
                # `from research_pipeline import x` names x; `from research_pipeline.db import y` names db.
                if top is not None and top in package:
                    found.add(top)
    found.discard(module)
    return found


def _library_names_imported(module: str) -> set[str]:
    """Names imported with ``from research_pipeline.library import X`` (or relative equivalent)."""
    names: set[str] = set()
    for file in _source_files(module):
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        for node in _import_nodes(tree):
            if isinstance(node, ast.ImportFrom) and _resolve(file, node)[0] == "research_pipeline.library":
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.Import) and any(a.name == "research_pipeline.library" for a in node.names):
                names.add("*whole module*")
            elif isinstance(node, ast.ImportFrom) and _resolve(file, node)[0] == "research_pipeline":
                if any(a.name == "library" for a in node.names):
                    names.add("*whole module*")
    return names


def _violations(group: set[str], banned: set[str]) -> list[str]:
    found = {m: sorted(_internal_imports(m) & banned) for m in sorted(group)}
    return [f"{m} imports {names}" for m, names in found.items() if names]


def test_every_module_is_classified():
    found = _top_level_names()
    counts = {m: sum(m in g for g in GROUPS.values()) for m in found}
    unclassified = sorted(m for m, c in counts.items() if c == 0)
    twice = sorted(m for m, c in counts.items() if c > 1)
    stale = sorted(set().union(*GROUPS.values()) - found)
    assert unclassified == [], f"add these to a group here and in docs/guide/architecture.md: {unclassified}"
    assert twice == [], f"in more than one group: {twice}"
    assert stale == [], f"listed but no such module: {stale}"


def test_shared_imports_only_shared():
    assert _violations(SHARED, PIPELINE | APP | COMPOSITION) == []


def test_pipeline_never_imports_app():
    assert _violations(PIPELINE, (APP - {"library"}) | COMPOSITION) == []


def test_pipeline_reads_only_listed_library_functions():
    from research_pipeline.library import PIPELINE_READS

    bad = {m: sorted(_library_names_imported(m) - PIPELINE_READS) for m in sorted(PIPELINE)}
    assert {m: names for m, names in bad.items() if names} == {}


def test_app_never_imports_pipeline():
    assert _violations(APP, PIPELINE | COMPOSITION) == []


def test_library_api_is_local_only():
    # The first library_api function that sends library content to a model must wire
    # require_local (as shelf does) and replace this test.
    seen: set[str] = set()
    todo = ["library_api"]
    while todo:
        m = todo.pop()
        if m not in seen:
            seen.add(m)
            todo.extend(_internal_imports(m))
    assert not seen & {"llm", "backends"}, sorted(seen)


def test_library_api_returns_data_not_text():
    import inspect
    import typing

    from research_pipeline import library_api

    fns = [f for n, f in inspect.getmembers(library_api, inspect.isfunction)
           if not n.startswith("_") and f.__module__ == library_api.__name__]
    assert fns
    text = [f.__name__ for f in fns if typing.get_type_hints(f).get("return") is str]
    assert text == [], f"return data objects, not display text: {text}"


def test_type_checking_imports_ignored():
    assert "TYPE_CHECKING" in (PACKAGE_ROOT / "llm.py").read_text(encoding="utf-8")
    assert "bench" not in _internal_imports("llm")
