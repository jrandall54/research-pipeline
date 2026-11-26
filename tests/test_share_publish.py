from datetime import date

import httpx
import pytest
import respx
from fakes import FakeRunner, done, paper

from research_pipeline import feed_format as ff
from research_pipeline import share_publish as sp
from research_pipeline.db import mark_in_digest, open_db, save_diagram, save_summary, set_status, upsert_paper
from research_pipeline.models import Status
from research_pipeline.progress import ListReporter
from research_pipeline.share_github import GithubError
from research_pipeline.summarize import Claim, Critique, PaperSummary, Summary

NOW = "2026-11-26T03:00:00Z"


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "t.db")
    yield c
    c.close()


def add(conn, pid, day, score, reasons):
    upsert_paper(conn, paper(pid))
    set_status(conn, pid, Status.triaged, score, reasons)
    mark_in_digest(conn, [pid], date.fromisoformat(day))
    conn.commit()


def test_collect_posts_filters_private_reasons(conn):
    add(conn, "2611.1", "2026-11-25", 0.9,
        ["topic: agents (0.80)", "liked similar: 2611.9", "near your shelf: thesis.md"])
    posts = sp.collect_posts(conn)
    assert posts[0].reasons == ["topic: agents (0.80)"]
    files = ff.build_files(posts, display_name="A", topics=["agents"], active=True, now=NOW, base_url=None)
    blob = b"".join(files.values())
    for secret in (b"liked similar", b"near your shelf", b"thesis.md"):
        assert secret not in blob


def test_collect_posts_ranks_within_day(conn):
    add(conn, "b", "2026-11-25", 0.5, [])
    add(conn, "a", "2026-11-25", 0.5, [])
    add(conn, "c", "2026-11-25", 0.9, [])
    add(conn, "d", "2026-11-24", 0.1, [])
    got = {(p.paper.id, p.rank) for p in sp.collect_posts(conn)}
    assert got == {("c", 1), ("a", 2), ("b", 3), ("d", 1)}
    upsert_paper(conn, paper("undigested"))
    assert "undigested" not in {p.paper.id for p in sp.collect_posts(conn)}


def test_collect_posts_excludes_diagrams(conn):
    from research_pipeline.summary_models import Diagram, DiagramEdge, DiagramNode

    add(conn, "x", "2026-11-25", 0.9, [])
    c = Claim(text="t", sections=["1 Intro"])
    save_summary(conn, "x", PaperSummary(
        summary=Summary(one_line="o", problem=c, approach=c, results=c, limitations=c),
        critique=Critique(strengths=[c], weaknesses=[], questions=["q?"]), basis="abstract", unverified=[]), "m",
        prompt_version="p1")
    save_diagram(conn, "x", Diagram(shape="steps", caption="SECRETDIAGRAM",
                                    nodes=[DiagramNode(id="a", label="A", sections=["1 Intro"]), DiagramNode(id="b", label="B", sections=["1 Intro"])],
                                    edges=[DiagramEdge(source="a", target="b")]), "m")
    posts = sp.collect_posts(conn)
    assert posts[0].summary is not None and posts[0].provenance
    files = ff.build_files(posts, display_name="A", topics=[], active=True, now=NOW, base_url=None)
    assert b"SECRETDIAGRAM" not in b"".join(files.values())


def test_write_site_removes_stale_archives(tmp_path):
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "2026-01.json").write_text("old")
    (tmp_path / "archive" / "2026-02.json").write_text("keep")
    assert sp.write_site(tmp_path, {"feed.json": b"{}", "archive/2026-02.json": b"keep"}) is True
    assert not (tmp_path / "archive" / "2026-01.json").exists()
    assert (tmp_path / "feed.json").read_bytes() == b"{}"
    assert sp.write_site(tmp_path, {"feed.json": b"{}", "archive/2026-02.json": b"keep"}) is False


def run_publish(conn, tmp_path, runner, reporter=None):
    return sp.publish(conn, tmp_path, runner, display_name="A", topics=["agents"], active=True,
                      now=NOW, handle="Alex", reporter=reporter)


def test_publish_unchanged_does_not_push(conn, tmp_path):
    add(conn, "a", "2026-11-25", 0.9, ["topic: agents (0.80)"])
    runner = FakeRunner({("git", "diff"): done(0)})  # nothing staged
    assert run_publish(conn, tmp_path, runner) == "unchanged"
    assert ["git", "push"] not in runner.calls


def test_publish_pushes_changes(conn, tmp_path):
    add(conn, "a", "2026-11-25", 0.9, ["topic: agents (0.80)"])
    runner = FakeRunner({("git", "diff"): done(1)})
    reporter = ListReporter()
    assert run_publish(conn, tmp_path, runner, reporter) == "pushed"
    assert ["git", "push"] in runner.calls
    assert [c[1] for c in runner.calls if c[0] == "git"][:2] == ["add", "diff"]
    feed, _ = ff.parse_feed((tmp_path / "feed.json").read_bytes())
    assert feed.items[0].id and "alex.github.io/rpipe-feed" in (tmp_path / "feed.json").read_text()
    assert {e.stage for e in reporter.events} == {"publish"}


def test_push_failure_raises_with_stderr(tmp_path):
    runner = FakeRunner({("git", "diff"): done(1), ("git", "push"): done(1, "", "remote: Permission denied")})
    with pytest.raises(GithubError, match="Permission denied"):
        sp.push(runner, tmp_path, "msg")


def test_feed_url_lowercases():
    assert sp.feed_url("Alex-M") == "https://alex-m.github.io/rpipe-feed/feed.json"


@respx.mock
def test_wait_live_polls_until_ok():
    good = ff.build_files([], display_name="A", topics=[], active=True, now=NOW, base_url=None)["feed.json"]
    route = respx.get("https://a.github.io/rpipe-feed/feed.json").mock(side_effect=[
        httpx.Response(404), httpx.Response(404), httpx.Response(200, content=good)])
    sleeps = []
    reporter = ListReporter()
    with httpx.Client() as http:
        assert sp.wait_live(http, "https://a.github.io/rpipe-feed/feed.json", reporter, sleep=sleeps.append)
    assert route.call_count == 3 and sleeps == [10, 10]
    assert {e.stage for e in reporter.events} == {"pages"}


@respx.mock
def test_wait_live_gives_up_and_rejects_non_feed():
    respx.get("https://a.github.io/rpipe-feed/feed.json").mock(return_value=httpx.Response(200, content=b"<html>"))
    with httpx.Client() as http:
        assert sp.wait_live(http, "https://a.github.io/rpipe-feed/feed.json", None,
                            timeout_s=30, sleep=lambda s: None) is False


def test_failed_push_is_retried_when_nothing_changed(tmp_path):
    failing = FakeRunner({("git", "diff"): done(1), ("git", "push"): done(1, "", "offline")})
    with pytest.raises(GithubError):
        sp.push(failing, tmp_path, "msg")
    # next night: nothing staged, but the commit is still ahead of GitHub
    retry = FakeRunner({("git", "diff"): done(0), ("git", "rev-list"): done(0, "1\n")})
    assert sp.push(retry, tmp_path, "msg") is True
    assert ["git", "push"] in retry.calls and not any(c[1] == "commit" for c in retry.calls)
    clean = FakeRunner({("git", "diff"): done(0), ("git", "rev-list"): done(0, "0\n")})
    assert sp.push(clean, tmp_path, "msg") is False
