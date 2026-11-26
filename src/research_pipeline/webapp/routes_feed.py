"""The feed: the latest digest's papers (or one earlier day) as cards."""

import re
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from research_pipeline import library_api
from research_pipeline.webapp import present
from research_pipeline.webapp.deps import get_conn, templates

router = APIRouter()
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


VIEWS = ("mine", "following", "mix")


def _view_of(request: Request, has_follows: bool) -> str:
    """The `?view=` choice: mine, following or mix. Anything else, or any choice with no follows, is mine."""
    view = request.query_params.get("view")
    return view if has_follows and view in VIEWS else "mine"


def _page(request: Request, conn: sqlite3.Connection, day: str | None):
    has_follows = bool(library_api.follows(conn))
    view = _view_of(request, has_follows)
    switch = {"switch": has_follows, "view": view,
              "vq": f"?view={view}" if view != "mine" else ""}  # "mine" keeps today's plain addresses
    if view == "following":
        dates = library_api.following_days(conn)
    elif view == "mix":
        dates = library_api.mix_days(conn)
    else:
        dates = library_api.digest_dates(conn)
    if day is None and not dates:
        return templates.TemplateResponse(request, "feed.html", {"nav": "feed", "day": None, **switch})
    day = day or dates[0]
    if day not in dates:
        raise HTTPException(404, "No digest for that day")
    if view == "mine":
        views = library_api.digest_papers(conn, day)
        states = library_api.summarize_states(conn, [v.id for v in views])
        cards = [present.card(v, states.get(v.id)) for v in views]
    else:
        posts = (library_api.following_posts if view == "following" else library_api.mix_posts)(conn, day)
        states = library_api.summarize_states(conn, [p.paper.id for p in posts])
        cards = [present.followed_card(p, states.get(p.paper.id)) for p in posts]
    older = [d for d in dates if d < day]
    ctx = {"nav": "feed", "day": day, "heading": present.day_heading(day), "cards": cards,
           "earlier": older[0] if older else None, **switch}
    return templates.TemplateResponse(request, "feed.html", ctx)


@router.get("/")
def feed(request: Request, conn: Conn):
    return _page(request, conn, None)


@router.get("/day/{day}")
def feed_day(day: str, request: Request, conn: Conn):
    if not _DATE.match(day):
        raise HTTPException(404, "No digest for that day")
    return _page(request, conn, day)
