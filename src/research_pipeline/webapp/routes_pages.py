"""The paper screen, Saved and Search."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from research_pipeline import ids, library_api, vault
from research_pipeline.db import UnknownPaperError
from research_pipeline.webapp import present
from research_pipeline.webapp.deps import get_conn, templates

router = APIRouter()
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
SEARCH_LIMIT = 30


def _with_publisher_summary(conn: sqlite3.Connection, v: library_api.PaperView) -> library_api.PaperView:
    """A paper copied in from a followed post has no summary of its own: show the publisher's."""
    if v.summary is None:
        post = library_api.followed_post(conn, v.id)
        if post is not None and post.paper.summary is not None:
            return v.model_copy(update={"summary": post.paper.summary})
    return v


def _cards(conn: sqlite3.Connection, views: list[library_api.PaperView]) -> list[present.CardData]:
    views = [_with_publisher_summary(conn, v) for v in views]
    states = library_api.summarize_states(conn, [v.id for v in views])
    return [present.card(v, states.get(v.id)) for v in views]


def _diagrams_off(request: Request) -> bool:
    """True when the vault's `diagrams` setting is "off" (Phase 9.3): stored diagrams are then hidden too.

    Read on every request, so `rpipe model set-diagrams` takes effect without restarting the app.
    A missing or unreadable config.yaml counts as not off, so a settings problem never breaks the page.
    """
    try:
        return vault.load_config(request.app.state.vault_db.parent / "config.yaml").diagrams == "off"
    except vault.ConfigError:
        return False


@router.get("/paper/{paper_id}")
def paper(paper_id: str, request: Request, conn: Conn):
    try:
        pid = ids.normalise_paper_id(paper_id)
    except ValueError:
        raise HTTPException(404, "That paper isn't in your library.") from None
    try:
        view: library_api.PaperView | None = library_api.get_paper(conn, pid)
    except UnknownPaperError:
        view = None
    # Your own summary wins. Otherwise a followed feed's post (with its summary) stands in.
    post = None
    if view is None or view.summary is None:
        post = library_api.followed_post(conn, pid)
        if post is not None and view is not None and post.paper.summary is None:
            post = None
    if view is None and post is None:
        raise HTTPException(404, "That paper isn't in your library.")
    if post is not None:
        view = post.paper
    assert view is not None
    if _diagrams_off(request):
        view = view.model_copy(update={"diagram": None})
    state = library_api.summarize_states(conn, [view.id]).get(view.id)
    if post is not None:
        day = [p.paper for p in library_api.following_posts(conn, post.digest_date)]
        provenance = None
    else:
        day = library_api.digest_papers(conn, view.digest_date) if view.digest_date else []
        provenance = library_api.summary_provenance(conn, view.id)
    ctx = {"nav": "feed", "d": present.paper_detail(view, state, provenance, post),
           "day": [(v.id, v.title) for v in day],
           "day_date": view.digest_date,
           "day_heading": present.day_heading(view.digest_date) if view.digest_date else None,
           "vq": "?view=following" if post is not None else ""}
    return templates.TemplateResponse(request, "paper.html", ctx)


@router.get("/saved")
def saved(request: Request, conn: Conn):
    cards = _cards(conn, library_api.saved_papers(conn))
    return templates.TemplateResponse(request, "list.html", {
        "nav": "saved", "heading": "Saved", "cards": cards, "empty": "Nothing saved yet.", "search": False})


@router.get("/search")
def search(request: Request, conn: Conn, q: str = ""):
    q = q.strip()
    cards = _cards(conn, library_api.search_papers(conn, q, SEARCH_LIMIT)) if q else []
    return templates.TemplateResponse(request, "list.html", {
        "nav": "search", "heading": "Search", "cards": cards, "search": True, "q": q,
        "empty": f'No papers match "{q}".' if q else None})
