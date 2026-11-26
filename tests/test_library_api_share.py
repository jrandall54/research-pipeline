"""library_api for following and Mix: reads return views; vote/save copy a followed post in."""

from datetime import date

import pytest

from research_pipeline import db, library_api
from research_pipeline.db import (
    mark_in_digest,
    open_db,
    replace_directory,
    replace_followed_posts,
    set_status,
    upsert_follow,
    upsert_paper,
)
from research_pipeline.models import PaperMeta, Status
from research_pipeline.summary_models import Claim, Critique, PaperSummary, Summary


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "t.db")
    yield c
    c.close()


def _post(pid, day="2026-11-10", rank=1, **kw):
    base = dict(
        paper_id=pid, digest_date=day, rank=rank, title=f"Title {pid}", abstract="quantum widgets",
        authors=["Ann"], url=f"http://x/{pid}", published=None, summary=None,
        reasons=["topic: security (0.80)", "keyword: widgets", "closest seed: s"], provenance=None,
    )
    base.update(kw)
    return base


def _follow(conn, fid, name, posts, handle=None):
    upsert_follow(conn, fid, f"http://h/{fid}", handle or f"@{name.lower()}", name, ["ml"])
    replace_followed_posts(conn, fid, posts)
    conn.commit()


def _own(conn, pid, day, score):
    upsert_paper(conn, PaperMeta(id=pid, title=f"Own {pid}", abstract="mine", source="arxiv",
                                 url=f"https://arxiv.org/abs/{pid}"))
    set_status(conn, pid, Status.triaged, score=score, reasons=["topic: security (0.9)"])
    conn.commit()
    mark_in_digest(conn, [pid], date.fromisoformat(day))


def test_following_posts_order(conn):
    _follow(conn, "fa", "Alex", [_post("a2", rank=2), _post("a1", rank=1)])
    _follow(conn, "fb", "Bea", [_post("b1", rank=1)])
    posts = library_api.following_posts(conn, "2026-11-10")
    assert [p.paper.id for p in posts] == ["a1", "a2", "b1"]
    assert posts[0].sources[0].name == "Alex"
    assert [r.kind for r in posts[0].reasons] == ["topic", "keyword"]
    assert library_api.following_days(conn) == ["2026-11-10"]


def test_mix_dedupes_with_own_paper(conn):
    _own(conn, "2611.00001", "2026-11-10", 0.9)
    _own(conn, "2611.00002", "2026-11-10", 0.5)
    _follow(conn, "fa", "Alex M.", [_post("2611.00001", rank=1), _post("z9", rank=2)])
    posts = library_api.mix_posts(conn, "2026-11-10")
    assert [p.paper.id for p in posts] == ["2611.00001", "2611.00002", "z9"]
    assert [s.name for s in posts[0].sources] == ["You", "Alex M."]
    assert posts[0].feed_id is None and posts[2].feed_id == "fa"
    assert library_api.mix_days(conn) == ["2026-11-10"]


def test_mix_paper_sorts_under_earliest_date(conn):
    _own(conn, "2611.00001", "2026-11-10", 0.9)
    _follow(conn, "fa", "Alex", [_post("2611.00001", day="2026-11-08")])
    assert library_api.mix_days(conn) == ["2026-11-08"]
    assert library_api.mix_posts(conn, "2026-11-10") == []
    assert len(library_api.mix_posts(conn, "2026-11-08")) == 1


def test_mix_excludes_not_in_mix(conn):
    _follow(conn, "fa", "Alex", [_post("a1")])
    library_api.set_in_mix(conn, "fa", False)
    assert library_api.mix_posts(conn, "2026-11-10") == []
    assert library_api.mix_days(conn) == []
    assert len(library_api.following_posts(conn, "2026-11-10")) == 1


def test_vote_on_followed_post_copies_paper_in(conn):
    _follow(conn, "fa", "Alex", [_post("a1")])
    library_api.vote(conn, "a1", "up")
    row = db.get_paper(conn, "a1")
    assert (row["source"], row["status"]) == ("feed", "skipped")
    assert library_api.get_paper(conn, "a1").vote == 1
    assert library_api.digest_papers(conn) == []
    assert [p.id for p in library_api.search_papers(conn, "quantum")] == ["a1"]
    assert library_api.followed_post(conn, "a1").paper.vote == 1


def test_save_followed_post_copies_in_and_lists(conn):
    _follow(conn, "fa", "Alex", [_post("a1")])
    library_api.save(conn, "a1", True)
    assert [p.id for p in library_api.saved_papers(conn)] == ["a1"]


def test_vote_on_followed_paper_you_have_copies_nothing(conn):
    _own(conn, "2611.00001", "2026-11-10", 0.9)
    _follow(conn, "fa", "Alex", [_post("2611.00001", title="Alex's title")])
    library_api.vote(conn, "2611.00001", "up")
    assert db.get_paper(conn, "2611.00001")["source"] == "arxiv"
    assert db.get_paper(conn, "2611.00001")["title"] == "Own 2611.00001"


def test_unfollow_keeps_saved_posts(conn):
    _follow(conn, "fa", "Alex", [_post("a1"), _post("a2", rank=2), _post("a3", rank=3)])
    library_api.save(conn, "a1", True)
    library_api.vote(conn, "a2", "down")
    library_api.unfollow(conn, "fa")
    assert library_api.follows(conn) == []
    assert library_api.following_posts(conn, "2026-11-10") == []
    kept = {r["paper_id"] for r in conn.execute("SELECT paper_id FROM followed_posts")}
    assert kept == {"a1", "a2"}
    assert library_api.followed_post(conn, "a1") is not None
    with pytest.raises(ValueError):
        library_api.unfollow(conn, "nope")


def test_follow_request_unknown_handle(conn):
    with pytest.raises(ValueError):
        library_api.follow_request(conn, "@ghost")


def test_follow_request_follows_directory_entry(conn):
    replace_directory(conn, [dict(feed_id="fa", feed_url="http://h/fa", handle="@alex", display_name="Alex",
                                  topics=["ml"], posts_per_day=2.0, preview=[], checked_at="n")])
    library_api.follow_request(conn, "@alex")
    [f] = library_api.follows(conn)
    assert (f.feed_id, f.display_name, f.waiting, f.stopped, f.posts, f.in_mix) == ("fa", "Alex", True, False, 0, True)
    assert library_api.directory(conn, [])[0].following is True


def test_directory_sorted_by_shared_topics(conn):
    def entry(fid, name, topics):
        return dict(feed_id=fid, feed_url="u", handle=f"@{fid}", display_name=name, topics=topics,
                    posts_per_day=1.0, preview=[{"title": "t"}], checked_at="n")
    replace_directory(conn, [entry("a", "Zed", ["ml"]), entry("b", "Bob", ["Security", "ml"]),
                             entry("c", "Amy", ["ml"]), entry("d", "Dan", ["art"])])
    out = library_api.directory(conn, ["security", "ML"])
    assert [(v.display_name, v.shared_topics) for v in out] == [("Bob", 2), ("Amy", 1), ("Zed", 1), ("Dan", 0)]
    assert out[0].preview == [{"title": "t"}]


def test_followed_post_returns_publisher_summary(conn):
    c = Claim(text="claim", sections=["1 Intro"])
    s = PaperSummary(summary=Summary(one_line="theirs", problem=c, approach=c, results=c, limitations=c),
                     critique=Critique(strengths=[c], weaknesses=[c], questions=["why?"]),
                     basis="abstract", unverified=[])
    _follow(conn, "fa", "Alex", [_post("a1", summary=s.model_dump(mode="json")), _post("a2", summary={"bad": 1})])
    view = library_api.followed_post(conn, "a1")
    assert view.paper.summary.summary.one_line == "theirs"
    assert view.paper.triage_reasons[0] == "topic: security (0.80)"
    assert (view.paper.vote, view.paper.saved) == (0, False)
    assert library_api.followed_post(conn, "a2").paper.summary is None
    assert library_api.followed_post(conn, "missing") is None


def test_follows_stopped_after_repeated_failures(conn):
    _follow(conn, "fa", "Alex", [])
    for _ in range(7):
        db.record_fetch(conn, "fa", ok=False, error="boom")
    conn.commit()
    [f] = library_api.follows(conn)
    assert f.stopped and not f.waiting and f.last_error == "boom"


def test_follow_request_refuses_own_handle_and_directory_marks_you(conn):
    replace_directory(conn, [{
        "feed_id": "github:me/rpipe-feed", "feed_url": "https://me.github.io/rpipe-feed/feed.json",
        "handle": "Me", "display_name": "Me M.", "topics": [], "posts_per_day": 1.0, "preview": [],
        "checked_at": "2026-11-25T00:00:00Z"}])
    conn.commit()
    with pytest.raises(ValueError, match="own feed"):
        library_api.follow_request(conn, "@me", own_handle="ME")
    assert library_api.follows(conn) == []
    assert library_api.directory(conn, [], "me")[0].is_you
    assert not library_api.directory(conn, [])[0].is_you


def test_is_feed_copy_only_for_source_feed(conn):
    _own(conn, "2611.00001", "2026-11-10", 0.5)
    _follow(conn, "fa", "Alex", [_post("2611.00002")])
    library_api._copy_in_followed_post(conn, "2611.00002")
    conn.commit()
    assert library_api.is_feed_copy(conn, "2611.00002")
    assert not library_api.is_feed_copy(conn, "2611.00001")
    assert not library_api.is_feed_copy(conn, "2611.99999")
