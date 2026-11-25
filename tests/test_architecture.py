"""Architecture rules: which code may talk to which. See docs/guide/architecture.md."""

import ast
from pathlib import Path

import research_pipeline

PACKAGE_ROOT = Path(research_pipeline.__file__).parent
CLI = PACKAGE_ROOT / "cli.py"

# The commands that belong to the app (the person using rpipe), by function name in cli.py.
APP_COMMANDS = {
    "vote", "_save_command", "feedback", "review", "show", "_show_paper",
    "shelf_add", "shelf_list", "shelf_remove", "shelf_sync",
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
