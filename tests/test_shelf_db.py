from research_pipeline.db import (
    delete_shelf_item,
    list_shelf_items,
    open_db,
    shelf_chunk_rows,
    shelf_embedding_id,
    upsert_shelf_item,
)


def _emb(conn, item_id):
    conn.execute(
        "INSERT INTO embeddings (item_id, embedding_model, vector, dim) VALUES (?, 'm', x'00', 1)",
        (item_id,),
    )
    conn.commit()


def _emb_ids(conn):
    return sorted(r[0] for r in conn.execute("SELECT item_id FROM embeddings"))


def test_fresh_db_has_shelf_tables_and_version_3(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"shelf_items", "shelf_chunks"} <= names
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_v2_db_migrates_to_v3(tmp_path):
    path = tmp_path / "v.sqlite"
    conn = open_db(path)
    conn.execute("DROP TABLE shelf_chunks")
    conn.execute("DROP TABLE shelf_items")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()
    conn = open_db(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"shelf_items", "shelf_chunks"} <= names
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_embedding_id_format():
    assert shelf_embedding_id("notes", 2) == "shelf:notes#2"


def test_upsert_replaces_chunks(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    upsert_shelf_item(conn, "a", "h1", ["one", "two", "three"])
    _emb(conn, shelf_embedding_id("a", 2))
    upsert_shelf_item(conn, "a", "h2", ["new"])
    rows = shelf_chunk_rows(conn)
    assert [(r["item_name"], r["chunk_idx"], r["text"]) for r in rows] == [("a", 0, "new")]
    items = list_shelf_items(conn)
    assert [(r["name"], r["content_hash"], r["n_chunks"]) for r in items] == [("a", "h2", 1)]
    assert items[0]["added_at"]
    assert _emb_ids(conn) == []


def test_delete_leaves_other_items_and_papers(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    upsert_shelf_item(conn, "a", "h", ["x", "y"])
    upsert_shelf_item(conn, "ab", "h", ["z"])
    upsert_shelf_item(conn, "other", "h", ["w"])
    for i in ("shelf:a#0", "shelf:a#1", "shelf:ab#0", "shelf:other#0", "2401.00001"):
        _emb(conn, i)
    delete_shelf_item(conn, "a")
    assert _emb_ids(conn) == ["2401.00001", "shelf:ab#0", "shelf:other#0"]
    assert [r["name"] for r in list_shelf_items(conn)] == ["ab", "other"]
    assert [r["item_name"] for r in shelf_chunk_rows(conn)] == ["ab", "other"]


def test_delete_name_with_like_wildcards(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    upsert_shelf_item(conn, "a_b", "h", ["x"])
    upsert_shelf_item(conn, "axb", "h", ["y"])
    _emb(conn, "shelf:a_b#0")
    _emb(conn, "shelf:axb#0")
    delete_shelf_item(conn, "a_b")
    assert _emb_ids(conn) == ["shelf:axb#0"]
