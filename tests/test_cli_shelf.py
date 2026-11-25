import pytest
from conftest import FakeEmbedder
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.cli import Pipeline
from research_pipeline.db import open_db
from research_pipeline.library import list_shelf_items
from research_pipeline.vault import init_vault

runner = CliRunner()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    monkeypatch.setattr(
        cli, "build_pipeline",
        lambda config: Pipeline([], FakeEmbedder, lambda ids: [], lambda: None, lambda p: []),
    )
    return path


def run(vault, *args):
    return runner.invoke(cli.app, ["shelf", *args, "--vault", str(vault)])


def items(vault):
    conn = open_db(vault / "vault.sqlite")
    try:
        return [(r["name"], r["n_chunks"]) for r in list_shelf_items(conn)]
    finally:
        conn.close()


def test_add_text_creates_note(vault):
    res = run(vault, "add", "Try retrieval for malware triage")
    assert res.exit_code == 0, res.output
    assert "try-retrieval-for-malware-triage" in res.output
    assert (vault / "my_work" / "try-retrieval-for-malware-triage.md").exists()
    assert items(vault) == [("try-retrieval-for-malware-triage", 1)]


def test_add_file_copies_it(vault, tmp_path):
    src = tmp_path / "ideas.md"
    src.write_text("# Idea\nFuzz drivers.\n")
    res = run(vault, "add", str(src))
    assert res.exit_code == 0, res.output
    assert "ideas" in res.output
    assert (vault / "my_work" / "ideas.md").read_text() == src.read_text()


@pytest.mark.parametrize("arg", ["notes/idea.md", "missing.txt"])
def test_add_mistyped_path_is_an_error(vault, arg):
    res = run(vault, "add", arg)
    assert res.exit_code == 1
    assert "file not found" in res.output.lower()
    assert items(vault) == []
    assert list((vault / "my_work").iterdir()) == []


def test_add_existing_directory_is_an_error(vault, tmp_path):
    res = run(vault, "add", str(tmp_path))
    assert res.exit_code == 1
    assert "file not found" in res.output.lower()


def test_add_note_with_spaces_ending_in_md_is_text(vault):
    res = run(vault, "add", "rewrite the README.md")
    assert res.exit_code == 0, res.output
    assert items(vault) == [("rewrite-the-readme-md", 1)]


def test_add_wrong_extension_file_error(vault, tmp_path):
    f = tmp_path / "paper.pdf"
    f.write_bytes(b"x")
    res = run(vault, "add", str(f))
    assert res.exit_code == 1
    assert ".md and .txt" in res.output


def test_list_empty_says_how_to_add(vault):
    res = run(vault, "list")
    assert res.exit_code == 0
    assert "rpipe shelf add" in res.output


def test_list_shows_name_chunks_date(vault):
    run(vault, "add", "alpha note")
    res = run(vault, "list")
    assert res.exit_code == 0
    assert "alpha-note" in res.output
    assert "1 chunk" in res.output
    assert "added " in res.output


def test_remove_then_list(vault):
    run(vault, "add", "alpha note")
    res = run(vault, "remove", "alpha-note")
    assert res.exit_code == 0, res.output
    assert "alpha-note" in res.output
    assert (vault / "my_work" / ".removed" / "alpha-note.md").exists()
    assert "alpha-note" not in run(vault, "list").output


def test_remove_unknown_suggests_close_name(vault):
    run(vault, "add", "alpha note")
    res = run(vault, "remove", "alpha-nots")
    assert res.exit_code == 1
    assert "alpha-note" in res.output


def test_sync_reports_counts(vault):
    run(vault, "add", "alpha note")
    run(vault, "add", "beta note")
    (vault / "my_work" / "gamma.md").write_text("gamma text\n")
    (vault / "my_work" / "alpha-note.md").write_text("changed alpha\n")
    (vault / "my_work" / "beta-note.md").unlink()
    res = run(vault, "sync")
    assert res.exit_code == 0, res.output
    assert "1 added" in res.output and "1 updated" in res.output and "1 removed" in res.output
    assert "gamma" in res.output


def test_sync_nothing_changed(vault):
    res = run(vault, "sync")
    assert res.exit_code == 0
    assert "0 added" in res.output


def test_sync_shows_ignored_files(vault):
    (vault / "my_work" / "paper.pdf").write_bytes(b"x")
    (vault / "my_work" / "a.md").write_text("one\n")
    (vault / "my_work" / "a.txt").write_text("shadowed\n")
    res = run(vault, "sync")
    assert res.exit_code == 0, res.output
    assert "Ignored 2" in res.output
    assert "paper.pdf" in res.output and "a.txt" in res.output
    assert ".md and .txt" in res.output


def test_remove_reports_every_file_moved(vault):
    work = vault / "my_work"
    work.mkdir(exist_ok=True)
    (work / "a.md").write_text("one\n", encoding="utf-8")
    (work / "a.txt").write_text("two\n", encoding="utf-8")
    assert run(vault, "sync").exit_code == 0
    res = run(vault, "remove", "a")
    assert res.exit_code == 0, res.output
    assert "a.md" in res.output and "a.txt" in res.output
    assert not (work / "a.txt").exists()


def test_sync_prints_ignored_reason(vault, monkeypatch):
    work = vault / "my_work"
    work.mkdir(exist_ok=True)
    (work / "bad.md").write_text("x\n", encoding="utf-8")
    from pathlib import Path

    orig = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError(13, "Permission denied")) if self.name == "bad.md" else orig(self))
    res = run(vault, "sync")
    assert res.exit_code == 0, res.output
    assert "bad.md" in res.output and "could not be read" in res.output
