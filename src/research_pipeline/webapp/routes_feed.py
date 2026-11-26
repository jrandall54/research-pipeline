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


def _page(request: Request, conn: sqlite3.Connection, day: str | None):
    dates = library_api.digest_dates(conn)
    if day is None and not dates:
        return templates.TemplateResponse(request, "feed.html", {"nav": "feed", "day": None})
    day = day or dates[0]
    if day not in dates:
        raise HTTPException(404, "No digest for that day")
    views = library_api.digest_papers(conn, day)
    states = library_api.summarize_states(conn, [v.id for v in views])
    older = [d for d in dates if d < day]
    ctx = {"nav": "feed", "day": day, "heading": present.day_heading(day),
           "cards": [present.card(v, states.get(v.id)) for v in views],
           "earlier": older[0] if older else None}
    return templates.TemplateResponse(request, "feed.html", ctx)


@router.get("/")
def feed(request: Request, conn: Conn):
    return _page(request, conn, None)


@router.get("/day/{day}")
def feed_day(day: str, request: Request, conn: Conn):
    if not _DATE.match(day):
        raise HTTPException(404, "No digest for that day")
    return _page(request, conn, day)
