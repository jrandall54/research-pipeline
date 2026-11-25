"""Shelf indexing: chunking, sync, add, remove (all offline, fake embedder)."""

import ast
from pathlib import Path

import pytest

from research_pipeline import db, library, shelf
from research_pipeline.shelf import MAX_CHUNK_CHARS, chunk_text, sync_shelf


@pytest.fixture
def conn(tmp_path):
    return db.open_db(tmp_path / "vault.sqlite")


@pytest.fixture
def my_work(tmp_path) -> Path:
    d = tmp_path / "my_work"
    d.mkdir()
    return d


def _vec_ids(conn) -> set[str]:
    return {r["item_id"] for r in conn.execute("SELECT item_id FROM embeddings")}


def _names(conn) -> list[str]:
    return [r["name"] for r in library.list_shelf_items(conn)]


# ---- chunking ----


def test_one_line_note_is_one_chunk():
    assert chunk_text("idea", "Try retrieval for malware triage.") == [
        "idea\nTry retrieval for malware triage."
    ]


def test_empty_and_whitespace_text_give_no_chunks():
    assert chunk_text("x", "") == []
    assert chunk_text("x", "  \n\n \t\n") == []


def test_headings_split_sections_and_prefix_chunks():
    text = "# Intro\n" + "a" * 250 + "\n\n## Method\n" + "b" * 250
    chunks = chunk_text("paper", text)
    assert len(chunks) == 2
    assert chunks[0].startswith("paper / Intro\n")
    assert chunks[1].startswith("paper / Method\n")


def test_short_paragraphs_merge():
    text = "a" * 50 + "\n\n" + "b" * 50
    chunks = chunk_text("n", text)
    assert len(chunks) == 1
    assert "a" * 50 in chunks[0] and "b" * 50 in chunks[0]


def test_short_paragraphs_do_not_merge_across_headings():
    chunks = chunk_text("n", "# A\nshort one\n\n# B\nshort two")
    assert len(chunks) == 2


def test_long_paragraph_splits_on_sentences_within_max():
    sentence = "This is a sentence about detecting malware with models. "
    text = sentence * 80  # about 4400 chars, one paragraph
    chunks = chunk_text("big", text)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_CHUNK_CHARS for c in chunks)
    assert all(c.rstrip().endswith(".") for c in chunks)


def test_unbroken_giant_word_run_still_respects_max():
    chunks = chunk_text("big", "x" * 4000)
    assert all(len(c) <= MAX_CHUNK_CHARS for c in chunks)


# ---- sync ----


def test_sync_indexes_md_and_txt_and_ignores_the_rest(conn, my_work, embedder):
    (my_work / "a.md").write_text("Notes on fuzzing.")
    (my_work / "b.txt").write_text("Notes on retrieval.")
    (my_work / "c.pdf").write_text("pdf")
    (my_work / ".hidden.md").write_text("secret")
    (my_work / ".removed").mkdir()
    (my_work / ".removed" / "old.md").write_text("old")
    (my_work / "sub").mkdir()
    (my_work / "sub" / "deep.md").write_text("deep")
    res = sync_shelf(conn, my_work, embedder)
    assert sorted(res.added) == ["a", "b"]
    assert res.ignored == ["c.pdf"]
    assert _names(conn) == ["a", "b"]
    assert _vec_ids(conn) == {"shelf:a#0", "shelf:b#0"}


def test_unchanged_file_is_not_reembedded(conn, my_work, embedder):
    (my_work / "a.md").write_text("Notes on fuzzing.")
    sync_shelf(conn, my_work, embedder)
    calls = embedder.calls
    res = sync_shelf(conn, my_work, embedder)
    assert embedder.calls == calls
    assert res.added == res.updated == res.removed == []


def test_edited_file_replaces_chunks_and_vectors(conn, my_work, embedder):
    f = my_work / "a.md"
    f.write_text("# One\n" + "a" * 250 + "\n\n# Two\n" + "b" * 250)
    sync_shelf(conn, my_work, embedder)
    assert _vec_ids(conn) == {"shelf:a#0", "shelf:a#1"}
    f.write_text("Just one short note now.")
    res = sync_shelf(conn, my_work, embedder)
    assert res.updated == ["a"]
    assert _vec_ids(conn) == {"shelf:a#0"}
    assert library.list_shelf_items(conn)[0]["n_chunks"] == 1
    assert len(library.shelf_chunk_rows(conn)) == 1


def test_deleted_file_drops_item(conn, my_work, embedder):
    (my_work / "a.md").write_text("Notes.")
    sync_shelf(conn, my_work, embedder)
    (my_work / "a.md").unlink()
    res = sync_shelf(conn, my_work, embedder)
    assert res.removed == ["a"]
    assert _names(conn) == []
    assert _vec_ids(conn) == set()


def test_empty_file_indexes_with_zero_chunks(conn, my_work, embedder):
    (my_work / "blank.md").write_text("  \n\n")
    (my_work / "zero.txt").write_text("")
    res = sync_shelf(conn, my_work, embedder)
    assert sorted(res.added) == ["blank", "zero"]
    assert all(r["n_chunks"] == 0 for r in library.list_shelf_items(conn))
    assert embedder.calls == 0
    assert sync_shelf(conn, my_work, embedder).added == []  # stable second run


def test_missing_embeddings_for_this_model_trigger_reembed(conn, my_work, embedder):
    (my_work / "a.md").write_text("Notes.")
    sync_shelf(conn, my_work, embedder)
    conn.execute("DELETE FROM embeddings")
    conn.commit()
    res = sync_shelf(conn, my_work, embedder)
    assert res.updated == ["a"]
    assert _vec_ids(conn) == {"shelf:a#0"}


def test_sync_with_missing_directory_is_empty(conn, tmp_path, embedder):
    res = sync_shelf(conn, tmp_path / "nope", embedder)
    assert res.added == [] and res.removed == []


# ---- add ----


def test_add_text_writes_note_and_lists_it(conn, my_work, embedder):
    name = shelf.add_to_shelf(conn, my_work, embedder, "Retrieval for malware triage is promising")
    assert name == "retrieval-for-malware-triage-is-promising"
    assert (my_work / f"{name}.md").read_text().startswith("Retrieval for malware")
    assert _names(conn) == [name]


def test_two_notes_with_same_first_words_get_distinct_names(conn, my_work, embedder):
    a = shelf.add_to_shelf(conn, my_work, embedder, "same first six words here one")
    b = shelf.add_to_shelf(conn, my_work, embedder, "same first six words here two")
    assert a != b
    assert sorted(_names(conn)) == sorted([a, b])
    assert len(list(my_work.glob("*.md"))) == 2


def test_add_punctuation_only_text_falls_back_to_note(conn, my_work, embedder):
    assert shelf.add_to_shelf(conn, my_work, embedder, "?!?!") == "note"


def test_add_file_copies_and_never_overwrites(conn, my_work, embedder, tmp_path):
    src = tmp_path / "essay.md"
    src.write_text("first version")
    assert shelf.add_to_shelf(conn, my_work, embedder, str(src)) == "essay"
    src.write_text("second version")
    assert shelf.add_to_shelf(conn, my_work, embedder, str(src)) == "essay-2"
    assert (my_work / "essay.md").read_text() == "first version"
    assert (my_work / "essay-2.md").read_text() == "second version"
    assert src.read_text() == "second version"  # original untouched


def test_add_rejects_empty_text_and_bad_extension(conn, my_work, embedder, tmp_path):
    with pytest.raises(ValueError, match="rpipe shelf add"):
        shelf.add_to_shelf(conn, my_work, embedder, "   ")
    pdf = tmp_path / "x.pdf"
    pdf.write_text("x")
    with pytest.raises(ValueError, match=r"\.md"):
        shelf.add_to_shelf(conn, my_work, embedder, str(pdf))
    assert list(my_work.iterdir()) == []


# ---- remove ----


def test_remove_moves_file_and_clears_rows(conn, my_work, embedder):
    name = shelf.add_to_shelf(conn, my_work, embedder, "a note to remove")
    shelf.remove_from_shelf(conn, my_work, name)
    assert not list(my_work.glob("*.md"))
    assert (my_work / ".removed" / f"{name}.md").exists()
    assert _names(conn) == []
    assert library.shelf_chunk_rows(conn) == []
    assert _vec_ids(conn) == set()


def test_remove_twice_uniquifies_in_removed(conn, my_work, embedder):
    for _ in range(2):
        (my_work / "a.md").write_text("again")
        sync_shelf(conn, my_work, embedder)
        shelf.remove_from_shelf(conn, my_work, "a")
    assert len(list((my_work / ".removed").iterdir())) == 2


def test_remove_unknown_name_suggests_close_matches(conn, my_work, embedder):
    shelf.add_to_shelf(conn, my_work, embedder, "fuzzing notes")
    with pytest.raises(KeyError) as e:
        shelf.remove_from_shelf(conn, my_work, "fuzzing-note")
    assert "fuzzing-notes" in str(e.value)


# ---- local only ----


def test_shelf_is_local_only():
    tree = ast.parse(Path(shelf.__file__).read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    banned = ("research_pipeline.llm", "requests", "httpx", "urllib", "http", "aiohttp")
    for n in names:
        assert not any(n == b or n.startswith(b + ".") for b in banned), n


# ---- fix wave: case collisions, unreadable files, multi-extension remove ----


class _Emb:
    model_name = "fake-bow"

    def embed(self, texts):
        import numpy as np

        return np.ones((len(texts), 4), dtype=np.float32)


def test_add_note_does_not_overwrite_case_variant_file(conn, my_work):
    mine = my_work / "Fuzzing-Ideas.md"
    mine.write_text("my precious text\n", encoding="utf-8")
    name = shelf.add_to_shelf(conn, my_work, _Emb(), "fuzzing ideas")
    assert name != "fuzzing-ideas"
    assert mine.read_text(encoding="utf-8") == "my precious text\n"
    assert (my_work / f"{name}.md").read_text(encoding="utf-8") == "fuzzing ideas\n"


def test_add_file_does_not_overwrite_case_variant_file(conn, my_work, tmp_path):
    mine = my_work / "Idea.md"
    mine.write_text("mine\n", encoding="utf-8")
    src = tmp_path / "idea.md"
    src.write_text("other\n", encoding="utf-8")
    name = shelf.add_to_shelf(conn, my_work, _Emb(), str(src))
    assert name != "idea"
    assert mine.read_text(encoding="utf-8") == "mine\n"
    assert (my_work / f"{name}.md").read_text(encoding="utf-8") == "other\n"


def test_remove_does_not_overwrite_case_variant_in_trash(conn, my_work):
    (my_work / ".removed").mkdir()
    old = my_work / ".removed" / "Idea.md"
    old.write_text("earlier\n", encoding="utf-8")
    (my_work / "idea.md").write_text("later\n", encoding="utf-8")
    sync_shelf(conn, my_work, _Emb())
    shelf.remove_from_shelf(conn, my_work, "idea")
    assert old.read_text(encoding="utf-8") == "earlier\n"
    moved = [p for p in (my_work / ".removed").iterdir() if p != old]
    assert len(moved) == 1 and moved[0].read_text(encoding="utf-8") == "later\n"
    assert moved[0].name.casefold() != "idea.md"


def test_sync_skips_unreadable_file_and_continues(conn, my_work, monkeypatch):
    (my_work / "good.md").write_text("fine\n", encoding="utf-8")
    bad = my_work / "bad.md"
    bad.write_text("nope\n", encoding="utf-8")
    real = Path.read_bytes

    def fake(self):
        if self.name == "bad.md":
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", fake)
    res = sync_shelf(conn, my_work, _Emb())
    assert res.added == ["good"]
    assert any("bad.md" in i and "could not be read" in i for i in res.ignored)
    assert _names(conn) == ["good"]


def test_remove_moves_every_file_with_that_stem(conn, my_work):
    (my_work / "a.md").write_text("one\n", encoding="utf-8")
    (my_work / "a.txt").write_text("two\n", encoding="utf-8")
    sync_shelf(conn, my_work, _Emb())
    moved = shelf.remove_from_shelf(conn, my_work, "a")
    assert sorted(moved) == ["a.md", "a.txt"]
    assert not (my_work / "a.md").exists() and not (my_work / "a.txt").exists()
    assert sync_shelf(conn, my_work, _Emb()).added == []
    assert _names(conn) == []


def test_progress_one_step_per_changed_file(conn, my_work, embedder):
    from research_pipeline.progress import ListReporter

    (my_work / "a.md").write_text("alpha")
    (my_work / "b.txt").write_text("beta")
    rep = ListReporter()
    sync_shelf(conn, my_work, embedder, reporter=rep)
    steps = [e for e in rep.events if e.kind == "step"]
    assert [e.label for e in steps] == ["a.md", "b.txt"]
    assert steps[0].total == 2


def test_progress_empty_shelf_is_start_and_done_only(conn, my_work, embedder):
    from research_pipeline.progress import ListReporter

    rep = ListReporter()
    sync_shelf(conn, my_work, embedder, reporter=rep)
    assert [e.kind for e in rep.events] == ["start", "done"]
