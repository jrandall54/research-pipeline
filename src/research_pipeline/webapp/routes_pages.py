"""The paper screen, Saved and Search."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from research_pipeline import ids, library_api
from research_pipeline.db import UnknownPaperError
from research_pipeline.webapp import present
from research_pipeline.webapp.deps import get_conn, templates

router = APIRouter()
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
SEARCH_LIMIT = 30


def _cards(conn: sqlite3.Connection, views: list[library_api.PaperView]) -> list[present.CardData]:
    states = library_api.summarize_states(conn, [v.id for v in views])
    return [present.card(v, states.get(v.id)) for v in views]


@router.get("/paper/{paper_id}")
def paper(paper_id: str, request: Request, conn: Conn):
    try:
        view = library_api.get_paper(conn, ids.normalise_paper_id(paper_id))
    except (ValueError, UnknownPaperError):
        raise HTTPException(404, "That paper isn't in your library.") from None
    state = library_api.summarize_states(conn, [view.id]).get(view.id)
    day = library_api.digest_papers(conn, view.digest_date) if view.digest_date else []
    ctx = {"nav": "feed", "d": present.paper_detail(view, state, library_api.summary_provenance(conn, view.id)),
           "day": [(v.id, v.title) for v in day],
           "day_date": view.digest_date,
           "day_heading": present.day_heading(view.digest_date) if view.digest_date else None}
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
