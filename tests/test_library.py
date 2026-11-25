"""The user-action functions (votes, saves, skips, shelf rows) live in library.py, not db.py."""


def test_db_no_longer_has_user_action_functions():
    import research_pipeline.db as db

    for name in ("record_vote", "set_saved", "current_votes", "upsert_shelf_item", "shelf_chunk_rows"):
        assert not hasattr(db, name), name


def test_pipeline_reads_exist():
    import research_pipeline.library as library

    assert all(callable(getattr(library, n)) for n in library.PIPELINE_READS)
